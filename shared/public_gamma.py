"""The public Gamma page's request, its keys, and its config.

A visitor on ``live.neuralstrike.co/gamma`` picks a symbol from the dropdown;
the public process asks options_svc to keep that symbol LIVE. The service adds
it to a HOT SET, which the 1-minute GEX branch publishes beside the three
permanent symbols until its lease runs out. Both ends import this module, so the
stream name, the command shape and the keys cannot drift between the process
that asks and the service that answers. Design:
docs/plans/2026-09-21-public-gamma-any-symbol-roadmap.md, Phase 1.

A hot symbol costs NO Schwab call: the collector already fetches every
dropdown symbol's chain each minute, and the branch only builds a hot symbol
from the chain the collector kept for it (no Term view, decision D6).

⚠ ``STREAM`` is also named in the live Redis ACL user's write selector
(``(%W~cmd:gamma_public +xadd)``, docs/dev-prod-environments.md). Renaming it
here without the ACL makes every public request fail with NOPERM.

On the Tier-1 allow-list beside ``shared.public_scan`` for the same reason: it
holds a validator and config, imports no engine, no bus and nothing that calls
Schwab. ``shared/tests/test_public_gamma.py`` pins its import set.

Missing file / bad TOML / bad value -> the built-in defaults, never a raise.
"""
from repo_paths import GAMMA_PUBLIC_TOML
from shared.config_toml import toml_loader
from shared.symbols import clean_symbol

# ── the request ──────────────────────────────────────────────────────────────

STREAM = "cmd:gamma_public"
COMMAND_TYPE = "gamma_public"

# The service's view of the hot set, for the page: which symbols are live, the
# cap, and the window. Only dropdown symbols can ever appear in it, and the
# dropdown list is public, so it names nothing a visitor typed.
STATUS_VIEW = "options:gamma_public_status"
STATUS_KEY = f"cache:{STATUS_VIEW}"
STATUS_EVENT = "events:options:gamma_public_status"

# The dropdown's list, published by options_svc beside the private page's own
# (``cache:options:gamma_symbols``) so the public page reads a key written FOR
# it, and the worker validates against the same list the page shows.
SYMBOLS_VIEW = "options:gamma_pub_symbols"
SYMBOLS_KEY = f"cache:{SYMBOLS_VIEW}"
SYMBOLS_EVENT = "events:options:gamma_pub_symbols"

# The views the public page draws history for (no Term: decision D6). Flow
# draws from the main payload and needs no history key.
HISTORY_VIEWS = ("GEX", "Charm", "DEX", "Vanna")


def request_command(raw_symbol):
    """The command dict asking to keep ``raw_symbol`` live, or ``None``.

    The symbol goes through ``shared.symbols.clean_symbol`` -- the app's one
    ticker allow-list -- because it becomes part of a Redis KEY NAME on the
    service side. Membership of the dropdown list is the SERVICE's check (it
    holds the list); ``None`` here means the string is not a ticker at all."""
    symbol = clean_symbol(raw_symbol)
    if symbol is None:
        return None
    return {"type": COMMAND_TYPE, "args": {"symbol": symbol}}


# How a request ended. The service logs one per request; the page words the
# ones a visitor can see (``live``/``added`` read from the status view,
# ``full``/``closed`` from its cap and window).
OUTCOMES = ("live", "added", "full", "closed", "expired", "invalid")

OUTCOME_TEXT = {
    "live": "Live, updating every minute.",
    "added": "Loading. The first update arrives within about a minute.",
    "full": "Every live slot is in use. Try again in a few minutes.",
    "closed": "Live updates run while the market is open. Showing the last session if there is one.",
    "expired": "The request waited too long. Trying again.",
    "invalid": "That symbol is not on this page's list.",
}


# ── the config ───────────────────────────────────────────────────────────────

DEFAULTS = {
    "hot": {
        # Symbols kept live at once beyond the three permanent ones. Each costs
        # one snapshot build and about 4 MB of Redis writes a minute; set from
        # the 2026-09-22 Phase 0 measurement (tools/measure_gamma_public.py).
        "cap": 8,
        # Minutes one request keeps a symbol live. The page renews it while
        # open (``renew_min``), so a symbol nobody watches drops out.
        "lease_min": 15,
        # Minutes a hot symbol's keys outlive its last write, so a page left
        # open across a short gap still draws.
        "keep_min": 30,
    },
    "page": {
        # How often an open page renews its symbol's lease. Under lease_min, so
        # one missed renewal does not drop a symbol someone is watching.
        "renew_min": 5,
    },
    "limits": {
        # A request older than this is dropped: it covers a replayed backlog and
        # a queue that has fallen behind.
        "max_wait_sec": 120,
    },
    "visitor": {
        # Symbol changes one visitor may send an hour, counted in the public
        # process by address and never stored. Renewals of the symbol on screen
        # are not counted. It stops ONE visitor cycling through the list and
        # holding every live slot.
        "picks_per_hour": 30,
    },
}

load, reset_cache = toml_loader(GAMMA_PUBLIC_TOML, DEFAULTS,
                                label="gamma_public.toml")


def _num(section, key, *, minimum):
    """A config number of the default's own type, at least ``minimum``, or the
    default. A bool is refused: ``True`` is an int and would read as 1."""
    default = DEFAULTS[section][key]
    raw = (load().get(section) or {}).get(key, default)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return default
    value = type(default)(raw)
    return value if value >= minimum else default


def hot() -> dict:
    """``{"cap", "lease_min", "keep_min"}``. A cap of 0 turns hot symbols off:
    only the three permanent symbols stay live."""
    return {"cap": _num("hot", "cap", minimum=0),
            "lease_min": _num("hot", "lease_min", minimum=1),
            "keep_min": _num("hot", "keep_min", minimum=1)}


def renew_min() -> int:
    return _num("page", "renew_min", minimum=1)


def max_wait_sec() -> int:
    return _num("limits", "max_wait_sec", minimum=10)


def picks_per_hour() -> int:
    return _num("visitor", "picks_per_hour", minimum=1)

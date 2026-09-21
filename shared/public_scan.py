"""The public Strategy Finder's request, its keys, and its config.

The public live origin (``webgui/live_main.py``) is read-only by four layers,
and this module is the ONE thing it may write: a request to scan one symbol,
on its own stream. Both ends import it, so the stream name, the command shape
and the result keys cannot drift between the process that asks and the
service that answers. Design:
docs/plans/2026-09-21-public-strategy-finder-roadmap.md, Phase 1.

⚠ ``STREAM`` is also named in the live Redis ACL user's write selector
(``(%W~cmd:finder_public +xadd)``, docs/dev-prod-environments.md). Renaming it
here without the ACL makes every public request fail with NOPERM.

On the Tier-1 allow-list for the same reason as ``shared.symbols``: it holds a
validator and config, imports no engine, no bus and nothing that calls Schwab.
``shared/tests/test_public_scan.py`` pins its import set.

Missing file / bad TOML / bad value -> the built-in defaults, never a raise.
"""
from repo_paths import FINDER_PUBLIC_TOML
from shared.config_toml import toml_loader
from shared.symbols import clean_symbol

# ── the request ──────────────────────────────────────────────────────────────

STREAM = "cmd:finder_public"
COMMAND_TYPE = "public_scan"

STATUS_VIEW = "options:finder_public_status"
STATUS_KEY = f"cache:{STATUS_VIEW}"
STATUS_EVENT = "events:options:finder_public_status"


def result_view(symbol) -> str:
    """The cache VIEW (Tier-1 spelling) holding one symbol's public scan."""
    return f"options:swing_pub:{str(symbol).strip().upper()}"


def result_key(symbol) -> str:
    """The cache KEY holding one symbol's public scan. Never the private
    ``cache:options:swing`` slot, which holds whatever the owner last scanned."""
    return f"cache:{result_view(symbol)}"


def result_event(symbol) -> str:
    return f"events:{result_view(symbol)}"


def request_command(raw_symbol):
    """The command dict for a public scan of ``raw_symbol``, or ``None``.

    The symbol goes through ``shared.symbols.clean_symbol`` -- the app's one
    ticker allow-list -- because it becomes part of a Redis KEY NAME on the
    service side. ``None`` means refuse: nothing is enqueued for a string the
    allow-list rejects."""
    symbol = clean_symbol(raw_symbol)
    if symbol is None:
        return None
    return {"type": COMMAND_TYPE, "args": {"symbol": symbol}}


# How a request ended. The service publishes one of these per symbol in the
# status view; the public page words them (OUTCOME_TEXT) rather than showing a
# code. "scanned" and "cached" carry results; every other code is a refusal.
OUTCOMES = ("scanned", "cached", "duplicate", "closed", "budget", "no_options",
            "expired", "invalid", "error")

OUTCOME_TEXT = {
    "scanned": "Scanned just now.",
    "cached": "Showing a scan from the last few minutes.",
    "duplicate": "This symbol was just checked. Try again in a minute.",
    "closed": "Scans run while the market is open. Showing the last scan if there is one.",
    "budget": "Today's public scans are used up. They reset tomorrow.",
    "no_options": "No listed options were found for this symbol.",
    "expired": "The request waited too long in the queue. Please try again.",
    "invalid": "That is not a symbol this page can scan.",
    "error": "The scan failed. Please try again later.",
}


# ── the config ───────────────────────────────────────────────────────────────

DEFAULTS = {
    # The one filter set every public scan runs (roadmap §2). The deltas and
    # the credit floor are the private page's own defaults. The 90-day cap
    # bounds cost: a public scan never stops to ask which expirations to load,
    # so a daily-expiry name (SPY, QQQ, $SPX) scans all of its ~60 in range.
    "scan": {
        "dte_min": 0,
        "dte_max": 90,
        "put_d_min": -0.20,
        "put_d_max": -0.10,
        "call_d_min": 0.10,
        "call_d_max": 0.20,
        "min_cr_fraction": 0.10,
    },
    "limits": {
        # A result younger than this is served instead of scanning again.
        "result_ttl_min": 15,
        # A symbol whose last scan is younger than this is not re-run.
        "dedup_sec": 60,
        # A symbol that turned out to have no options is not re-run for this
        # long. Without it, one visitor typing junk tickers could spend the
        # whole day's budget, re-running each junk symbol every minute.
        "negative_ttl_min": 240,
        # A request older than this is dropped unscanned: it covers a replayed
        # backlog and a queue that has fallen behind.
        "max_wait_sec": 180,
        # Scans per trading day, all visitors together. A refusal costs nothing.
        "daily_budget": 200,
        # How long a symbol's result key lives. Visitors choose the symbols, so
        # without an expiry the key count would grow without limit.
        "result_keep_hours": 24,
    },
    "visitor": {
        # Scan requests one visitor may make an hour, counted in the public
        # process by address and never stored. The daily budget is the hard
        # cap on cost; this stops ONE visitor filling the queue for everyone.
        "scans_per_hour": 10,
    },
    "display": {
        # Per-leg bid/ask/mark on the public page. OFF until the owner settles
        # Schwab's market-data terms (roadmap decision D2).
        "show_leg_quotes": False,
        # The best N ideas of each strategy type a public result keeps. Trimmed
        # when the result is WRITTEN, so every visitor downloads less; the rest
        # are counted as "not shown". The private Finder keeps 25.
        "rows_per_type": 5,
    },
    "warm": {
        # Scanned once each morning at [slots.finder_public] warm, through the
        # same worker and refusals as a visitor's request, so the page's default
        # symbol has a fresh result. Each costs one scan of the daily budget.
        "symbols": ["SPY", "QQQ"],
    },
}

load, reset_cache = toml_loader(FINDER_PUBLIC_TOML, DEFAULTS,
                                label="finder_public.toml")


def _num(section, key, *, minimum):
    """A config number of the default's own type, at least ``minimum``, or the
    default. A bool is refused: ``True`` is an int and would read as 1."""
    default = DEFAULTS[section][key]
    raw = (load().get(section) or {}).get(key, default)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return default
    value = type(default)(raw)
    return value if value >= minimum else default


def scan_pin() -> dict:
    """The keyword arguments every public scan passes to ``compute.swing_scan``."""
    scan = DEFAULTS["scan"]
    return {k: _num("scan", k, minimum=-1.0 if k.startswith(("put_", "call_"))
                    else 0) for k in scan}


def limits() -> dict:
    return {k: _num("limits", k, minimum=1 if k != "dedup_sec" else 0)
            for k in DEFAULTS["limits"]}


def scans_per_hour() -> int:
    return _num("visitor", "scans_per_hour", minimum=1)


def rows_per_type() -> int:
    return _num("display", "rows_per_type", minimum=1)


def warm_symbols() -> list:
    """The morning warm-up list, every entry through the ticker allow-list.
    A bad entry is dropped, never sent; a missing or malformed list is the
    default."""
    raw = (load().get("warm") or {}).get("symbols", DEFAULTS["warm"]["symbols"])
    if not isinstance(raw, list):
        raw = DEFAULTS["warm"]["symbols"]
    out = []
    for item in raw:
        sym = clean_symbol(item)
        if sym and sym not in out:
            out.append(sym)
    return out


def show_leg_quotes() -> bool:
    raw = (load().get("display") or {}).get("show_leg_quotes",
                                           DEFAULTS["display"]["show_leg_quotes"])
    return raw if isinstance(raw, bool) else DEFAULTS["display"]["show_leg_quotes"]

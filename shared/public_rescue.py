"""The public Rescue form's requests, their keys, and its config.

The public live origin (``webgui/live_main.py``) is read-only by four layers.
Since 2026-09-21 it may make exactly two kinds of write: the Strategy Finder's
scan request (``shared.public_scan``) and the two requests below, on their own
stream. Both ends import this module, so the stream name, the command shapes,
the validators and the result keys cannot drift between the process that asks
and the service that answers. Design:
docs/plans/2026-09-21-public-rescue-adhoc-roadmap.md.

Two requests:

* **ladder** - a symbol's expirations and each expiration's STRIKES, with no
  bid, ask or mark (decision D1). What the form's dropdowns offer. Keyed by
  symbol and shared by every visitor on that symbol.
* **compute** - the ranked rescue menu for one trade a visitor describes.
  Keyed by a hash of the normalized trade, so two visitors entering the same
  trade share one compute and the key name says nothing about the trade.

⚠ The visitor controls every field of a trade, not one string. Every field is
normalized here BEFORE anything is written - an unknown key is dropped, a
non-finite number or a boolean is refused - and the worker runs the same
normalizer again on what it reads. A NaN reaching a strike is the repo's
most-repeated bug class (CLAUDE.md, "A NaN clamps to the HIGH bound").

⚠ ``STREAM`` is also named in the live Redis ACL user's write selector
(``(%W~cmd:rescue_public +xadd)``, docs/dev-prod-environments.md). Renaming it
here without the ACL makes every public request fail with NOPERM.

On the Tier-1 allow-list beside ``shared.public_scan``: stdlib, config and the
ticker allow-list only. ``shared/tests/test_public_rescue.py`` pins the imports.

Missing file / bad TOML / bad value -> the built-in defaults, never a raise.
"""
import datetime as _dt
import hashlib
import json
import math

from repo_paths import RESCUE_PUBLIC_TOML
from shared.config_toml import toml_loader
from shared.symbols import clean_symbol

# ── the stream and the keys ──────────────────────────────────────────────────

STREAM = "cmd:rescue_public"
LADDER_TYPE = "public_rescue_ladder"
COMPUTE_TYPE = "public_rescue"

STATUS_VIEW = "options:rescue_public_status"
STATUS_KEY = f"cache:{STATUS_VIEW}"
STATUS_EVENT = "events:options:rescue_public_status"


def ladder_view(symbol) -> str:
    """The cache VIEW (Tier-1 spelling) of the ONE public chain key per symbol.

    Shared by the public Rescue form, the Calculator and the Simulator
    (``shared.public_tools.chain_view`` returns this same view), so visitors on
    any of the three share one fetch per symbol. It carries expirations and
    strikes always, and quotes only while ``public_scan.show_leg_quotes()`` is
    on - never otherwise (decision D1)."""
    return f"options:pub_chain:{str(symbol).strip().upper()}"


def result_view(key) -> str:
    """The cache VIEW holding the rescue menu for the trade hashed to ``key``.
    Never the private ``cache:options:rescue:adhoc`` slot, which holds whatever
    the owner last computed."""
    return f"options:rescue_pub:{key}"


def answer_view(key) -> str:
    """The cache VIEW holding how the request hashed to ``key`` ended. Every
    request gets one - a refusal as much as a result - so a page waiting on its
    own request reads its own answer and never a map of everyone's."""
    return f"options:rescue_pub_answer:{key}"


def cache_key(view) -> str:
    return f"cache:{view}"


def event(view) -> str:
    return f"events:{view}"


# ── validation ───────────────────────────────────────────────────────────────

# What ``compute_rescue_adhoc`` advises on (``rescue.RESCUE_ADHOC_SUPPORTED``'s
# spec codes). No stock structures: the private form excludes them too.
SINGLE = ("LONG_CALL", "LONG_PUT", "NAKED_CALL", "NAKED_PUT")
SPREADS = ("PCS", "CCS", "IC", "VERT_CALL_DEBIT", "VERT_PUT_DEBIT")
RANGE = ("CONDOR_CALL", "CONDOR_PUT", "BUTTERFLY_CALL", "BUTTERFLY_PUT")
STRATEGIES = SINGLE + SPREADS + RANGE

MAX_QTY = 100
MAX_LEGS = 4
MAX_STRIKE = 1_000_000.0
# A per-share credit or debit past this is not an option price.
MAX_CREDIT = 100_000.0
# Expirations older than this are refused: a trade already expired has nothing
# to rescue. Later ones are left to the listed-expiration check in the worker.
_PAST_GRACE_DAYS = 1
# Expirations further out than this are refused before anything is written.
# Listed equity and index options (LEAPS included) run to about 2.5 years, so
# three years clears every real listing; without it ``9999-12-31`` was a valid
# expiry, a visitor-chosen string reaching a cache key and a date computation.
MAX_EXPIRY_DAYS = 3 * 365
_KEY_LEN = 24
_FLAT_STRIKES = ("short_strike", "long_strike", "call_short", "call_long")


def _finite(v, *, lo=None, hi=None):
    """A finite float in (lo, hi], or None. A bool is refused: ``True`` is an
    int and would read as 1.0."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    try:
        f = float(v)
    except OverflowError:            # an int too large for a float
        return None
    if not math.isfinite(f):
        return None
    if lo is not None and not f > lo:
        return None
    if hi is not None and f > hi:
        return None
    return f


def _strike(v):
    return _finite(v, lo=0.0, hi=MAX_STRIKE)


def _qty(v):
    """A whole number 1..MAX_QTY, or None. ``3.0`` is 3; ``2.5`` is refused."""
    f = _finite(v)
    if f is None or f != int(f):
        return None
    n = int(f)
    return n if 1 <= n <= MAX_QTY else None


def clean_expiry(raw, today=None):
    """An ISO date ``YYYY-MM-DD`` that is not already past and at most
    ``MAX_EXPIRY_DAYS`` after ``today``, or None."""
    if not isinstance(raw, str) or len(raw.strip()) != 10:
        return None
    try:
        day = _dt.date.fromisoformat(raw.strip())
    except ValueError:
        return None
    today = today or _dt.date.today()
    if day < today - _dt.timedelta(days=_PAST_GRACE_DAYS):
        return None
    if day > today + _dt.timedelta(days=MAX_EXPIRY_DAYS):
        return None
    return day.isoformat()


def _clean_legs(raw):
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_LEGS:
        return None
    out = []
    for leg in raw:
        if not isinstance(leg, dict):
            return None
        right = str(leg.get("right") or "").strip().upper()
        side = str(leg.get("side") or "").strip().lower()
        strike, qty = _strike(leg.get("strike")), _qty(leg.get("qty"))
        if right not in ("CALL", "PUT") or side not in ("long", "short") \
                or strike is None or qty is None:
            return None
        out.append({"right": right, "side": side, "strike": strike, "qty": qty})
    return out


def clean_spec(raw, today=None):
    """The trade ``raw`` describes, normalized to exactly the fields
    ``compute_rescue_adhoc`` reads, or None.

    Accepts the two shapes the private form's ``adhoc_spec_from_legs`` builds:
    flat strikes (single options, verticals, iron condor) or a ``legs`` list
    (condors and butterflies). Anything else is dropped; any field that is
    present but unusable refuses the whole trade rather than being guessed at."""
    if not isinstance(raw, dict):
        return None
    symbol = clean_symbol(raw.get("symbol"))
    strategy = str(raw.get("strategy") or "").strip().upper()
    expiration = clean_expiry(raw.get("expiration"), today)
    quantity = _qty(raw.get("quantity", 1))
    credit_raw = raw.get("entry_credit")
    credit = 0.0 if credit_raw is None else _finite(credit_raw)
    if symbol is None or strategy not in STRATEGIES or expiration is None \
            or quantity is None or credit is None or abs(credit) > MAX_CREDIT:
        return None
    spec = {"symbol": symbol, "strategy": strategy, "expiration": expiration,
            "quantity": quantity, "entry_credit": credit}
    if strategy in RANGE:
        legs = _clean_legs(raw.get("legs"))
        if legs is None:
            return None
        spec["legs"] = legs
        return spec
    for name in _FLAT_STRIKES:
        if raw.get(name) is None:
            continue
        strike = _strike(raw.get(name))
        if strike is None:
            return None
        spec[name] = strike
    if "short_strike" not in spec:
        return None
    if strategy in SPREADS and "long_strike" not in spec:
        return None
    if strategy == "IC" and not ("call_short" in spec and "call_long" in spec):
        return None
    return spec


def _hash(*parts) -> str:
    text = json.dumps(parts, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:_KEY_LEN]


def spec_key(spec) -> str:
    """The result key of a NORMALIZED trade. Content-addressed: the same trade
    always hashes the same.

    ⚠ The hash keeps the trade out of the key NAME, nothing more. The result
    stored under it holds the whole position for ``result_keep_min``, readable
    by anything holding a Redis read credential - the public process included.
    And it is unsalted: a visitor who enters an exact trade and is served a
    cached answer learns someone entered it in the last few minutes."""
    return _hash("compute", spec)


def structure_key(spec) -> str:
    """The trade LESS its prices and size: symbol, strategy, expiration and
    strikes. Every price a visitor types is a different trade to the cache, so
    the worker caps runs per structure on this key instead."""
    return _hash("structure", {k: v for k, v in spec.items()
                               if k not in ("entry_credit", "quantity")})


def spec_strikes(spec):
    """``[(side, strike), ...]`` for every strike the trade names, side being
    ``"call"`` or ``"put"``."""
    strategy = spec.get("strategy")
    if "legs" in spec:
        return [(leg["right"].lower(), leg["strike"]) for leg in spec["legs"]]
    if strategy == "IC":
        return [("put", spec["short_strike"]), ("put", spec["long_strike"]),
                ("call", spec["call_short"]), ("call", spec["call_long"])]
    side = "call" if strategy in ("CCS", "LONG_CALL", "NAKED_CALL",
                                  "VERT_CALL_DEBIT") else "put"
    return [(side, spec[k]) for k in ("short_strike", "long_strike") if k in spec]


def strikes_on_ladder(spec, ladder) -> bool:
    """Whether every strike the trade names is listed, on its side, in
    ``ladder`` (``{"call": [...], "put": [...]}`` for the trade's expiration).
    Bounds what a request can ask for to strikes that exist, rather than any
    positive number a visitor can type."""
    listed = {side: set(ladder.get(side) or []) for side in ("call", "put")}
    return all(strike in listed[side] for side, strike in spec_strikes(spec))


def ladder_key(symbol, expiry=None) -> str:
    """The answer key of a strikes-list request. Hashed like a trade's, so the
    answer keys never spell out which symbols visitors asked for."""
    return _hash("ladder", symbol, expiry or "")


def is_key(raw) -> bool:
    return (isinstance(raw, str) and len(raw) == _KEY_LEN
            and all(c in "0123456789abcdef" for c in raw))


# ── the commands ─────────────────────────────────────────────────────────────

def ladder_command(raw_symbol, raw_expiry=None, today=None):
    """The command for a strikes list, or None. ``raw_expiry`` asks for one more
    expiration's strikes on top of what is already loaded."""
    symbol = clean_symbol(raw_symbol)
    if symbol is None:
        return None
    args = {"symbol": symbol}
    if raw_expiry is not None:
        expiry = clean_expiry(raw_expiry, today)
        if expiry is None:
            return None
        args["expiry"] = expiry
    return {"type": LADDER_TYPE, "args": args}


def compute_command(raw_spec, today=None):
    """The command for a rescue menu, or None."""
    spec = clean_spec(raw_spec, today)
    if spec is None:
        return None
    return {"type": COMPUTE_TYPE, "args": {"spec": spec}}


def request_key(command) -> str | None:
    """The answer key a command's outcome is written under, or None for a
    command this module would not have built."""
    if not isinstance(command, dict):
        return None
    args = command.get("args") if isinstance(command.get("args"), dict) else {}
    if command.get("type") == LADDER_TYPE:
        symbol = clean_symbol(args.get("symbol"))
        return None if symbol is None else ladder_key(symbol, args.get("expiry"))
    if command.get("type") == COMPUTE_TYPE:
        spec = clean_spec(args.get("spec"))
        return None if spec is None else spec_key(spec)
    return None


# How a request ended. "done" and "cached" carry a result; every other code is
# a refusal, and each is decided before any Schwab call except "error".
OUTCOMES = ("done", "cached", "duplicate", "throttled", "closed", "budget",
            "not_listed", "off_ladder", "no_options", "expired", "invalid", "error")

OUTCOME_TEXT = {
    "done": "Rescue options computed just now.",
    "cached": "Showing rescue options computed in the last few minutes.",
    "duplicate": "This was just asked for. Try again in a minute.",
    "throttled": ("This trade was just computed several times with other prices. "
                  "Try again in a few minutes."),
    "closed": "Rescue runs while the market is open.",
    "budget": "Today's public rescues are used up. They reset tomorrow.",
    "not_listed": "That expiration is not listed for this symbol.",
    "off_ladder": "A strike in this trade is not listed for that expiration.",
    "no_options": "No listed options were found for this symbol.",
    "expired": "The request waited too long in the queue. Please try again.",
    "invalid": "That trade could not be read. Check the legs and try again.",
    "error": "The request failed. Please try again later.",
}


# ── the config ───────────────────────────────────────────────────────────────

DEFAULTS = {
    "limits": {
        # A rescue menu younger than this is served instead of recomputing.
        # Short: the menu reprices the trade live, and the market moves.
        "result_ttl_min": 5,
        # A strikes list younger than this is served. Strikes rarely change
        # during a session; a new expiration is listed at most daily.
        "ladder_ttl_min": 60,
        # The same request inside this many seconds of its last run is not re-run.
        "dedup_sec": 60,
        # Runs of one trade STRUCTURE (the trade less its prices and size) per
        # ``result_ttl_min``. Each price typed is a new trade to the cache, so
        # without this one structure could be rerun for every price.
        "structure_runs": 3,
        # A request older than this is dropped unrun: it covers a replayed
        # backlog and a queue that has fallen behind.
        "max_wait_sec": 120,
        # How long each kind of key lives. Visitors choose the symbols and the
        # trades, so every key must expire or the count grows without limit.
        "result_keep_min": 30,
        "ladder_keep_min": 180,
        "answer_keep_min": 15,
    },
    "visitor": {
        # Requests one visitor may make an hour, counted in the public process
        # by address and never stored. The daily budgets cap the cost; these
        # stop ONE visitor filling the queue for everyone.
        "computes_per_hour": 20,
        "ladders_per_hour": 60,
    },
    "budget": {
        # Schwab-spending public requests per trading day across the Rescue
        # form, the Calculator and the Simulator TOGETHER - one Schwab
        # allowance, so one budget (services/options_svc/public_budget.py).
        # A request refused for any other reason never counts against it.
        "daily_budget": 600,
    },
}

load, reset_cache = toml_loader(RESCUE_PUBLIC_TOML, DEFAULTS,
                                label="rescue_public.toml")


def _num(section, key, *, minimum):
    default = DEFAULTS[section][key]
    raw = (load().get(section) or {}).get(key, default)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return default
    if not math.isfinite(raw):      # TOML accepts nan/inf; int() of them raises
        return default
    value = type(default)(raw)
    return value if value >= minimum else default


def limits() -> dict:
    return {k: _num("limits", k, minimum=1 if k != "dedup_sec" else 0)
            for k in DEFAULTS["limits"]}


def computes_per_hour() -> int:
    return _num("visitor", "computes_per_hour", minimum=1)


def ladders_per_hour() -> int:
    return _num("visitor", "ladders_per_hour", minimum=1)


def budget() -> int:
    """The ONE daily budget every public worker spends from."""
    return _num("budget", "daily_budget", minimum=1)

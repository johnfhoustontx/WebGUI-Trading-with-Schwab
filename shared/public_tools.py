"""The public Calculator and Simulator's requests, their keys, and its config.

The public live origin (``webgui/live_main.py``) is read-only by four layers,
with a short list of named writes: the Strategy Finder's scan request
(``shared.public_scan``), the Rescue form's two requests
(``shared.public_rescue``), and the requests below. Both ends import this
module, so the stream names, the command shapes, the validators and the result
keys cannot drift between the process that asks and the service that answers.
Design: docs/plans/2026-09-21-public-calculator-simulator-design.md.

Two streams, and the split is the point:

* ``TOOLS_STREAM`` - requests that SPEND Schwab calls: a symbol's chain, one
  more expiration, a trade rating, a Simulator snapshot and its expirations.
* ``MATH_STREAM`` - pure pricing over data already held: reprice a position,
  imply one contract's volatility, run a Simulator what-if sweep.

⚠ One stream would queue every reprice a visitor's edit triggers behind
whatever snapshot fetch is ahead of it - seconds of Schwab round trips - and
the page would freeze while it waited. A snapshot fetch must never stall
repricing, which is why the worker reads the two on separate loops.

⚠ The visitor controls every field of a request, not one string. Every field is
normalized here BEFORE anything is written - an unknown key is dropped, a
non-finite number or a boolean is refused, and one unusable field refuses the
whole request rather than being guessed at - and the worker runs the same
builders again on what it reads. A NaN reaching a strike, a spot or an IV is
the repo's most-repeated bug class (CLAUDE.md, "A NaN clamps to the HIGH
bound"). The number primitives are ``shared.public_rescue``'s, imported rather
than copied, so the two forms cannot disagree about what a number is.

⚠ ``TOOLS_STREAM`` and ``MATH_STREAM`` are also named in the live Redis ACL
user's write selectors (docs/dev-prod-environments.md, section 2, steps 4e and
4f). Renaming one here without the ACL makes every public request on it fail
with NOPERM.

On the Tier-1 allow-list beside ``shared.public_rescue``: stdlib, config, the
ticker allow-list and the Rescue validators only.
``shared/tests/test_public_tools.py`` pins the imports.

Missing file / bad TOML / bad value -> the built-in defaults, never a raise.
"""
import math

from repo_paths import TOOLS_PUBLIC_TOML
from shared import public_rescue as _pr
from shared.config_toml import toml_loader
from shared.symbols import clean_symbol

# ── the streams and the keys ─────────────────────────────────────────────────

TOOLS_STREAM = "cmd:tools_public"
MATH_STREAM = "cmd:tools_public_math"
TOOLS_TYPE = "public_tool"
MATH_TYPE = "public_math"
TOOLS_KINDS = ("chain", "expiry", "rate", "sim_snapshot", "sim_expiry")
MATH_KINDS = ("price", "iv", "sweep")

STATUS_VIEW = "options:tools_public_status"
STATUS_KEY = f"cache:{STATUS_VIEW}"


def chain_view(symbol) -> str:
    """The cache VIEW holding one symbol's public chain. ⚠ The SAME key the
    Rescue form's strikes list uses, deliberately: one public chain per symbol,
    so a visitor on the Calculator and one on Rescue share one fetch."""
    return _pr.ladder_view(symbol)


def result_view(key) -> str:
    """The cache VIEW holding the result of the request hashed to ``key``."""
    return f"options:tools_pub:{key}"


def answer_view(key) -> str:
    """The cache VIEW holding how the request hashed to ``key`` ended. Every
    request gets one - a refusal as much as a result - so a page waiting on its
    own request reads its own answer and never a map of everyone's."""
    return f"options:tools_pub_answer:{key}"


def cache_key(view) -> str:
    return f"cache:{view}"


def event(view) -> str:
    return f"events:{view}"


# ── validation ───────────────────────────────────────────────────────────────

MAX_LEGS = 8
MAX_STRIKE = 1_000_000.0
MAX_SPOT = 1_000_000.0
# A per-share price past this is not an option or share price.
MAX_PREMIUM = 100_000.0
MAX_IV = 5.0                  # 500% annualised: past any listed option's IV
MAX_RATE = 0.20
MAX_IVADJ = 1.0
NUM_STRIKES = (5, 60)         # rows in the Calculator's P&L matrix
MAX_SWEEP_DAYS = 366.0
MAX_CODE_LEN = 32
_CODE_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_")


def _in_range(v, lo, hi):
    """A finite float in the CLOSED range [lo, hi], or None. ``_pr._finite``'s
    lower bound is exclusive, which is right for a strike and wrong for a rate
    or an IV adjustment, where 0 is a real and common value."""
    f = _pr._finite(v)
    return f if f is not None and lo <= f <= hi else None


def _whole(v, lo, hi):
    """A whole number in [lo, hi], or None. ``24.0`` is 24; ``24.5`` is refused."""
    f = _pr._finite(v)
    if f is None or f != int(f):
        return None
    n = int(f)
    return n if lo <= n <= hi else None


# The strategy/structure codes the engines know: ``rate_trade.CALC_TO_SCORER``'s
# keys, which ``shared/tests/test_cross_tier_mirrors.py`` pins to both that map
# and the Calculator's templates (this module cannot import either). Any other
# code is folded to ``CUSTOM`` - the engines treat an unknown code as CUSTOM
# anyway, and leaving it would make PCSA, PCSB, ... distinct cache, dedup and
# structure-cap keys for one and the same rating.
STRUCTURE_CODES = (
    "LONG_CALL", "LONG_PUT", "NAKED_CALL", "NAKED_PUT", "PCS", "CCS",
    "VERT_CALL_DEBIT", "VERT_PUT_DEBIT", "LONG_STRADDLE", "SHORT_STRADDLE",
    "LONG_STRANGLE", "SHORT_STRANGLE", "IC", "CONDOR_CALL", "CONDOR_PUT",
    "BUTTERFLY_CALL", "BUTTERFLY_PUT", "IRON_BUTTERFLY", "CALENDAR_CALL",
    "CALENDAR_PUT", "DIAGONAL_CALL", "DIAGONAL_PUT", "COVERED_CALL",
    "PROTECTIVE_PUT", "COLLAR", "CUSTOM",
)


def _known_code(raw):
    """``_clean_code``, then an unknown code folded to ``CUSTOM``. None only for
    something that is not a code at all."""
    code = _clean_code(raw)
    if code is None:
        return None
    return code if code in STRUCTURE_CODES else "CUSTOM"


def _clean_code(raw):
    """A strategy or structure code - ``PCS``, ``COVERED_CALL`` - or None.
    Only ASCII letters and underscores, so the code can name nothing but a code.

    ⚠ The characters are checked BEFORE upper-casing: ``"ß".upper()`` is
    ``"SS"``, so a check after it would let non-ASCII input through as a
    different, valid-looking code."""
    if not isinstance(raw, str):
        return None
    code = raw.strip()
    if not 1 <= len(code) <= MAX_CODE_LEN or not set(code) <= _CODE_CHARS:
        return None
    return code.upper()


def _clean_calc_leg(raw, today):
    """One Calculator leg, normalized to exactly the six keys of the app's
    leg dict, or None.

    ⚠ A share leg's ``qty`` counts 100-share LOTS, like an option's contracts,
    and its ``strike`` and ``expiry`` are forced to None whatever was sent: a
    stale expiry on a share leg moves the pricing horizon (CLAUDE.md, "A STOCK
    leg is 100-share LOTS")."""
    if not isinstance(raw, dict):
        return None
    option_type = str(raw.get("option_type") or "").strip().lower()
    side = str(raw.get("side") or "").strip().lower()
    qty = _pr._qty(raw.get("qty"))
    # A missing premium refuses the leg rather than reading as 0: the page
    # always sends one, and 0.0 is a real price (a worthless option). It is the
    # per-share price paid or received - the SIDE carries the sign - so a
    # negative one is refused.
    premium = _in_range(raw.get("premium"), 0.0, MAX_PREMIUM)
    if option_type not in ("call", "put", "stock") or side not in ("long", "short") \
            or qty is None or premium is None:
        return None
    if option_type == "stock":
        strike = expiry = None
    else:
        strike = _pr._finite(raw.get("strike"), lo=0.0, hi=MAX_STRIKE)
        expiry = _pr.clean_expiry(raw.get("expiry"), today)
        if strike is None or expiry is None:
            return None
    return {"option_type": option_type, "side": side, "qty": qty,
            "premium": premium, "strike": strike, "expiry": expiry}


def _clean_sim_leg(raw, today):
    """One Simulator leg - the ``kind``/``strike``/``expiry``/``side``/``qty``
    shape its engines price - or None. Options only: the Simulator's engines
    have no share concept."""
    if not isinstance(raw, dict):
        return None
    kind = str(raw.get("kind") or "").strip().lower()
    side = str(raw.get("side") or "").strip().lower()
    strike = _pr._finite(raw.get("strike"), lo=0.0, hi=MAX_STRIKE)
    expiry = _pr.clean_expiry(raw.get("expiry"), today)
    qty = _pr._qty(raw.get("qty"))
    if kind not in ("call", "put") or side not in ("long", "short") \
            or strike is None or expiry is None or qty is None:
        return None
    return {"kind": kind, "strike": strike, "expiry": expiry, "side": side,
            "qty": qty}


def _clean_legs(raw, clean_one, today):
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_LEGS:
        return None
    out = [clean_one(leg, today) for leg in raw]
    return None if any(leg is None for leg in out) else out


def _math_args(raw, today):
    kind = raw.get("kind")
    symbol = clean_symbol(raw.get("symbol"))
    if symbol is None:
        return None
    if kind == "price":
        # ⚠ No ``price_rows``: the worker derives the matrix's price axis
        # itself, so a visitor cannot ask for an arbitrarily large grid.
        args = {
            "kind": kind, "symbol": symbol,
            "strategy": _known_code(raw.get("strategy")),
            "spot": _pr._finite(raw.get("spot"), lo=0.0, hi=MAX_SPOT),
            "iv": _pr._finite(raw.get("iv"), lo=0.0, hi=MAX_IV),
            "rate": _in_range(raw.get("rate"), 0.0, MAX_RATE),
            "ivadj": _in_range(raw.get("ivadj"), -MAX_IVADJ, MAX_IVADJ),
            "qty": _pr._qty(raw.get("qty")),
            "expiry": _pr.clean_expiry(raw.get("expiry"), today),
            "legs": _clean_legs(raw.get("legs"), _clean_calc_leg, today),
            "num_strikes": _whole(raw.get("num_strikes"), *NUM_STRIKES),
        }
    elif kind == "iv":
        # No mark: the worker reads it off the chain it holds, so the IV it
        # implies is from a real quote rather than a number a visitor typed.
        option_type = str(raw.get("option_type") or "").strip().lower()
        args = {
            "kind": kind, "symbol": symbol,
            "expiry": _pr.clean_expiry(raw.get("expiry"), today),
            "strike": _pr._finite(raw.get("strike"), lo=0.0, hi=MAX_STRIKE),
            "option_type": option_type if option_type in ("call", "put") else None,
        }
    elif kind == "sweep":
        args = {
            "kind": kind, "symbol": symbol,
            "dt": _in_range(raw.get("dt"), 0.0, MAX_SWEEP_DAYS),
            "legs": _clean_legs(raw.get("legs"), _clean_sim_leg, today),
        }
    else:
        return None
    return None if any(v is None for v in args.values()) else args


# A reloading page needs its legs' expirations and no more; each one is Schwab
# work the visitor chooses, so two is the cap.
MAX_SNAPSHOT_EXPIRIES = 2


def _clean_expiries(raw, today):
    """A snapshot request's OPTIONAL expirations, sorted and de-duplicated, or
    ``()`` for none, or None when any entry is unusable (the whole request is
    then refused). ``[]`` and an absent list are the same request."""
    if raw is None:
        return ()
    if not isinstance(raw, list) or len(raw) > MAX_SNAPSHOT_EXPIRIES:
        return None
    out = [_pr.clean_expiry(e, today) for e in raw]
    if any(e is None for e in out):
        return None
    return tuple(sorted(set(out)))


def _tools_args(raw, today):
    kind = raw.get("kind")
    symbol = clean_symbol(raw.get("symbol"))
    if symbol is None:
        return None
    args = {"kind": kind, "symbol": symbol}
    if kind == "chain":
        return args
    if kind == "sim_snapshot":
        # When a public snapshot has expired, the page asks for a new one with
        # its legs' expirations, so they come back with the load rather than
        # one Schwab fetch each. Omitted when empty, so a bare request keeps
        # the key it always had.
        exps = _clean_expiries(raw.get("expiries"), today)
        if exps is None:
            return None
        if exps:
            args["expiries"] = list(exps)
        return args
    if kind in ("expiry", "sim_expiry"):
        args["expiry"] = _pr.clean_expiry(raw.get("expiry"), today)
    elif kind == "rate":
        args["structure"] = _known_code(raw.get("structure"))
        args["legs"] = _clean_legs(raw.get("legs"), _clean_calc_leg, today)
    else:
        return None
    return None if any(v is None for v in args.values()) else args


def math_command(raw, today=None):
    """The command for a pricing request on ``MATH_STREAM``, or None. A tools
    kind is refused here: each kind lives on exactly one stream."""
    if not isinstance(raw, dict) or raw.get("kind") not in MATH_KINDS:
        return None
    args = _math_args(raw, today)
    return None if args is None else {"type": MATH_TYPE, "args": args}


def tools_command(raw, today=None):
    """The command for a Schwab-spending request on ``TOOLS_STREAM``, or None.
    A math kind is refused here."""
    if not isinstance(raw, dict) or raw.get("kind") not in TOOLS_KINDS:
        return None
    args = _tools_args(raw, today)
    return None if args is None else {"type": TOOLS_TYPE, "args": args}


# How a request ended: Rescue's codes plus ``load_first``, a request that needs
# a chain or a Simulator snapshot this service no longer holds (a restart, an
# eviction, or one never loaded), and ``price_needed``, a rating whose option
# legs carry no price of the visitor's own while quotes are off (the rating
# would otherwise be priced at the chain's marks). "done" and "cached" carry a
# result.
OUTCOMES = _pr.OUTCOMES + ("load_first", "price_needed")

# Worded for these tools, not copied from Rescue's: its sentences name the
# rescue menu ("Rescue runs while the market is open"), which would be wrong on
# the Calculator. The CODES are Rescue's, so a page can share the handling.
OUTCOME_TEXT = {
    "done": "Done just now.",
    "cached": "Showing a result from the last few minutes.",
    "duplicate": "This was just asked for. Try again in a minute.",
    "throttled": ("This trade was just rated several times with other prices. "
                  "Try again in a few minutes."),
    "closed": "Loading and rating run while the market is open.",
    "budget": "Today's public loads are used up. They reset tomorrow.",
    "not_listed": "That expiration is not listed for this symbol.",
    "off_ladder": "A strike in this trade is not listed for that expiration.",
    "no_options": "No listed options were found for this symbol.",
    "expired": "The request waited too long in the queue. Please try again.",
    "invalid": "That request could not be read. Check the legs and try again.",
    "error": "The request failed. Please try again later.",
    "load_first": "Load the symbol first.",
    "price_needed": "Type a price for every option leg first.",
}


# Rescue's hash, so ``is_key`` below recognises both forms' keys alike.
_hash = _pr._hash


def _stream_tag(command):
    """``"tools"`` or ``"math"`` for a command shaped as a builder builds one,
    else None."""
    if not isinstance(command, dict) or not isinstance(command.get("args"), dict):
        return None
    kind = command["args"].get("kind")
    if command.get("type") == TOOLS_TYPE and kind in TOOLS_KINDS:
        return "tools"
    if command.get("type") == MATH_TYPE and kind in MATH_KINDS:
        return "math"
    return None


def request_key(command, today=None) -> str | None:
    """The key a command's result and answer are written under, or None for a
    command neither builder would produce.

    The args are RE-NORMALIZED through the builder for the command's type
    before hashing, as ``public_rescue.request_key`` does: a hand-built command
    carrying a junk key or an unnormalized value (``" spy "``) hashes to the
    same key as the properly built one, and args that fail validation get no
    key at all. Two visitors making the same request share one run.

    ⚠ The hash keeps the request out of the key NAME, nothing more: the result
    under it is readable by anything holding a Redis read credential, and it is
    unsalted (see ``public_rescue.spec_key``)."""
    tag = _stream_tag(command)
    if tag is None:
        return None
    build = tools_command if tag == "tools" else math_command
    clean = build(command["args"], today)
    return None if clean is None else _hash(tag, clean["args"])


def structure_key(args) -> str | None:
    """A rating request LESS its prices and sizes: every leg's ``premium`` and
    ``qty`` removed. Each price typed is a new request to the cache, so the
    worker caps ratings per structure on this key instead. None for anything
    that is not a mapping."""
    if not isinstance(args, dict):
        return None
    legs = [{k: v for k, v in leg.items() if k not in ("premium", "qty")}
            for leg in (args.get("legs") or [])]
    return _hash("structure", {**args, "legs": legs})


def is_key(raw) -> bool:
    return _pr.is_key(raw)


# ── the config ───────────────────────────────────────────────────────────────

DEFAULTS = {
    "limits": {
        # A priced result younger than this is served instead of recomputing.
        "result_ttl_min": 5,
        # A trade rating younger than this is served.
        "rate_ttl_min": 15,
        # The same request inside this many seconds of its last run is not re-run.
        "dedup_sec": 60,
        # Ratings of one trade STRUCTURE (less its prices and size) per
        # ``rate_ttl_min``: every price typed is a new trade to the cache.
        "structure_runs": 3,
        # A request older than this is dropped unrun: it covers a replayed
        # backlog and a queue that has fallen behind.
        "max_wait_sec": 120,
        # How long each kind of key lives. Visitors choose the symbols and the
        # trades, so every key must expire or the count grows without limit.
        "result_keep_min": 30,
        "answer_keep_min": 15,
        # Simulator snapshots held at once, and for how long. Each is a whole
        # chain in the service's memory.
        "snapshot_limit": 8,
        "snapshot_ttl_min": 15,
        # Public chains held at once, shared with Rescue's strikes lists.
        "chain_hold_limit": 16,
    },
    "visitor": {
        # Requests one visitor may make an hour, counted in the public process
        # by address and never stored. Tools spend Schwab calls; math does not
        # but still costs the service CPU, so its cap is far higher.
        "tools_per_hour": 60,
        "math_per_hour": 600,
    },
}
# ⚠ The daily Schwab budget is deliberately NOT here: it is one budget shared
# with the Rescue form, so it cannot belong to either form's config alone.

load, reset_cache = toml_loader(TOOLS_PUBLIC_TOML, DEFAULTS,
                                label="tools_public.toml")


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


def tools_per_hour() -> int:
    return _num("visitor", "tools_per_hour", minimum=1)


def math_per_hour() -> int:
    return _num("visitor", "math_per_hour", minimum=1)

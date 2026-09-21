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

from repo_paths import TOOLS_PUBLIC_TOML
from shared import public_rescue as _pr
from shared.config_toml import toml_loader

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
    value = type(default)(raw)
    return value if value >= minimum else default


def limits() -> dict:
    return {k: _num("limits", k, minimum=1 if k != "dedup_sec" else 0)
            for k in DEFAULTS["limits"]}


def tools_per_hour() -> int:
    return _num("visitor", "tools_per_hour", minimum=1)


def math_per_hour() -> int:
    return _num("visitor", "math_per_hour", minimum=1)

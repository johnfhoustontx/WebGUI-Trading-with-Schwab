"""Single source of truth for cross-app paths and service ports in the
Trading With Schwab monorepo. Apps prepend the repo root to sys.path and
import the constants they need from here."""
from pathlib import Path
import sys
import tomllib

REPO_ROOT       = Path(__file__).resolve().parent
SCHWAB_PROXY    = REPO_ROOT / "schwab-proxy"
OPTIONS_SCANNER = REPO_ROOT / "options-scanner"
SENTIMENT       = REPO_ROOT / "sentiment-dashboard"
CLAUDE_DRIVER   = REPO_ROOT / "claude-driver"
TRADE_ANALYZER  = REPO_ROOT / "trade-analyzer"
SWING_MODEL        = TRADE_ANALYZER / "data" / "swing_model.json"
SWING_MODEL_REPORT = TRADE_ANALYZER / "data" / "swing_model_report.md"
PORTFOLIO_ANALYZER = REPO_ROOT / "portfolio-analyzer"
SHARED          = REPO_ROOT / "shared"
SHARED_DIR      = SHARED  # alias used by services importing shared-dir-relative files
WEBGUI          = REPO_ROOT / "webgui"

BRIDGE_PATH = SHARED / "sentiment_bridge.json"
APPSETTINGS = SHARED / "appsettings.json"
TOKENS      = SHARED / "tokens.json"
NOTIFICATIONS_CONFIG = SHARED / "notifications.json"

# App styling config (webgui theme) — edit + restart the webgui to restyle the
# app without touching code. Missing file/keys fall back to the built-in
# dark-navy defaults in webgui/pages/options/theme.py.
THEME_TOML = REPO_ROOT / "config" / "theme.toml"

# Options-flow alert thresholds (crossover + unusual-activity spike). Edit +
# restart options_svc to tune. Missing file/keys fall back to the built-in
# defaults in services/options_svc/flow_alerts.py.
FLOW_ALERTS_TOML = REPO_ROOT / "config" / "flow_alerts.toml"

# Market session windows + the extended-hours activation date. Read by
# shared/market_calendar.py (mtime-cached). Edit + restart the affected service
# to change a window.
SESSIONS_TOML = REPO_ROOT / "config" / "sessions.toml"

# The autonomous driver's risk envelope (daily target band, per-trade + daily risk
# caps, VIX ceiling, loss halt). Read by shared/driver_limits.py, which BOTH
# driver_svc.settings and options_svc.compute use - they cannot import each other,
# and the per-trade cap has to agree on both sides or the driver approves a size
# the paper sizer then zeroes. Edit + restart both services.
DRIVER_TOML = REPO_ROOT / "config" / "driver.toml"

# Trade-management stop/target rules (take-profit fraction, stop multiple, delta
# drift + hard ceiling, cut-DTE, the trail ladders). Read by shared/trade_mgmt.py,
# which BOTH options-scanner/signal_recommender.py (the auto-manage cycle) and
# services/options_svc/rescue.py (the at-risk detector) use, so the two cannot
# drift apart. Edit + restart options_svc.
TRADE_MGMT_TOML = REPO_ROOT / "config" / "trade_mgmt.toml"

# Scanner selection floors - IV-rank minimums, per-VIX-regime credit floors,
# directional delta band, score cutoffs. These are the knobs that decide whether a
# signal fires at all (and the documented reason index names rarely do). Read by
# shared/scanner_config.py. Edit + restart options_svc.
SCANNER_TOML = REPO_ROOT / "config" / "scanner.toml"

# The traded symbol universe: what GEX collection polls, the Net-Prem display
# groups, and the BIG10 basket. Read by shared/symbols.py from all three tiers -
# the same lists were previously duplicated in four modules and held together only
# by tests. Adding a symbol has a real Schwab API-budget cost; see the file.
SYMBOLS_TOML = REPO_ROOT / "config" / "symbols.toml"

# Dedicated paper-account DB for the autonomous Driver — a SEPARATE file from the
# manual paper_account.db so the driver's book is fully isolated (zero schema change;
# every paper_account_db/paper_engine fn already takes db_path).
DRIVER_PAPER_DB = OPTIONS_SCANNER / "data" / "paper_account_driver.db"

# History of Gamma Analyze briefings (the 4×/day Auto briefings + ad-hoc/manual runs).
# Stores the STRUCTURED analysis payload (the source of truth) per (date, slot); the
# HTML report is regenerated on demand from it. Written by options_svc; read by the
# gamma_briefing_report.py utility. One row per (CT date, slot).
GAMMA_BRIEFING_DB = OPTIONS_SCANNER / "data" / "gamma_briefings.db"

# Intraday 2-min sentiment + trend series for the /sentiment "Daily Sentiment &
# Trend" graphs. Rolling last 5 trading days; written by sentiment_svc each refresh.
SENTIMENT_INTRADAY_DB = SENTIMENT / "data" / "sentiment_intraday.db"

# Daily cap-weighted cross-sector Put/Call ratio for the 5-trading-day
# options-flow-direction delta. One row per LOCAL calendar date; written by
# sentiment_svc each RTH refresh.
SECTOR_PCR_HISTORY_DB = SENTIMENT / "data" / "sector_pcr.db"

# Daily bars + scored momentum levels for the Momentum Cascade. max_date() on the
# bars table drives the nightly delta fetch; the scores table is what the webgui
# page reads, so the page never calls the proxy. Written by sentiment_svc nightly.
MOMENTUM_DB = SENTIMENT / "data" / "momentum.db"

# Daily committed market-state (the five-state classifier's RTH output) recorded
# for later validation/backtesting. One row per LOCAL calendar date (today's row
# REPLACE-updates each RTH recompute); written by sentiment_svc.
MARKET_STATE_HISTORY_DB = SENTIMENT / "data" / "market_state.db"

# Offline five-state-classifier validation study outputs (markdown report + JSON
# artifact) written by sentiment-dashboard/validate_market_state.py. Run manually;
# sentiment-dashboard/data/ is gitignored, mirroring SWING_MODEL_REPORT.
MARKET_STATE_VALIDATION_REPORT = SENTIMENT / "data" / "market_state_validation.md"
MARKET_STATE_VALIDATION_JSON   = SENTIMENT / "data" / "market_state_validation.json"

# Trade-service on-disk stores. All three are FORWARD-ACCRUING: none can be
# backfilled, because each records something the source does not serve as
# history. They are worth starting long before their readers exist, since they
# pay in calendar time rather than effort. services/trade_svc/data/ is
# gitignored (generated).
TRADE_SVC_DATA = REPO_ROOT / "services" / "trade_svc" / "data"

# EquityDeepDive IV/RV history. Schwab serves no IV history, so IV rank
# accumulates forward from the first run; each on-demand Deep Dive records a
# snapshot. On-demand only (no scheduled job yet).
IV_HISTORY_DB = TRADE_SVC_DATA / "iv_history.db"

# What the model SAID, per symbol per day — composite, band, percentile, both
# verdicts, the gates and the artifact version. Phase 6's labeler attaches the
# realized forward excess returns; the live-IC monitor reads the pair. A model's
# historical output is unrecoverable after the fact (artifact, cross-section and
# gates all move), so this is written from Phase 1 onward.
REC_JOURNAL_DB = TRADE_SVC_DATA / "rec_journal.db"

# Point-in-time fundamentals. Live-parsed ratios describe TODAY, so validating
# the Investor verdict against forward returns is impossible without a store
# that remembers what each field read on the day it was read.
FUNDAMENTALS_HISTORY_DB = TRADE_SVC_DATA / "fundamentals_history.db"

# FINRA's bi-monthly short-interest cycles. Schwab ships both short-interest
# fields as a 0.0 sentinel for every symbol, so the short side's squeeze gate
# needs the regulatory filing itself; the float denominator still comes from
# Schwab (``marketCapFloat``, which is float in SHARES despite the name).
SHORT_INTEREST_DB = TRADE_SVC_DATA / "short_interest.db"

# ------------------------------------------------------------------ environment
# Which environment this CHECKOUT is (dev or prod), and the behavior flags that
# follow from it. Resolution lives here rather than in a new module because this
# file already parses config TOML and is imported by ~40 others — adding a module
# in front of it would create an import-order hazard for no gain.
#
# Design: docs/plans/2026-08-08-dev-prod-environments-design.md

_ENV_DEFAULTS = {
    "port_offset": 0,
    "proxy_port": None,        # None -> port_offset applies to ports.toml's proxy
    "redis_db": 0,
    "owns_proxy": True,
    "allow_claude": True,
    "allow_notifications": True,
    "schedulers": True,
    "autonomous_trading": True,
}


def _read_env_marker(root):
    """``(name, peer_root)`` from the gitignored ``config/env.local.toml``.

    NEVER raises. A missing, unreadable or malformed marker resolves to
    ``("prod", None)`` — the behavior this repo had before environments existed.
    Failing safe matters more than reporting the error: a half-applied profile on
    a live trading stack is worse than no profile.

    A marker that EXISTS but will not parse is different from one that is absent:
    it is positive evidence someone meant to select a non-default environment, so
    it warns on stderr (which the launchers already capture to logs/<name>.err.log)
    before falling back. Two ways to get there are easy and silent otherwise:
    PowerShell's ``>`` writes UTF-8 *with BOM*, and a Windows path typed the
    natural way (``peer_root = "D:\\WebGUI Trading Prod"``) is an invalid ``\\W``
    escape that discards the WHOLE document, ``name`` included. ``utf-8-sig``
    handles the first; the warning surfaces the second. See
    config/env.local.example.toml, which sidesteps the escape by construction.
    """
    path = root / "config" / "env.local.toml"
    if not path.exists():
        return "prod", None
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:  # noqa: BLE001 — bad TOML, encoding, permissions.
        print(f"repo_paths: {path} exists but could not be read ({exc}); "
              f"resolving to prod", file=sys.stderr)
        return "prod", None
    name = str(raw.get("name") or "prod").strip().lower() or "prod"
    peer = raw.get("peer_root") or None
    return name, (Path(str(peer)) if peer else None)


def _resolve_env(root, under_pytest=None):
    """``(name, flags, peer_root)`` for a checkout root. Never raises.

    Takes ``root`` explicitly so it is unit-testable against a tmp_path — this
    module is imported long before any test runs, so reloading it is not a
    workable alternative.

    ``under_pytest`` defaults to detecting pytest. It is a parameter rather than
    a bare ``sys.modules`` check inside the body so BOTH branches are testable
    against a tmp_path — including the one that matters most, that a dev marker
    still yields prod ports under pytest.

    Downstream tests come in two shapes: patch a FLAG with
    ``monkeypatch.setitem(repo_paths.ENV_FLAGS, "schedulers", True)`` (safe — the
    dict is a fresh ``dict(_ENV_DEFAULTS)`` copy, so it never aliases the defaults,
    and every value is an immutable scalar), but patch a by-value export like
    ``OWNS_PROXY`` with ``monkeypatch.setattr`` **on the module that consumed it**,
    since ``from repo_paths import OWNS_PROXY`` binds a copy.
    """
    name, peer = _read_env_marker(root)
    try:
        profiles = tomllib.loads(
            (root / "config" / "environments.toml").read_text(encoding="utf-8-sig"))
    except Exception:  # noqa: BLE001 — missing file, bad TOML, encoding.
        profiles = {}
    if name not in profiles:
        name = "prod"
    flags = dict(_ENV_DEFAULTS)
    over = profiles.get(name)
    if isinstance(over, dict):
        flags.update(over)
    # Under pytest the process PRESENTS AS PROD regardless of the marker — the
    # ports, the Redis DB index, proxy ownership, AND the environment's identity.
    # Tests are hermetic (the bus is already fakeredis), so these are inert
    # constants, and forcing them is what lets the existing suites pass unchanged
    # inside a dev checkout: tests/test_repo_paths_ports.py asserts the literal
    # 8210-8214 and a MEMURAI_URL ending "/0". `owns_proxy` belongs to that
    # topology even though it is not a port — it says WHOSE the port is, and a
    # consumer that branches on it (tools/stop_all.py drops the proxy from its
    # kill list) would otherwise behave one way under a dev checkout's tests and
    # another under prod's, which is the divergence this whole branch exists to
    # rule out. `name` is pinned for exactly the same reason, one level up: it
    # drives IS_DEV, so an unpinned name leaves every `if IS_DEV:` branch
    # checkout-dependent — found the hard way, when webgui/pages/status.py began
    # withholding the Memurai restart in dev and its "every card is restartable"
    # test would have failed only inside a dev checkout. Pinning it here means an
    # IS_DEV consumer needs no special handling; a test that wants the dev branch
    # monkeypatches IS_DEV on the module that consumed it, as with OWNS_PROXY.
    # The profile lookup above is deliberately left alone — every flag is
    # explicitly overridden below, so where they came from no longer matters.
    # Every suppression is forced ON so no test can reach Anthropic or a
    # notification channel.
    if under_pytest is None:
        under_pytest = "pytest" in sys.modules
    if under_pytest:
        name = "prod"
        flags.update(port_offset=0, proxy_port=None, redis_db=0, owns_proxy=True,
                     allow_claude=False, allow_notifications=False,
                     schedulers=False, autonomous_trading=False)
    return name, flags, peer


# ENV_FLAGS, not ENV: ENV_NAME is the string, so a bare `repo_paths.ENV` at a call
# site invites `if ENV == "dev"` (silently always False) or `if ENV:` (always True).
ENV_NAME, ENV_FLAGS, PEER_ROOT = _resolve_env(REPO_ROOT)
IS_DEV = ENV_NAME == "dev"
OWNS_PROXY = bool(ENV_FLAGS.get("owns_proxy", True))

_ports = tomllib.loads(
    (REPO_ROOT / "config" / "ports.toml").read_text(encoding="utf-8-sig"))


def _derive_ports(ports: dict, flags: dict) -> dict:
    """Apply an environment profile to the base port table. PURE.

    ``port_offset`` shifts the ports this repo OWNS (the six services and the
    webgui). Two things are deliberately left alone:

    * the **Memurai port** — both environments share one Redis server and are
      separated by logical DB index instead, so there is no second service to
      install or monitor;
    * ``options_analytics`` / ``approval`` / the **ML servers** — external
      processes this repo neither starts nor owns.

    ``proxy_port`` overrides the offset entirely, which is how dev borrows prod's
    proxy on :8100 rather than holding a second copy of the one rotating Schwab
    OAuth refresh token.

    Returns bare numbers, ``redis_db`` among them — the URLs are assembled at the
    constants block below, and the snapshot tooling wants the DB index as an int
    rather than something to parse back out of a URL.

    The ``int()`` coercions are deliberately unguarded: a non-numeric value in
    config/environments.toml must fail loudly at import, because the plausible
    fallback is worse — a malformed ``redis_db`` quietly becoming 0 would point a
    dev checkout at prod's live cache. tests/test_env_profile.py type-checks the
    shipped profiles so that crash cannot reach a running stack.
    """
    off = int(flags.get("port_offset") or 0)
    override = flags.get("proxy_port")
    proxy = int(override) if override else int(ports["proxy"]) + off
    return {
        "proxy_port": proxy,
        "nicegui_port": int(ports["nicegui"]) + off,
        "service_ports": {k: int(v) + off for k, v in ports["services"].items()},
        "memurai_port": int(ports["memurai"]),
        "redis_db": int(flags.get("redis_db") or 0),
    }


_derived = _derive_ports(_ports, ENV_FLAGS)

PROXY_PORT       = _derived["proxy_port"]
PROXY_URL        = f"http://127.0.0.1:{PROXY_PORT}"
ANALYTICS_URL    = f"http://127.0.0.1:{_ports['options_analytics']}"
APPROVAL_PORT    = _ports["approval"]
NICEGUI_PORT     = _derived["nicegui_port"]
NICEGUI_URL      = f"http://127.0.0.1:{NICEGUI_PORT}"
ML_SERVER_URLS   = {k: f"http://127.0.0.1:{v}" for k, v in _ports["ml_servers"].items()}
MEMURAI_PORT  = _derived["memurai_port"]
# The logical Redis DB index this environment owns. Exported as an int rather
# than left for callers to parse back out of MEMURAI_URL or re-coerce from
# ENV_FLAGS: tools/snapshot_from_prod.py compares it against PROD's index before
# it FLUSHDBs, and that comparison must not hinge on a string that might be "1"
# in one place and 1 in another. Already int()-coerced by _derive_ports.
REDIS_DB      = _derived["redis_db"]
MEMURAI_URL   = f"redis://127.0.0.1:{MEMURAI_PORT}/{REDIS_DB}"
SERVICE_PORTS = dict(_derived["service_ports"])
SERVICE_URLS  = {k: f"http://127.0.0.1:{v}" for k, v in SERVICE_PORTS.items()}

r"""check_env.py - validate that the Trading With Schwab apps are up.

Hybrid checks:
    schwab-proxy          HTTP GET {PROXY_URL}/health
    claude-driver         HTTP GET 127.0.0.1:{APPROVAL_PORT}/status
    options-scanner       psutil scan for `python ... dashboard.py`
    sentiment-dashboard   scan for sentiment_dashboard.py + bridge freshness
    trade-analyzer        scan for trade_analyzer.py (on-demand; not required)

Prints a status table. Exit code 0 only if every *required* app is healthy,
non-zero otherwise — usable as a gate in scripts / start_all.bat.

Paths and ports come from repo_paths.py — no hard-coded D:\ paths or ports.
"""
import datetime as _dt
import sys
import time
import pathlib
from dataclasses import dataclass

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # repo root
from repo_paths import PROXY_URL, APPROVAL_PORT, BRIDGE_PATH, SENTIMENT  # noqa: E402

sys.path.insert(0, str(SENTIMENT))  # for market_calendar.is_trading_day

# States
UP = "UP"
DOWN = "DOWN"
STALE = "STALE"
NO_TOKEN = "NO TOKEN"
IDLE = "IDLE"     # alive but not expected to be publishing (market closed)

HTTP_TIMEOUT = 2.0           # seconds per probe
BRIDGE_MAX_AGE_MIN = 15      # bridge stale beyond this *during market hours*

# Mirror sentiment_dashboard's AUTO_FETCH_START/END_HOUR (CT, local machine
# time). The dashboard only republishes the bridge on trading days within this
# window, so outside it a "stale" bridge is expected, not a fault.
MARKET_OPEN_HOUR = 8
MARKET_CLOSE_HOUR = 16


@dataclass
class CheckRow:
    name: str
    state: str
    detail: str = ""
    required: bool = True


#############################################
# PURE HELPERS (unit-tested)
#############################################

def classify_proxy_health(payload):
    """Map a /health JSON payload to (state, detail)."""
    if payload.get("status") != "ok":
        return DOWN, f"status={payload.get('status')!r}"
    if not payload.get("has_token"):
        return NO_TOKEN, "no Schwab token — visit /auth"
    if payload.get("token_expired"):
        return NO_TOKEN, "access token expired — refresh/re-auth"
    return UP, "token ok"


def freshness_state(path, max_age_min, now_ts):
    """UP if file mtime within max_age_min of now_ts, STALE if older, DOWN if absent."""
    path = pathlib.Path(path)
    if not path.exists():
        return DOWN
    age_min = (now_ts - path.stat().st_mtime) / 60.0
    return STALE if age_min > max_age_min else UP


def cmdline_matches(cmdline, script_name):
    """True if any cmdline token references script_name (handles full paths)."""
    if not cmdline:
        return False
    needle = script_name.replace("/", "\\").lower()
    for token in cmdline:
        t = str(token).replace("/", "\\").lower()
        if t == needle or t.endswith("\\" + needle):
            return True
    return False


def should_be_publishing(now_dt):
    """True iff the sentiment dashboard is expected to be refreshing the bridge
    right now: a trading day within the market-hours window. Off-hours,
    weekends, and NYSE holidays return False (bridge is idle by design)."""
    from market_calendar import is_trading_day
    if not is_trading_day(now_dt.date()):
        return False
    return MARKET_OPEN_HOUR <= now_dt.hour <= MARKET_CLOSE_HOUR


def classify_sentiment(alive, bridge_exists, is_stale, publishing):
    """Map sentiment-dashboard signals to (state, detail). Pure."""
    if not alive:
        return DOWN, "not running"
    if not publishing:
        return IDLE, "running, market closed - bridge idle (expected)"
    if not bridge_exists:
        return STALE, "running, no bridge file yet (market hours)"
    if is_stale:
        return STALE, f"bridge > {BRIDGE_MAX_AGE_MIN} min old during market hours"
    return UP, "running, bridge fresh"


_HEALTHY = (UP, IDLE)


def compute_exit_code(rows):
    """0 only if every required row is healthy (UP or IDLE)."""
    for r in rows:
        if r.required and r.state not in _HEALTHY:
            return 1
    return 0


def format_table(rows):
    name_w = max((len(r.name) for r in rows), default=4)
    state_w = max(len(UP), len(NO_TOKEN))
    lines = []
    for r in rows:
        mark = {UP: "OK", DOWN: "!!", STALE: "~~", NO_TOKEN: "!?",
                IDLE: "--"}.get(r.state, "??")
        lines.append(f"  [{mark}] {r.name:<{name_w}}  {r.state:<{state_w}}  {r.detail}")
    return "\n".join(lines)


#############################################
# LIVE PROBES (not unit-tested)
#############################################

def _http_json(url):
    import requests
    resp = requests.get(url, timeout=HTTP_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _proc_alive(script_name):
    import psutil
    for proc in psutil.process_iter(["cmdline"]):
        try:
            if cmdline_matches(proc.info.get("cmdline") or [], script_name):
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return False


def check_proxy():
    try:
        state, detail = classify_proxy_health(_http_json(f"{PROXY_URL}/health"))
    except Exception as e:
        return CheckRow("schwab-proxy", DOWN, f"unreachable ({type(e).__name__})")
    return CheckRow("schwab-proxy", state, detail)


def check_approval():
    try:
        _http_json(f"http://127.0.0.1:{APPROVAL_PORT}/status")
    except Exception as e:
        return CheckRow("claude-driver", DOWN, f"unreachable ({type(e).__name__})")
    return CheckRow("claude-driver", UP, f"approval server :{APPROVAL_PORT}")


def check_options_scanner():
    alive = _proc_alive("dashboard.py")
    return CheckRow("options-scanner", UP if alive else DOWN,
                    "dashboard.py running" if alive else "not running")


def check_sentiment():
    alive = _proc_alive("sentiment_dashboard.py")
    bridge_exists = pathlib.Path(BRIDGE_PATH).exists()
    is_stale = freshness_state(BRIDGE_PATH, BRIDGE_MAX_AGE_MIN, time.time()) == STALE
    publishing = should_be_publishing(_dt.datetime.now())
    state, detail = classify_sentiment(alive, bridge_exists, is_stale, publishing)
    return CheckRow("sentiment-dashboard", state, detail)


def check_trade_analyzer():
    alive = _proc_alive("trade_analyzer.py")
    return CheckRow("trade-analyzer", UP if alive else DOWN,
                    "running" if alive else "not running (on-demand)",
                    required=False)


def run_all_checks():
    return [
        check_proxy(),
        check_options_scanner(),
        check_sentiment(),
        check_approval(),
        check_trade_analyzer(),
    ]


def main(argv=None):
    rows = run_all_checks()
    print("Trading With Schwab - environment status\n")
    print(format_table(rows))
    code = compute_exit_code(rows)
    print("\n" + ("All required apps healthy." if code == 0
                   else "One or more required apps are DOWN/STALE."))
    return code


if __name__ == "__main__":
    raise SystemExit(main())

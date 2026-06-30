"""Single source of truth for cross-app paths and service ports in the
Trading With Schwab monorepo. Apps prepend the repo root to sys.path and
import the constants they need from here."""
from pathlib import Path
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

# Dedicated paper-account DB for the autonomous Driver — a SEPARATE file from the
# manual paper_account.db so the driver's book is fully isolated (zero schema change;
# every paper_account_db/paper_engine fn already takes db_path).
DRIVER_PAPER_DB = OPTIONS_SCANNER / "data" / "paper_account_driver.db"

# Intraday 2-min sentiment + trend series for the /sentiment "Daily Sentiment &
# Trend" graphs. Rolling last 5 trading days; written by sentiment_svc each refresh.
SENTIMENT_INTRADAY_DB = SENTIMENT / "data" / "sentiment_intraday.db"

_ports = tomllib.loads((REPO_ROOT / "config" / "ports.toml").read_text())
PROXY_PORT       = _ports["proxy"]
PROXY_URL        = f"http://127.0.0.1:{PROXY_PORT}"
ANALYTICS_URL    = f"http://127.0.0.1:{_ports['options_analytics']}"
APPROVAL_PORT    = _ports["approval"]
NICEGUI_PORT     = _ports["nicegui"]
NICEGUI_URL      = f"http://127.0.0.1:{NICEGUI_PORT}"
ML_SERVER_URLS   = {k: f"http://127.0.0.1:{v}" for k, v in _ports["ml_servers"].items()}
MEMURAI_PORT  = _ports["memurai"]
MEMURAI_URL   = f"redis://127.0.0.1:{MEMURAI_PORT}/0"
SERVICE_PORTS = dict(_ports["services"])
SERVICE_URLS  = {k: f"http://127.0.0.1:{v}" for k, v in SERVICE_PORTS.items()}

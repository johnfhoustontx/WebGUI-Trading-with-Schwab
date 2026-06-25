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
PORTFOLIO_ANALYZER = REPO_ROOT / "portfolio-analyzer"
SHARED          = REPO_ROOT / "shared"
SHARED_DIR      = SHARED  # alias used by services importing shared-dir-relative files
WEBGUI          = REPO_ROOT / "webgui"

BRIDGE_PATH = SHARED / "sentiment_bridge.json"
APPSETTINGS = SHARED / "appsettings.json"
TOKENS      = SHARED / "tokens.json"

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

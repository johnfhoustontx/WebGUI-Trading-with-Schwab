"""
SchwabProxy - Centralized Schwab API Token Manager & Proxy
Version: 1.0.1
Last Updated: 2026-05-26

Added trader API endpoints for order placement:
  GET  /accounts                  — fetch account list (hashValue for orders)
  POST /orders/{account_hash}     — place order via Schwab Trader API

Added trade stream tracker (TRADE STREAM TRACKER section):
  POST /track / POST /untrack     — register OptionsScanner paper trades
  reconcile daemon                — self-heal registry from trades.db (OPEN rows)
  asyncio stream worker           — LEVELONE_OPTIONS subs -> trade_detector ->
                                    perf_writer events + IV snapshots

Version 1.0.0 Changes:
- Initial implementation
- Token management with thread-safe locking
- Proxy endpoints: /quote, /quotes, /chains, /pricehistory
- Built-in rate limiter (200 ms between Schwab calls)
- Health check at /health
- Automatic token refresh on 401
- Bootstrap tokens from existing app token files
"""

import json
import os
import sys
import signal
import time
import base64
import asyncio
import logging
from logging.handlers import TimedRotatingFileHandler, RotatingFileHandler
import sqlite3
import threading
import pathlib
import collections
import itertools
from pathlib import Path

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # repo root
from repo_paths import OPTIONS_SCANNER, APPSETTINGS, TOKENS, PROXY_PORT
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from zoneinfo import ZoneInfo

import requests
import uvicorn
from urllib.parse import urlparse, parse_qs

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse

from trade_registry import TradeRegistry, resolve_legs
import trade_detector
import perf_writer
import stream_bridge

#############################################
# LOGGING
#############################################

LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

# Dedicated ERROR-only log, rotated weekly on Monday. backupCount=4 keeps ~4
# weeks of history and auto-deletes the oldest file on each rotation, so the
# errors.log* family never grows unbounded — effectively a weekly purge. The
# active file is `errors.log`; rotated files get a `.YYYY-MM-DD` suffix.
_error_handler = TimedRotatingFileHandler(
    LOG_DIR / "errors.log",
    when="W0",          # rotate weekly, Monday
    interval=1,
    backupCount=4,
    encoding="utf-8",
)
_error_handler.setLevel(logging.ERROR)  # only ERROR/CRITICAL land here
_error_handler.setFormatter(
    logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",  # full date — rotated files span days
    )
)

# Full INFO stream, size-rotated so it never grows unbounded: ~10 MB active
# file + 5 backups (~60 MB ceiling), matching errors.log's bounded-history
# policy (was a plain FileHandler that grew without limit).
_info_handler = RotatingFileHandler(
    LOG_DIR / "schwab_proxy.log",
    maxBytes=10 * 1024 * 1024,
    backupCount=5,
    encoding="utf-8",
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        _info_handler,
        logging.StreamHandler(),
        _error_handler,
    ],
)
logger = logging.getLogger("schwab_proxy")

#############################################
# CONFIGURATION
#############################################

SCHWAB_AUTH_URL = "https://api.schwabapi.com/v1/oauth/authorize"
SCHWAB_TOKEN_URL = "https://api.schwabapi.com/v1/oauth/token"
SCHWAB_BASE_URL = "https://api.schwabapi.com/marketdata/v1"
SCHWAB_TRADER_URL = "https://api.schwabapi.com/trader/v1"

CONFIG_PATHS = [
    APPSETTINGS,
]

TOKEN_FILE = Path(__file__).parent / "proxy_tokens.json"

MIN_REQUEST_INTERVAL = 0.2  # seconds between Schwab API calls

# Bounded retry for transient Schwab failures (timeouts, dropped connections,
# 5xx/404). Reads (market-data + Trader GETs) are retried; order POSTs are NOT
# (a lost response on a submitted order must never cause a duplicate).
MAX_RETRIES = 3            # total attempts for a retryable request
RETRY_BACKOFF_BASE = 0.25  # seconds; sleep = base * 2**attempt → 0.25, 0.5, ...


def _is_retryable_status(status_code: int) -> bool:
    """Whether an HTTP status warrants a retry.

    Deterministic 4xx CLIENT errors (400/403/404/…) will not change on a
    re-request, so retrying them just burns MAX_RETRIES attempts + backoff (the
    mechanism behind the old errors.log flood). We retry ONLY 5xx server errors;
    network/timeout exceptions are retried separately at the call site. 401 is
    NOT handled here — it has its own token-refresh-then-retry path.
    """
    return status_code >= 500

# Daily auto-shutdown: 15:30 America/Chicago, Mon–Fri (handles DST automatically).
SHUTDOWN_TZ = ZoneInfo("America/Chicago")
SHUTDOWN_HOUR = 15
SHUTDOWN_MINUTE = 30


def _next_shutdown_dt(now: datetime) -> datetime:
    target = now.replace(hour=SHUTDOWN_HOUR, minute=SHUTDOWN_MINUTE, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    while target.weekday() >= 5:  # 5=Sat, 6=Sun
        target += timedelta(days=1)
    return target


def _shutdown_scheduler():
    while True:
        now = datetime.now(SHUTDOWN_TZ)
        target = _next_shutdown_dt(now)
        sleep_s = (target - now).total_seconds()
        logger.info(f"Auto-shutdown scheduled for {target.isoformat()} ({sleep_s/3600:.2f}h)")
        time.sleep(max(sleep_s, 1))
        logger.info("Auto-shutdown time reached — terminating proxy")
        os.kill(os.getpid(), signal.SIGTERM if os.name != "nt" else signal.SIGINT)
        time.sleep(5)
        os._exit(0)

#############################################
# TOKEN MANAGER (Thread-Safe)
#############################################

class TokenManager:
    """Thread-safe Schwab OAuth token manager."""

    def __init__(self):
        self.config = self._load_config()
        self.app_key = self.config["AppKey"]
        self.app_secret = self.config["AppSecret"]
        self.callback_url = self.config.get("CallbackUrl", "https://127.0.0.1:8182")
        self.tokens: Dict = {}
        self._lock = threading.Lock()
        self._last_request_time = 0.0
        self.session = requests.Session()
        self._bootstrap_tokens()

    def _load_config(self) -> Dict:
        for path in CONFIG_PATHS:
            if path.exists():
                with open(path) as f:
                    cfg = json.load(f)
                logger.info(f"Loaded config from {path}")
                return cfg.get("Schwab", cfg)
        raise FileNotFoundError(f"appsettings.json not found. Searched: {CONFIG_PATHS}")

    def _bootstrap_tokens(self):
        fallback_paths = [
            TOKEN_FILE,
            Path.home() / ".schwab-mcp" / "token.json",
            TOKENS,
        ]
        for path in fallback_paths:
            if path.exists():
                try:
                    with open(path) as f:
                        raw = json.load(f)
                    self.tokens = self._normalize(raw)
                    logger.info(f"Bootstrapped tokens from {path}")
                    self._save_tokens()
                    return
                except Exception as e:
                    logger.warning(f"Failed to load {path}: {e}")
        logger.warning("No existing tokens found — manual OAuth required")

    def _normalize(self, raw: Dict) -> Dict:
        if "token" in raw and isinstance(raw["token"], dict):
            return self._normalize(raw["token"])
        if "access_token" in raw:
            now = datetime.utcnow()
            expires_in = raw.get("expires_in", 1800)
            return {
                "AccessToken": raw["access_token"],
                "RefreshToken": raw.get("refresh_token", ""),
                "ExpiresIn": expires_in,
                "ExpiresAt": (now + timedelta(seconds=expires_in)).isoformat() + "Z",
                "RefreshTokenExpiresAt": raw.get(
                    "RefreshTokenExpiresAt",
                    (now + timedelta(days=7)).isoformat() + "Z",
                ),
            }
        if "AccessToken" in raw:
            return raw
        return raw

    def _save_tokens(self):
        TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(TOKEN_FILE, "w") as f:
            json.dump(self.tokens, f, indent=2)

    def _is_expired(self) -> bool:
        ea = self.tokens.get("ExpiresAt", "")
        if not ea:
            return True
        try:
            exp = datetime.fromisoformat(ea.replace("Z", "+00:00")).replace(tzinfo=None)
            return datetime.utcnow() >= (exp - timedelta(minutes=5))
        except Exception:
            return True

    def _is_refresh_expired(self) -> bool:
        ea = self.tokens.get("RefreshTokenExpiresAt", "")
        if not ea:
            return True
        try:
            exp = datetime.fromisoformat(ea.replace("Z", "+00:00")).replace(tzinfo=None)
            return datetime.utcnow() >= exp
        except Exception:
            return True

    def _refresh(self):
        rt = self.tokens.get("RefreshToken", "")
        if not rt:
            raise RuntimeError("No refresh token — manual re-auth required")
        creds = base64.b64encode(f"{self.app_key}:{self.app_secret}".encode()).decode()
        resp = requests.post(
            SCHWAB_TOKEN_URL,
            headers={"Authorization": f"Basic {creds}", "Content-Type": "application/x-www-form-urlencoded"},
            data={"grant_type": "refresh_token", "refresh_token": rt},
            timeout=30,
        )
        if resp.status_code != 200:
            logger.error(f"Token refresh failed ({resp.status_code}): {resp.text}")
            raise RuntimeError(f"Token refresh failed: {resp.status_code}")
        data = resp.json()
        now = datetime.utcnow()
        expires_in = data.get("expires_in", 1800)
        self.tokens["AccessToken"] = data["access_token"]
        self.tokens["ExpiresIn"] = expires_in
        self.tokens["ExpiresAt"] = (now + timedelta(seconds=expires_in)).isoformat() + "Z"
        if "refresh_token" in data:
            self.tokens["RefreshToken"] = data["refresh_token"]
            rt_exp = data.get("refresh_token_expires_in", 604800)
            self.tokens["RefreshTokenExpiresAt"] = (now + timedelta(seconds=rt_exp)).isoformat() + "Z"
        self._save_tokens()
        logger.info("Token refreshed successfully")

    def ensure_valid_token(self):
        with self._lock:
            if not self.tokens.get("AccessToken"):
                raise RuntimeError("No access token — run initial OAuth flow")
            if self._is_expired():
                if self._is_refresh_expired():
                    raise RuntimeError("Refresh token expired — re-auth required")
                self._refresh()

    def exchange_auth_code(self, code: str):
        creds = base64.b64encode(f"{self.app_key}:{self.app_secret}".encode()).decode()
        resp = requests.post(
            SCHWAB_TOKEN_URL,
            headers={"Authorization": f"Basic {creds}", "Content-Type": "application/x-www-form-urlencoded"},
            data={"grant_type": "authorization_code", "code": code, "redirect_uri": self.callback_url},
            timeout=30,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"Auth code exchange failed ({resp.status_code}): {resp.text}")
        data = resp.json()
        with self._lock:
            self.tokens = self._normalize(data)
            self._save_tokens()
        logger.info("OAuth authorization code exchanged — tokens saved")

    def _rate_limit(self):
        now = time.monotonic()
        elapsed = now - self._last_request_time
        if elapsed < MIN_REQUEST_INTERVAL:
            time.sleep(MIN_REQUEST_INTERVAL - elapsed)
        self._last_request_time = time.monotonic()

    def api_request(self, endpoint: str, params: Optional[Dict] = None) -> Dict:
        """Marketdata API GET request, with bounded retry on transient failures.

        Idempotent GET, so TRANSIENT failures (timeouts → 504, dropped
        connections → 502, 5xx server errors) are retried up to MAX_RETRIES with
        exponential backoff. Deterministic 4xx client errors (400/403/404/…) are
        NOT retried — they won't change on a re-request — except 401, which keeps
        its token-refresh-then-retry path. On final failure the original
        {status_code, data, error} shape is returned unchanged, so consumers see
        no contract difference.
        """
        self.ensure_valid_token()
        headers = {"Authorization": f'Bearer {self.tokens["AccessToken"]}', "Accept": "application/json"}
        url = f"{SCHWAB_BASE_URL}{endpoint}"
        result: Dict = {"status_code": 502, "data": None, "error": "no attempt made"}
        for attempt in range(MAX_RETRIES):
            self._rate_limit()
            try:
                resp = self.session.get(url, headers=headers, params=params, timeout=30)
                if resp.status_code == 401:
                    logger.warning("401 — refreshing and retrying")
                    with self._lock:
                        self._refresh()
                    headers["Authorization"] = f'Bearer {self.tokens["AccessToken"]}'
                    self._rate_limit()
                    resp = self.session.get(url, headers=headers, params=params, timeout=30)
                if resp.status_code == 200:
                    return {"status_code": 200, "data": resp.json(), "error": None}
                logger.error(f"Schwab {resp.status_code}: {resp.text[:200]}")
                result = {"status_code": resp.status_code, "data": None, "error": resp.text[:500]}
                # A deterministic 4xx (400/403/404/…) won't change on re-request:
                # return immediately instead of burning the remaining attempts.
                if not _is_retryable_status(resp.status_code):
                    return result
            except requests.exceptions.Timeout:
                result = {"status_code": 504, "data": None, "error": "Schwab API timeout"}
            except requests.exceptions.RequestException as e:
                result = {"status_code": 502, "data": None, "error": str(e)}
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_BACKOFF_BASE * (2 ** attempt))
                logger.info(f"Retrying {endpoint} (attempt {attempt + 2}/{MAX_RETRIES})")
        return result


#############################################
# FASTAPI APPLICATION
#############################################

app = FastAPI(
    title="Schwab API Proxy",
    version="1.0.1",
    description="Centralized token manager & API proxy for Schwab market data and trading",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

token_mgr: Optional[TokenManager] = None

@app.on_event("startup")
def startup():
    global token_mgr
    token_mgr = TokenManager()
    has_token = bool(token_mgr.tokens.get("AccessToken"))
    logger.info(f"Schwab Proxy started on port {PROXY_PORT} | token loaded: {has_token}")
    threading.Thread(target=_shutdown_scheduler, name="auto-shutdown", daemon=True).start()

    # Trade stream tracker — ensure perf tables exist, then start the reconcile
    # sweep and the asyncio stream worker. A failure to start streaming must NOT
    # prevent the core REST proxy from serving, so each start is guarded.
    try:
        perf_writer.init_schema()
    except Exception:
        logger.exception("perf_writer.init_schema failed (continuing)")
    try:
        threading.Thread(
            target=_reconcile_loop, name="reconcile", daemon=True).start()
    except Exception:
        logger.exception("failed to start reconcile thread (continuing)")
    try:
        threading.Thread(
            target=_stream_worker, name="stream-worker", daemon=True).start()
    except Exception:
        logger.exception("failed to start stream worker (continuing)")


#############################################
# HEALTH & STATUS
#############################################

@app.get("/health")
def health():
    has_token = bool(token_mgr.tokens.get("AccessToken"))
    return {
        "status": "ok",
        "has_token": has_token,
        "token_expired": token_mgr._is_expired() if has_token else True,
        "refresh_token_expired": token_mgr._is_refresh_expired() if has_token else True,
        "token_file": str(TOKEN_FILE),
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }


#############################################
# OAUTH RE-AUTH ENDPOINTS
#############################################

AUTH_PAGE_HTML = """<!DOCTYPE html>
<html><head><title>Schwab OAuth Login</title>
<style>
  body {{ font-family: system-ui, sans-serif; max-width: 640px; margin: 60px auto; padding: 0 20px; }}
  h1 {{ color: #1a5276; }}
  .step {{ margin: 18px 0; padding: 12px 16px; background: #f4f6f7; border-left: 4px solid #2e86c1; }}
  a.login {{ display: inline-block; padding: 10px 24px; background: #2e86c1; color: #fff;
             text-decoration: none; border-radius: 4px; font-weight: bold; }}
  a.login:hover {{ background: #1a5276; }}
  input[type=text] {{ width: 100%; padding: 8px; font-size: 14px; box-sizing: border-box; }}
  button {{ padding: 8px 20px; background: #27ae60; color: #fff; border: none; border-radius: 4px;
            cursor: pointer; font-size: 14px; margin-top: 8px; }}
  button:hover {{ background: #1e8449; }}
  .status {{ padding: 10px; margin-bottom: 16px; border-radius: 4px; }}
  .status.ok {{ background: #d5f5e3; color: #1e8449; }}
  .status.expired {{ background: #fadbd8; color: #922b21; }}
</style></head>
<body>
  <h1>Schwab OAuth Login</h1>
  <div class="status {status_class}">{status_msg}</div>
  <div class="step"><strong>Step 1:</strong> Click the link below to log in at Schwab.<br><br>
    <a class="login" href="{auth_url}" target="_blank">Log in to Schwab</a></div>
  <div class="step"><strong>Step 2:</strong> After logging in, Schwab will redirect to a page that
    <em>won't load</em>. That's expected.<br>Copy the <strong>entire URL</strong> from your browser's address bar.</div>
  <div class="step"><strong>Step 3:</strong> Paste the URL below and click Submit.
    <form action="/auth/callback" method="get">
      <input type="text" name="url" placeholder="https://127.0.0.1:8182?code=..." />
      <br><button type="submit">Submit</button>
    </form></div>
</body></html>"""

CALLBACK_SUCCESS_HTML = """<!DOCTYPE html>
<html><head><title>Auth Success</title>
<style>
  body { font-family: system-ui, sans-serif; max-width: 640px; margin: 60px auto; padding: 0 20px; text-align: center; }
  .ok { color: #1e8449; font-size: 48px; }
  p { color: #555; }
</style></head>
<body>
  <div class="ok">&#10003;</div>
  <h1>Authentication Successful</h1>
  <p>Tokens saved. The proxy is ready to serve API requests.</p>
  <p><a href="/health">Check health</a></p>
</body></html>"""

CALLBACK_ERROR_HTML = """<!DOCTYPE html>
<html><head><title>Auth Failed</title>
<style>
  body {{ font-family: system-ui, sans-serif; max-width: 640px; margin: 60px auto; padding: 0 20px; text-align: center; }}
  .err {{ color: #922b21; font-size: 48px; }}
  pre {{ text-align: left; background: #f4f6f7; padding: 12px; overflow-x: auto; font-size: 13px; }}
</style></head>
<body>
  <div class="err">&#10007;</div>
  <h1>Authentication Failed</h1>
  <pre>{error}</pre>
  <p><a href="/auth">Try again</a></p>
</body></html>"""


@app.get("/auth", response_class=HTMLResponse)
def auth_start():
    auth_url = (
        f"{SCHWAB_AUTH_URL}"
        f"?client_id={token_mgr.app_key}"
        f"&redirect_uri={token_mgr.callback_url}"
    )
    refresh_expired = token_mgr._is_refresh_expired()
    status_class = "expired" if refresh_expired else "ok"
    status_msg = ("Refresh token is expired — re-authentication required."
                  if refresh_expired else "Tokens are valid. Re-authenticate only if needed.")
    return AUTH_PAGE_HTML.format(auth_url=auth_url, status_class=status_class, status_msg=status_msg)


@app.get("/auth/callback", response_class=HTMLResponse)
def auth_callback(code: Optional[str] = None, url: Optional[str] = None):
    if url and not code:
        try:
            parsed = urlparse(url)
            codes = parse_qs(parsed.query).get("code", [])
            if codes:
                code = codes[0]
        except Exception:
            pass
    if not code:
        return HTMLResponse(
            CALLBACK_ERROR_HTML.format(error="No authorization code found."), status_code=400)
    try:
        token_mgr.exchange_auth_code(code)
        return CALLBACK_SUCCESS_HTML
    except Exception as e:
        return HTMLResponse(CALLBACK_ERROR_HTML.format(error=str(e)), status_code=500)


#############################################
# MARKETDATA PROXY ENDPOINTS
#############################################

@app.get("/quote")
def get_quote(symbol: str):
    result = token_mgr.api_request("/quotes", params={"symbols": symbol, "fields": "quote"})
    if result["status_code"] != 200:
        raise HTTPException(status_code=result["status_code"], detail=result["error"])
    return result["data"]


@app.get("/quotes")
def get_quotes(symbols: str = Query(..., description="Comma-separated symbols")):
    result = token_mgr.api_request("/quotes", params={"symbols": symbols, "fields": "quote"})
    if result["status_code"] != 200:
        raise HTTPException(status_code=result["status_code"], detail=result["error"])
    return result["data"]


@app.get("/chains")
def get_option_chain(
    symbol: str,
    contractType: str = "ALL",
    range: str = Query("ALL", alias="range"),
    fromDate: Optional[str] = None,
    toDate: Optional[str] = None,
    strikeCount: Optional[int] = None,
):
    params: Dict[str, Any] = {"symbol": symbol, "contractType": contractType, "range": range}
    if fromDate:    params["fromDate"] = fromDate
    if toDate:      params["toDate"] = toDate
    if strikeCount is not None: params["strikeCount"] = strikeCount
    result = token_mgr.api_request("/chains", params=params)
    if result["status_code"] != 200:
        raise HTTPException(status_code=result["status_code"], detail=result["error"])
    return result["data"]


@app.get("/pricehistory")
def get_price_history(
    symbol: str,
    periodType: str = "year",
    period: int = 1,
    frequencyType: str = "daily",
    frequency: int = 1,
    needExtendedHoursData: bool = False,
):
    params = {
        "symbol": symbol, "periodType": periodType, "period": period,
        "frequencyType": frequencyType, "frequency": frequency,
        "needExtendedHoursData": str(needExtendedHoursData).lower(),
    }
    # Schwab's price-history endpoint is /pricehistory?symbol=… — the symbol lives
    # in the query string (already in ``params``), not the path. A prior
    # ``/{symbol}/pricehistory`` first-attempt was a guaranteed 404 that fell back
    # to this same call: it 404'd on every fetch, and ``api_request`` retried each
    # 404 MAX_RETRIES times with backoff — flooding errors.log (~99% of all ERRORs)
    # and wasting ~0.75s of retry sleep per fetch. Call the correct endpoint once.
    result = token_mgr.api_request("/pricehistory", params=params)
    if result["status_code"] != 200:
        raise HTTPException(status_code=result["status_code"], detail=result["error"])
    return result["data"]


@app.get("/instruments")
def get_instruments(
    symbol: str,
    projection: str = "fundamental",
):
    """Schwab marketdata /instruments lookup (default projection=fundamental).

    With ``projection=fundamental`` Schwab returns
    ``{"instruments": [{"fundamental": {...}, "symbol", "description", ...}]}``.
    The Trade service uses this to fetch P/E, growth, ROE, margins, etc. for the
    Investor verdict. Other projections (``symbol-search``, ``desc-search``, …)
    pass through unchanged.
    """
    result = token_mgr.api_request(
        "/instruments", params={"symbol": symbol, "projection": projection}
    )
    if result["status_code"] != 200:
        raise HTTPException(status_code=result["status_code"], detail=result["error"])
    return result["data"]


@app.get("/passthrough")
def passthrough(endpoint: str, params: Optional[str] = None):
    """Generic passthrough for any Schwab marketdata endpoint."""
    p = {}
    if params:
        for pair in params.split(","):
            if "=" in pair:
                k, v = pair.split("=", 1)
                p[k.strip()] = v.strip()
    result = token_mgr.api_request(endpoint, params=p or None)
    if result["status_code"] != 200:
        raise HTTPException(status_code=result["status_code"], detail=result["error"])
    return result["data"]


#############################################
# TRADER API ENDPOINTS (order placement)
#############################################

def trader_request(method: str, endpoint: str, json_body: dict = None) -> dict:
    """
    Authenticated Schwab Trader API request.
    Uses SCHWAB_TRADER_URL (trader/v1) — separate from marketdata/v1.
    """
    token_mgr.ensure_valid_token()
    # Do NOT set a static Content-Type here. Schwab's Trader API rejects a
    # `Content-Type: application/json` header on a bodyless GET with an opaque
    # 400 ({"errors":[{"status":500,"title":"Internal Server Error"}]}) — this
    # silently broke /accounts, /positions, /transactions. For the POST path,
    # requests sets Content-Type automatically from json=json_body, so omitting
    # it here is correct for both verbs.
    headers = {
        "Authorization": f'Bearer {token_mgr.tokens["AccessToken"]}',
        "Accept":        "application/json",
    }
    url = f"{SCHWAB_TRADER_URL}{endpoint}"
    is_get = method.upper() == "GET"
    # Idempotent GETs (accounts/positions/transactions) get bounded retry on
    # transient failures. Order POSTs do NOT: a lost response on a submitted
    # order must never be retried, or it could double-submit. So POSTs fail
    # fast with a single attempt, exactly as before.
    attempts = MAX_RETRIES if is_get else 1
    result: dict = {"status_code": 502, "data": None, "error": "no attempt made"}
    # Reuse the TokenManager's pooled Session (keep-alive to api.schwabapi.com)
    # instead of bare requests.* — the marketdata path already does, so the trader
    # path no longer pays a fresh TLS handshake per /accounts/positions/orders call.
    session = token_mgr.session
    for attempt in range(attempts):
        try:
            resp = (session.get(url, headers=headers, timeout=30) if is_get
                    else session.post(url, headers=headers, json=json_body, timeout=30))
            if resp.status_code == 401:
                logger.warning("Trader API 401 — refreshing token and retrying")
                with token_mgr._lock:
                    token_mgr._refresh()
                headers["Authorization"] = f'Bearer {token_mgr.tokens["AccessToken"]}'
                resp = (session.get(url, headers=headers, timeout=30) if is_get
                        else session.post(url, headers=headers, json=json_body, timeout=30))
            if resp.status_code in (200, 201):
                return {"status_code": resp.status_code, "data": resp.json() if resp.text else {}, "error": None}
            logger.error(f"Trader API {resp.status_code}: {resp.text[:300]}")
            result = {"status_code": resp.status_code, "data": None, "error": resp.text[:500]}
            # A deterministic 4xx won't change on re-request — don't retry it.
            # (POSTs already fail fast via attempts=1; this also short-circuits
            # a 4xx on the idempotent GET path.)
            if not _is_retryable_status(resp.status_code):
                return result
        except requests.exceptions.RequestException as e:
            result = {"status_code": 502, "data": None, "error": str(e)}
        if attempt < attempts - 1:
            time.sleep(RETRY_BACKOFF_BASE * (2 ** attempt))
            logger.info(f"Retrying trader {endpoint} (attempt {attempt + 2}/{attempts})")
    return result


def _normalize_positions(raw: dict) -> list[dict]:
    """Flatten Schwab account positions into a stable shape.

    Quantity is net (long minus short). Options carry their underlying so the
    app can roll them into the underlying's sector; futures keep their own symbol.
    """
    acct = raw.get("securitiesAccount", raw)
    out = []
    for p in acct.get("positions", []):
        inst = p.get("instrument", {})
        symbol = inst.get("symbol", "")
        asset_type = inst.get("assetType", "UNKNOWN")
        underlying = inst.get("underlyingSymbol") or symbol
        qty = float(p.get("longQuantity", 0)) - float(p.get("shortQuantity", 0))
        out.append({
            "symbol": symbol,
            "asset_type": asset_type,
            "underlying": underlying,
            "quantity": qty,
            "avg_price": float(p.get("averagePrice", 0)),
            "market_value": float(p.get("marketValue", 0)),
            "day_pl": float(p.get("currentDayProfitLoss", 0)),
            "total_pl": float(p.get("longOpenProfitLoss", 0)) + float(p.get("shortOpenProfitLoss", 0)),
        })
    return out


def _merge_positions(positions: list[dict]) -> list[dict]:
    """Fold same-``symbol`` positions (across accounts) into one holding.

    A user with multiple linked Schwab accounts can hold the same instrument in
    more than one of them. The portfolio model downstream is keyed by symbol
    (entries, baselines), so the aggregate view wants a single row per symbol:
    quantity, market value and P&L are summed, and ``avg_price`` is re-weighted
    by absolute quantity. Rows for a symbol that appears only once pass through
    untouched; first-seen order is preserved.
    """
    groups: dict[str, list[dict]] = {}
    order: list[str] = []
    for p in positions:
        sym = p.get("symbol", "")
        if sym not in groups:
            groups[sym] = []
            order.append(sym)
        groups[sym].append(p)

    out: list[dict] = []
    for sym in order:
        rows = groups[sym]
        if len(rows) == 1:
            out.append(dict(rows[0]))
            continue
        merged = dict(rows[0])
        merged["quantity"] = sum(float(r.get("quantity", 0)) for r in rows)
        merged["market_value"] = sum(float(r.get("market_value", 0)) for r in rows)
        merged["day_pl"] = sum(float(r.get("day_pl", 0)) for r in rows)
        merged["total_pl"] = sum(float(r.get("total_pl", 0)) for r in rows)
        abs_qty = sum(abs(float(r.get("quantity", 0))) for r in rows)
        cost = sum(float(r.get("avg_price", 0)) * abs(float(r.get("quantity", 0)))
                   for r in rows)
        merged["avg_price"] = (cost / abs_qty) if abs_qty else 0.0
        out.append(merged)
    return out


def _normalize_transactions(raw: list) -> list[dict]:
    """Reduce Schwab transactions to trade rows the app can sync.

    Only TRADE activity with a security transfer item is kept. Quantity sign
    follows Schwab's amount; instruction is derived from amount sign.

    Assumed Schwab Trader API shape (transactions list, each item):
      {"activityId", "time", "type", "transferItems": [
          {"instrument": {"symbol", "assetType", "underlyingSymbol"?},
           "amount", "price", ...}, ...]}
    The 8-key output contract below is stable regardless of upstream nesting.
    """
    out = []
    for t in raw or []:
        if t.get("type") != "TRADE":
            continue
        items = [i for i in t.get("transferItems", []) if i.get("instrument")]
        # A Schwab TRADE carries multiple transfer items: the security leg(s)
        # AND a CURRENCY (cash) leg. Pick the security leg and skip the currency
        # leg; skip the whole transaction if it has no security leg (e.g. a pure
        # cash movement mislabeled as a trade). Confirmed live: without this,
        # every row came back as CURRENCY_USD instead of the actual symbol.
        sec_items = [
            i for i in items
            if i["instrument"].get("assetType") not in ("CURRENCY",)
        ]
        if not sec_items:
            continue
        item = sec_items[0]  # TODO: multi-leg trades (e.g. spreads) only capture leg[0]; revisit when a live multi-leg sample is available
        inst = item["instrument"]
        amount = float(item.get("amount", 0))
        out.append({
            "trade_id": str(t.get("activityId", "")),
            "symbol": inst.get("symbol", ""),
            "asset_type": inst.get("assetType", "UNKNOWN"),
            "underlying": inst.get("underlyingSymbol") or inst.get("symbol", ""),
            "quantity": abs(amount),
            "price": float(item.get("price", 0)),
            "instruction": "BUY" if amount >= 0 else "SELL",
            "trade_date": (t.get("time", "") or "")[:10],
        })
    return out


@app.get("/accounts")
def get_accounts():
    """
    Fetch linked Schwab account hashes (Trader API).
    Returns list with hashValue field used for order placement.
    """
    result = trader_request("GET", "/accounts/accountNumbers")
    if result["status_code"] not in (200, 201):
        raise HTTPException(status_code=result["status_code"], detail=result["error"])
    return result["data"]


@app.post("/orders/{account_hash}")
def place_order(account_hash: str, order: dict):
    """
    Place an order via Schwab Trader API.
    account_hash: hashValue from GET /accounts.
    order: Schwab order body (orderType, session, price, orderLegCollection, etc.)
    """
    result = trader_request("POST", f"/accounts/{account_hash}/orders", json_body=order)
    if result["status_code"] not in (200, 201):
        raise HTTPException(status_code=result["status_code"], detail=result["error"])
    logger.info(f"Order placed — account={account_hash[:8]}... status={result['status_code']}")
    return {"status": "submitted", "status_code": result["status_code"], "data": result["data"]}


@app.get("/positions")
def get_positions_default():
    """Merged positions across ALL linked Schwab accounts.

    The user may have several linked accounts; this aggregates every one of them
    into a single book and folds same-symbol holdings into one row (see
    :func:`_merge_positions`) so the portfolio view is whole-account. A
    per-account fetch failure is logged and skipped; only a total failure (every
    account errored) surfaces as an error.
    """
    accts = trader_request("GET", "/accounts/accountNumbers")
    if accts["status_code"] not in (200, 201):
        raise HTTPException(status_code=accts["status_code"], detail=accts["error"])
    hashes = accts["data"]
    if not hashes:
        raise HTTPException(status_code=404, detail="No linked accounts")
    merged: list[dict] = []
    failures = 0
    for h in hashes:
        try:
            merged.extend(get_positions(h["hashValue"])["positions"])
        except HTTPException as exc:
            failures += 1
            logger.error(f"Positions fetch failed for one account: {exc.detail}")
    if failures and failures == len(hashes):
        raise HTTPException(status_code=502,
                            detail="All linked account position fetches failed")
    return {"positions": _merge_positions(merged)}


@app.get("/positions/{account_hash}")
def get_positions(account_hash: str):
    """Normalized positions for one account."""
    result = trader_request("GET", f"/accounts/{account_hash}?fields=positions")
    if result["status_code"] not in (200, 201):
        raise HTTPException(status_code=result["status_code"], detail=result["error"])
    return {"positions": _normalize_positions(result["data"])}


@app.get("/transactions/{account_hash}")
def get_transactions(account_hash: str, start_date: str, end_date: str):
    """Normalized trade transactions in [start_date, end_date] (YYYY-MM-DD)."""
    endpoint = (f"/accounts/{account_hash}/transactions"
                f"?startDate={start_date}T00:00:00.000Z"
                f"&endDate={end_date}T23:59:59.000Z&types=TRADE")
    result = trader_request("GET", endpoint)
    if result["status_code"] not in (200, 201):
        raise HTTPException(status_code=result["status_code"], detail=result["error"])
    return {"transactions": _normalize_transactions(result["data"])}


#############################################
# TRADE STREAM TRACKER
#############################################
#
# Subscribes to LEVELONE_OPTIONS for the legs of every OPEN OptionsScanner paper
# trade, runs trade_detector.evaluate() on each tick, and writes lifecycle events
# + IV snapshots into trade_performance.db (via perf_writer, which never raises).
#
# Three moving parts, all daemon threads started in startup():
#   1. /track + /untrack REST endpoints (OptionsScanner pushes trades here).
#   2. _reconcile_loop  — periodic read-only sweep of trades.db to self-heal the
#      registry (catch trades that opened/closed while we weren't told).
#   3. _stream_worker   — owns an asyncio event loop + the schwab-py StreamClient;
#      all subscribe/unsubscribe calls are marshalled onto that loop via
#      run_coroutine_threadsafe from the REST/reconcile threads.
#
# schwab-py 1.5.1 streaming API used (verified by introspection on this machine):
#   methods: StreamClient.level_one_option_subs / level_one_option_add /
#            level_one_option_unsubs ; add_level_one_option_handler(fn)
#   message: handle_message() relabels numeric field keys to the UPPERCASE
#            LevelOneOptionFields enum names (e.g. "BID_PRICE", "ASK_PRICE",
#            "VOLATILITY", "UNDERLYING_PRICE"); the subscribed symbol is in "key".
#            We read those named keys and fall back to the raw numeric strings.

OPTIONSCANNER_TRADES_DB = OPTIONS_SCANNER / "data" / "trades.db"

# mirrors signal_recommender.py; OptionsScanner passes target_mid/stop_mid in
# /track, these are only the reconcile fallback when those fields are absent.
TP_FRAC = 0.50
STOP_MULT = 2.0

RECONCILE_INTERVAL = 30  # seconds

_registry = TradeRegistry()
_stream_loop: Optional[asyncio.AbstractEventLoop] = None  # set by the worker thread
_stream_client = None
_leg_quotes: Dict[str, Dict[str, Any]] = {}  # osi -> {"bid","ask","iv"}
_entry_snapped: set = set()  # trade_ids that already got an entry IV snapshot

# Equity SSE subscriber registry. Equities ride the SHARED stream worker session
# (the same StreamClient that serves LEVELONE_OPTIONS) — they never open a second
# Schwab login. Each SSE request registers a subscriber; the worker's equity
# handler fans matching ticks to each subscriber's queue (via its own loop).
_equity_subscribers: dict = {}          # sub_id -> {"loop", "queue", "symbols": set[str]}
_equity_refcount = collections.Counter()  # symbol -> active subscriber count
_equity_subscribed: set = set()         # the symbol set currently subscribed on Schwab
_equity_lock = threading.Lock()         # guards the three structures above
_equity_id_counter = itertools.count()  # monotonic SSE subscriber ids


def _now_iso() -> str:
    return datetime.now(SHUTDOWN_TZ).isoformat()


#############################################
# TRACK / UNTRACK CORE (shared by REST + reconcile)
#############################################

def _track(body: dict) -> dict:
    """Resolve a trade's legs to OSI symbols, register it, and subscribe its legs.

    `body` keys: trade_id, symbol, strategy, expiration, quantity, entry_credit,
    short_strike, long_strike, call_short, call_long, target_mid, stop_mid.
    Returns {"status":"ok","legs":{...}} or {"status":"error","detail":...}.
    Never raises — callers (REST + reconcile) rely on this.
    """
    trade_id = body.get("trade_id")
    try:
        expiration = body["expiration"]
        # Skip already-expired trades: Schwab 400s on a chain request for a past
        # expiration, and reconcile would retry every cycle forever. Today's 0-DTE
        # (expiration == today) is still tracked. Stale OPEN rows just get skipped.
        try:
            if datetime.fromisoformat(expiration).date() < datetime.now(SHUTDOWN_TZ).date():
                logger.info("track %s: skipping expired trade (exp %s)", trade_id, expiration)
                return {"status": "skipped", "detail": "expired"}
        except (ValueError, TypeError):
            pass  # malformed date — fall through to the normal path
        result = token_mgr.api_request(
            "/chains",
            params={
                "symbol": body["symbol"],
                "contractType": "ALL",
                "fromDate": expiration,
                "toDate": expiration,
            },
        )
        if result["status_code"] != 200 or not result["data"]:
            detail = f"chain fetch failed ({result['status_code']}): {result['error']}"
            logger.error("track %s: %s", trade_id, detail)
            return {"status": "error", "detail": detail}

        legs = resolve_legs(
            result["data"],
            body["strategy"],
            body["short_strike"],
            body["long_strike"],
            body.get("call_short"),
            body.get("call_long"),
        )

        # target/stop: prefer caller-supplied, else mirror the recommender rules.
        credit = body["entry_credit"]
        target_mid = body.get("target_mid")
        stop_mid = body.get("stop_mid")
        if target_mid is None:
            target_mid = round(credit * (1 - TP_FRAC), 2)
        if stop_mid is None:
            stop_mid = round(credit * (1 + STOP_MULT), 2)

        state = {
            "trade_id": trade_id,
            "strategy": body["strategy"],
            "entry_credit": credit,
            "quantity": body.get("quantity", 1),
            "short_strike": body["short_strike"],
            "long_strike": body["long_strike"],
            "target_mid": target_mid,
            "stop_mid": stop_mid,
            "legs": legs,
            "fired": perf_writer.load_fired(trade_id),
        }
        _registry.add(state)
        _subscribe(set(legs.values()))
        logger.info("tracking %s (%s) legs=%s", trade_id, body["strategy"], legs)
        return {"status": "ok", "legs": legs}
    except KeyError as exc:
        detail = f"strike/field not found: {exc}"
        logger.error("track %s: %s", trade_id, detail)
        return {"status": "error", "detail": detail}
    except Exception as exc:  # never let a bad track break the caller/reconcile
        logger.exception("track %s failed", trade_id)
        return {"status": "error", "detail": str(exc)}


def _untrack(trade_id: str) -> dict:
    """Remove a trade from the registry and unsubscribe any legs no longer used."""
    try:
        state = _registry.get(trade_id)
        if state is None:
            return {"status": "ok"}
        legs_before = set(state.get("legs", {}).values())
        _registry.remove(trade_id)
        # legs still referenced by other tracked trades must stay subscribed.
        still_used = _registry.legs_union()
        orphaned = legs_before - still_used
        _entry_snapped.discard(trade_id)
        if orphaned:
            _unsubscribe(orphaned)
        logger.info("untracked %s; orphaned legs=%s", trade_id, orphaned)
        return {"status": "ok"}
    except Exception as exc:
        logger.exception("untrack %s failed", trade_id)
        return {"status": "error", "detail": str(exc)}


#############################################
# TRACK / UNTRACK REST ENDPOINTS (Task 12)
#############################################

@app.post("/track")
def track(body: dict):
    """Begin streaming-tracking an OptionsScanner paper trade.

    Returns HTTP 200 even on resolution failure (with status="error") so a
    transient chain hiccup doesn't break the caller — reconcile retries later.
    """
    return _track(body)


@app.post("/untrack")
def untrack(body: dict):
    """Stop tracking a trade. Body: {"trade_id": ...}."""
    trade_id = body.get("trade_id")
    if not trade_id:
        return {"status": "error", "detail": "trade_id required"}
    return _untrack(trade_id)


#############################################
# RECONCILE LOOP (Task 13)
#############################################

_RECONCILE_SQL = (
    "SELECT trade_id, symbol, strategy, expiration, quantity, entry_credit, "
    "short_strike, long_strike, call_short, call_long "
    "FROM trades WHERE status='OPEN'"
)


def _read_open_trades() -> Optional[Dict[str, dict]]:
    """Read OPEN trades from trades.db READ-ONLY. Returns None if the DB can't be
    opened/read this cycle (file may not exist yet) so the caller can skip."""
    try:
        conn = sqlite3.connect(
            f"file:{OPTIONSCANNER_TRADES_DB}?mode=ro", uri=True, timeout=5.0)
    except Exception as exc:
        logger.debug("reconcile: trades.db open failed: %s", exc)
        return None
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(_RECONCILE_SQL).fetchall()
        return {r["trade_id"]: dict(r) for r in rows}
    except Exception as exc:
        logger.warning("reconcile: query failed: %s", exc)
        return None
    finally:
        conn.close()


def _reconcile_loop():
    """Every RECONCILE_INTERVAL s, sync the registry with trades.db OPEN rows."""
    while True:
        time.sleep(RECONCILE_INTERVAL)
        try:
            open_trades = _read_open_trades()
            if open_trades is None:
                continue  # DB unavailable this cycle
            open_ids = set(open_trades)
            tracked_ids = {s["trade_id"] for s in _registry.all_trades()}

            added = 0
            for trade_id in open_ids - tracked_ids:
                row = open_trades[trade_id]
                try:
                    credit = row["entry_credit"]
                    body = dict(row)
                    body["target_mid"] = round(credit * (1 - TP_FRAC), 2)
                    body["stop_mid"] = round(credit * (1 + STOP_MULT), 2)
                    res = _track(body)
                    if res.get("status") == "ok":
                        added += 1
                except Exception:
                    logger.exception("reconcile: track %s failed", trade_id)

            removed = 0
            for trade_id in tracked_ids - open_ids:
                try:
                    _untrack(trade_id)
                    removed += 1
                except Exception:
                    logger.exception("reconcile: untrack %s failed", trade_id)

            logger.info(
                "reconcile: tracked=%d added=%d removed=%d",
                len(tracked_ids) + added - removed, added, removed)
        except Exception:
            logger.exception("reconcile loop iteration failed")


#############################################
# STREAM WORKER (Task 14)
#############################################

def _field(item: dict, name: str, num: str):
    """Read a relabeled field by enum NAME, falling back to its raw numeric key."""
    if name in item:
        return item[name]
    return item.get(num)


def _quote_for_trade(state: dict) -> Optional[dict]:
    """Build the {leg_name: {"bid","ask"}} map the detector expects, or None if
    any leg is currently unquoted."""
    legs = {}
    for leg_name, osi in state.get("legs", {}).items():
        q = _leg_quotes.get(osi)
        if not q or q.get("bid") is None or q.get("ask") is None:
            return None
        legs[leg_name] = {"bid": q["bid"], "ask": q["ask"]}
    return legs


def _iv_snapshot(state: dict, moment: str, ts: str, underlying) -> dict:
    """Compose a perf_iv_snapshots row from the current _leg_quotes IVs."""
    legs = state.get("legs", {})

    def iv_of(leg_name):
        osi = legs.get(leg_name)
        q = _leg_quotes.get(osi) if osi else None
        return q.get("iv") if q else None

    return {
        "trade_id": state["trade_id"],
        "moment": moment,
        "ts": ts,
        "put_short_iv": iv_of("put_short"),
        "put_long_iv": iv_of("put_long"),
        "call_short_iv": iv_of("call_short"),
        "call_long_iv": iv_of("call_long"),
        "underlying": underlying,
    }


def _on_option_message(msg):
    """schwab-py level-one option handler. msg has a 'content' list; each item has
    a 'key' (OSI symbol) plus relabeled field values. Runs the detector per trade.

    NOTE: this is invoked on the stream worker's event loop. perf_writer writes
    never raise; we still wrap the body so a single bad tick can't kill the loop.
    """
    try:
        content = msg.get("content") or []
        for item in content:
            key = item.get("key") or _field(item, "SYMBOL", "0")
            if not key:
                continue
            bid = _field(item, "BID_PRICE", "2")
            ask = _field(item, "ASK_PRICE", "3")
            iv = _field(item, "VOLATILITY", "10")
            underlying = _field(item, "UNDERLYING_PRICE", "35")

            # Merge into the per-osi quote cache (fields can arrive partial).
            cur = _leg_quotes.setdefault(key, {"bid": None, "ask": None, "iv": None})
            if bid is not None:
                cur["bid"] = bid
            if ask is not None:
                cur["ask"] = ask
            if iv is not None:
                cur["iv"] = iv

            for trade_id, _leg_name in _registry.for_osi(key):
                state = _registry.get(trade_id)
                if state is None:
                    continue
                legs = _quote_for_trade(state)
                if legs is None:
                    continue  # not all legs quoted yet

                ts = _now_iso()

                # Entry IV snapshot on first fully-quoted sighting.
                if trade_id not in _entry_snapped:
                    perf_writer.record_iv_snapshot(
                        _iv_snapshot(state, "entry", ts, underlying))
                    _entry_snapped.add(trade_id)

                event = trade_detector.evaluate(state, legs, underlying, ts)
                if event:
                    perf_writer.record_event(event)
                    perf_writer.record_iv_snapshot(
                        _iv_snapshot(state, event["event_type"], ts, underlying))
                    state["fired"].add(event["event_type"])
                    if event["terminal"]:
                        _untrack(trade_id)
    except Exception:
        logger.exception("_on_option_message failed")


def _subscribe(osis):
    """Marshal a level-one option ADD subscription onto the stream loop. No-op if
    the loop/client isn't up yet (the worker subscribes legs_union on login)."""
    osis = [o for o in osis if o]
    if not osis or _stream_loop is None or _stream_client is None:
        return
    try:
        asyncio.run_coroutine_threadsafe(
            _stream_client.level_one_option_add(list(osis)), _stream_loop)
    except Exception:
        logger.exception("subscribe failed for %s", osis)


def _unsubscribe(osis):
    """Marshal a level-one option UNSUBS onto the stream loop. No-op if not up."""
    osis = [o for o in osis if o]
    if not osis or _stream_loop is None or _stream_client is None:
        return
    try:
        asyncio.run_coroutine_threadsafe(
            _stream_client.level_one_option_unsubs(list(osis)), _stream_loop)
    except Exception:
        logger.exception("unsubscribe failed for %s", osis)


#############################################
# EQUITY SUBSCRIPTION MARSHALLING (shared session)
#############################################

def _update_equity_refcount(counter, symbols, add: bool) -> set:
    """Apply +1/-1 per symbol to `counter`; drop symbols whose count hits 0.
    Returns the current union (set of symbols with count > 0). Pure."""
    for s in symbols:
        if add:
            counter[s] += 1
        else:
            counter[s] -= 1
            if counter[s] <= 0:
                del counter[s]
    return set(counter)


def _equity_enqueue(queue, tick):
    """Bounded put with drop-oldest. Runs on the subscriber's event loop via
    call_soon_threadsafe, so queue ops are loop-safe here."""
    try:
        queue.put_nowait(tick)
    except asyncio.QueueFull:
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
        try:
            queue.put_nowait(tick)
        except asyncio.QueueFull:
            pass


def _on_equity_message(msg):
    """LEVELONE_EQUITIES handler on the shared stream worker loop. Normalizes
    each content item and fans it out to matching SSE subscribers' queues via
    each subscriber's own loop (call_soon_threadsafe). Never let a bad tick kill
    the loop."""
    try:
        for item in msg.get("content") or []:
            tick = _normalize_level1_equity(item)
            sym = tick.get("symbol")
            if not sym:
                continue
            with _equity_lock:
                targets = [s for s in _equity_subscribers.values() if sym in s["symbols"]]
            for s in targets:
                try:
                    s["loop"].call_soon_threadsafe(_equity_enqueue, s["queue"], tick)
                except Exception:
                    pass
    except Exception:
        logger.exception("_on_equity_message failed")


def _apply_equity_subscription():
    """Marshal an equity-subscription reconcile onto the stream worker loop.

    The reconcile coroutine reads the CURRENT refcount union itself (on the
    worker loop) rather than acting on a snapshot captured here, so concurrent
    connect/disconnect calls can't apply stale unions out of dispatch order.
    Safe no-op if the loop/client isn't up yet (the worker re-subscribes the
    union on login)."""
    if _stream_loop is None or _stream_client is None:
        return
    try:
        asyncio.run_coroutine_threadsafe(
            _reconcile_equity_subscription(), _stream_loop)
    except Exception:
        logger.exception("equity subscription reconcile dispatch failed")


async def _reconcile_equity_subscription():
    """Runs on the stream worker loop. Reconciles Schwab's equity subscription to
    the current refcount union using subs (replace-semantics) / unsubs."""
    global _equity_subscribed
    with _equity_lock:
        union = set(_equity_refcount)
        prev = set(_equity_subscribed)
        _equity_subscribed = set(union)
    try:
        if union and union != prev:
            await _stream_client.level_one_equity_subs(list(union))
        elif not union and prev:
            await _stream_client.level_one_equity_unsubs(list(prev))
    except Exception:
        logger.exception("equity subscription reconcile failed")


def _stream_worker():
    """Daemon thread: owns an asyncio loop + StreamClient and runs the receive loop.

    Reconnects with backoff on disconnect. When the registry is empty the socket
    stays idle (we still await handle_message)."""
    global _stream_loop, _stream_client
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    _stream_loop = loop

    async def _main():
        global _stream_client, _equity_subscribed
        backoff = 5
        while True:
            try:
                _stream_client = stream_bridge.build_stream_client(
                    token_mgr, token_mgr.app_key, token_mgr.app_secret)
                await _stream_client.login()
                _stream_client.add_level_one_option_handler(_on_option_message)
                _stream_client.add_level_one_equity_handler(_on_equity_message)
                # Reconcile may have populated the registry before login.
                existing = _registry.legs_union()
                if existing:
                    await _stream_client.level_one_option_subs(list(existing))
                # Re-subscribe the equity union too: SSE subscribers may have
                # connected before the worker was up, and equities must resume
                # after any reconnect (level_one_equity_subs REPLACES the set).
                with _equity_lock:
                    eq_union = set(_equity_refcount)
                    _equity_subscribed = set(eq_union)
                if eq_union:
                    await _stream_client.level_one_equity_subs(list(eq_union))
                logger.info(
                    "stream worker: logged in; %d option legs, %d equity symbols",
                    len(existing), len(eq_union))
                backoff = 5
                while True:
                    await _stream_client.handle_message()
            except Exception:
                logger.exception(
                    "stream worker error; reconnecting in %ds", backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    try:
        loop.run_until_complete(_main())
    except Exception:
        logger.exception("stream worker loop terminated")


#############################################
# EQUITY STREAM (LEVELONE_EQUITIES) -> SSE
#############################################
#
# Exposes the proxy's Schwab equity stream as Server-Sent Events at
# GET /stream/quotes?symbols=AAPL,XLK,SPY. Equities ride the SHARED stream
# worker session (the same StreamClient that serves LEVELONE_OPTIONS) — they do
# NOT open a second Schwab login, because Schwab allows only one streamer session
# per credential. Each SSE request registers as a subscriber; the worker's equity
# handler (_on_equity_message) fans matching normalized ticks onto each
# subscriber's asyncio.Queue, and the StreamingResponse generator drains the
# queue and frames each tick as an SSE `data:` line.
#
# schwab-py handle_message() relabels numeric field keys to the UPPERCASE
# LevelOneEquityFields enum names (e.g. "LAST_PRICE", "NET_CHANGE"); the
# subscribed symbol is in "key". _normalize_level1_equity reads the relabeled
# names first and falls back to the raw numeric strings, mirroring _field().

# Schwab LEVELONE_EQUITIES field map: (relabeled enum NAME, raw numeric key).
# Numeric keys taken from schwab-py's StreamClient.LevelOneEquityFields enum:
#   3 = LAST_PRICE, 18 = NET_CHANGE. handle_message() relabels these to the
# UPPERCASE enum names; we read the name first and fall back to the numeric key.
# These are the regular (not extended-hours) fields. The numeric values are
# verified against schwab-py's enum but the exact populated fields per tick
# should still be sanity-checked against a real LEVELONE_EQUITIES payload.
# TODO(live): confirm LAST_PRICE/NET_CHANGE are populated as expected on a live
# stream sample (vs. e.g. REGULAR_MARKET_* variants 29/31 during RTH).
_L1_EQUITY_FIELDS = {
    "last": ("LAST_PRICE", "3"),
    "net_change": ("NET_CHANGE", "18"),
}


def _coerce_float(value):
    """Coerce a streamed field value to float, or None if absent/unparseable."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_level1_equity(content_item: dict) -> dict:
    """Map a single LEVELONE_EQUITIES content item to a compact quote dict.

    Returns {"symbol": <key>, "last": <float|None>, "net_change": <float|None>}.
    Reads each field by its relabeled enum NAME, falling back to the raw numeric
    key; missing or unparseable values become None. Extra/unknown fields are
    ignored.
    """
    out = {"symbol": content_item.get("key", "")}
    for out_name, (enum_name, num_key) in _L1_EQUITY_FIELDS.items():
        out[out_name] = _coerce_float(_field(content_item, enum_name, num_key))
    return out


def _sse_format(payload: dict) -> str:
    """Frame a dict as a single SSE event: `data: <json>\\n\\n`."""
    return "data: " + json.dumps(payload) + "\n\n"


@app.get("/stream/quotes")
async def stream_quotes(symbols: str = Query(...)):
    """SSE stream of LEVELONE_EQUITIES quotes for `symbols` (comma-separated).

    Rides the SHARED stream worker session (the same StreamClient that serves
    LEVELONE_OPTIONS), so it does NOT open a second Schwab login — Schwab allows
    only one streamer session per credential. This request registers as a
    subscriber; the worker's equity handler fans matching ticks to our queue.

    The normalizer and SSE framing are unit-tested; the live fan-out/subscription
    is verified end-to-end against a running proxy.
    """
    syms = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if not syms:
        raise HTTPException(status_code=400, detail="symbols query param required")

    async def event_generator():
        loop = asyncio.get_running_loop()
        queue: "asyncio.Queue[dict]" = asyncio.Queue(maxsize=1000)
        sub_id = next(_equity_id_counter)
        with _equity_lock:
            _equity_subscribers[sub_id] = {"loop": loop, "queue": queue, "symbols": set(syms)}
            _update_equity_refcount(_equity_refcount, syms, add=True)
        _apply_equity_subscription()
        logger.info("equity SSE subscriber %d: %s", sub_id, ",".join(syms))
        try:
            while True:
                try:
                    tick = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield _sse_format(tick)
        finally:
            with _equity_lock:
                _equity_subscribers.pop(sub_id, None)
                _update_equity_refcount(_equity_refcount, syms, add=False)
            _apply_equity_subscription()
            logger.info("equity SSE subscriber %d closed", sub_id)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


#############################################
# ENTRY POINT
#############################################

if __name__ == "__main__":
    # Pass the app OBJECT, not the "schwab_proxy:app" import string. The string
    # form makes uvicorn import this module a SECOND time (it is already loaded
    # as __main__), which re-runs the top-level logging setup and creates a
    # DUPLICATE TimedRotatingFileHandler on errors.log. Two open handles on the
    # same file make the weekly rollover's os.rename fail on Windows with
    # "[WinError 32] file in use". The object form (valid since reload=False and
    # no workers) loads the module once → one handler → rollover succeeds.
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=PROXY_PORT,
        reload=False,
        log_level="info",
    )

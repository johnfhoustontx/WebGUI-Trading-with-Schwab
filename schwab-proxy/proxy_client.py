"""
SchwabProxyClient - Drop-in client adapters for the Schwab API Proxy
Version: 1.0.0
Last Updated: 2026-04-06

Provides two adapter classes so existing apps can switch to the proxy
with minimal code changes:

  SchwabPyProxyClient   — mimics schwab-py interface (returns Response-like)
                          Used by Options Scanner (dashboard.py / scanner_engine.py)

  SchwabProxyClient     — mimics SchwabClient interface (returns dicts/DataFrames)
                          Used by Sentiment Dashboard (sentiment_dashboard.py)

Version 1.0.0 Changes:
- Initial implementation
- SchwabPyProxyClient with get_quotes, get_option_chain, get_price_history_every_day
- SchwabProxyClient with get_quote, get_quotes, get_daily_history, _request
- Auto-detection of proxy availability with fallback warning
"""

import logging
import os
import re
import sys, pathlib
from dataclasses import dataclass
from typing import Optional, Dict, Any, List

import requests

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # repo root
from repo_paths import IS_DEV, PROXY_URL

logger = logging.getLogger(__name__)

PROXY_BASE = PROXY_URL


def _client_secret() -> Optional[str]:
    """The shared secret to send on proxy requests, or None (default → send nothing).

    Resolves the SAME source the proxy checks: env ``PROXY_SHARED_SECRET`` → gitignored
    ``shared/proxy_secret.txt``. When set, clients attach it as the ``X-Proxy-Secret``
    header so the (optionally) auth-guarded trading endpoints accept them. Backward-
    compatible: unset → no header, exactly as before. Never raises."""
    env = os.environ.get("PROXY_SHARED_SECRET")
    if env and env.strip():
        return env.strip()
    try:
        from repo_paths import SHARED_DIR
        p = pathlib.Path(SHARED_DIR) / "proxy_secret.txt"
        if p.exists():
            s = p.read_text(encoding="utf-8").strip()
            if s:
                return s
    except Exception:  # noqa: BLE001 — missing/unreadable secret → send no header.
        pass
    return None


def _apply_secret(session: "requests.Session") -> None:
    """Attach the X-Proxy-Secret header to a session when a secret is configured."""
    secret = _client_secret()
    if secret:
        session.headers["X-Proxy-Secret"] = secret


# A caller label travels in an HTTP header and is a key in the proxy's counts.
_LABEL_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]")
_LABEL_MAX = 40


def caller_label(name) -> str:
    """``name`` as a label that is safe to send in the ``X-Caller`` header.

    Anything outside letters, digits, ``_``, ``.`` and ``-`` becomes ``_``: a
    header value with a newline, an em dash or any non-latin-1 character makes
    every request from the process fail before it is sent, and that reads as
    "proxy down". Empty is ``unknown``. A dev checkout borrows prod's proxy, so
    its label carries a ``dev.`` prefix and is counted apart. At most 40
    characters, the width the proxy keeps. Never raises."""
    try:
        label = _LABEL_UNSAFE.sub("_", str(name if name is not None else "").strip())
    except Exception:  # noqa: BLE001 — identity is a label, never a failure.
        label = ""
    label = label or "unknown"
    if IS_DEV:
        label = "dev." + label
    return label[:_LABEL_MAX]


def _caller_name() -> str:
    """Who this process is, for the proxy's per-caller counts: the env override,
    else the service folder for ``services/<name>/app.py``, else the script
    name. Never raises."""
    env = os.environ.get("TRADING_CALLER")
    if env and env.strip():
        return caller_label(env)
    name = ""
    try:
        if sys.argv and sys.argv[0]:
            # Resolved, so ``python app.py`` run from inside a service folder
            # still reports the folder.
            entry = pathlib.Path(sys.argv[0]).resolve()
            name = entry.parent.name if entry.name == "app.py" else entry.stem
    except Exception:  # noqa: BLE001 — identity is a label, never a failure.
        name = ""
    return caller_label(name)


def _apply_identity(session: "requests.Session") -> None:
    """Attach the X-Caller header the proxy counts requests by."""
    session.headers["X-Caller"] = _caller_name()


# What a priority request carries. The proxy reads it in ``_wants_priority``.
PRIORITY_HEADERS = {"X-Priority": "1"}


def _store_age(headers):
    """Seconds since the answer left Schwab, from ``X-Store-Age``; None when the
    proxy did not say."""
    try:
        return float(headers.get("X-Store-Age"))
    except (TypeError, ValueError, AttributeError):
        return None

#############################################
# HELPERS
#############################################


@dataclass
class FakeResponse:
    """Mimics httpx.Response / requests.Response for schwab-py compatibility.
    
    The Options Scanner calls .status_code and .json() on responses from
    schwab-py, so we need this thin wrapper.
    """
    status_code: int
    _data: Any = None
    _text: str = ""
    # Set by the proxy's local store: "hit" / "subset" / "miss" / ... and how
    # many seconds ago the data left Schwab. None from an older proxy.
    store_kind: Optional[str] = None
    store_age: Optional[float] = None

    def json(self):
        return self._data

    @property
    def text(self):
        return self._text


class _ContractType:
    ALL = "ALL"
    CALL = "CALL"
    PUT = "PUT"


class _Options:
    ContractType = _ContractType()


#############################################
# ADAPTER 1: schwab-py compatible
# (for Options Scanner)
#############################################

class SchwabPyProxyClient:
    """Drop-in replacement for schwab-py client in the Options Scanner.
    
    Matches the interface used in scanner_engine.py:
        client.get_quotes(symbols)           -> FakeResponse
        client.get_option_chain(symbol, ...) -> FakeResponse
        client.get_price_history_every_day(symbol) -> FakeResponse
        client.Options.ContractType.ALL      -> "ALL"
    """

    Options = _Options()

    # This client can mark a request for the proxy's priority lane
    # (``priority=True`` on get_option_chain / get_quotes). A caller checks for
    # a literal True before passing the argument, so a client double with a
    # fixed signature is called exactly as before.
    supports_priority = True

    def __init__(self, base_url: str = PROXY_BASE):
        self.base = base_url
        self.session = requests.Session()
        _apply_secret(self.session)
        _apply_identity(self.session)

    def _get(self, path: str, params: Optional[Dict] = None,
             priority: bool = False) -> FakeResponse:
        try:
            # The mark travels as a HEADER, never a parameter: it must not reach
            # Schwab and must not change which stored answer matches. An
            # ordinary request passes no ``headers`` at all.
            extra = {"headers": PRIORITY_HEADERS} if priority else {}
            resp = self.session.get(f"{self.base}{path}", params=params,
                                    timeout=30, **extra)
            if resp.status_code == 200:
                headers = getattr(resp, "headers", None) or {}
                return FakeResponse(status_code=200, _data=resp.json(),
                                    store_kind=headers.get("X-Store"),
                                    store_age=_store_age(headers))
            else:
                return FakeResponse(
                    status_code=resp.status_code,
                    _text=resp.text,
                )
        except Exception as e:
            logger.error(f"Proxy request failed: {e}")
            return FakeResponse(status_code=502, _text=str(e))

    def get_quotes(self, symbols, priority: bool = False) -> FakeResponse:
        """Get quotes for one or more symbols.
        
        schwab-py accepts a list or string; we normalize to CSV.
        """
        if isinstance(symbols, (list, tuple)):
            sym_str = ",".join(symbols)
        else:
            sym_str = str(symbols)
        return self._get("/quotes", params={"symbols": sym_str}, priority=priority)

    def get_option_expirations(self, symbol: str) -> FakeResponse:
        """Every listed expiration for ``symbol`` — Schwab's ``/expirationchain``
        (no strikes, ~0.2 s), through the proxy's generic ``/passthrough``."""
        return self._get("/passthrough", params={
            "endpoint": "/expirationchain", "params": f"symbol={symbol}"})

    def get_option_chain(
        self,
        symbol: str,
        contract_type=None,
        from_date=None,
        to_date=None,
        max_age=None,
        priority: bool = False,
        **kwargs,
    ) -> FakeResponse:
        """Get option chain for a symbol. ``priority`` asks the proxy to send
        the request ahead of waiting ordinary ones (the one-minute collection
        poll; see schwab-proxy/rate_gate.py)."""
        params: Dict[str, Any] = {"symbol": symbol}
        if contract_type is not None:
            ct = contract_type if isinstance(contract_type, str) else "ALL"
            params["contractType"] = ct
        if from_date is not None:
            params["fromDate"] = (
                from_date.isoformat()
                if hasattr(from_date, "isoformat")
                else str(from_date)
            )
        if to_date is not None:
            params["toDate"] = (
                to_date.isoformat()
                if hasattr(to_date, "isoformat")
                else str(to_date)
            )
        if max_age is not None:
            params["maxAge"] = max_age
        return self._get("/chains", params=params, priority=priority)

    def get_price_history_every_day(self, symbol: str) -> FakeResponse:
        """Get daily price history for ~1 year (matches schwab-py helper)."""
        return self._get("/pricehistory", params={
            "symbol": symbol,
            "periodType": "year",
            "period": 1,
            "frequencyType": "daily",
            "frequency": 1,
        })

    def get_price_history_every_minute(self, symbol: str) -> FakeResponse:
        """Get 1-min intraday history for the last 2 days (matches schwab-py helper)."""
        return self._get("/pricehistory", params={
            "symbol": symbol,
            "periodType": "day",
            "period": 2,
            "frequencyType": "minute",
            "frequency": 1,
        })

    def get_price_history_every_five_minutes(self, symbol: str) -> FakeResponse:
        """Get 5-min intraday history for the last 5 days (matches schwab-py helper)."""
        return self._get("/pricehistory", params={
            "symbol": symbol,
            "periodType": "day",
            "period": 5,
            "frequencyType": "minute",
            "frequency": 5,
        })


#############################################
# ADAPTER 2: SchwabClient compatible
# (for Sentiment Dashboard)
#############################################

class SchwabProxyClient:
    """Drop-in replacement for the custom SchwabClient used by
    the Sentiment Dashboard.
    
    Matches the interface used in sentiment_dashboard.py:
        client.get_quote(symbol)             -> dict
        client.get_quotes(symbols)           -> dict
        client.get_daily_history(sym, months) -> DataFrame
        client._request(endpoint, params)    -> dict
    """

    def __init__(self, base_url: str = PROXY_BASE):
        self.base = base_url
        self.session = requests.Session()
        _apply_secret(self.session)
        _apply_identity(self.session)

    def _proxy_get(self, path: str, params: Optional[Dict] = None) -> Optional[Dict]:
        try:
            resp = self.session.get(f"{self.base}{path}", params=params, timeout=30)
            if resp.status_code == 200:
                return resp.json()
            logger.error(f"Proxy {resp.status_code}: {resp.text[:200]}")
            return None
        except Exception as e:
            logger.error(f"Proxy request failed: {e}")
            return None

    @staticmethod
    def _extract_change_pct(q: Dict) -> float:
        """Extract daily % change with multi-field fallback.

        Schwab API has used several field names across versions
        (netPercentChangeInDouble, netPercentChange). When all are
        missing/zero, derive from lastPrice vs closePrice.
        """
        for field in ("netPercentChange", "netPercentChangeInDouble",
                      "regularMarketPercentChangeInDouble"):
            v = q.get(field)
            if v not in (None, 0, 0.0):
                return v
        # Derive from last vs prior close
        last = q.get("lastPrice") or q.get("regularMarketLastPrice") or 0
        close = q.get("closePrice") or q.get("regularMarketLastPrice", 0)
        if last and close and close != 0:
            return (last - close) / close * 100
        return 0.0

    def get_quote(self, symbol: str) -> Optional[Dict]:
        """Get a single symbol quote. Returns {'last': float, ...}."""
        data = self._proxy_get("/quote", params={"symbol": symbol})
        if not data:
            return None
        # Schwab returns {SYMBOL: {quote: {...}}} — unwrap
        for sym, info in data.items():
            q = info.get("quote", info.get("reference", info))
            return {
                "last": q.get("lastPrice", 0),
                "change": q.get("netChange", 0),
                "change_pct": self._extract_change_pct(q),
                "high": q.get("highPrice", 0),
                "low": q.get("lowPrice", 0),
                "volume": q.get("totalVolume", 0),
                "symbol": sym,
            }
        return None

    def get_quotes(self, symbols: List[str]) -> Dict[str, Dict]:
        """Get quotes for multiple symbols."""
        data = self._proxy_get("/quotes", params={"symbols": ",".join(symbols)})
        if not data:
            return {}
        result = {}
        for sym, info in data.items():
            q = info.get("quote", info.get("reference", info))
            result[sym] = {
                "last": q.get("lastPrice", 0),
                "change": q.get("netChange", 0),
                "change_pct": self._extract_change_pct(q),
                "high": q.get("highPrice", 0),
                "low": q.get("lowPrice", 0),
                "volume": q.get("totalVolume", 0),
            }
        return result

    def get_daily_history(self, symbol: str, months: int = 12):
        """Get daily OHLCV history. Returns a pandas DataFrame or None."""
        import pandas as pd

        # Map months to Schwab periodType/period
        if months <= 1:
            period_type, period = "month", 1
        elif months <= 3:
            period_type, period = "month", 3
        elif months <= 6:
            period_type, period = "month", 6
        else:
            period_type, period = "year", 1

        data = self._proxy_get("/pricehistory", params={
            "symbol": symbol,
            "periodType": period_type,
            "period": period,
            "frequencyType": "daily",
            "frequency": 1,
        })
        if not data or "candles" not in data:
            return None

        candles = data["candles"]
        if not candles:
            return None

        df = pd.DataFrame(candles)
        df["datetime"] = pd.to_datetime(df["datetime"], unit="ms")
        return df

    def get_intraday_history(self, symbol: str, minutes: int = 15, days: int = 1):
        """Intraday minute-bar OHLCV history -> pandas DataFrame or None."""
        import pandas as pd
        data = self._proxy_get("/pricehistory", params={
            "symbol": symbol, "periodType": "day", "period": days,
            "frequencyType": "minute", "frequency": minutes,
        })
        if not data or not data.get("candles"):
            return None
        df = pd.DataFrame(data["candles"])
        df["datetime"] = pd.to_datetime(df["datetime"], unit="ms")
        return df

    def _request(self, endpoint: str, params: Optional[Dict] = None) -> Optional[Dict]:
        """Generic pass-through (used for /chains in SPX P/C calculation)."""
        return self._proxy_get(endpoint, params=params)

    def get_quote_raw(self, symbol: str) -> Optional[Dict]:
        """One symbol's RAW ``/quotes`` reply, fundamental block included.

        ``{SYMBOL: {"quote": ..., "fundamental": ..., ...}}`` exactly as Schwab
        sends it (not flattened like :meth:`get_quotes`), or None when the proxy
        failed. Goes through ``/passthrough``, which splits ``params`` on commas —
        so a second symbol would be mangled and a comma is refused outright.
        Used by trade_svc's daily dividend pull.
        """
        if "," in (symbol or ""):
            raise ValueError(f"one symbol per call, got {symbol!r}")
        return self._proxy_get("/passthrough", params={
            "endpoint": "/quotes",
            "params": f"symbols={symbol},fields=fundamental",
        })

    def get_fundamentals(self, symbol: str) -> Optional[Dict]:
        """Fetch Schwab fundamentals for a symbol (the inner ``fundamental`` dict).

        Calls the proxy ``/instruments?projection=fundamental`` endpoint and
        unwraps ``instruments[0].fundamental`` (P/E, growth, ROE, margins, …),
        or returns None if the proxy/Schwab gave nothing usable. Used by the
        Trade service's Investor verdict.
        """
        data = self._proxy_get("/instruments",
                               params={"symbol": symbol, "projection": "fundamental"})
        if not data:
            return None
        instruments = data.get("instruments") or []
        if not instruments:
            return None
        return instruments[0].get("fundamental")

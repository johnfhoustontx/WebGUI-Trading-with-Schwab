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

import json
import logging
import time
import sys, pathlib
from dataclasses import dataclass
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta

import requests

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # repo root
from repo_paths import PROXY_URL

logger = logging.getLogger(__name__)

PROXY_BASE = PROXY_URL

#############################################
# HELPERS
#############################################

def proxy_available(base: str = PROXY_BASE) -> bool:
    """Check if the Schwab proxy is running."""
    try:
        r = requests.get(f"{base}/health", timeout=3)
        return r.status_code == 200 and r.json().get("status") == "ok"
    except Exception:
        return False


@dataclass
class FakeResponse:
    """Mimics httpx.Response / requests.Response for schwab-py compatibility.
    
    The Options Scanner calls .status_code and .json() on responses from
    schwab-py, so we need this thin wrapper.
    """
    status_code: int
    _data: Any = None
    _text: str = ""

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

    def __init__(self, base_url: str = PROXY_BASE):
        self.base = base_url
        self.session = requests.Session()

    def _get(self, path: str, params: Optional[Dict] = None) -> FakeResponse:
        try:
            resp = self.session.get(f"{self.base}{path}", params=params, timeout=30)
            if resp.status_code == 200:
                return FakeResponse(status_code=200, _data=resp.json())
            else:
                return FakeResponse(
                    status_code=resp.status_code,
                    _text=resp.text,
                )
        except Exception as e:
            logger.error(f"Proxy request failed: {e}")
            return FakeResponse(status_code=502, _text=str(e))

    def get_quotes(self, symbols) -> FakeResponse:
        """Get quotes for one or more symbols.
        
        schwab-py accepts a list or string; we normalize to CSV.
        """
        if isinstance(symbols, (list, tuple)):
            sym_str = ",".join(symbols)
        else:
            sym_str = str(symbols)
        return self._get("/quotes", params={"symbols": sym_str})

    def get_option_chain(
        self,
        symbol: str,
        contract_type=None,
        from_date=None,
        to_date=None,
        **kwargs,
    ) -> FakeResponse:
        """Get option chain for a symbol."""
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
        return self._get("/chains", params=params)

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

    def _request(self, endpoint: str, params: Optional[Dict] = None) -> Optional[Dict]:
        """Generic pass-through (used for /chains in SPX P/C calculation)."""
        return self._proxy_get(endpoint, params=params)

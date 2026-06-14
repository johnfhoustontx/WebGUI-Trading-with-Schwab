"""Fetch a fresh chain snapshot + 2-day 1-min underlying history from Schwab.

Mirrors patterns used in scanner_engine.py / gamma_tool.py:
- ``client.get_option_chain(symbol, contract_type=..., from_date=..., to_date=...)``
  returns a response whose ``.json()`` yields a dict shaped like
  ``{"underlyingPrice", "callExpDateMap", "putExpDateMap", ...}``.
- Schwab returns ``volatility`` as a *percent* (e.g. ``18.0``), which gamma_tool
  consistently divides by 100 to get sigma. We store ``iv`` as a decimal here.
- For 1-min intraday history we use ``client.get_price_history_every_minute``
  (the schwab-py wrapper that pairs with ``get_price_history_every_day``).

No persistence; pure function. Caller is responsible for caching.
"""
from datetime import date, datetime, timedelta
import logging

import pandas as pd

from options_simulator.engine import ChainSnapshot, ContractRow

log = logging.getLogger(__name__)


def _candles_to_series(candles, cutoff_days: int | None = None) -> pd.Series:
    """Turn a Schwab ``candles`` array into a close-price Series indexed in
    Central Time (project-wide convention; matches the CT-based scheduling
    documented in CLAUDE.md). Index is tz-naive after conversion so downstream
    code that compares to ``datetime.now()`` keeps working."""
    if not candles:
        return pd.Series(dtype=float)
    if cutoff_days is not None:
        cutoff_ms = int((datetime.now() - timedelta(days=cutoff_days)).timestamp() * 1000)
        filt = [c for c in candles if int(c.get("datetime", 0)) >= cutoff_ms]
        candles = filt or candles
    idx = (pd.to_datetime([c["datetime"] for c in candles], unit="ms", utc=True)
              .tz_convert("America/Chicago")
              .tz_localize(None))
    return pd.Series([float(c["close"]) for c in candles], index=idx)


def _fetch_price_history(client, symbol: str) -> pd.Series:
    """Best-effort underlying history. Probes the client for the finest
    available resolution: 1-min (last 2 days) → 5-min (last 5 days) →
    daily (last 30 days) → empty. The replay panel compresses overnight
    gaps so the 1-min volume is manageable."""
    if hasattr(client, "get_price_history_every_minute"):
        try:
            resp = client.get_price_history_every_minute(symbol)
            data = resp.json() if hasattr(resp, "json") else resp
            series = _candles_to_series((data or {}).get("candles") or [],
                                         cutoff_days=2)
            if not series.empty:
                return series
        except Exception:
            log.exception("1-min price-history fetch failed for %s; trying 5-min", symbol)

    if hasattr(client, "get_price_history_every_five_minutes"):
        try:
            resp = client.get_price_history_every_five_minutes(symbol)
            data = resp.json() if hasattr(resp, "json") else resp
            series = _candles_to_series((data or {}).get("candles") or [],
                                         cutoff_days=5)
            if not series.empty:
                return series
        except Exception:
            log.exception("5-min price-history fetch failed for %s; trying daily", symbol)

    if hasattr(client, "get_price_history_every_day"):
        try:
            resp = client.get_price_history_every_day(symbol)
            data = resp.json() if hasattr(resp, "json") else resp
            return _candles_to_series((data or {}).get("candles") or [],
                                       cutoff_days=30)
        except Exception:
            log.exception("daily price-history fetch failed for %s", symbol)

    log.warning("no price-history method available on client; replay tab will degrade")
    return pd.Series(dtype=float)


def fetch_snapshot(client, symbol: str, expiry: date | None = None,
                   horizon_days: int = 90) -> ChainSnapshot:
    """Fetch chain for ``symbol`` plus 2-day 1-min underlying history.

    If ``expiry`` is given, request only that date. Otherwise pull all expiries
    in the next ``horizon_days`` so the UI can populate an Expiry picker from
    what the underlying actually offers.
    """
    # --- Option chain ----------------------------------------------------
    if expiry is not None:
        kwargs = {"from_date": expiry, "to_date": expiry}
    else:
        today = date.today()
        kwargs = {"from_date": today, "to_date": today + timedelta(days=horizon_days)}
    try:
        kwargs["contract_type"] = client.Options.ContractType.ALL
    except Exception:  # pragma: no cover - defensive for non-schwab-py clients
        pass

    chain_resp = client.get_option_chain(symbol, **kwargs)
    if hasattr(chain_resp, "status_code") and chain_resp.status_code != 200:
        # Surface Schwab/proxy's actual error message so the user can see
        # whether it's a bad symbol, date range, etc.
        detail = ""
        try:
            body = chain_resp.json() if hasattr(chain_resp, "json") else None
            if isinstance(body, dict):
                detail = (body.get("detail") or body.get("error")
                          or body.get("message") or "")
                if isinstance(detail, dict):
                    detail = detail.get("message") or str(detail)
            if not detail and hasattr(chain_resp, "text"):
                detail = (chain_resp.text or "")[:300]
        except Exception:
            pass
        raise RuntimeError(
            f"Schwab chain fetch for {symbol!r} returned HTTP "
            f"{chain_resp.status_code}"
            + (f" — {detail}" if detail else "")
        )
    chain = chain_resp.json() if hasattr(chain_resp, "json") else chain_resp
    chain = chain or {}

    spot = float(chain.get("underlyingPrice") or 0.0)

    contracts = []
    for side_key, side_kind in (("callExpDateMap", "call"), ("putExpDateMap", "put")):
        for exp_str, strike_map in (chain.get(side_key) or {}).items():
            # Schwab keys are "YYYY-MM-DD:DTE" — parse the date so each contract
            # carries its own expiry (matters when we fetch multi-expiry chains).
            date_part = exp_str.split(":", 1)[0]
            try:
                row_expiry = datetime.strptime(date_part, "%Y-%m-%d").date()
            except ValueError:
                log.warning("could not parse expiry from %r; skipping", exp_str)
                continue
            for _strike_str, opt_list in (strike_map or {}).items():
                for opt in opt_list:
                    bid = float(opt.get("bid", 0) or 0)
                    ask = float(opt.get("ask", 0) or 0)
                    # Schwab returns volatility in percent (e.g. 18.0); store decimal.
                    iv_pct = float(opt.get("volatility", 0) or 0)
                    contracts.append(ContractRow(
                        strike=float(opt.get("strikePrice", 0) or 0),
                        kind=side_kind,
                        bid=bid,
                        ask=ask,
                        mid=(bid + ask) / 2.0,
                        iv=iv_pct / 100.0,
                        expiry=row_expiry,
                    ))

    # --- Underlying price history ---------------------------------------
    # Prefer 1-min resolution (direct schwab-py). The SchwabProxy wrapper
    # exposes only get_price_history_every_day; fall back to daily so the
    # replay tab still shows *something* through the proxy.
    price_history = _fetch_price_history(client, symbol)

    return ChainSnapshot(
        spot=spot,
        as_of=datetime.now(),
        r=0.04,
        symbol=symbol.upper(),
        contracts=contracts,
        price_history=price_history,
    )

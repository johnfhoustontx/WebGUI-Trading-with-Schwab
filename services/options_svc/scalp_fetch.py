"""The Opening Scalp's bar fetcher: Schwab price history -> ``scalp.db``.

Fills the store ``scalp_store`` owns, for every symbol in ``[bars] symbols``
(``config/scalp.toml``), at 1 and 5 minutes. ``backfill`` walks BACK from today
once, by hand (``tools/scalp_backfill.py``); ``top_up`` runs nightly from the
``[slots.scalp_bars]`` scheduler branch and fetches only what is newer than the
store holds.

Dated history goes through the proxy's existing ``/passthrough`` (``endpoint=
/pricehistory``, ``params`` a COMMA-joined ``k=v`` string), so no proxy change is
involved. Measured on prod through exactly this route (2026-10-18):

* ``startDate`` / ``endDate`` are epoch milliseconds;
* one response holds at most 40,000 candles, hence the windows below (10 days of
  1-minute, 30 days of 5-minute bars, extended hours included, stay well under);
* 1-minute history reaches back about 6 weeks, 5-minute about 7 months; past the
  edge Schwab answers 200 with ``{"empty": true, "candles": []}``;
* a candle's ``datetime`` is the bar START in epoch ms UTC.

Never ``priority=True``: that lane belongs to the one-minute GEX poll. This module
imports nothing from ``compute``, and the proxy client is built lazily, so a
module that only needs ``ct`` (every later scalp task) constructs nothing at import.
"""
import datetime as _dt
import logging
import time
from zoneinfo import ZoneInfo

from services import _degrade
from shared import market_calendar as _mc
from shared import scalp_config
from shared.numeric import finite as _finite

from services.options_svc import scalp_store

log = logging.getLogger("options_svc.scalp_fetch")

#: Bar sizes stored, in minutes; the store's ``interval`` column holds the same.
INTERVALS = (1, 5)
#: Calendar days per request, each under the 40,000-candle response cap.
WINDOW_DAYS = {1: 10, 5: 30}
#: Consecutive empty windows that end a backfill walk (past retention, or a gap).
EMPTY_WINDOWS_TO_STOP = 2
#: What ``backfill`` / ``top_up`` report for a key whose walk hit a FAILED fetch
#: (not an empty answer): rows already written for it are kept, the walk stops.
FAILED = -1
#: Trading days back a top-up reaches when the store holds nothing yet.
TOP_UP_FALLBACK_TRADING_DAYS = 3

_CT = ZoneInfo("America/Chicago")


def ct(ts: int) -> _dt.datetime:
    """Epoch SECONDS -> an aware Central-time datetime (zoneinfo, so 08:30 CT
    stays 08:30 CT on both sides of a daylight-saving change)."""
    return _dt.datetime.fromtimestamp(ts, tz=_CT)


def candles_to_rows(symbol: str, interval: int, payload) -> list:
    """Schwab ``{"candles": [{"datetime": ms, ...}]}`` -> store rows.

    ``ts = ms // 1000``. A candle with any non-finite open/high/low/close, or no
    usable ``datetime``, is dropped (absent, never zero); a missing volume is
    stored as ``None``. Anything that is not the expected shape gives ``[]``."""
    candles = payload.get("candles") if isinstance(payload, dict) else None
    if not isinstance(candles, list):
        return []
    rows = []
    for c in candles:
        if not isinstance(c, dict):
            continue
        ms = _finite(c.get("datetime"))
        o, h, lo, cl = (_finite(c.get(k)) for k in ("open", "high", "low", "close"))
        if ms is None or None in (o, h, lo, cl):
            continue
        rows.append((symbol, interval, int(ms // 1000), o, h, lo, cl,
                     _finite(c.get("volume"))))
    return rows


def _default_client():
    # Lazy on purpose: ``services._proxy`` builds the proxy client at import.
    from services import _proxy
    return _proxy.schwab_py_client


def fetch_range(symbol, interval, start_ms, end_ms, client=None):
    """One ``/passthrough`` price-history call; the rows in ``[start_ms, end_ms)``
    (as Schwab returns them).

    ``[]`` is a genuine empty answer (200, no candles: past retention, or a closed
    stretch). ``None`` is a FAILURE (non-200, an exception, a body that is not a
    candle payload), logged and counted as a degrade. The two must never be read
    alike: an empty window ends history, a failed one is a hole."""
    try:
        client = client if client is not None else _default_client()
        query = (f"symbol={symbol},frequencyType=minute,frequency={int(interval)},"
                 f"startDate={int(start_ms)},endDate={int(end_ms)},"
                 f"needExtendedHoursData=true")
        resp = client._get("/passthrough",
                           params={"endpoint": "/pricehistory", "params": query})
        if getattr(resp, "status_code", None) != 200:
            log.warning("scalp bars %s/%s: HTTP %s", symbol, interval,
                        getattr(resp, "status_code", None))
            return None
        payload = resp.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("candles"), list):
            log.warning("scalp bars %s/%s: unexpected body", symbol, interval)
            return None
        return candles_to_rows(symbol, int(interval), payload)
    except Exception:
        _degrade.degraded("options.scalp_fetch", detail=f"{symbol}/{interval}")
        return None


def _ms(moment: _dt.datetime) -> int:
    return int(moment.timestamp() * 1000)


def _day_start(day: _dt.date) -> _dt.datetime:
    """Midnight CT at the start of ``day``."""
    return _dt.datetime(day.year, day.month, day.day, tzinfo=_CT)


def _end_ms(today) -> int:
    """The far end of a walk: now, or the end of the CT day ``today`` names."""
    if today is None:
        return int(time.time() * 1000)
    return _ms(_day_start(today + _dt.timedelta(days=1)))


def _today_ct() -> _dt.date:
    return _dt.datetime.now(_CT).date()


def _span(days: int) -> int:
    return days * 86_400_000


def backfill(db_path=None, client=None, today=None) -> dict:
    """Fill the store back from ``today`` (default: today in CT), per symbol and
    interval, one window at a time, until two consecutive windows come back empty.

    Returns the rows written per ``"SYMBOL/interval"``. A FAILED fetch (not an
    empty one) stops that key's walk at once, is not read as the end of history,
    and reports the key as ``FAILED`` (-1); rows it wrote before are kept and the
    other keys carry on. Re-running continues safely (a stored bar is replaced)."""
    out = {}
    end_all = _end_ms(today if today is not None else _today_ct())
    for symbol in scalp_config.bar_symbols():
        for interval in INTERVALS:
            key = f"{symbol}/{interval}"
            out[key] = 0
            end, empty = end_all, 0
            while empty < EMPTY_WINDOWS_TO_STOP:
                start = end - _span(WINDOW_DAYS[interval])
                rows = fetch_range(symbol, interval, start, end, client=client)
                if rows is None:
                    out[key] = FAILED
                    break
                if rows:
                    empty = 0
                    out[key] += scalp_store.upsert_bars(rows, db_path=db_path)
                else:
                    empty += 1
                end = start
    return out


def _fallback_start_ms(today: _dt.date) -> int:
    day = today
    for _ in range(TOP_UP_FALLBACK_TRADING_DAYS):
        day = _mc.prev_trading_day(day)
    return _ms(_day_start(day))


def top_up(db_path=None, client=None, today=None) -> dict:
    """Fetch what the store lacks, per symbol and interval: from its latest bar
    (that bar is fetched again and replaced, it may have been a partial) to now,
    or from three trading days back when it holds none. Returns the rows written
    per ``"SYMBOL/interval"``.

    A FAILED window stops that key's walk AT the failed window and reports the key
    as ``FAILED`` (-1): nothing after the hole is written, so ``latest_ts`` cannot
    move past it and tomorrow's top-up starts from the same place. Other keys
    carry on."""
    out = {}
    day = today if today is not None else _today_ct()
    end_all = _end_ms(today)
    for symbol in scalp_config.bar_symbols():
        for interval in INTERVALS:
            key = f"{symbol}/{interval}"
            out[key] = 0
            latest = scalp_store.latest_ts(symbol, interval, db_path=db_path)
            start = latest * 1000 if latest is not None else _fallback_start_ms(day)
            step = _span(WINDOW_DAYS[interval])
            while start < end_all:
                stop = min(start + step, end_all)
                rows = fetch_range(symbol, interval, start, stop, client=client)
                if rows is None:
                    out[key] = FAILED
                    break
                out[key] += scalp_store.upsert_bars(rows, db_path=db_path)
                start = stop
    return out

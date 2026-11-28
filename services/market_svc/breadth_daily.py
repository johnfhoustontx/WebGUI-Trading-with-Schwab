"""The day's row of the breadth history (``shared/breadth_history.py``).

Each session is written twice, because Schwab's daily history runs a session
behind (at 22:00 CT on 2026-11-27 its last bar was still 2026-11-25):

1. ``settle_min`` after the close (the early close on a half day): the closing
   quotes, plus the settled new highs / lows from ``cache:market:new_highs``.
2. From ``correct_at`` the next morning: Schwab's own daily bar for that day,
   which replaces every closing-quote value. A value the bar moved by more than
   ``tolerance_pct`` is counted as a degrade, so a quote that was not the close
   is seen.

Facts the rules below answer, measured on prod on 2026-11-27:

- A daily candle is stamped 00:00 Central of its session day.
- ``$ADVN``, ``$DECN``, ``$TRIN``, ``$UVOL`` and ``$DVOL`` carry weekend-dated
  junk bars (Friday's value repeated on Saturday and Sunday: 18 of 5,021 in
  ``$ADVN``); ``$SPX`` has none. So a session is a day ``$SPX`` has a bar.
- ``$TRIN`` has 128 bars with a close of 0. A value at or below 0 is absent.
- The raw scale of ``$UVOL`` / ``$DVOL`` wanders from day to day, but their
  SHARE agreed with TradingView on every day checked. Only the share is kept.
- The live ``$UVOL`` quote read 92233720368.54776 (the 64-bit ceiling / 1e8) on a
  strong up day. Volumes at ``CLIP`` or above are refused, and the close row
  takes its up share from ``$TRIN`` instead: up/down volume = (adv/dec) / TRIN.
- After the close an ETF's ``lastPrice`` is an after-hours print, so SPY and RSP
  wait for the morning bar. For an index ``lastPrice`` is the session's close
  (``closePrice`` is the PRIOR session's).

Nothing here publishes to Redis. Design:
docs/plans/2026-11-27-market-cycle-design.md.
"""
import datetime as _dt
from zoneinfo import ZoneInfo

from services import _degrade
from services.market_svc import compute
from shared import breadth_config
from shared import breadth_history
from shared import market_calendar as mc
from shared.numeric import finite

_CT = ZoneInfo("America/Chicago")
NEW_HIGHS_VIEW = "cache:market:new_highs"
_AREA = "market.breadth"

HISTORY_SYMBOLS = ("$SPX", "$ADVN", "$DECN", "$TRIN", "$UVOL", "$DVOL",
                   "$VIX", "$VIX3M", "SPY", "RSP")
CLOSE_SYMBOLS = ("$SPX", "$ADVN", "$DECN", "$TRIN", "$VIX", "$VIX3M")
# At or above this a volume is the feed's ceiling, not a reading.
CLIP = 9.0e10

_HISTORY, _CLOSE, _OWN = "schwab_history", "schwab_close_quote", "own_count"
_INDEX = "$SPX"
# One symbol's closing value -> one series.
_CLOSES = {"$ADVN": "nyse_adv", "$DECN": "nyse_dec", "$TRIN": "nyse_trin",
           "$VIX": "vix_close", "$VIX3M": "vix3m_close"}
_NYSE = ("nyse_adv", "nyse_dec", "nyse_trin", "nyse_up_share")
# cache:market:new_highs window keys, in the series' own words.
_WINDOWS = ("52w", "13w", "4w")


def _positive(value):
    f = finite(value)
    return f if f is not None and f > 0 else None


def _volume(value):
    f = _positive(value)
    return f if f is not None and f < CLIP else None


def session_day(candle: dict) -> str | None:
    """The candle's session day in Central time, ``"YYYY-MM-DD"``."""
    ms = finite(candle.get("datetime")) if isinstance(candle, dict) else None
    if ms is None:
        return None
    try:
        return _dt.datetime.fromtimestamp(ms / 1000, _CT).date().isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def up_share(up, down) -> float | None:
    """The share of volume in rising stocks, ``up / (up + down)``."""
    up, down = _volume(up), _volume(down)
    if up is None or down is None:
        return None
    return up / (up + down)


def up_share_from_trin(adv, dec, trin) -> float | None:
    """The same share from the closing counts and the Arms index: TRIN is
    (adv / dec) / (up volume / down volume), so the volume ratio is
    r = (adv / dec) / TRIN and the share r / (1 + r)."""
    adv, dec, trin = _positive(adv), _positive(dec), _positive(trin)
    if adv is None or dec is None or trin is None:
        return None
    r = (adv / dec) / trin
    return r / (1 + r)


def _by_day(candles) -> dict[str, dict]:
    out = {}
    for c in candles if isinstance(candles, list) else ():
        day = session_day(c)
        if day is not None:
            out[day] = c
    return out


def history_rows(candles: dict[str, list[dict]]) -> list[tuple]:
    """Rows from Schwab's daily bars, ``{symbol: [candle]}``, for every day
    ``$SPX`` has a bar. Empty when ``$SPX`` has none."""
    spx = _by_day(candles.get(_INDEX))
    if not spx:
        return []
    days = sorted(spx)
    bars = {sym: _by_day(candles.get(sym)) for sym in HISTORY_SYMBOLS}
    rows = []

    def add(series, day, value):
        if value is not None:
            rows.append((series, day, value, _HISTORY))

    for day in days:
        index = spx[day]
        add("spx_close", day, _positive(index.get("close")))
        add("spx_high", day, _positive(index.get("high")))
        add("spx_low", day, _positive(index.get("low")))
        for sym, series in _CLOSES.items():
            c = bars[sym].get(day)
            add(series, day, _positive(c.get("close")) if c else None)
        up, down = bars["$UVOL"].get(day), bars["$DVOL"].get(day)
        add("nyse_up_share", day,
            up_share(up.get("close"), down.get("close")) if up and down else None)
        spy, rsp = bars["SPY"].get(day), bars["RSP"].get(day)
        if spy:
            add("spy_close", day, _positive(spy.get("close")))
            add("spy_volume", day, _volume(spy.get("volume")))
        if rsp:
            add("rsp_close", day, _positive(rsp.get("close")))
    return rows


def _quote(quotes, sym, field="lastPrice"):
    entry = quotes.get(sym) if isinstance(quotes, dict) else None
    q = entry.get("quote") if isinstance(entry, dict) else None
    return _positive(q.get(field)) if isinstance(q, dict) else None


def _own_counts(day, new_highs) -> list[tuple]:
    if not isinstance(new_highs, dict) or new_highs.get("enabled") is not True \
            or new_highs.get("session") != day:
        return []
    counts = new_highs.get("counts")
    counts = counts if isinstance(counts, dict) else {}
    rows = []
    universe = finite(new_highs.get("universe"))
    if universe is not None and universe > 0:
        rows.append(("us_universe", day, universe, _OWN))
    for window in _WINDOWS:
        sides = counts.get(window)
        for side in ("highs", "lows"):
            n = finite(sides.get(side)) if isinstance(sides, dict) else None
            if n is not None and n >= 0:        # zero is a real count
                rows.append((f"us_{window}_{side}", day, n, _OWN))
    return rows


def close_rows(day: str, quotes: dict, new_highs: dict | None, min_issues: int) -> list[tuple]:
    """Rows for ``day`` from the closing ``/quotes`` answer and the day's new
    highs / lows view. SPY and RSP are left for the morning bar."""
    rows = []

    def add(series, value):
        if value is not None:
            rows.append((series, day, value, _CLOSE))

    add("spx_close", _quote(quotes, _INDEX))
    add("spx_high", _quote(quotes, _INDEX, "highPrice"))
    add("spx_low", _quote(quotes, _INDEX, "lowPrice"))
    adv, dec = _quote(quotes, "$ADVN"), _quote(quotes, "$DECN")
    issues_ok = adv is not None and dec is not None and adv + dec >= min_issues
    for sym, series in _CLOSES.items():
        if series in _NYSE and not issues_ok:
            continue
        add(series, _quote(quotes, sym))
    if issues_ok:
        add("nyse_up_share", up_share_from_trin(adv, dec, _quote(quotes, "$TRIN")))
    return rows + _own_counts(day, new_highs)


def differences(day: str, history: list[tuple], stored: dict, tolerance_pct: float) -> list[dict]:
    """The closing-quote values that ``day``'s history bar moved by more than
    ``tolerance_pct`` percent, in the order of ``history``."""
    out = []
    for series, row_day, now, _src in history:
        was = stored.get(series)
        if row_day != day or not was or was[1] != _CLOSE or not was[0]:
            continue
        pct = abs(now - was[0]) / abs(was[0]) * 100
        if pct > tolerance_pct:
            out.append({"series": series, "was": was[0], "now": now, "pct": pct})
    return out


# ── the service: when, and the two writes ───────────────────────────────────
def due_close(now, stored: bool, state: dict, cfg: dict) -> bool:
    """Whether the day's close row is due at ``now`` (Central): switched on, a
    trading day, ``settle_min`` past the close (the early close on a half day),
    not yet stored, and past any back-off."""
    day = now.date()
    if not cfg["enabled"] or stored or not mc.is_trading_day(day):
        return False
    if now < mc.session_close_on(day) + _dt.timedelta(minutes=cfg["settle_min"]):
        return False
    return now.timestamp() >= state.get("close_next_at", 0)


def due_correction(now, state: dict, cfg: dict) -> str | None:
    """The previous session's day when its correction is due at ``now``: a
    trading day, inside ``correct_at`` .. ``correct_until``, not yet done, and
    past any back-off."""
    day = now.date()
    if not cfg["enabled"] or not mc.is_trading_day(day):
        return None
    start, until = breadth_config.clock(cfg["correct_at"]), breadth_config.clock(cfg["correct_until"])
    if not start <= now.time() < until:
        return None
    prev = mc.prev_trading_day(day).isoformat()
    if state.get("corrected") == prev or now.timestamp() < state.get("correct_next_at", 0):
        return None
    return prev


def _new_highs(bus):
    env = bus.cache_get(NEW_HIGHS_VIEW)
    return env.payload if env is not None else None


def _write_close(bus, state, now, cfg, conn) -> bool:
    day = now.date().isoformat()
    quotes = compute.fetch_raw_quotes(list(CLOSE_SYMBOLS))
    if not quotes:
        state["close_next_at"] = now.timestamp() + cfg["retry_min"] * 60
        _degrade.degraded(_AREA, detail="close quotes", exc_info=False)
        return False
    rows = close_rows(day, quotes, _new_highs(bus), cfg["min_issues"])
    if not rows:
        state["close_next_at"] = now.timestamp() + cfg["retry_min"] * 60
        _degrade.degraded(_AREA, detail="close quotes held no usable value", exc_info=False)
        return False
    return breadth_history.upsert(conn, rows, now.date())["written"] > 0


def _write_correction(state, now, day, cfg, conn) -> bool:
    candles = {sym: compute.fetch_daily(sym, period_type="month", period=1)
               for sym in HISTORY_SYMBOLS}
    rows = [r for r in history_rows(candles) if r[1] == day]
    if not rows:
        # Schwab has not posted the bar yet (or the index fetch failed).
        state["correct_next_at"] = now.timestamp() + cfg["retry_min"] * 60
        return False
    moved = differences(day, rows, breadth_history.day_row(conn, day), cfg["tolerance_pct"])
    got = breadth_history.upsert(conn, rows, now.date())
    state["corrected"] = day
    if moved:
        detail = ", ".join(f"{m['series']} {m['was']:g} -> {m['now']:g}" for m in moved)
        _degrade.degraded(_AREA, detail=f"{day} corrected past tolerance: {detail}",
                          exc_info=False)
    return got["written"] > 0


def refresh(bus, state: dict, now=None, db_path=None) -> bool:
    """Write the close row or the morning correction when one is due; True when
    it wrote. Never raises. ``state`` is the caller's dict and survives between
    calls; the store itself says whether today is already written, so a
    restart does not write it twice."""
    try:
        now = now or _dt.datetime.now(_CT)
        cfg = breadth_config.load()
        if not cfg["enabled"]:
            return False
        conn = breadth_history.connect(db_path)
        try:
            prev = due_correction(now, state, cfg)
            if prev is not None:
                return _write_correction(state, now, prev, cfg, conn)
            today = breadth_history.day_row(conn, now.date().isoformat())
            stored = "nyse_adv" in today or "spx_close" in today
            if due_close(now, stored, state, cfg):
                return _write_close(bus, state, now, cfg, conn)
            return False
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 — the breadth row can never stop the poll.
        _degrade.degraded(_AREA)
        return False

"""Traded volume and what it says: ``cache:market:volume``. PURE, no I/O.

Plain data in, a plain dict out: the bars and quotes come through
``compute.fetch_bars`` and ``compute.fetch_raw_quotes``, and the scheduler
publishes the result. The Desk's Market read takes its Volume row from this
view, and the daily market reports print its ``statements`` word for word.

Three figures, each on one basis:
- **Pace.** A symbol's volume so far today against the median of its own recent
  sessions BY THE SAME TIME OF DAY. Comparing a part of today with a whole day
  would read every morning as light.
- **Up share.** Of that volume, the share in five-minute bars whose price rose.
- **Sector share.** Of the dollars traded across the sector funds, the share in
  sectors that are rising.

⚠ A five-minute bar's volume is NOT on the basis of a quote's ``totalVolume``
or a daily bar: measured 2026-11-01, SPY's regular-session bars summed to about
0.63 of its daily bar. So pace compares bars with bars, and the sector share
compares quotes with quotes.

Absence is not a reading: a missing or non-finite figure is ``None``, and a
statement says it is unavailable, never that volume was zero or light. Before
the open nothing here is read as a signal: the bars are regular-session only,
and pre-market trading is thin, so the view reports the LAST session and says so.

What volume implies is measured, and narrower than folklore: over 149 sessions
heavy volume went with a wider range for the rest of the session and did not
predict direction. The wording keeps to that.
Design: docs/plans/2026-11-01-volume-in-read-and-reports-design.md.
"""
import datetime as _dt
import statistics

from shared import market_calendar as _mc
from shared.numeric import finite

from services.market_svc import classify

BAR_MIN = 5                 # the width of the bars this reads, in minutes
# Schwab's largest ``period`` for a minute series is 10 days, today included.
BAR_PERIOD_DAYS = 10

SESSION, AFTER_CLOSE, BEFORE_OPEN, CLOSED = "session", "after_close", "before_open", "closed"
HEAVY, USUAL, LIGHT = "heavy", "usual", "light"
RISING, FALLING, MIXED = "rising", "falling", "mixed"

# How a statement names a fund. A symbol not listed is named by its ticker.
_NAMES = {"SPY": "the S&P 500 fund (SPY)", "QQQ": "the Nasdaq-100 fund (QQQ)",
          "IWM": "the small-company fund (IWM)", "DIA": "the Dow fund (DIA)"}
_WORDS = {HEAVY: "heavy", USUAL: "about usual", LIGHT: "light"}


def _ct(now):
    return now.replace(tzinfo=_mc.CT) if now.tzinfo is None else now.astimezone(_mc.CT)


# ── what is due ──────────────────────────────────────────────────────────────
def phase_at(at) -> str:
    """Which part of the day ``at`` is in, for the view's own session rules."""
    at, day = _ct(at), _ct(at).date()
    if not _mc.is_trading_day(day):
        return CLOSED
    if at < _mc.regular_open_on(day):
        return BEFORE_OPEN
    return SESSION if at < _mc.regular_close_on(day) else AFTER_CLOSE


def due(at, last_key, cfg):
    """``(key, through)`` when a fetch is due, else None. ``through`` is the end
    of the last whole bar in the session, or None outside it.

    In the session a fetch runs once per ``refresh_min``, ``settle_sec`` after
    the mark so the bar has closed; ``settle_sec`` of at least 60 also keeps it
    out of the minute after the hour and half hour, where Schwab refuses calls.
    After the close, once, for the whole session. Before the open and on a day
    with no session, once for the day."""
    at = _ct(at)
    day = at.date()
    phase = phase_at(at)
    if phase in (BEFORE_OPEN, CLOSED):
        if at.minute % 30 == 0:
            return None
        key = f"{day} {phase}"
        return None if key == last_key else (key, None)
    step = cfg["refresh_min"]
    close = _mc.regular_close_on(day)
    # Bars count from the open, and the marks from the hour: a session opening on
    # a clock multiple of the step has the two line up, and the bar filter below
    # only ever counts a bar that has ended.
    mark = at.replace(minute=at.minute // step * step, second=0, microsecond=0)
    mark = min(mark, close)
    if (at - mark).total_seconds() < cfg["settle_sec"]:
        return None
    if mark <= _mc.regular_open_on(day):
        return None
    key = f"{day} {mark:%H:%M}"
    if last_key is not None and last_key.startswith(str(day)) and key <= last_key:
        return None
    return key, mark


# ── the figures ──────────────────────────────────────────────────────────────
def session_bars(candles) -> dict:
    """``{session date: [(start, open, close, volume), ...]}`` of the whole
    regular-session bars in a Schwab candle list. A candle with no usable time or
    volume is left out, not counted as zero. Total over a malformed list."""
    out = {}
    for c in candles if isinstance(candles, list) else ():
        if not isinstance(c, dict):
            continue
        ms, vol = finite(c.get("datetime")), finite(c.get("volume"))
        if ms is None or vol is None or vol < 0:
            continue
        start = _dt.datetime.fromtimestamp(ms / 1000.0, _mc.CT)
        day = start.date()
        if not _mc.is_trading_day(day) or not (
                _mc.regular_open_on(day) <= start < _mc.regular_close_on(day)):
            continue
        out.setdefault(day, []).append((start, finite(c.get("open")),
                                        finite(c.get("close")), vol))
    return {d: sorted(rows, key=lambda r: r[0]) for d, rows in out.items()}


def _upto(rows, clock):
    """The bars of one session that had ENDED by the clock time ``clock``."""
    width = _dt.timedelta(minutes=BAR_MIN)
    return [r for r in rows if (r[0] + width).time() <= clock]


def word_for(pace, cfg):
    if pace is None:
        return None
    return HEAVY if pace >= cfg["heavy_ratio"] else LIGHT if pace <= cfg["light_ratio"] else USUAL


def symbol_reading(symbol, candles, day, clock, cfg) -> dict:
    """One symbol's pace and up share through the clock time ``clock`` on ``day``."""
    out = {"symbol": symbol, "pace": None, "up_share": None, "word": None,
           "bars": 0, "baseline_sessions": 0}
    sessions = session_bars(candles)
    today = _upto(sessions.get(day, []), clock)
    out["bars"] = len(today)
    if len(today) < cfg["min_bars"]:
        return out
    up = sum(r[3] for r in today if None not in (r[1], r[2]) and r[2] > r[1])
    down = sum(r[3] for r in today if None not in (r[1], r[2]) and r[2] < r[1])
    if up + down > 0:
        out["up_share"] = up / (up + down)

    before = sorted(d for d in sessions if d < day)
    totals = []
    for d in reversed(before):
        # An early close has no bars this late in the day, so it says nothing
        # about what is usual by now.
        if _mc.regular_close_on(d).time() < clock:
            continue
        rows = _upto(sessions[d], clock)
        total = sum(r[3] for r in rows)
        if rows and total > 0:
            totals.append(total)
        if len(totals) == cfg["baseline_sessions"]:
            break
    out["baseline_sessions"] = len(totals)
    volume = sum(r[3] for r in today)
    if len(totals) >= cfg["min_sessions"] and volume > 0:
        out["pace"] = volume / statistics.median(totals)
        out["word"] = word_for(out["pace"], cfg)
    return out


def symbol_side(reading, cfg):
    """Which way a symbol's volume leans: ``rising``, ``falling``, ``mixed``, or
    None when its up share is absent."""
    share = finite(reading.get("up_share"))
    if share is None:
        return None
    return (RISING if share >= cfg["up_share_strong"] else
            FALLING if share <= cfg["up_share_weak"] else MIXED)


def sector_split(raw, cfg):
    """``{up_share, funds}`` of the dollars traded across the sector funds, or
    None with fewer than ``min_sectors`` usable. A flat sector counts on neither
    side, and a fund with no volume is left out."""
    rising = falling = 0.0
    funds = 0
    for sym in cfg["sector_symbols"]:
        q = raw.get(sym) if isinstance(raw, dict) else None
        leg = classify.normalize_quote(q) if q else None
        if leg is None:
            continue
        inner = q.get("quote", q) if isinstance(q, dict) else {}
        volume = finite(inner.get("totalVolume") if isinstance(inner, dict) else None)
        if volume is None or volume <= 0 or leg[0] <= 0:
            continue
        funds += 1
        dollars = leg[0] * volume
        if leg[2] > 0:
            rising += dollars
        elif leg[2] < 0:
            falling += dollars
    if funds < cfg["min_sectors"] or rising + falling <= 0:
        return None
    return {"up_share": rising / (rising + falling), "funds": funds}


def _agree(values):
    """The one value they all hold, else None. Total over an empty list."""
    distinct = set(values)
    return distinct.pop() if len(distinct) == 1 else None


def range_hint(readings):
    """``wider`` or ``narrower`` when every symbol reads heavy or every one reads
    light, else None. The one thing heavy volume was measured to imply."""
    return {HEAVY: "wider", LIGHT: "narrower"}.get(_agree(r.get("word") for r in readings))


# ── the words ────────────────────────────────────────────────────────────────
def _clock_words(at):
    hour = at.hour % 12 or 12
    return f"{hour}:{at.minute:02d} {'a.m.' if at.hour < 12 else 'p.m.'}"


def _day_words(day):
    return f"{day:%A}, {day:%B} {day.day}"


def _share(v):
    return f"{v * 100:.0f}%"


def _symbol_statements(r, lead, *, whole_session) -> list:
    name = _NAMES.get(r["symbol"], r["symbol"])
    out = []
    pace = finite(r.get("pace"))
    if pace is None:
        out.append(f"{lead}, the volume of {name} is not available to compare, "
                   "so it is not read.")
    else:
        usual = ("its median full-session volume" if whole_session
                 else "its median volume by this time of day")
        out.append(f"{lead}, {name} traded {pace:.2f} times {usual} over the last "
                   f"{r['baseline_sessions']} sessions, which is {_WORDS[r['word']]}.")
    share = finite(r.get("up_share"))
    if share is not None:
        out.append(f"{lead}, {_share(share)} of the volume in {name} came in "
                   "five-minute periods when its price rose.")
    return out


def _impact_statements(view, cfg) -> list:
    """What the figures add up to. Written here, from fixed sentences, so the
    reports cannot turn a reading into a claim it was not measured to support."""
    readings = view["symbols"]
    if not readings or any(r.get("pace") is None for r in readings):
        return []
    word = _agree(r["word"] for r in readings)
    side = _agree(symbol_side(r, cfg) for r in readings)
    finished = view["phase"] != SESSION
    out = []
    # Of the bars, not of the day: a session can trade most heavily on its way down
    # and still close up on the day, as 2026-10-30 did, so no sentence here says "the
    # move" was up or down.
    if word == HEAVY and side in (RISING, FALLING):
        way = "up" if side == RISING else "down"
        tense = "was" if finished else "has been"
        out.append(f"Heavy volume came mostly in {side} five-minute periods, so the "
                   f"heavier trading {tense} on the way {way}.")
    elif word == HEAVY:
        out.append("Heavy volume came without leaning clearly toward rising or falling "
                   "five-minute periods.")
    elif word == LIGHT:
        out.append("Volume was light, so any move had light trading behind it."
                   if finished else
                   "Volume is light, so any move so far has light trading behind it.")
    if not finished:
        hint = range_hint(readings)
        if hint is not None:
            out.append(f"{_WORDS[word].capitalize()} volume has gone with {hint} moves "
                       "over the rest of the session, and it has not said which way.")
    return out


def statements(view, cfg) -> list:
    """The view as plain sentences, for the reports to print word for word.

    Kept free of semicolons, apostrophes and quotes, which a model normalises,
    and of shorthand, for the reports' plain-English rule."""
    phase = view["phase"]
    out = []
    if phase == BEFORE_OPEN:
        out.append("The regular session has not opened, so there is no volume reading "
                   "for today. Pre-market trading is thin and is not read as a signal.")
    elif phase == CLOSED:
        out.append("The market is closed today, so there is no volume reading for today.")
    day = _dt.date.fromisoformat(view["session_date"]) if view.get("session_date") else None
    if day is None:
        out.append("Volume figures are not available.")
        return out
    if phase == SESSION:
        hh, mm = (int(x) for x in view["through"].split(":"))
        lead = f"Through {_clock_words(_dt.time(hh, mm))}"
    elif phase == AFTER_CLOSE:
        lead = "Over the full regular session"
    else:
        lead = f"In the session of {_day_words(day)}"
    whole = phase != SESSION
    for r in view["symbols"]:
        out.extend(_symbol_statements(r, lead, whole_session=whole))
    sectors = view.get("sectors")
    if isinstance(sectors, dict):
        out.append(f"Across {sectors['funds']} sector funds, {_share(sectors['up_share'])} "
                   "of the dollars traded were in sectors that were rising.")
    out.extend(_impact_statements(view, cfg))
    return out


# ── a reading ────────────────────────────────────────────────────────────────
def build(candles_by_symbol, raw_sectors, at, cfg, *, ts, through=None) -> dict:
    """One view. ``candles_by_symbol`` is ``{symbol: candles}`` for the configured
    symbols; a symbol whose fetch failed holds None and reads as absent.

    In the session, ``through`` is the end of the last whole bar. After the close
    it is the close, and before the open or on a closed day this reports the
    newest earlier session. The sector split is read only from a session or a
    close of TODAY: a quote before the open carries thin pre-market prints."""
    at = _ct(at)
    today = at.date()
    phase = phase_at(at)
    if phase in (BEFORE_OPEN, CLOSED):
        held = set()
        for candles in candles_by_symbol.values():
            held.update(d for d in session_bars(candles) if d < today)
        day = max(held) if held else None
        clock = _mc.regular_close_on(day).time() if day else None
    else:
        day = today
        clock = (through or at).time()
    readings = ([symbol_reading(s, c, day, clock, cfg)
                 for s, c in candles_by_symbol.items()] if day else [])
    sectors = sector_split(raw_sectors, cfg) if phase in (SESSION, AFTER_CLOSE) else None
    view = {
        "enabled": True, "date": today.isoformat(), "ts": ts, "phase": phase,
        "session_date": day.isoformat() if day else None,
        "through": clock.strftime("%H:%M") if clock else None,
        "symbols": readings, "sectors": sectors,
        "range_hint": range_hint(readings) if phase == SESSION else None,
    }
    view["statements"] = statements(view, cfg)
    return view

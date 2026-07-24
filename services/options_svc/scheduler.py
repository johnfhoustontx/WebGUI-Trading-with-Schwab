"""Server-side options auto-scan scheduler (Task 2.4).

The Tier-2 analog of the page's former ``_autoscan_loop`` in
``webgui/pages/options/scanner.py``. The schedule helpers (constants +
``_is_trading_day``/``_is_market_hours``/``_slot_key``/``autoscan_due``/
``_market_now``) are ported VERBATIM from that page — replicated here (not
imported from the legacy ``scanner`` CLI) to avoid dragging its heavy import
chain — and the cross-app module-name collisions — in.

Cadence: check the current slot every 30 s and run at most one rescan per
trading-day 15-min slot within the 08:00–15:15 CT window. Passed to the
scaffold as ``make_app(scheduler=loop)``; the scaffold runs it once and the
loop+sleep here own the cadence. The BLOCKING rescan runs in the default
executor so the event loop stays responsive.
"""
import asyncio
import logging
from datetime import date as _date, time as _time
from zoneinfo import ZoneInfo

from services.options_svc import compute, handlers

log = logging.getLogger(__name__)

# ── Schedule logic (ported verbatim from scanner.py) ───────────────────────
_CT = ZoneInfo("America/Chicago")
_SCAN_START = (8, 0)    # 08:00 CT
_SCAN_END = (15, 15)    # 15:15 CT
# US market holidays 2026–2027 (keep in sync with the other service schedulers,
# options-scanner/scanner.py Config.HOLIDAYS, and webgui/alerts.py). Includes
# Juneteenth; observed dates per NYSE (Sat→prior Fri, Sun→following Mon). Update yearly.
_HOLIDAYS = {
    # 2026
    _date(2026, 1, 1), _date(2026, 1, 19), _date(2026, 2, 16), _date(2026, 4, 3),
    _date(2026, 5, 25), _date(2026, 6, 19), _date(2026, 7, 3), _date(2026, 9, 7),
    _date(2026, 11, 26), _date(2026, 12, 25),
    # 2027
    _date(2027, 1, 1), _date(2027, 1, 18), _date(2027, 2, 15), _date(2027, 3, 26),
    _date(2027, 5, 31), _date(2027, 6, 18), _date(2027, 7, 5), _date(2027, 9, 6),
    _date(2027, 11, 25), _date(2027, 12, 24),
}


def _is_trading_day(now):
    return now.weekday() < 5 and now.date() not in _HOLIDAYS


def _is_market_hours(now):
    t = now.time()
    return _time(*_SCAN_START) <= t <= _time(*_SCAN_END)


def _slot_key(now):
    return (now.date().isoformat(), now.hour, now.minute // 15)


def autoscan_due(now, last_slot):
    """(should_scan, slot_key): True at most once per 15-min slot, only on a
    trading day within the 08:00–15:15 CT window."""
    if not (_is_trading_day(now) and _is_market_hours(now)):
        return (False, last_slot)
    slot = _slot_key(now)
    return (slot != last_slot, slot)


def _market_now():
    import datetime as _dt
    return _dt.datetime.now(_CT)


# ── GEX history-collection cadence (mirrors options-scanner/gex_collector.py) ─
# The options service is now the Tier-2 owner of intraday GEX history collection
# (the standalone gex_collector.py window was fragile — when it died, the Gamma
# heatmap froze at the first snapshots). These constants mirror gex_collector's
# START/STOP/POLL so the cadence + window stay aligned; defined locally (not
# imported) to keep this module's import light, matching how the scan-window
# constants above are ported verbatim rather than imported.
_GEX_START = (8, 0)      # gex_collector.START_HOUR/START_MIN
_GEX_STOP = (15, 20)     # gex_collector.STOP_HOUR/STOP_MIN
_GEX_INTERVAL_MIN = 1    # gex_collector.POLL_INTERVAL_MIN


def _in_gex_window(now):
    return _GEX_START <= (now.hour, now.minute) < _GEX_STOP


# ── Gamma display persistence (the last session shows until the next one starts) ─
# The Gamma charts show the most-recent-available session 24/7 — the by-strike
# charts come from the live chain and the heatmap from the ACTIVE SESSION DATE, so
# both stay visible PRE- and POST-market (there is no overnight blanking):
#   • weekday after close → today's data persists through the night;
#   • Fri/holiday-eve → persists through the weekend/holiday;
#   • premarket (before the 08:00 CT collection start) → the PRIOR session shows,
#     then today's data takes over once its first snapshots land.
def _prev_trading_day(d):
    """Most recent trading day strictly before date ``d`` (skips weekends/holidays)."""
    import datetime as _dtmod
    d = d - _dtmod.timedelta(days=1)
    while d.weekday() >= 5 or d in _HOLIDAYS:
        d -= _dtmod.timedelta(days=1)
    return d


def active_session_date(now=None):
    """The trading day whose Gamma data should be displayed now: today once
    collection has started (>= 08:00 CT on a trading day), else the most recent
    prior trading day.

    Drives heatmap persistence so the display shows the last completed session
    PRE- and POST-market: Friday's data stays shown all weekend, and on a trading
    day BEFORE 08:00 CT (premarket) the prior session shows until today's snapshots
    begin — at which point today's data takes over."""
    now = now or _market_now()
    d = now.date()
    is_td = d.weekday() < 5 and d not in _HOLIDAYS
    if is_td and (now.hour, now.minute) >= _GEX_START:
        return d
    return _prev_trading_day(d)


def _gex_slot_key(now):
    return (now.date().isoformat(), now.hour, now.minute // _GEX_INTERVAL_MIN)


def gex_due(now, last_slot):
    """(should_collect, slot): True at most once per 1-min slot, only on a
    trading day within the 08:00–15:20 CT GEX-collection window (starts 30 min
    pre-open). Mirrors ``autoscan_due`` on the gex_collector cadence/window."""
    if not (_is_trading_day(now) and _in_gex_window(now)):
        return (False, last_slot)
    slot = _gex_slot_key(now)
    return (slot != last_slot, slot)


# ── Paper auto-manage cadence ───────────────────────────────────────────────
# The Paper Portfolio page used to require a manual "Run manage cycle" click to
# reprice open paper positions and auto-close target/stop hits. The always-on
# service now runs that cycle automatically on a fixed cadence within market
# hours so the paper account stays current (and hits close) with no page open —
# mirrors gex_due/autoscan_due on its own interval/window (the trading window).
_MANAGE_INTERVAL_MIN = 5    # auto-manage every 5 min within market hours


def _manage_slot_key(now):
    return (now.date().isoformat(), now.hour, now.minute // _MANAGE_INTERVAL_MIN)


def manage_due(now, last_slot):
    """(should_manage, slot): True at most once per 5-min slot, only on a
    trading day within the 08:00–15:15 CT window. Drives the paper auto-manage
    cycle so open paper positions are repriced + auto-closed unattended."""
    if not (_is_trading_day(now) and _is_market_hours(now)):
        return (False, last_slot)
    slot = _manage_slot_key(now)
    return (slot != last_slot, slot)


# ── Manual Paper Portfolio hourly entry+manage cadence ──────────────────────
# The MANUAL Paper Portfolio (the user's own paper account) runs its entry cycle
# (open new paper trades from the current captured signals) + manage cycle
# (reprice open positions + auto-close target/stop hits) once at the TOP OF THE
# HOUR, 09:00–14:00 CT — the last run at 14:00 (2pm), with NO run at 15:00 (3pm)
# when the regular session closes. Trading days only. This REPLACES the manual
# account's former 5-min manage cadence (see manage_due); the isolated DRIVER
# account stays on the manage_due 5-min slot. Each hour fires ONCE within a grace
# window (mirrors analyze_slot_due) so a missed 30 s tick / mid-window service
# start still fires without backfilling a long-stale hour.
_PAPER_HOURS = (9, 10, 11, 12, 13, 14)   # CT top-of-hour run hours (no 15:00)
_PAPER_GRACE_MIN = 20  # fire within this many minutes of the top of the hour, else skip


def paper_cycle_due(now, ran_slots):
    """The CT hour whose manual Paper Portfolio entry+manage cycle is due now, or None.

    Fires each listed hour (09:00–14:00 CT) ONCE per trading day, when
    ``:00 <= now < :00 + grace`` and that ``(date, hour)`` isn't already in
    ``ran_slots``. The caller records the returned ``(date, hour)`` so it won't
    refire. Mirrors ``analyze_slot_due`` — the grace window tolerates a missed
    tick / mid-window start without backfilling a long-stale hour."""
    if not _is_trading_day(now):
        return None
    import datetime as _dt
    day = now.date().isoformat()
    for h in _PAPER_HOURS:
        if (day, h) in ran_slots:
            continue
        target = now.replace(hour=h, minute=0, second=0, microsecond=0)
        if target <= now < target + _dt.timedelta(minutes=_PAPER_GRACE_MIN):
            return h
    return None


# ── Per-tick refresh gating (header + GEX status) ───────────────────────────
# refresh_header (a proxy quotes call + bridge read) and publish_gex_status (a
# SQLite read + cache write) used to run on EVERY 30 s tick, 24/7 — making proxy
# calls + DB opens + Redis writes all night and all weekend with no browser open.
# Gate them: every tick during market hours (their natural ~30 s cadence), but
# only once per _OFFHOURS_INTERVAL_MIN slot off-hours/weekends/holidays — enough
# to keep the header/status current for a service started off-hours, without the
# round-the-clock churn.
_OFFHOURS_INTERVAL_MIN = 5


def periodic_refresh_due(now, last_slot):
    """(should_refresh, slot) for the per-tick header + GEX-status refreshes.

    Trading day within market hours → always True (refresh every tick), slot
    unchanged. Otherwise → True at most once per _OFFHOURS_INTERVAL_MIN-minute
    slot (so the first off-hours tick still refreshes, then throttles)."""
    if _is_trading_day(now) and _is_market_hours(now):
        return (True, last_slot)
    slot = (now.date().isoformat(), now.hour, now.minute // _OFFHOURS_INTERVAL_MIN)
    return (slot != last_slot, slot)


# ── Scheduled $SPX/SPY/QQQ Gamma Analyze (Claude briefing) cadence ──────────
# The Gamma Analyze button is ALSO auto-run at four fixed points on each trading
# day so the day's index dealer-positioning briefings are generated unattended (in
# addition to ad-hoc button use). Times are in CT (this module's clock); the ET
# reference is in the comment. Each slot fires ONCE per day, within a grace window
# after its target — so a missed 30 s tick or a mid-window service start still fires
# it, but a long-late start does NOT backfill a stale slot.
_ANALYZE_SLOTS = {
    "premarket": (8, 0),    # 09:00 ET — premarket
    "open":      (8, 48),   # 09:48 ET — ~18 min after the 09:30 open
    "midday":    (11, 30),  # 12:30 ET — midday
    "close":     (14, 58),  # 15:58 ET — at the close
}
_ANALYZE_GRACE_MIN = 20  # fire within this many minutes of the target, else skip


def analyze_slot_due(now, ran_slots):
    """Name of the scheduled-analyze slot due now, or None.

    Fires each slot ONCE per trading day, when ``target <= now < target + grace`` and
    that ``(date, slot)`` isn't already in ``ran_slots``. The grace window tolerates a
    missed tick / mid-window service start without backfilling a long-stale slot. The
    caller records the returned ``(date, slot)`` in ``ran_slots`` so it won't refire."""
    if not _is_trading_day(now):
        return None
    import datetime as _dt
    day = now.date().isoformat()
    for name, (h, m) in _ANALYZE_SLOTS.items():
        if (day, name) in ran_slots:
            continue
        target = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if target <= now < target + _dt.timedelta(minutes=_ANALYZE_GRACE_MIN):
            return name
    return None


# ── Scheduled "trades needing action" digest cadence (10/1/3 CT) ────────────
# A thrice-daily push (Telegram/Discord) summarizing open trades that need a
# human decision. Times are CT (this module's clock). Each slot fires ONCE per
# trading day within a grace window (mirrors analyze_slot_due) so a missed 30 s
# tick / mid-window start still fires it without backfilling a long-stale slot.
_ACTION_ALERT_SLOTS = {
    "morning": (10, 0),   # 10:00 CT
    "midday":  (13, 0),   # 13:00 CT
    "close":   (15, 0),   # 15:00 CT — pre-close sweep (market close)
}
_ACTION_ALERT_GRACE_MIN = 20


def action_alert_due(now, ran_slots):
    """Name of the action-alert slot due now, or None.

    Fires each slot ONCE per trading day when ``target <= now < target + grace``
    and that ``(date, slot)`` isn't already in ``ran_slots``. The caller records
    the returned ``(date, slot)`` so it won't refire. Mirrors ``analyze_slot_due``."""
    if not _is_trading_day(now):
        return None
    import datetime as _dt
    day = now.date().isoformat()
    for name, (h, m) in _ACTION_ALERT_SLOTS.items():
        if (day, name) in ran_slots:
            continue
        target = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if target <= now < target + _dt.timedelta(minutes=_ACTION_ALERT_GRACE_MIN):
            return name
    return None


# ── Scheduled end-of-day summary cadence (~15:10 CT) ─────────────────────────
# A once-daily push AFTER the regular-session close (15:00 CT / 4pm ET) + 0-DTE
# settlement, summarizing the day's result per paper book. 15:10 gives the driver's
# 5-min manage cycle time to settle expiries; the wide grace tolerates a late/mid-window
# service start. Fires ONCE per trading day within the grace (mirrors action_alert_due).
_EOD_SUMMARY_SLOTS = {
    "close": (15, 10),   # 15:10 CT — post-close daily result
}
_EOD_SUMMARY_GRACE_MIN = 30


def eod_summary_due(now, ran_slots):
    """Name of the EOD-summary slot due now, or None.

    Fires each slot ONCE per trading day when ``target <= now < target + grace`` and that
    ``(date, slot)`` isn't already in ``ran_slots``. The caller records the returned
    ``(date, slot)`` so it won't refire. Mirrors ``action_alert_due``."""
    if not _is_trading_day(now):
        return None
    import datetime as _dt
    day = now.date().isoformat()
    for name, (h, m) in _EOD_SUMMARY_SLOTS.items():
        if (day, name) in ran_slots:
            continue
        target = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if target <= now < target + _dt.timedelta(minutes=_EOD_SUMMARY_GRACE_MIN):
            return name
    return None


# ── 30-min Market Snapshot cadence (:00 & :30, 08:30–15:00 CT) ───────────────
_MKT_SNAP_START = (8, 30)
_MKT_SNAP_END = (15, 0)          # last slot fires at 15:00
_MKT_SNAP_GRACE_MIN = 10         # < 30 so a slot can't bleed into the next


def _market_snapshot_slots():
    """The (h, m) :00/:30 slot targets within [start, end], inclusive."""
    import datetime as _dt
    out = []
    cur = _dt.time(*_MKT_SNAP_START)
    end = _dt.time(*_MKT_SNAP_END)
    h, m = cur.hour, cur.minute
    while (h, m) <= (end.hour, end.minute):
        out.append((h, m))
        m += 30
        if m >= 60:
            m -= 60
            h += 1
    return out


def market_snapshot_due(now, ran_slots):
    """The "HH:MM" market-snapshot slot due now, or None. Once per slot per trading
    day within a 10-min grace (mirrors ``action_alert_due``). The caller records the
    returned ``(date, "HH:MM")`` in ``ran_slots`` so it won't refire."""
    if not _is_trading_day(now):
        return None
    import datetime as _dt
    day = now.date().isoformat()
    for h, m in _market_snapshot_slots():
        name = f"{h:02d}:{m:02d}"
        if (day, name) in ran_slots:
            continue
        target = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if target <= now < target + _dt.timedelta(minutes=_MKT_SNAP_GRACE_MIN):
            return name
    return None


# ── Scheduler loop ─────────────────────────────────────────────────────────
POLL_INTERVAL_SEC = 30  # check the slot every 30s (mirrors the page's autoscan loop cadence)


def launch_branches(running, branches, create_task):
    """Launch the tick's due (key, coro) branches as BACKGROUND tasks.

    Supersedes the old ``_gather_due`` (A4): gathering ran the branches
    concurrently but made the TICK wait for all of them — so a 30-90s rescan
    stalled the next tick's slot gates, and (measured 2026-07-17) ~37% of 1-min
    GEX-collection slots were silently dropped. Launching keyed tasks returns to
    the sleep immediately; the 1-min gate keeps firing on time.

    Anti-stacking: a branch whose PREVIOUS instance is still running is skipped
    for this slot (its fresh coroutine is closed, a warning logged) — a branch
    can only ever delay ITSELF, never the others. ``running`` maps key → last
    task and is bounded by the fixed branch-key set (~7). Each branch carries
    its own try/except, so a task never raises. Returns the launched keys."""
    launched = []
    for key, coro in branches:
        prev = running.get(key)
        if prev is not None and not prev.done():
            log.warning("scheduler branch %r still running; skipping this slot", key)
            coro.close()
            continue
        running[key] = create_task(coro)
        launched.append(key)
    return launched


async def loop(bus):
    """Server-side 15-min auto-scan + gated header/GEX-status refresh.

    Each 30 s tick: refresh the compact header view (quotes + VIX regime +
    sentiment dot) + GEX-status view — every tick during market hours, throttled
    to every _OFFHOURS_INTERVAL_MIN off-hours (see periodic_refresh_due) so the
    service stops the round-the-clock proxy/SQLite/Redis churn — then, on each
    trading-day 15-min slot within 08:00-15:15 CT, run one rescan (plus 2-min GEX
    collection; the isolated DRIVER paper account auto-manages on its own 5-min
    slot, and the MANUAL Paper Portfolio runs entry+manage hourly at the top of
    the hour 09:00–14:00 CT — see paper_cycle_due — with no 15:00 run). Mirrors
    the page's former _autoscan_loop. The BLOCKING calls run in an executor so the
    event loop stays responsive. Each is independently guarded so one failure can't
    kill the loop or skip the others — in particular the driver manage tick and the
    hourly manual paper cycle are guarded separately so one can't skip the other."""
    loop_ = asyncio.get_event_loop()
    last_slot = None
    # R6: one-shot startup self-heal — reconcile buying_power_reserved against open
    # positions for BOTH the manual + isolated driver accounts, correcting any BP
    # orphaned by a crash between reserve_buying_power and insert_position in a
    # prior run (a non-atomic open sequence). Idempotent/defensive; guarded so a
    # cold DB never stops the loop from starting.
    try:
        drift = await loop_.run_in_executor(None, compute.reconcile_paper_buying_power)
        if drift and (abs(drift.get("manual", 0.0)) >= 0.01
                      or abs(drift.get("driver", 0.0)) >= 0.01):
            log.warning("startup BP reconcile corrected drift: %s", drift)
    except Exception:
        log.exception("startup buying-power reconcile degraded")
    last_gex_slot = None  # 2-min GEX history-collection slot (see gex_due)
    last_manage_slot = None  # 5-min DRIVER paper auto-manage slot (see manage_due)
    paper_ran = set()  # (date, hour) of fired hourly manual paper cycles (see paper_cycle_due)
    last_periodic_slot = None  # header + gex_status throttle slot (see periodic_refresh_due)
    analyze_ran = set()  # (date, slot) of fired scheduled Gamma Analyze runs (see analyze_slot_due)
    action_alert_ran = set()  # (date, slot) of fired action-alert pushes (see action_alert_due)
    eod_summary_ran = set()  # (date, slot) of fired EOD-summary pushes (see eod_summary_due)
    # One-shot startup refresh so the Paper Portfolio page has data on first
    # load. The paper account only changes on user actions (entry/manage/reset
    # commands re-publish it), so it is NOT polled every tick. Guarded so a
    # cold DB / missing account never stops the loop from starting.
    try:
        await loop_.run_in_executor(None, handlers.refresh_paper_account, bus)
    except Exception:
        log.exception("startup refresh_paper_account degraded")
    # One-shot startup refresh of the paper-trade ledger so the Paper Trades page
    # has data on first load. The ledger only changes on user actions
    # (reload/close/delete/delete-all commands re-publish it), so it is NOT polled
    # every tick. Guarded so a cold DB never stops the loop from starting.
    try:
        await loop_.run_in_executor(None, handlers.refresh_paper_trades, bus)
    except Exception:
        log.exception("startup refresh_paper_trades degraded")
    # One-shot startup refresh of the open captured-signals view so the Captured
    # Signals page has data on first load. The signal set only changes on user
    # actions (reload/reprice/close commands re-publish it), so it is NOT polled
    # every tick. Guarded so a cold DB never stops the loop from starting.
    try:
        await loop_.run_in_executor(None, handlers.refresh_captured, bus)
    except Exception:
        log.exception("startup refresh_captured degraded")
    # One-shot startup refresh of the Gamma snapshot ($SPX default) so the Gamma
    # page has data on first load. The page drives subsequent refreshes by
    # enqueuing ``gamma_refresh`` with the current symbol (its own 120s timer), so
    # it is NOT polled here. Guarded so a cold proxy never stops the loop starting.
    try:
        await loop_.run_in_executor(None, handlers.refresh_gamma, bus, "$SPX")
    except Exception:
        log.exception("startup refresh_gamma degraded")
    # One-shot startup publish of the Gamma dropdown symbol universe (collected
    # symbols minus $VIX) so the Gamma page's dropdown is populated on first load.
    # The watchlist rarely changes mid-session; a service restart republishes.
    try:
        await loop_.run_in_executor(None, handlers.publish_gamma_symbols, bus)
    except Exception:
        log.exception("startup publish_gamma_symbols degraded")
    # One-shot startup publish of the GEX-collector status view so the Gamma
    # page's status bar has data on first load. Refreshed every tick below.
    # Guarded so a cold DB never stops the loop from starting.
    try:
        await loop_.run_in_executor(None, handlers.publish_gex_status, bus)
    except Exception:
        log.exception("startup publish_gex_status degraded")
    # One-shot startup publish of the stored-briefings index so the Gamma page's
    # history picker is populated on first load (refreshed after each new briefing).
    try:
        await loop_.run_in_executor(None, handlers.publish_gamma_briefing_index, bus)
    except Exception:
        log.exception("startup publish_gamma_briefing_index degraded")
    running = {}  # branch key → last launched task (see launch_branches)
    while True:
        now = _market_now()  # one clock read per tick, reused by every gate below
        # Decide which branches are DUE this tick (synchronous slot-gating,
        # unchanged), then LAUNCH their blocking work as keyed background tasks
        # (launch_branches) — the tick returns to its 30s sleep immediately, so a
        # slow branch (a 30-90s rescan, a slow Claude briefing) can never stall
        # the next tick's slot gates (the old gather did, dropping ~37% of the
        # 1-min GEX slots — measured 2026-07-17). A branch whose previous
        # instance is still running is skipped for the slot, so it can only ever
        # delay ITSELF. Each branch is its OWN coroutine carrying its OWN
        # try/except, so a failure in one can't affect the others.
        branches = []

        # Header + GEX-status refresh: every tick during market hours (their natural
        # ~30s cadence), throttled to every _OFFHOURS_INTERVAL_MIN off-hours so the
        # service stops making proxy/SQLite/Redis calls round the clock. Each is
        # independently guarded so one hiccup never stalls the other or the loop.
        try:
            p_due, p_slot = periodic_refresh_due(now, last_periodic_slot)
            last_periodic_slot = p_slot
        except Exception:
            log.exception("periodic_refresh_due gate degraded")
            p_due = False

        async def _periodic_branch():
            try:
                await loop_.run_in_executor(None, handlers.refresh_header, bus)
            except Exception:
                log.exception("refresh_header branch degraded")
            try:
                await loop_.run_in_executor(None, handlers.publish_gex_status, bus)
            except Exception:
                log.exception("publish_gex_status branch degraded")

        if p_due:
            branches.append(("periodic", _periodic_branch()))

        try:
            due, slot = autoscan_due(now, last_slot)
            if due:
                last_slot = slot
        except Exception:
            log.exception("autoscan_due gate degraded")
            due = False

        async def _rescan_branch():
            try:
                await loop_.run_in_executor(None, handlers.rescan, bus)
            except Exception:
                log.exception("rescan branch degraded")  # never let the scheduler die

        if due:
            branches.append(("rescan", _rescan_branch()))

        # Intraday GEX history collection — write a snapshot round on each 2-min
        # slot within market hours so the Gamma heatmap keeps populating all
        # session (replaces the standalone gex_collector window). The blocking
        # poll (~4 chains) runs in the executor; independently guarded so a poll
        # failure never skips the autoscan above or kills the loop.
        try:
            g_due, g_slot = gex_due(now, last_gex_slot)
            if g_due:
                last_gex_slot = g_slot
        except Exception:
            log.exception("gex_due gate degraded")
            g_due = False

        async def _gex_branch():
            try:
                await loop_.run_in_executor(None, handlers.collect_gex_history, bus)
            except Exception:
                log.exception("collect_gex_history branch degraded")
            # Republish the Gamma snapshot for the currently-viewed symbol right after
            # collection so the intraday heatmap + candles stay current server-side —
            # otherwise the gamma cache only refreshes while a page is open (its 120 s
            # timer) and shows a stale, cut-off session on the next load after a gap.
            try:
                await loop_.run_in_executor(None, handlers.refresh_gamma_current, bus)
            except Exception:
                log.exception("refresh_gamma_current branch degraded")

        if g_due:
            branches.append(("gex", _gex_branch()))

        # DRIVER paper auto-manage — reprice + auto-close the ISOLATED driver
        # account's open positions on each 5-min slot within market hours. (The
        # MANUAL Paper Portfolio no longer manages here — it runs entry+manage
        # hourly on its own paper_cycle_due slot below.) The blocking cycle (proxy
        # reprice) runs in the executor; independently guarded so a failure never
        # skips the work above or kills the loop. No-op-safe when the driver
        # account doesn't exist yet.
        m_due = False
        try:
            m_due, m_slot = manage_due(now, last_manage_slot)
            if m_due:
                last_manage_slot = m_slot
        except Exception:
            log.exception("manage_due gate degraded")
            m_due = False

        async def _driver_manage_branch():
            try:
                await loop_.run_in_executor(
                    None, handlers.run_driver_manage_and_refresh, bus)
            except Exception:
                log.exception("run_driver_manage_and_refresh branch degraded")

        if m_due:
            branches.append(("driver_manage", _driver_manage_branch()))

        # Manual Paper Portfolio hourly entry+manage — open new paper trades from
        # the current captured signals AND reprice/auto-close existing ones, once
        # at the top of each hour 09:00–14:00 CT (last run 14:00 / 2pm; NO 15:00
        # run at the regular-session close). Replaces the manual account's former
        # 5-min manage cadence. The hour is latched in paper_ran BEFORE the blocking
        # cycle so a slow cycle can't double-fire on the next tick. Independently
        # guarded so a failure never skips the work above or kills the loop.
        try:
            paper_h = paper_cycle_due(now, paper_ran)
            if paper_h is not None:
                paper_ran.add((now.date().isoformat(), paper_h))
        except Exception:
            log.exception("paper_cycle_due gate degraded")
            paper_h = None

        async def _paper_cycle_branch():
            try:
                await loop_.run_in_executor(
                    None, handlers.run_paper_entry_and_manage, bus)
            except Exception:
                log.exception("run_paper_entry_and_manage branch degraded")

        if paper_h is not None:
            branches.append(("paper_cycle", _paper_cycle_branch()))

        # Scheduled $SPX/SPY/QQQ Gamma Analyze — auto-run the Analyze command at
        # premarket / ~18 min after the open / midday / close on each trading day
        # (in addition to ad-hoc button use). Each slot fires once per day within a
        # grace window; the result is cached under its OWN slot key (NOT the ad-hoc
        # key) so no browser tab auto-opens. The slot is latched in analyze_ran
        # BEFORE the blocking Claude call so a slow call can't double-fire on the
        # next tick. Independently guarded so a failure never skips the work above
        # or kills the loop.
        try:
            a_slot = analyze_slot_due(now, analyze_ran)
            if a_slot:
                analyze_ran.add((now.date().isoformat(), a_slot))
        except Exception:
            log.exception("analyze_slot_due gate degraded")
            a_slot = None

        async def _analyze_branch(slot_name):
            try:
                await loop_.run_in_executor(
                    None, handlers.run_scheduled_gamma_analyze, bus, slot_name)
            except Exception:
                log.exception("run_scheduled_gamma_analyze branch degraded")

        if a_slot:
            branches.append(("analyze", _analyze_branch(a_slot)))

        # Scheduled "trades needing action" digest — push a Telegram/Discord
        # summary at 10:00 / 13:00 / 15:00 CT on each trading day. The slot is
        # latched in action_alert_ran BEFORE the blocking collect+push so a slow
        # push can't double-fire on the next tick. Independently guarded.
        try:
            aa_slot = action_alert_due(now, action_alert_ran)
            if aa_slot:
                action_alert_ran.add((now.date().isoformat(), aa_slot))
        except Exception:
            log.exception("action_alert_due gate degraded")
            aa_slot = None

        async def _action_alert_branch(slot_name):
            try:
                await loop_.run_in_executor(
                    None, handlers.run_action_alert, bus, slot_name)
            except Exception:
                log.exception("run_action_alert branch degraded")

        if aa_slot:
            branches.append(("action_alert", _action_alert_branch(aa_slot)))

        # Scheduled end-of-day summary — push a per-book day-P&L digest at ~15:10 CT on
        # each trading day. Latched in eod_summary_ran BEFORE the blocking collect+push so
        # a slow push can't double-fire on the next tick. Independently guarded.
        try:
            eod_slot = eod_summary_due(now, eod_summary_ran)
            if eod_slot:
                eod_summary_ran.add((now.date().isoformat(), eod_slot))
        except Exception:
            log.exception("eod_summary_due gate degraded")
            eod_slot = None

        async def _eod_summary_branch(slot_name):
            try:
                await loop_.run_in_executor(
                    None, handlers.run_eod_summary, bus, slot_name)
            except Exception:
                log.exception("run_eod_summary branch degraded")

        if eod_slot:
            branches.append(("eod_summary", _eod_summary_branch(eod_slot)))

        # Launch all DUE branches as keyed background tasks (bounded by the fixed
        # key set). The tick does NOT wait for them — see launch_branches.
        launch_branches(running, branches, asyncio.create_task)
        await asyncio.sleep(POLL_INTERVAL_SEC)

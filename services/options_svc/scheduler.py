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
from datetime import time as _time

from services.options_svc import compute, handlers
from shared.market_calendar import CT as _CT, HOLIDAYS as _HOLIDAYS, is_trading_day as _cal_is_trading_day

log = logging.getLogger(__name__)

# ── Schedule logic (ported verbatim from scanner.py) ───────────────────────
_SCAN_START = (8, 0)    # 08:00 CT
_SCAN_END = (15, 15)    # 15:15 CT


def _is_trading_day(now):
    return _cal_is_trading_day(now)


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
_GEX_START = (8, 30)     # gex_collector.START_HOUR/START_MIN
_GEX_STOP = (15, 20)     # gex_collector.STOP_HOUR/STOP_MIN
_GEX_INTERVAL_MIN = 2    # gex_collector.POLL_INTERVAL_MIN


def _in_gex_window(now):
    return _GEX_START <= (now.hour, now.minute) < _GEX_STOP


# ── Gamma display persistence (after close → next trading day's midnight) ─────
# The Gamma candles + heatmap should keep showing the LAST trading session's data
# after market close / once the collector stops, then clear for the next session.
# The display window for session date D is [D 00:00, next_trading_day(D) 00:00):
#   • weekday after close → shows today until tonight's midnight;
#   • Fri/holiday-eve → persists through the weekend/holiday until the midnight
#     before the next trading day (e.g. Mon 00:00);
#   • after that midnight, until the new session's data exists → cleared.
def _prev_trading_day(d):
    """Most recent trading day strictly before date ``d`` (skips weekends/holidays)."""
    import datetime as _dtmod
    d = d - _dtmod.timedelta(days=1)
    while d.weekday() >= 5 or d in _HOLIDAYS:
        d -= _dtmod.timedelta(days=1)
    return d


def active_session_date(now=None):
    """The trading day whose Gamma data should be displayed now: today if today is
    a trading day, else the most recent prior trading day.

    Drives heatmap persistence: Friday's data stays shown all weekend (active date
    = Friday) until Monday becomes 'today' — a trading day whose rows don't exist
    yet, so the heatmap naturally clears."""
    now = now or _market_now()
    d = now.date()
    if d.weekday() < 5 and d not in _HOLIDAYS:
        return d
    return _prev_trading_day(d)


def gamma_cleared(now=None):
    """True in the overnight 'cleared' window of a trading day — after midnight CT
    but before the session/collection start (08:30 CT).

    The prior session's data was displayed until midnight; the new session hasn't
    produced any data yet, so Gamma shows nothing. False on non-trading days
    (weekend/holiday) so the last session's data persists, and False after 08:30
    so the live session shows."""
    now = now or _market_now()
    d = now.date()
    is_td = d.weekday() < 5 and d not in _HOLIDAYS
    return is_td and (now.hour, now.minute) < _GEX_START


def _gex_slot_key(now):
    return (now.date().isoformat(), now.hour, now.minute // _GEX_INTERVAL_MIN)


def gex_due(now, last_slot):
    """(should_collect, slot): True at most once per 2-min slot, only on a
    trading day within the 08:30–15:20 CT GEX-collection window. Mirrors
    ``autoscan_due`` on the gex_collector cadence/window."""
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


# ── Scheduler loop ─────────────────────────────────────────────────────────
POLL_INTERVAL_SEC = 30  # check the slot every 30s (mirrors the page's autoscan loop cadence)


async def _gather_due(coros):
    """Run the tick's due-branch coroutines CONCURRENTLY and wait for all.

    A4 fix: the due branches are independent (GEX collection writes
    gex_history.db, manage writes the paper account, rescan writes the scan
    cache), so a slow branch (e.g. the 15-min rescan) must not delay the START
    of the time-critical ones (2-min GEX collect / 5-min manage). Each branch
    carries its OWN try/except, so it never raises here; ``return_exceptions``
    is set as a belt-and-suspenders guard so one branch's failure can't affect
    the others. The set of branches per tick is small and fixed (≤5), so this
    is bounded — no unbounded task spawning."""
    if not coros:
        return
    await asyncio.gather(*coros, return_exceptions=True)


async def loop(bus):
    """Server-side 15-min auto-scan + gated header/GEX-status refresh.

    Each 30 s tick: refresh the compact header view (quotes + VIX regime +
    sentiment dot) + GEX-status view — every tick during market hours, throttled
    to every _OFFHOURS_INTERVAL_MIN off-hours (see periodic_refresh_due) so the
    service stops the round-the-clock proxy/SQLite/Redis churn — then, on each
    trading-day 15-min slot within 08:00-15:15 CT, run one rescan (plus 2-min GEX
    collection + 5-min paper auto-manage — both the MANUAL and the isolated DRIVER
    paper accounts — on their own windows). Mirrors the page's former
    _autoscan_loop. The BLOCKING calls run in an executor so the event loop stays
    responsive. Each is independently guarded so one failure can't kill the loop or
    skip the others — in particular the driver manage tick is guarded separately
    from the manual one so a driver-side failure can't skip the manual refresh."""
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
    last_manage_slot = None  # 5-min paper auto-manage slot (see manage_due)
    last_periodic_slot = None  # header + gex_status throttle slot (see periodic_refresh_due)
    analyze_ran = set()  # (date, slot) of fired scheduled Gamma Analyze runs (see analyze_slot_due)
    action_alert_ran = set()  # (date, slot) of fired action-alert pushes (see action_alert_due)
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
    while True:
        now = _market_now()  # one clock read per tick, reused by every gate below
        # A4: decide which branches are DUE this tick (synchronous slot-gating,
        # unchanged), then run their blocking work CONCURRENTLY so a slow branch
        # (e.g. the 15-min rescan) can't delay the START of the time-critical ones
        # (2-min GEX collect / 5-min manage). The slot-DUE logic + last_*_slot
        # bookkeeping stay serial here; only the executor-await work is gathered.
        # Each branch is its OWN coroutine carrying its OWN try/except, so a
        # failure/hang in one can't affect the others (per-branch isolation).
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
            branches.append(_periodic_branch())

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
            branches.append(_rescan_branch())

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
            branches.append(_gex_branch())

        # Paper auto-manage — reprice open paper positions + auto-close hits on
        # each 5-min slot within market hours (replaces the manual-only "Run
        # manage cycle" button; the button still works for on-demand runs). The
        # blocking cycle (proxy reprice) runs in the executor; independently
        # guarded so a failure never skips the work above or kills the loop.
        m_due = False
        try:
            m_due, m_slot = manage_due(now, last_manage_slot)
            if m_due:
                last_manage_slot = m_slot
        except Exception:
            log.exception("manage_due gate degraded")
            m_due = False

        # The manual + driver manage refreshes share the manage_due slot but each
        # keeps its OWN try/except — a driver-side failure can NEITHER skip the
        # manual refresh NOR kill the loop. No-op-safe when the driver account
        # doesn't exist yet. Kept in one branch coroutine so the manual refresh
        # runs first (its book is the primary one), driver second.
        async def _manage_branch():
            try:
                await loop_.run_in_executor(None, handlers.run_manage_and_refresh, bus)
            except Exception:
                log.exception("run_manage_and_refresh branch degraded")
            # Driver paper account auto-manage — reprice + auto-close the ISOLATED
            # driver account's open positions on the SAME 5-min cadence (it reuses
            # the manage_due slot decided above). In its OWN try/except.
            try:
                await loop_.run_in_executor(
                    None, handlers.run_driver_manage_and_refresh, bus)
            except Exception:
                log.exception("run_driver_manage_and_refresh branch degraded")

        if m_due:
            branches.append(_manage_branch())

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
            branches.append(_analyze_branch(a_slot))

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
            branches.append(_action_alert_branch(aa_slot))

        # Run all DUE branches concurrently (bounded, ≤5). A slow branch can't
        # delay the start of the time-critical ones; per-branch try/except keeps
        # each isolated.
        await _gather_due(branches)
        await asyncio.sleep(POLL_INTERVAL_SEC)

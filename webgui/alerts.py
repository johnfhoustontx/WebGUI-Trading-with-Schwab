"""Pure helpers for app-wide scanner alerts + nav badges (unit-tested).

The wiring (timer, audio element, badge UI) lives in main.py's _layout; this
module holds only the decision logic so it can be tested without NiceGUI.
"""
import datetime as dt
from zoneinfo import ZoneInfo

from pages.options.scanner import _sig_key
from shared.market_calendar import is_holiday as _cal_is_holiday
from shared.market_calendar import is_trading_day as _cal_is_trading_day

CT = ZoneInfo("America/Chicago")
_OPEN, _CLOSE = dt.time(8, 0), dt.time(15, 0)   # CT trading window

# NYSE full-closure holidays come from shared/market_calendar.py — the one
# calendar the whole stack now reads, derived algorithmically so there is no
# yearly edit.


def is_market_holiday(day):
    """True if ``day`` (a ``date``) is an NYSE full-closure holiday.

    Year-general: this used to test membership against a bounded 2026-27 set,
    which would have called every holiday from 2028 an ordinary day."""
    return _cal_is_holiday(day)


def _signals(scan):
    scan = scan or {}
    return (scan.get("signals_0dte") or []) + (scan.get("signals_swing") or [])


def scanner_keys(scan):
    """Set of stable signal keys across both tables."""
    return {_sig_key(s) for s in _signals(scan)}


def captured_keys(captured):
    """Set of stable keys for the currently open captured signals — their
    ``signal_id``s.

    Immune to REPRICING: a mark update keeps the same id, so a captured badge built
    on ``unread_count`` fires on a genuinely NEW captured signal, not on the
    periodic reprice-republish that merely bumps the view's version (that
    version-churn was the "captured badge keeps showing" bug). Defensive: a
    bad/None view, or a signal missing its id, contributes nothing.
    """
    sigs = (captured or {}).get("signals") if isinstance(captured, dict) else None
    sigs = sigs if isinstance(sigs, list) else []
    return {s.get("signal_id") for s in sigs
            if isinstance(s, dict) and s.get("signal_id")}


def scanner_scores(scan):
    """{signal_key: composite_score} across both tables."""
    return {_sig_key(s): (s.get("composite_score") or 0) for s in _signals(scan)}


def unread_count(current_keys, acked_keys):
    """How many current keys have not been acknowledged."""
    return len(set(current_keys) - set(acked_keys or set()))


def qualifying_new(scan, alerted, min_score):
    """Signal keys that are new (not yet alerted) AND score >= min_score."""
    scores = scanner_scores(scan)
    alerted = alerted or set()
    return {k for k, sc in scores.items() if k not in alerted and sc >= (min_score or 0)}


def new_signal_text(n):
    """In-app toast text for ``n`` new qualifying scanner signals (singular/plural)."""
    return f"{n} new scanner signal" + ("" if n == 1 else "s")


def in_market_hours(now):
    """True on a trading day within 08:00–15:00 CT (now is a tz-aware datetime).

    A trading day is a weekday that is NOT an NYSE full-closure holiday, so
    market-hours-gated alerts (scanner chimes + health/staleness alerts) stay
    silent on weekends AND holidays.
    """
    ct = now.astimezone(CT)
    return _cal_is_trading_day(ct.date()) and _OPEN <= ct.time() <= _CLOSE


def should_alert(settings, qualifying, now):
    """Whether to chime: enabled, something qualifies, and the market-hours gate passes."""
    if not settings.get("alert_enabled") or not qualifying:
        return False
    if settings.get("alert_market_hours_only") and not in_market_hours(now):
        return False
    return True


# ── Health / staleness alerts (R4b / R8) ─────────────────────────────────────
# The same view-freshness threshold the System Status page uses (status.py
# _STALE_AFTER_SEC). Kept local so alerts.py stays a pure, NiceGUI-free module.
STALE_AFTER_SEC = 600

# Per-view staleness overrides (seconds). A view is only "stale" if the owning
# service has plausibly MISSED a scheduled publish, so the threshold must exceed
# that view's publish cadence + a grace. ``options:scan`` autoscans once per 15-min
# slot (options_svc scheduler ``autoscan_due``), so the default 600 s falsely flags
# it as stale for ~5 min every cycle — 20 min gives a full cycle + grace while still
# catching a genuinely wedged/dead scanner. Add an entry here for any view whose real
# publish cadence exceeds STALE_AFTER_SEC.
STALE_OVERRIDES = {"options:scan": 20 * 60}


def stale_after(view):
    """Staleness threshold (seconds) for ``view`` — a per-view override or the default."""
    return STALE_OVERRIDES.get(view, STALE_AFTER_SEC)


def unhealthy_keys(freshness, health):
    """Set of stable keys for everything currently STALE or DOWN.

    * ``freshness`` — ``{view: is_stale_bool}`` for the representative scheduled
      cache views (a True means the view's newest write is older than the stale
      threshold, or nothing has been published).
    * ``health`` — ``{service_key: is_up_bool_or_None}`` for probed Tier-2
      services. ``None`` (couldn't determine) is treated as healthy so a probe
      we skipped/failed to run doesn't raise a false alarm.

    Keys are namespaced (``stale:<view>`` / ``down:<service>``) so a view and a
    service can never collide, and so the transition-dedup set is unambiguous.
    """
    keys = set()
    for view, stale in (freshness or {}).items():
        if stale:
            keys.add(f"stale:{view}")
    for svc, up in (health or {}).items():
        if up is False:            # only a definite down; None => can't tell => skip
            keys.add(f"down:{svc}")
    return keys


def health_alert_gate(settings, now):
    """Whether health/staleness alerts may fire right now (enable + market-hours).

    Uses the SAME gate as scanner alerts — the ``alert_enabled`` master toggle and
    the ``alert_market_hours_only`` window — so a user who silenced alerts (or is
    off-hours with the gate on) isn't chimed for a service that's expectedly idle.
    """
    if not settings.get("alert_enabled"):
        return False
    if settings.get("alert_market_hours_only") and not in_market_hours(now):
        return False
    return True


def new_health_alerts(freshness, health, alerted, settings, now):
    """Decide health/staleness alerts for this tick, with transition-dedup.

    Pure. Returns ``(fire_keys, next_alerted)``:

    * ``fire_keys`` — keys that transitioned INTO unhealthy this tick (not in
      ``alerted``) AND pass the enable/market-hours gate. Empty when the gate is
      closed (so nothing chimes off-hours), even though ``next_alerted`` still
      tracks state so a later in-hours tick doesn't re-fire a persistent problem.
    * ``next_alerted`` — the unhealthy set to carry forward. Anything that
      recovered (no longer unhealthy) is DROPPED, so it will alert again if it
      breaks a second time (fire-on-transition, clear-on-heal).

    The badge count is computed separately (``len(unhealthy_keys(...))``) and is
    NOT gated — a persistent problem keeps its badge even while the chime is
    deduped/off-hours.
    """
    current = unhealthy_keys(freshness, health)
    alerted = set(alerted or set())
    newly = current - alerted
    fire = newly if health_alert_gate(settings, now) else set()
    # Carry forward only what is still unhealthy (recovered keys drop out and can
    # re-alert later). Include ``newly`` even when the gate blocked the chime so a
    # persistent problem isn't re-chimed on the next in-hours tick.
    next_alerted = current
    return fire, next_alerted


def health_alert_text(n):
    """In-app toast text for ``n`` newly stale/down components (singular/plural)."""
    return f"{n} service alert" + ("" if n == 1 else "s") + " — stale or down"


# ── Options-flow alerts (crossover + unusual activity) ───────────────────────
def new_flow_alerts(view, acked):
    """``(new_alerts_in_order, updated_acked)`` from a flow-alerts view.

    New = alert dicts whose ``id`` isn't already acked. The updated set carries
    every id present so each alert fires once (fire-on-first-seen). Defensive:
    a bad/None view → ``([], acked)`` unchanged.

    ``big_delta`` is quiet-live: it is excluded from the chime/toast trigger set
    (the Flow Alerts screen still shows it — this only silences the audible/visual
    nudge) but its id is still marked seen, so it's considered exactly once.
    """
    lst = (view or {}).get("alerts") if isinstance(view, dict) else None
    lst = lst if isinstance(lst, list) else []
    new, all_ids = [], set(acked)
    for a in lst:
        if isinstance(a, dict) and a.get("id") and a["id"] not in all_ids:
            all_ids.add(a["id"])
            if a.get("type") == "big_delta":
                continue          # quiet-live: no chime/toast, still on the screen
            new.append(a)
    return new, all_ids


def should_flow_alert(settings, new_flow, now):
    """Whether to fire options-flow alerts — enabled, something new, market hours.

    Mirrors ``should_alert``. The flow branch previously had NO time gate while the
    scanner and health branches both honored ``alert_market_hours_only``, so flow
    alerts could chime long after the close (the server stops publishing at 15:20
    CT, but a backlog replay on the GUI side fires whenever it happens).
    """
    if not settings.get("flow_alerts_enabled", True) or not new_flow:
        return False
    if settings.get("alert_market_hours_only") and not in_market_hours(now):
        return False
    return True

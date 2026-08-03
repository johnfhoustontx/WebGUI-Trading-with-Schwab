"""Acceptance gate for the Phase B window migration: the shared helpers must
reproduce each legacy predicate EXACTLY, minute by minute.

Fourteen hardcoded window constants across five services, the webgui and the
options-scanner engines are about to be repointed at
``shared/market_calendar.py``. That migration's bar is *identical output*, so
this module pins the equivalence BEFORE anything moves. If one of these tests
fails, the shared helper diverged from production behavior -- **the legacy
predicate is the specification, not the other way round.**

Every legacy predicate is reconstructed as a literal local function here rather
than imported from its service module. The service modules already read their
holiday set from ``shared.market_calendar``, so importing them would make this
partly circular; transcribed literals keep the oracle independent.

Two divergences are REAL and are pinned explicitly rather than glossed (see
``test_sentiment_rth_...`` and ``test_driver_entry_...``): the migration must
preserve each one deliberately.

The sweep covers every minute of four days, each chosen for a reason:
before activation, the activation date itself, a weekend, and a holiday.
"""
import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from shared import market_calendar as mc

CT = ZoneInfo("America/Chicago")
ET = ZoneInfo("America/New_York")

# The exact 20-date literal every one of the eight consolidated sites contained,
# transcribed so this file is an independent oracle (see the module docstring).
_LEGACY_HOLIDAYS = frozenset({
    dt.date(2026, 1, 1), dt.date(2026, 1, 19), dt.date(2026, 2, 16),
    dt.date(2026, 4, 3), dt.date(2026, 5, 25), dt.date(2026, 6, 19),
    dt.date(2026, 7, 3), dt.date(2026, 9, 7), dt.date(2026, 11, 26),
    dt.date(2026, 12, 25),
    dt.date(2027, 1, 1), dt.date(2027, 1, 18), dt.date(2027, 2, 15),
    dt.date(2027, 3, 26), dt.date(2027, 5, 31), dt.date(2027, 6, 18),
    dt.date(2027, 7, 5), dt.date(2027, 9, 6), dt.date(2027, 11, 25),
    dt.date(2027, 12, 24),
})

DAYS = [
    dt.date(2026, 8, 14),   # Friday, BEFORE the ETH activation date
    dt.date(2026, 8, 17),   # Monday, the activation date itself
    dt.date(2026, 8, 22),   # Saturday
    dt.date(2026, 9, 7),    # Labor Day (holiday)
]


@pytest.fixture(autouse=True)
def _clean_config_cache():
    """Every test starts from the real sessions.toml, not a neighbour's stub."""
    mc.reset_config_cache()
    yield
    mc.reset_config_cache()


def _minutes(day, tz=CT):
    """Every minute boundary of ``day`` in ``tz`` (1440 tz-aware datetimes)."""
    for hour in range(24):
        for minute in range(60):
            yield dt.datetime(day.year, day.month, day.day, hour, minute,
                              tzinfo=tz)


# ---------------------------------------------------------------------------
# The legacy predicates, transcribed verbatim
# ---------------------------------------------------------------------------


def _legacy_is_trading_day(d):
    """Verbatim shape of the predicate every scheduler used pre-consolidation."""
    return d.weekday() < 5 and d not in _LEGACY_HOLIDAYS


def _legacy_is_market_hours(now):
    """options_svc/scheduler.py ``_is_market_hours`` -- 08:00-15:15 CT INCLUSIVE."""
    return dt.time(8, 0) <= now.time() <= dt.time(15, 15)


def _legacy_in_gex_window(now):
    """options_svc/scheduler.py ``_in_gex_window`` -- 08:00-15:20, stop EXCLUSIVE.

    Compares ``(hour, minute)`` tuples, so seconds are ignored.
    """
    return (8, 0) <= (now.hour, now.minute) < (15, 20)


def _legacy_sentiment_is_rth(now):
    """sentiment_svc/scheduler.py ``_is_rth`` -- 08:30-15:00 CT, end EXCLUSIVE."""
    if not _legacy_is_trading_day(now.date()):
        return False
    return dt.time(8, 30) <= now.time() < dt.time(15, 0)


def _legacy_portfolio_is_rth(now):
    """portfolio_svc/scheduler.py ``_is_rth`` -- 08:30-15:00 CT INCLUSIVE.

    market_svc/scheduler.py's ``_is_rth`` is byte-identical to this one.
    """
    if not _legacy_is_trading_day(now.date()):
        return False
    return dt.time(8, 30) <= now.time() <= dt.time(15, 0)


def _legacy_market_snapshot_window(now):
    """options_svc ``_MKT_SNAP_START``/``_MKT_SNAP_END`` -- 08:30-15:00 CT.

    ``market_snapshot_due`` fires on :00/:30 slot targets rather than testing a
    window, and ``_market_snapshot_slots`` walks ``[start, end]`` INCLUSIVE (the
    last slot fires at 15:00), so this is the span those slots live in.
    """
    if not _legacy_is_trading_day(now.date()):
        return False
    return dt.time(8, 30) <= now.time() <= dt.time(15, 0)


def _legacy_market_snapshot_slots():
    """options_svc/scheduler.py ``_market_snapshot_slots`` -- verbatim."""
    out = []
    h, m = 8, 30
    while (h, m) <= (15, 0):
        out.append((h, m))
        m += 30
        if m >= 60:
            m -= 60
            h += 1
    return out


def _legacy_driver_entry(now):
    """driver_svc/scheduler.py ``checkpoint_due`` entry gate -- 09:45-15:30 ET.

    ``if hm < RTH_START or hm >= RTH_END: return (False, ...)`` -- so the end is
    EXCLUSIVE, and on ``(hour, minute)`` tuples, meaning the whole 15:30 minute
    is out.
    """
    if not _legacy_is_trading_day(now.date()):
        return False
    hm = (now.hour, now.minute)
    return (9, 45) <= hm < (15, 30)


def _legacy_alerts_in_market_hours(now):
    """webgui/alerts.py ``in_market_hours`` -- 08:00-15:00 CT INCLUSIVE."""
    ct = now.astimezone(CT)
    return (_legacy_is_trading_day(ct.date())
            and dt.time(8, 0) <= ct.time() <= dt.time(15, 0))


# ---------------------------------------------------------------------------
# 1. Trading days
# ---------------------------------------------------------------------------


def test_is_trading_day_matches_legacy():
    for day in DAYS:
        assert mc.is_trading_day(day) == _legacy_is_trading_day(day), day


def test_is_trading_day_matches_legacy_across_both_holiday_years():
    """A wider sweep, so the four-day sample can't hide a derivation slip."""
    day = dt.date(2026, 1, 1)
    while day <= dt.date(2027, 12, 31):
        assert mc.is_trading_day(day) == _legacy_is_trading_day(day), day
        day += dt.timedelta(days=1)


# ---------------------------------------------------------------------------
# 2. scan window -- 08:00-15:15 CT inclusive
# ---------------------------------------------------------------------------


def test_scan_window_matches_legacy_0800_1515():
    for day in DAYS:
        for now in _minutes(day):
            legacy = (_legacy_is_trading_day(now.date())
                      and _legacy_is_market_hours(now))
            assert mc.in_window("scan", now) == legacy, now


# ---------------------------------------------------------------------------
# 3. collection window -- 08:00-15:20 CT, stop EXCLUSIVE
# ---------------------------------------------------------------------------


def test_collection_window_matches_legacy_gex_window_exclusive_stop():
    for day in DAYS:
        for now in _minutes(day):
            legacy = (_legacy_is_trading_day(now.date())
                      and _legacy_in_gex_window(now))
            assert mc.in_collection_window(now, eth_eligible=False) == legacy, now


def test_collection_stop_is_exclusive_at_the_second_level():
    """15:19:59 collects, 15:20:00 does not -- the legacy ``<`` stop."""
    day = dt.date(2026, 8, 17)
    assert mc.in_collection_window(
        dt.datetime(*day.timetuple()[:3], 15, 19, 59, tzinfo=CT)) is True
    assert mc.in_collection_window(
        dt.datetime(*day.timetuple()[:3], 15, 20, 0, tzinfo=CT)) is False


def test_eth_eligible_collection_is_inert_before_activation():
    """The inertness guarantee, exhaustively over the pre-activation Friday.

    On 2026-08-14 an ``eth_eligible`` symbol must collect on EXACTLY the legacy
    08:00-15:20 schedule -- the flag buys nothing until 2026-08-17.
    """
    day = dt.date(2026, 8, 14)
    assert mc.extended_hours_active(day) is False
    for now in _minutes(day):
        legacy = (_legacy_is_trading_day(now.date())
                  and _legacy_in_gex_window(now))
        assert mc.in_collection_window(now, eth_eligible=True) == legacy, now


def test_eth_eligible_collection_widens_on_the_activation_date():
    """Power check for the test above: the flag is not simply ignored always.

    On the activation date an eligible symbol starts at 06:30 while everything
    else still starts at 08:00.
    """
    now = dt.datetime(2026, 8, 17, 7, 0, tzinfo=CT)
    assert mc.in_collection_window(now, eth_eligible=True) is True
    assert mc.in_collection_window(now, eth_eligible=False) is False


# ---------------------------------------------------------------------------
# 4. is_regular_hours -- 08:30-15:00 CT inclusive (portfolio_svc + market_svc)
# ---------------------------------------------------------------------------


def test_is_regular_hours_matches_portfolio_and_market_rth():
    for day in DAYS:
        for now in _minutes(day):
            assert mc.is_regular_hours(now) == _legacy_portfolio_is_rth(now), now


# ---------------------------------------------------------------------------
# 5. market_snapshot window -- 08:30-15:00 CT
# ---------------------------------------------------------------------------


def test_market_snapshot_window_matches_legacy():
    for day in DAYS:
        for now in _minutes(day):
            assert mc.in_window("market_snapshot", now) == \
                _legacy_market_snapshot_window(now), now


def test_every_legacy_market_snapshot_slot_falls_inside_the_named_window():
    """The window must span the :00/:30 slot targets, ends included."""
    day = dt.date(2026, 8, 17)     # a trading day
    slots = _legacy_market_snapshot_slots()
    assert slots[0] == (8, 30) and slots[-1] == (15, 0)
    for h, m in slots:
        now = dt.datetime(day.year, day.month, day.day, h, m, tzinfo=CT)
        assert mc.in_window("market_snapshot", now) is True, (h, m)


# ---------------------------------------------------------------------------
# 6. driver_entry -- 09:45-15:30 ET
# ---------------------------------------------------------------------------


def test_driver_entry_window_matches_legacy_except_the_1530_boundary_minute():
    """**Divergence, pinned.** ``checkpoint_due`` excludes its end
    (``hm >= RTH_END`` → not due) while ``in_window`` is INCLUSIVE at both ends.

    Sweeping ET minute boundaries, the two agree everywhere except the 15:30
    stamp on a trading day. ``driver_svc``'s migration must therefore NOT simply
    call ``in_window("driver_entry", ...)`` -- doing so would open a 16th entry
    slot at 15:30 ET, exactly the "no new entries into the close" rule the
    constant exists to enforce.
    """
    divergent = set()
    for day in DAYS:
        for now in _minutes(day, tz=ET):
            legacy = _legacy_driver_entry(now)
            if mc.in_window("driver_entry", now) != legacy:
                divergent.add((now.date(), now.time()))
    expected = {(d, dt.time(15, 30)) for d in DAYS if _legacy_is_trading_day(d)}
    assert divergent == expected


def test_driver_entry_agrees_with_legacy_outside_that_one_minute():
    """The same sweep with the known boundary minute excluded -- everything else
    must match exactly, so the pin above can't be hiding a second difference."""
    for day in DAYS:
        for now in _minutes(day, tz=ET):
            if now.time() == dt.time(15, 30):
                continue
            assert mc.in_window("driver_entry", now) == \
                _legacy_driver_entry(now), now


def test_driver_entry_is_evaluated_in_eastern_time_not_central():
    """The window carries ``tz = "America/New_York"``, so a CT-aware datetime is
    converted rather than compared in CT. 09:00 CT is 10:00 ET -- inside the
    window -- while the numerically-equal 09:00 ET is not."""
    assert mc.in_window("driver_entry",
                        dt.datetime(2026, 8, 17, 9, 0, tzinfo=CT)) is True
    assert mc.in_window("driver_entry",
                        dt.datetime(2026, 8, 17, 9, 0, tzinfo=ET)) is False
    # Same instant expressed two ways must give the same answer.
    et_now = dt.datetime(2026, 8, 17, 10, 0, tzinfo=ET)
    assert mc.in_window("driver_entry", et_now) is \
        mc.in_window("driver_entry", et_now.astimezone(CT))


# ---------------------------------------------------------------------------
# 7. The sentiment_svc one-minute divergence
# ---------------------------------------------------------------------------


def test_sentiment_rth_is_one_minute_narrower_than_is_regular_hours():
    """**Divergence, pinned.** ``sentiment_svc._is_rth`` ends EXCLUSIVE at 15:00
    while ``is_regular_hours`` is INCLUSIVE at both ends.

    Sweeping minute boundaries, the only stamps where they differ are the 15:00
    ones on a trading day. That is why ``sentiment_svc``'s migration must NOT
    simply call ``is_regular_hours`` -- it would widen the RTH gate by a minute,
    firing one extra 2-min composite refresh and intraday recording per day.
    """
    divergent = set()
    for day in DAYS:
        for now in _minutes(day):
            if mc.is_regular_hours(now) != _legacy_sentiment_is_rth(now):
                divergent.add((now.date(), now.time()))
    expected = {(d, dt.time(15, 0)) for d in DAYS if _legacy_is_trading_day(d)}
    assert divergent == expected


def test_sentiment_rth_agrees_with_is_regular_hours_outside_that_minute():
    for day in DAYS:
        for now in _minutes(day):
            if now.time() == dt.time(15, 0):
                continue
            assert mc.is_regular_hours(now) == _legacy_sentiment_is_rth(now), now


def test_sentiment_divergence_is_exactly_one_instant_at_second_granularity():
    """Sharper than the minute sweep: both predicates use ``time()`` including
    seconds, so 15:00:30 is already outside BOTH. The true divergence is the
    single instant 15:00:00."""
    day = dt.date(2026, 8, 17)
    exact = dt.datetime(day.year, day.month, day.day, 15, 0, 0, tzinfo=CT)
    later = dt.datetime(day.year, day.month, day.day, 15, 0, 30, tzinfo=CT)
    assert mc.is_regular_hours(exact) is True
    assert _legacy_sentiment_is_rth(exact) is False
    assert mc.is_regular_hours(later) is False
    assert _legacy_sentiment_is_rth(later) is False


# ---------------------------------------------------------------------------
# 8. Drift guards on config/sessions.toml
# ---------------------------------------------------------------------------


def test_config_windows_still_match_the_legacy_constants():
    """If someone edits sessions.toml, this gate fails loudly rather than the
    migration silently changing behavior."""
    assert mc.window_bounds("scan") == (dt.time(8, 0), dt.time(15, 15))
    assert mc.window_bounds("collection") == (dt.time(8, 0), dt.time(15, 20))
    assert mc.window_bounds("market_snapshot") == (dt.time(8, 30), dt.time(15, 0))
    assert mc.window_bounds("driver_entry") == (dt.time(9, 45), dt.time(15, 30))
    # session_flip is a single time, not a span (options_svc ``_GEX_START``,
    # the active_session_date flip).
    assert mc.session_flip_time() == dt.time(8, 0)
    # The regular session bounds the portfolio/market RTH gates read as.
    assert mc._session_bounds("regular") == (dt.time(8, 30), dt.time(15, 0))
    # The ETH collection start, still inert before the activation date.
    assert mc.activation_date() == dt.date(2026, 8, 17)


def test_alerts_window_has_no_named_equivalent():
    """webgui/alerts.py runs 08:00-15:00 CT inclusive -- a span no named window
    reproduces (``scan`` closes at 15:15, ``market_snapshot`` opens at 08:30).

    Pinned so the webgui migration can't quietly repoint at a near-miss: an
    alerts gate on ``scan`` would chime for 15 extra minutes a day, and one on
    ``market_snapshot`` would go silent for the first 30.
    """
    day = dt.date(2026, 8, 17)
    for name in ("scan", "market_snapshot"):
        differs = [now for now in _minutes(day)
                   if mc.in_window(name, now) != _legacy_alerts_in_market_hours(now)]
        assert differs, f"{name} unexpectedly equals the alerts window"
    assert mc.window_bounds("scan")[1] == dt.time(15, 15)
    assert mc.window_bounds("market_snapshot")[0] == dt.time(8, 30)


def test_in_window_rejects_an_unknown_name():
    with pytest.raises(KeyError):
        mc.in_window("nope", dt.datetime(2026, 8, 17, 10, 0, tzinfo=CT))

from datetime import datetime
from zoneinfo import ZoneInfo

from gex_status import MARKET_OPEN, classify_collector_status

TZ = ZoneInfo("America/Chicago")


def test_fresh_during_market_hours():
    now = datetime(2026, 4, 13, 10, 0, tzinfo=TZ)
    text, color = classify_collector_status(
        age_seconds=120, now_ct=now, has_data=True, last_ts=None,
    )
    assert "2m" in text
    assert color == "green"


def test_stale_during_market_hours():
    now = datetime(2026, 4, 13, 10, 0, tzinfo=TZ)
    text, color = classify_collector_status(
        age_seconds=1080, now_ct=now, has_data=True, last_ts=None,
    )
    assert "stale" in text.lower()
    assert color == "#c48b00"


def test_no_data_after_first_poll_window():
    now = datetime(2026, 4, 13, 8, 40, tzinfo=TZ)
    text, color = classify_collector_status(
        age_seconds=None, now_ct=now, has_data=False, last_ts=None,
    )
    assert "not running" in text.lower()
    assert color == "red"


def test_premarket_shows_scheduled_start():
    """The message names the CONFIGURED collection start, not a literal.

    It asserted a hardcoded "8:30" until 2026-08-03, pinning a live misreport:
    collection moved to 08:00 CT on 2026-07-11 but this classifier still said
    "starts 8:30" for that half hour every morning.
    """
    now = datetime(2026, 4, 13, 5, 0, tzinfo=TZ)   # before any plausible open
    text, color = classify_collector_status(
        age_seconds=None, now_ct=now, has_data=False, last_ts=None,
    )
    assert f"{MARKET_OPEN.hour}:{MARKET_OPEN.minute:02d}" in text
    assert color == "gray"


def test_scheduled_start_matches_the_shared_collection_window():
    """Drift guard: the classifier's open IS config/sessions.toml's collection
    start (08:00 CT), so the status line can never diverge from what the
    collector actually schedules on."""
    from shared import market_calendar as mc
    assert MARKET_OPEN == mc.window_bounds("collection")[0]
    assert MARKET_OPEN == datetime(2026, 4, 13, 8, 0).time()


def test_after_hours_with_last_run():
    now = datetime(2026, 4, 13, 16, 0, tzinfo=TZ)
    last_ts = int(datetime(2026, 4, 13, 15, 15, tzinfo=TZ).timestamp())
    text, color = classify_collector_status(
        age_seconds=2700, now_ct=now, has_data=True, last_ts=last_ts,
    )
    assert "15:15" in text
    assert color == "gray"


def test_weekend_shows_idle():
    now = datetime(2026, 4, 11, 10, 0, tzinfo=TZ)  # Saturday
    text, color = classify_collector_status(
        age_seconds=None, now_ct=now, has_data=False, last_ts=None,
    )
    assert color == "gray"


# --- Named sessions + the GTH "awaiting opens" case (E3) --------------------
# Cboe's vocabulary, NOT "pre-market": GTH (06:30-08:25 CT) and Curb
# (15:00-15:15 CT). Both are INERT before the 2026-08-17 activation date.
ACTIVE = 2026, 8, 17          # a Monday, on/after activation
BEFORE = 2026, 8, 14          # the Friday before -- ETH not yet live


def test_session_label_names_the_cboe_sessions():
    from gex_status import session_label
    assert session_label(datetime(*ACTIVE, 7, 0, tzinfo=TZ)) == "GTH"
    assert session_label(datetime(*ACTIVE, 10, 0, tzinfo=TZ)) == "Regular"
    assert session_label(datetime(*ACTIVE, 15, 5, tzinfo=TZ)) == "Curb"
    assert session_label(datetime(*ACTIVE, 20, 0, tzinfo=TZ)) == "Closed"
    assert session_label(datetime(2026, 8, 15, 10, 0, tzinfo=TZ)) == "Closed"  # Sat


def test_session_label_is_inert_before_activation():
    """Before 2026-08-17 the extended windows are not sessions at all."""
    from gex_status import session_label
    assert session_label(datetime(*BEFORE, 7, 0, tzinfo=TZ)) == "Closed"
    assert session_label(datetime(*BEFORE, 10, 0, tzinfo=TZ)) == "Regular"


def test_gth_no_data_is_awaiting_opens_not_a_dead_collector():
    """H7. A class begins its GTH opening rotation only on the first round-lot
    print in the underlying plus a two-sided quote, and GTH underlying liquidity
    is thin -- so an eligible symbol can legitimately have no quotes for part or
    all of GTH. No rows written means the last-snapshot age grows, and that is
    NORMAL during GTH, never a collector failure.

    (The status is global, not per-symbol: the probe symbol $SPX is not even ETH
    eligible, so its age ALWAYS grows through GTH.)
    """
    now = datetime(*ACTIVE, 7, 0, tzinfo=TZ)
    # Age of ~16h: yesterday's final snapshot. Would read "stale" in RTH.
    text, color = classify_collector_status(
        age_seconds=57_600, now_ct=now, has_data=True, last_ts=None,
    )
    assert "stale" not in text.lower()
    assert "not running" not in text.lower()
    assert color != "red" and color != "#c48b00"
    assert "gth" in text.lower()


def test_gth_with_no_data_at_all_is_also_not_a_fault():
    now = datetime(*ACTIVE, 7, 0, tzinfo=TZ)
    text, color = classify_collector_status(
        age_seconds=None, now_ct=now, has_data=False, last_ts=None,
    )
    assert "not running" not in text.lower()
    assert color == "gray"


def test_gth_reports_green_when_rows_are_actually_landing():
    """Leniency is not blindness: if snapshots ARE arriving, say so."""
    now = datetime(*ACTIVE, 7, 0, tzinfo=TZ)
    text, color = classify_collector_status(
        age_seconds=60, now_ct=now, has_data=True, last_ts=None,
    )
    assert color == "green"


def test_after_gth_the_full_universe_is_collected_so_stale_is_stale_again():
    """08:00-08:25 CT is inside BOTH the GTH session and the regular collection
    window -- the whole ~45-symbol universe is polled, so a growing age there is
    a genuine fault and must still read as one."""
    now = datetime(*ACTIVE, 8, 10, tzinfo=TZ)
    text, color = classify_collector_status(
        age_seconds=57_600, now_ct=now, has_data=True, last_ts=None,
    )
    assert "stale" in text.lower()
    assert color == "#c48b00"


def test_premarket_before_activation_still_shows_the_scheduled_start():
    """The GTH leniency must not leak backwards: before activation, 07:00 is
    still just 'collection has not started'."""
    now = datetime(*BEFORE, 7, 0, tzinfo=TZ)
    text, color = classify_collector_status(
        age_seconds=57_600, now_ct=now, has_data=True, last_ts=None,
    )
    assert f"{MARKET_OPEN.hour}:{MARKET_OPEN.minute:02d}" in text
    assert color == "gray"

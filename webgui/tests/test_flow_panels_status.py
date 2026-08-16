"""Tests for the flow panels' honesty fixes: the status pill, the axis units and
the tenor chip.

All three come from one defect — the panel HEADER was derived from the clock or
the live chain while the BODY is the last collected session. They agree during
market hours and diverge the moment the page outlives its session, which is
exactly when a reader most needs to be told.
"""
import datetime as dt
import re
from zoneinfo import ZoneInfo

import pytest

from pages.options import flow_panels as F

CT = ZoneInfo("America/Chicago")


def _ts(y, m, d, hh, mm):
    return int(dt.datetime(y, m, d, hh, mm, tzinfo=CT).timestamp())


# --------------------------------------------------------------- status pill
def test_a_fresh_sample_reads_live():
    now = _ts(2026, 8, 14, 10, 0)
    s = F.session_pill([now - 60], now=now)
    assert s["live"] is True and s["text"] == "LIVE · STREAMING"


def test_friday_data_on_saturday_names_the_session():
    """The reported bug: 'LIVE' at 9pm Saturday over Friday's series."""
    s = F.session_pill([_ts(2026, 8, 14, 15, 0)], now=_ts(2026, 8, 15, 20, 54))
    assert s["live"] is False
    assert s["text"] == "LAST SESSION · FRI 14 AUG"
    assert s["short"] == "FRI 14 AUG"


def test_after_the_close_on_the_same_day_says_closed_not_live():
    """Collection stops at 15:20 CT; the page keeps showing the session. Between
    the close and midnight it is neither streaming nor a previous session."""
    s = F.session_pill([_ts(2026, 8, 14, 15, 0)], now=_ts(2026, 8, 14, 19, 0))
    assert s["live"] is False and s["text"] == "SESSION CLOSED · 15:00 CT"


def test_the_boundary_is_the_documented_age():
    now = _ts(2026, 8, 14, 10, 0)
    assert F.session_pill([now - F.LIVE_MAX_AGE_SEC], now=now)["live"] is True
    assert F.session_pill([now - F.LIVE_MAX_AGE_SEC - 1], now=now)["live"] is False


def test_the_pill_reads_the_NEWEST_sample_not_the_first():
    now = _ts(2026, 8, 14, 10, 0)
    s = F.session_pill([now - 86_400, now - 30], now=now)
    assert s["live"] is True


@pytest.mark.parametrize("times", [None, [], ["x"], [None]])
def test_no_usable_timestamps_is_not_live(times):
    s = F.session_pill(times, now=_ts(2026, 8, 14, 10, 0))
    assert s["live"] is False and s["text"] == "NO DATA"


def test_a_stale_pill_drops_the_green_and_the_pulse():
    """The dot is an assertion of its own — a pulsing green light says 'arriving
    now' whatever the words beside it say."""
    live = F._live_pill("LIVE · STREAMING", True)
    stale = F._live_pill("LAST SESSION · FRI 14 AUG", False)
    assert "fx-pulse" in live and "fx-pulse" not in stale
    assert F.C["live"] in live and F.C["live"] not in stale


# ---------------------------------------------------------------- axis units
@pytest.mark.parametrize("value,text", [
    (0, "0"), (237, "237M"), (946, "946M"), (1_000, "1B"), (1_200, "1.2B"),
    (12_500, "12.5B"), (1_000_000, "1T"), (0.5, "0.5M"),
])
def test_axis_money_carries_its_scale(value, text):
    """A bare '946' could be dollars, thousands or millions — the axis has to say."""
    assert F.fmt_axis_money(value) == text


def test_axis_money_signed_uses_a_real_minus():
    assert F.fmt_axis_money(425, signed=True) == "+425M"
    assert F.fmt_axis_money(-90, signed=True) == "−90M"      # U+2212, not '-'
    assert "-" not in F.fmt_axis_money(-90, signed=True)


def test_axis_zero_stays_plain():
    """A signed or unit-suffixed zero reads as a measurement, not the origin."""
    assert F.fmt_axis_money(0, signed=True) == "0"
    assert F.fmt_axis_money(0.0) == "0"


def test_axis_money_survives_junk():
    for bad in (None, "x", float("nan"), float("inf")):
        assert F.fmt_axis_money(bad) == "—"


def test_whole_units_drop_the_decimal():
    """'1.0B' wastes a column of width for no information."""
    assert F.fmt_axis_money(1_000) == "1B"
    assert F.fmt_axis_money(2_000) == "2B"


# ------------------------------------------------------------ panel wiring
def _flow_rows(day, n=6):
    return [{"ts": _ts(2026, 8, day, 9, i), "spot": 7800.0 + i,
             "call_prem": (600 + i) * 1e6, "put_prem": (500 + i) * 1e6}
            for i in range(n)]


def test_divergence_panel_shows_the_session_not_a_live_claim():
    html, _payload = F.divergence_panel(
        _flow_rows(14), [], "$SPX", F.dte_label(0), "u")
    assert "LAST SESSION" in html or "SESSION CLOSED" in html
    assert "LIVE · STREAMING" not in html


def test_the_axis_labels_in_the_rendered_panel_carry_units():
    html, _ = F.divergence_panel(_flow_rows(14), [], "$SPX", F.dte_label(0), "u")
    ticks = re.findall(r">(\d[\d,.]*[MBT])<", html)
    assert ticks, "no unit-suffixed axis label found"


def test_the_net_premium_panel_is_titled_for_the_tab_that_opens_it():
    """It is reached by clicking 'Net Prem'; 'FLOW FIELD' named neither the
    metric nor the door."""
    rows = {"SPY": [(_ts(2026, 8, 14, 9, i), 10.0 + i) for i in range(5)]}
    html, _ = F.field_panel(rows, ["SPY"], {}, "dollars", "u")
    assert "NET PREMIUM" in html
    assert "FLOW FIELD" not in html


def test_the_net_premium_pill_dates_its_series_and_keeps_the_count():
    rows = {"SPY": [(_ts(2026, 8, 14, 9, i), 10.0 + i) for i in range(5)],
            "QQQ": [(_ts(2026, 8, 14, 9, i), -4.0) for i in range(5)]}
    html, _ = F.field_panel(rows, ["SPY", "QQQ"], {}, "dollars", "u")
    assert "2 SYMBOLS" in html
    assert "FRI 14 AUG" in html or "CT" in html


def test_skew_mode_keeps_its_percentage_axis():
    """Only the DOLLAR axis gained a scale suffix — a percentage already had one."""
    rows = {"SPY": [(_ts(2026, 8, 14, 9, i), 10.0 + i) for i in range(5)]}
    html, _ = F.field_panel(rows, ["SPY"], {}, "skew", "u")
    assert re.search(r">[+−]\d+%<", html)

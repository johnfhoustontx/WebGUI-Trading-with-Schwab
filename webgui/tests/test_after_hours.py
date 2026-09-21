"""The after-hours price warning on the public Rescue, Calculator and Simulator."""
import datetime as dt

from pages import copy
from pages.options import after_hours, calc_live, rescue_live, sim_live

WINDOW = {"start": "08:40", "end": "15:00"}


def test_no_warning_while_prices_are_live():
    assert after_hours.line(True, True, WINDOW) == ""


def test_no_warning_when_the_tool_does_not_run_after_hours():
    # The page says it is closed instead; a stale-price caveat would imply it runs.
    assert after_hours.line(False, False, WINDOW) == ""


def test_the_warning_after_hours_names_the_live_hours():
    text = after_hours.line(False, True, WINDOW)
    assert text == copy.AFTER_HOURS_PRICES.format(**WINDOW)
    assert "may be stale or incorrect" in text and "08:40–15:00 CT" in text


def test_state_reads_the_real_calendar():
    ct = after_hours.CT
    assert after_hours.state("tools_public", dt.datetime(2026, 9, 21, 16, 30, tzinfo=ct)) \
        == (False, True)
    assert after_hours.state("rescue_public", dt.datetime(2026, 9, 21, 10, 0, tzinfo=ct)) \
        == (True, True)


def test_state_failing_reads_as_live(monkeypatch):
    from shared import market_calendar

    def boom(*a, **k):
        raise RuntimeError("no calendar")
    monkeypatch.setattr(market_calendar, "in_window", boom)
    assert after_hours.state("tools_public") == (True, False)


def test_intros_name_the_live_hours_when_after_hours_is_on():
    for mod in (rescue_live, calc_live, sim_live):
        assert "Prices are live 08:40–15:00 CT" in mod.intro_text(WINDOW, True)
        assert "Prices are live" not in mod.intro_text(WINDOW, False)


def test_each_page_mounts_the_warning_for_its_own_window():
    import inspect
    assert rescue_live.WINDOW == "rescue_public"
    assert calc_live.WINDOW == "tools_public"
    for mod, name in ((rescue_live, "WINDOW"), (calc_live, "WINDOW"),
                      (sim_live, "_calc_live.WINDOW")):
        assert f"_after_hours.mount({name}, window)" in inspect.getsource(mod)

"""Tests for the page kit (pages/ui_kit.py) - the one look and behaviour."""
import datetime as dt

import pytest
from nicegui import ui

from pages import ui_kit as kit
from pages.options import theme

UTC = dt.timezone.utc


def _utc(day, h, m):
    return dt.datetime(2026, 9, day, h, m, tzinfo=UTC)


# -- freshness: the header's "Updated" stamp ----------------------------------
def test_nothing_published_is_waiting_never_a_time():
    assert kit.freshness(None, _utc(18, 15, 0)) == ("Waiting for data", "waiting")
    assert kit.freshness("not a time", _utc(18, 15, 0)) == ("Waiting for data", "waiting")


def test_freshness_reads_central_time():
    # 15:42 UTC on 2026-09-18 is 10:42 CDT
    assert kit.freshness("2026-09-18T15:42:00+00:00", _utc(18, 15, 50)) == \
        ("Updated 10:42 AM CT", "fresh")


def test_a_naive_stamp_is_utc_not_local():
    assert kit.freshness("2026-09-18T15:42:00", _utc(18, 15, 50))[0] == \
        "Updated 10:42 AM CT"


def test_a_stamp_from_another_day_names_the_day():
    assert kit.freshness("2026-09-17T20:15:00+00:00", _utc(18, 15, 0)) == \
        ("Updated Sep 17 3:15 PM CT", "fresh")


def test_past_the_threshold_is_stale():
    assert kit.freshness("2026-09-18T15:00:00+00:00", _utc(18, 15, 30),
                         stale_after_sec=600) == ("Stale · updated 10:00 AM CT", "stale")


def test_no_threshold_means_never_stale():
    """None = the view is not due to publish now (e.g. the scanner at night),
    so its age says nothing."""
    assert kit.freshness("2026-09-11T15:00:00+00:00", _utc(18, 15, 0))[1] == "fresh"


# -- toasts --------------------------------------------------------------------
def test_toast_args_one_position_and_a_type_always():
    assert kit.toast_args("ok", "Saved") == {
        "message": "Saved", "type": "positive", "position": "bottom",
        "timeout": 4000, "multi_line": False}
    assert kit.toast_args("warn", "x")["timeout"] == 8000
    assert kit.toast_args("error", "x")["type"] == "negative"
    assert kit.toast_args("info", "x" * 81)["multi_line"] is True


def test_an_unknown_toast_kind_is_an_error():
    with pytest.raises(ValueError):
        kit.toast_args("loud", "x")


# -- buttons -------------------------------------------------------------------
def test_button_kinds_map_to_the_theme_tokens():
    assert kit.button_classes("primary") == theme.BTN_PRIMARY
    assert kit.button_classes("secondary") == theme.BTN
    assert kit.button_classes("danger") == theme.BTN_DANGER
    assert kit.button_classes("quiet") == theme.BTN_QUIET
    assert kit.button_classes("danger_solid") == theme.BTN_DANGER_SOLID


def test_an_unknown_button_kind_is_an_error_not_a_default():
    with pytest.raises(ValueError):
        kit.button_classes("blue")


def test_preview_tokens_override_the_live_ones():
    toks = dict(theme._TOKENS, BTN_PRIMARY="bg-[#123456]")
    assert kit.button_classes("primary", toks) == "bg-[#123456]"


def test_button_is_sentence_case_and_not_quasar_blue():
    with ui.card():
        b = kit.button("Run scan", kind="primary", icon="play_arrow")
    assert b._props.get("no-caps") is True
    assert "bg-primary" not in b.classes
    assert set(theme.BTN_PRIMARY.split()) <= set(b.classes)


def test_an_icon_button_must_say_what_it_does():
    with pytest.raises(TypeError):
        kit.icon_button("delete")          # tooltip is required


def test_set_busy_spins_and_disables_then_releases():
    with ui.card():
        b = kit.button("Load", kind="primary")
    kit.set_busy(b)
    assert b._props.get("loading") is True and not b.enabled
    kit.set_busy(b, False)
    assert not b._props.get("loading") and b.enabled


def test_the_backstop_releases_a_button_whose_answer_never_came():
    with ui.card():
        b = kit.button("Load")
    kit.set_busy(b, timeout=0)
    assert b._kit_busy["timer"].active is True
    b._kit_busy["tick"]()
    assert b.enabled and not b._props.get("loading")
    assert b._kit_busy["timer"].active is False

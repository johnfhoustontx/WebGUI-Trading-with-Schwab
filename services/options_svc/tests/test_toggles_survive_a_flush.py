"""The two operator switches outlive Redis (audit AR-12).

Auto-close and the manual book's break-even lifecycle lived ONLY in Redis. A
flush put each back to its default with nothing on screen to say so: the
Settings page reads its own file and went on showing the choice the operator
had made, while the service acted on the default.
"""
import json

import pytest

from services.options_svc import handlers
from shared.bus import Bus
from shared.bus.client import reset_fake_bus
from shared.contracts.envelope import Command


@pytest.fixture
def bus():
    return Bus(fake=True)


def _flush():
    reset_fake_bus()
    return Bus(fake=True)


def test_the_test_suite_never_writes_the_real_file():
    import repo_paths
    assert handlers.TOGGLES_PATH != repo_paths.OPTIONS_TOGGLES


def test_auto_close_switched_off_stays_off_after_a_flush(bus):
    handlers.handle_command(bus, Command(type="set_autoclose", args={"enabled": False}))
    assert handlers.autoclose_enabled(bus) is False
    fresh = _flush()
    assert fresh.cache_get(handlers.CACHE_AUTOCLOSE_ENABLED) is None
    assert handlers.autoclose_enabled(fresh) is False


def test_the_lifecycle_switched_on_stays_on_after_a_flush(bus):
    handlers.handle_command(
        bus, Command(type="set_manual_paper_lifecycle", args={"enabled": True}))
    fresh = _flush()
    assert handlers.manual_paper_lifecycle_enabled(fresh) is True


def test_a_restored_switch_is_put_back_in_redis(bus):
    handlers.handle_command(bus, Command(type="set_autoclose", args={"enabled": False}))
    fresh = _flush()
    handlers.autoclose_enabled(fresh)
    assert fresh.cache_get(handlers.CACHE_AUTOCLOSE_ENABLED).payload == {"enabled": False}


def test_with_nothing_saved_the_defaults_are_what_they_were(bus):
    assert handlers.autoclose_enabled(bus) is True
    assert handlers.manual_paper_lifecycle_enabled(bus) is False


def test_redis_wins_over_the_file_when_it_has_an_answer(bus):
    handlers.handle_command(bus, Command(type="set_autoclose", args={"enabled": False}))
    bus.cache_set(handlers.CACHE_AUTOCLOSE_ENABLED, {"enabled": True})
    assert handlers.autoclose_enabled(bus) is True


def test_the_two_switches_do_not_overwrite_each_other(bus):
    handlers.handle_command(bus, Command(type="set_autoclose", args={"enabled": False}))
    handlers.handle_command(
        bus, Command(type="set_manual_paper_lifecycle", args={"enabled": True}))
    saved = json.loads(handlers.TOGGLES_PATH.read_text(encoding="utf-8"))
    assert saved == {"autoclose": False, "manual_paper_lifecycle": True}


@pytest.mark.parametrize("junk", ['{"autoclose": "no"}', "not json", "[]", ""])
def test_an_unusable_file_reads_as_no_saved_choice(bus, junk):
    handlers.TOGGLES_PATH.parent.mkdir(parents=True, exist_ok=True)
    handlers.TOGGLES_PATH.write_text(junk, encoding="utf-8")
    assert handlers.autoclose_enabled(bus) is True
    assert handlers.manual_paper_lifecycle_enabled(bus) is False


def test_a_file_that_cannot_be_written_does_not_stop_the_switch(bus, monkeypatch):
    def boom(*a, **k):
        raise OSError("read-only")

    monkeypatch.setattr(handlers.os, "replace", boom)
    handlers.handle_command(bus, Command(type="set_autoclose", args={"enabled": False}))
    assert handlers.autoclose_enabled(bus) is False        # Redis still has it

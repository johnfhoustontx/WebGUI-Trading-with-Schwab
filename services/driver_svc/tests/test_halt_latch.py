"""A same-day RISK halt is a stop, not a suggestion.

``enable`` did ``set_control(enabled=True, halted=False, reason=None)`` —
unconditionally clearing whatever latched the halt. For a manual STOP that is the
documented, intended behaviour (the confirm dialog literally says "Enable re-arms
it (clears the halt)"). For the AUTOMATIC daily-loss / drawdown halt it defeats
the guarantee: "stop the bleed for the day" became a soft flag any routine re-arm
cleared — and, because commands are Redis stream entries, a REPLAYED ``enable``
from earlier in the day cleared it with nobody touching a button.

The distinction is what caused the halt, not when:

* manual STOP  -> the user's own switch; ``enable`` may un-flip it.
* risk halt    -> a control; ``enable`` arms the driver for the next session but
                  leaves it latched. Clearing it TODAY takes a deliberate
                  ``{"clear_halt": true}``.

``halted_date`` already existed in the contract for the documented "re-arm next
day", and was WRITTEN but never READ — so a stale halt never expired either.
"""
import datetime as dt

import pytest

from services.driver_svc import handlers
from shared.bus import Bus


@pytest.fixture
def bus():
    return Bus(fake=True)


def _today():
    return dt.date.today().isoformat()


def _yesterday():
    return (dt.date.today() - dt.timedelta(days=1)).isoformat()


def _enable(bus, **args):
    from shared.contracts.envelope import Command
    handlers.handle_command(bus, Command(type="enable", args=args or {}))
    return handlers.read_control(bus)


# --- the risk halt holds ----------------------------------------------------

def test_a_same_day_risk_halt_survives_a_plain_enable(bus):
    handlers.set_control(bus, halted=True, reason="daily loss cap hit",
                         halted_date=_today())
    ctrl = _enable(bus)
    assert ctrl["halted"] is True, "a same-day risk halt must not be cleared by a re-arm"
    assert ctrl["reason"] == "daily loss cap hit", "and the reason must survive"


def test_enable_still_arms_the_driver_even_while_halted(bus):
    """The halt blocks trading; it should not also block re-arming for tomorrow."""
    handlers.set_control(bus, enabled=False, halted=True,
                         reason="daily loss cap hit", halted_date=_today())
    ctrl = _enable(bus)
    assert ctrl["enabled"] is True
    assert ctrl["halted"] is True


def test_a_deliberate_clear_halt_overrides_it(bus):
    """The operator override is preserved - it just has to be deliberate."""
    handlers.set_control(bus, halted=True, reason="daily loss cap hit",
                         halted_date=_today())
    ctrl = _enable(bus, clear_halt=True)
    assert ctrl["halted"] is False
    assert ctrl["reason"] is None


# --- what still clears freely ------------------------------------------------

def test_a_manual_stop_is_still_cleared_by_enable(bus):
    """Documented behaviour, and the /driver STOP dialog promises it."""
    handlers.set_control(bus, halted=True, reason=handlers.MANUAL_STOP_REASON,
                         halted_date=_today())
    ctrl = _enable(bus)
    assert ctrl["halted"] is False


def test_a_STALE_risk_halt_expires_on_enable(bus):
    """halted_date existed for exactly this and was never read, so a prior-day
    halt never expired."""
    handlers.set_control(bus, halted=True, reason="daily loss cap hit",
                         halted_date=_yesterday())
    ctrl = _enable(bus)
    assert ctrl["halted"] is False


def test_a_halt_with_no_date_is_treated_as_stale(bus):
    """Back-compat: a control written before halted_date was populated must not
    become permanently unclearable."""
    handlers.set_control(bus, halted=True, reason="daily loss cap hit")
    ctrl = _enable(bus)
    assert ctrl["halted"] is False


def test_enabling_when_nothing_is_halted_is_unchanged(bus):
    ctrl = _enable(bus)
    assert ctrl["enabled"] is True and ctrl["halted"] is False


# --- the replay case that motivated this ------------------------------------

def test_a_replayed_enable_cannot_clear_a_risk_halt(bus):
    """Commands are stream entries and a fresh consumer group replays the
    backlog, so an ``enable`` issued this morning can be re-delivered AFTER the
    loss halt latched. Re-running it must not resume trading."""
    handlers.set_control(bus, enabled=True, halted=False)
    _enable(bus)                                        # the original, replayed later
    handlers.set_control(bus, halted=True, reason="daily loss cap hit",
                         halted_date=_today())
    ctrl = _enable(bus)                                 # the replay lands
    assert ctrl["halted"] is True

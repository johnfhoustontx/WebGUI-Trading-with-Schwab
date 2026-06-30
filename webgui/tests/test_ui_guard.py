"""Tests for pages.ui_guard — dead-client callback guards + log filter."""
import asyncio
import logging

import pytest

from pages.ui_guard import (
    guard,
    guard_async,
    install_deleted_slot_log_filter,
    is_deleted_error,
    _DeletedSlotFilter,
)

_DELETED = "The client this element belongs to has been deleted."
_SLOT = "The parent element this slot belongs to has been deleted."
# The exact message raised by NiceGUI's Timer._run_in_loop on a disconnect race
# (timer.py line 90 → elements/timer.py _get_context → element.py parent_slot).
_TIMER_SLOT = "The parent slot of the element has been deleted."


def test_is_deleted_error():
    assert is_deleted_error(RuntimeError(_DELETED))
    assert is_deleted_error(RuntimeError(_SLOT))
    assert is_deleted_error(RuntimeError(_TIMER_SLOT))  # the timer-race message
    assert not is_deleted_error(RuntimeError("something else"))
    assert not is_deleted_error(ValueError(_DELETED))


def _record(exc):
    """A LogRecord carrying ``exc`` as exc_info (None → no exception)."""
    exc_info = (type(exc), exc, None) if exc is not None else None
    return logging.LogRecord("nicegui", logging.ERROR, __file__, 1, "boom", None, exc_info)


def test_log_filter_drops_deleted_slot_record():
    """The benign timer-race 'has been deleted' record is dropped (filter → False)."""
    assert _DeletedSlotFilter().filter(_record(RuntimeError(_TIMER_SLOT))) is False
    assert _DeletedSlotFilter().filter(_record(RuntimeError(_DELETED))) is False


def test_log_filter_keeps_real_errors():
    """A real error or a record with no exception logs normally (filter → True)."""
    assert _DeletedSlotFilter().filter(_record(RuntimeError("real bug"))) is True
    assert _DeletedSlotFilter().filter(_record(ValueError("other"))) is True
    assert _DeletedSlotFilter().filter(_record(None)) is True


def test_install_log_filter_is_idempotent():
    """Installing attaches exactly one filter to the 'nicegui' logger, even if called twice."""
    logger = logging.getLogger("nicegui")
    logger.filters = [f for f in logger.filters if not isinstance(f, _DeletedSlotFilter)]
    try:
        install_deleted_slot_log_filter()
        install_deleted_slot_log_filter()
        n = sum(isinstance(f, _DeletedSlotFilter) for f in logger.filters)
        assert n == 1
    finally:
        logger.filters = [f for f in logger.filters if not isinstance(f, _DeletedSlotFilter)]


def test_guard_swallows_deleted_error_returns_none():
    @guard
    def boom():
        raise RuntimeError(_DELETED)
    assert boom() is None


def test_guard_reraises_other_runtime_error():
    @guard
    def boom():
        raise RuntimeError("nope")
    with pytest.raises(RuntimeError):
        boom()


def test_guard_passes_through_return_value():
    @guard
    def ok(x):
        return x + 1
    assert ok(41) == 42


def test_guard_async_swallows_deleted_error():
    @guard_async
    async def boom():
        raise RuntimeError(_SLOT)
    assert asyncio.run(boom()) is None


def test_guard_async_reraises_other():
    @guard_async
    async def boom():
        raise RuntimeError("nope")
    with pytest.raises(RuntimeError):
        asyncio.run(boom())


def test_guard_async_awaits_and_returns():
    @guard_async
    async def ok():
        await asyncio.sleep(0)
        return 7
    assert asyncio.run(ok()) == 7

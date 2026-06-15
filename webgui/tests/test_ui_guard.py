"""Tests for pages.ui_guard — dead-client callback guards."""
import asyncio

import pytest

from pages.ui_guard import guard, guard_async, is_deleted_error

_DELETED = "The client this element belongs to has been deleted."
_SLOT = "The parent element this slot belongs to has been deleted."


def test_is_deleted_error():
    assert is_deleted_error(RuntimeError(_DELETED))
    assert is_deleted_error(RuntimeError(_SLOT))
    assert not is_deleted_error(RuntimeError("something else"))
    assert not is_deleted_error(ValueError(_DELETED))


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

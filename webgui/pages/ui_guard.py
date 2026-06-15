"""Guards for NiceGUI UI callbacks (timers + event handlers).

When the client that owns a page's elements is deleted — the browser tab
navigated away, closed, or reconnected — any callback that still tries to mutate
those widgets raises ``RuntimeError('... has been deleted.')`` (either "The client
this element belongs to has been deleted." or "The parent element this slot
belongs to has been deleted."). NiceGUI's own ``handle_exception`` then re-raises
on the dead slot, doubling the noise.

These decorators make a dead-client callback a clean no-op while re-raising every
other error. Apply them to the closures registered as ``ui.timer`` callbacks and
``on_click``/``.on(...)`` event handlers (timers and post-await handlers are the
only paths that can run after a client is gone; synchronous render-time code runs
while the client is alive).
"""
import functools


def is_deleted_error(exc: BaseException) -> bool:
    """True for the NiceGUI 'client/slot has been deleted' RuntimeError."""
    return isinstance(exc, RuntimeError) and "has been deleted" in str(exc)


def guard(fn):
    """Wrap a sync UI callback; swallow the client/slot-deleted RuntimeError."""
    @functools.wraps(fn)
    def _wrapped(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except RuntimeError as exc:
            if is_deleted_error(exc):
                return None
            raise
    return _wrapped


def guard_async(fn):
    """Wrap an async UI callback; swallow the client/slot-deleted RuntimeError."""
    @functools.wraps(fn)
    async def _wrapped(*args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        except RuntimeError as exc:
            if is_deleted_error(exc):
                return None
            raise
    return _wrapped

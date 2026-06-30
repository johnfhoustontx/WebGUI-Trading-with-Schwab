"""Guards for NiceGUI UI callbacks (timers + event handlers).

When the client that owns a page's elements is deleted — the browser tab
navigated away, closed, or reconnected — any callback that still tries to mutate
those widgets raises ``RuntimeError('... has been deleted.')`` ("The client this
element belongs to has been deleted.", "The parent element this slot belongs to
has been deleted.", or "The parent slot of the element has been deleted.").
NiceGUI's own ``handle_exception`` then re-raises on the dead slot, doubling the
noise.

These decorators make a dead-client callback a clean no-op while re-raising every
other error. Apply them to the closures registered as ``ui.timer`` callbacks and
``on_click``/``.on(...)`` event handlers (timers and post-await handlers are the
only paths that can run after a client is gone; synchronous render-time code runs
while the client is alive).

**The decorators can't cover one path.** NiceGUI's ``Timer._run_in_loop``
acquires its parent-slot context (``timer.py`` line 90) *before* the
``_should_stop()`` deleted-check on the next line, so on a client
disconnect/reconnect race the timer touches a deleted slot and raises
``RuntimeError('The parent slot of the element has been deleted.')`` *before* the
wrapped callback is ever invoked. That exception escapes the loop and NiceGUI logs
it via its default exception handler (``app._exception_handlers = [log.exception]``)
— the noisy traceback. The client is already gone (nothing to update), so it is
benign. ``install_deleted_slot_log_filter`` drops *only* that one log record at the
``nicegui`` logger; every other error still logs in full. Install it once at app
startup (see ``main.py``).
"""
import functools
import logging


def is_deleted_error(exc: BaseException | None) -> bool:
    """True for the NiceGUI 'client/slot has been deleted' RuntimeError."""
    return isinstance(exc, RuntimeError) and "has been deleted" in str(exc)


class _DeletedSlotFilter(logging.Filter):
    """Drop log records carrying a benign 'has been deleted' RuntimeError.

    See the module docstring: the only such records are the NiceGUI
    timer-disconnect race (and any other deleted-client/slot error that reaches
    the default ``log.exception`` handler). The client is gone, so the error is
    noise. Returns False (drop) for those records only; True (keep) for all else.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        exc = record.exc_info[1] if record.exc_info else None
        return not is_deleted_error(exc)


def install_deleted_slot_log_filter() -> None:
    """Attach ``_DeletedSlotFilter`` to the ``nicegui`` logger (idempotent).

    Filters on a logger run in ``Logger.handle`` before its handlers fire (and
    before propagation to ancestors), so a dropped record produces no output. Safe
    to call more than once — it adds the filter only if not already present.
    """
    logger = logging.getLogger("nicegui")
    if not any(isinstance(f, _DeletedSlotFilter) for f in logger.filters):
        logger.addFilter(_DeletedSlotFilter())


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

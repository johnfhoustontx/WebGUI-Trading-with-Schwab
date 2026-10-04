"""
paper_lock.py - One lock for every mutation of the paper books
Version: 1.0.0
Last Updated: 2026-10-03

The options service mutates the paper books (the Account in paper_account.db,
the Ledger in trades.db) from two kinds of thread: the scheduler's executor
threads (the hourly entry+manage cycle, the 15:05 settle pass, the captured
manage tick's Ledger refresh) and the command consumer's (a manual close, a
Rescue apply, a Paper click, a reset). Each mutation is several statements -
read the book, decide, write a row, move cash - and until 2026-10-03 nothing
kept two of them from interleaving (audit AR-02).

``BOOK_LOCK`` is that guard, and ``serialized`` is how an entry point takes it.

* ONE lock for both books, on purpose. ``run_manage_and_refresh`` touches both
  on one tick, and two locks would need an ordering rule someone has to keep.
* RE-ENTRANT, because entry points call each other (``apply_adjustment`` ->
  ``apply_roll`` -> ``paper_engine._close``).
* PROCESS-WIDE, which is enough: every writer is a thread of the one
  options_svc process. A second writing process would need a database-side
  guard instead - the status condition on ``close_position`` is the part of
  this that already holds across processes.

It is held across the network reads a cycle makes (a manage cycle reprices
every open position), so a command that arrives mid-cycle waits for the cycle.
That is the intended trade: the cycle is the only thing it can collide with.

Version 1.0.0 Changes:
- Initial implementation
"""
import functools
import threading

#############################################
# THE LOCK
#############################################

BOOK_LOCK = threading.RLock()

_MARK = "__book_serialized__"


def serialized(fn):
    """Decorator: run ``fn`` holding ``BOOK_LOCK``. Released on return or raise."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with BOOK_LOCK:
            return fn(*args, **kwargs)
    setattr(wrapper, _MARK, True)
    return wrapper


def is_serialized(fn) -> bool:
    """Whether ``fn`` was wrapped by :func:`serialized` (for the guard tests)."""
    return bool(getattr(fn, _MARK, False))

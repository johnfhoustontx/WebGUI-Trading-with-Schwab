"""One built scan for every visitor on the PUBLIC origin.

The Market Scanner's day union reaches about 4.5 MB of JSON by the close, and
the private page gives each tab its own parse and its own five thousand row
dicts. That is right for one owner. Published as Option Signals, the same page
is served to anyone, by a process with a memory cap: a copy per tab is a cost
a stranger controls. So on that origin every tab draws from ONE build, held
here.

A single slot, keyed on object IDENTITY. A version-gated read
(``bus_client.read_gated``, which is what the scanner's ``_shared_view`` and
the checklist's reads are) hands back the same object until a view's version
moves, so "these are the same objects" is exactly "nothing was republished" -
with no version probe to race the payload it describes (the trap
``bus_client.read_gated`` documents), and no deep compare of megabytes.

``max_age`` is the one thing identity cannot see: the Opportunity Board moves
every minute and feeds the checklist, and the private page re-stamps against
it on a fixed cadence (``checks_feed.TABLE_REFRESH_SEC``). A build older than
that is rebuilt whatever its inputs.

No widget and no bus: the caller reads, and passes what it read.
"""
import threading
import time

_lock = threading.Lock()
_slot = {"parts": None, "built": None, "at": 0.0}


def get(parts, build, *, max_age, now=time.monotonic):
    """``build()``'s result, shared: the SAME object for every caller whose
    ``parts`` are the same objects (``is``) as the last build's, until that
    build is ``max_age`` seconds old.

    Callers arriving together wait on one build - the lock is held across it,
    which is the point: a new scan would otherwise be built once per open tab,
    all at the same moment. **Blocking**; go through ``run.io_bound``.

    A build that raises is not remembered, and the slot is emptied, so the
    last good build is never served under inputs it was not built from.

    ⚠ READ-ONLY. Every visitor's tab holds what this returns. A caller that
    stamps, sorts or pops it changes what all of them draw."""
    parts = tuple(parts)
    with _lock:
        held = _slot["parts"]
        if (held is not None and len(held) == len(parts)
                and all(a is b for a, b in zip(held, parts))
                and now() - _slot["at"] < max_age):
            return _slot["built"]
        _slot.update(parts=None, built=None, at=0.0)
        built = build()
        _slot.update(parts=parts, built=built, at=now())
        return built


def reset():
    """Empty the slot (tests)."""
    with _lock:
        _slot.update(parts=None, built=None, at=0.0)

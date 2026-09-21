"""Live context for the checklist, read with version-gated memos.

Separate from ``checks`` so that module stays pure. The Opportunity Board view
updates every minute; the tables re-stamp on the REFRESH_VIEWS changing or a
5-minute timer (operator decision). The Trade detail panel reuses the context its
page last stamped the rows with, and reads one itself (off the loop) only when the
page holds none yet.

⚠ ``read_context`` BLOCKS: each view is read under a per-view lock held across a
Redis round-trip. Every caller runs it through ``run.io_bound``, never on the
event loop.

⚠ ``read_gated`` hands back the SAME payload object to every tab and thread
until the view's version moves, so every payload here - ``matrix`` (and the
board rows it indexes), ``regime``, ``calibration``, ``caps`` - is shared state.
No caller (``checks``, ``book_fit``, the pages) may mutate one; copy first.
"""
import threading

import bus_client

# NOTE view names carry no `cache:` prefix - bus_client.read* adds it.
MATRIX_VIEW = "options:matrix"
REGIME_VIEW = "sentiment:regime"
CALIBRATION_VIEW = "options:calibration"
CAPS_VIEW = "options:ledger_caps"
# The calibration view moves about once a day (the nightly rebuild); listing it
# here is what gets a new Track record line onto the tables that evening rather
# than whenever the Opportunity Board next moves.
REFRESH_VIEWS = (CAPS_VIEW, REGIME_VIEW, CALIBRATION_VIEW)
TABLE_REFRESH_SEC = 300.0

# Module-level on purpose, shared by every tab in this process (the same shape
# as detail.py's calibration memo): the webgui is single-user, and a version
# probe per view is all an unchanged read costs whoever asks next.
_memos = {MATRIX_VIEW: {}, REGIME_VIEW: {}, CALIBRATION_VIEW: {}, CAPS_VIEW: {}}
# One lock per view around the probe / read / memo store. Every tab's re-stamp
# runs on its own io_bound worker thread; unlocked, a thread that read an OLD
# payload could store its version and payload over a newer thread's (or, the
# two stores interleaving, pair the new version with the old payload), and the
# memo would then serve that payload until the view next moved.
_locks = {view: threading.Lock() for view in _memos}


def _gated(view):
    """One view's payload through its version-gated memo, or None. **Blocking** -
    it holds the view's lock across a Redis round-trip; reach it only through
    :func:`read_context` under ``run.io_bound``."""
    try:
        with _locks[view]:
            payload, _changed = bus_client.read_gated(view, _memos[view])
        return payload
    except Exception:  # noqa: BLE001 - a missing view costs its checks, not the page
        return None


def _index_board(board):
    """``{SYMBOL: row}`` from the board payload, or None when there is no board."""
    if not isinstance(board, dict):
        return None
    rows = board.get("rows")
    if not isinstance(rows, (list, tuple)):
        rows = []
    out = {}
    for r in rows:
        if not isinstance(r, dict):
            continue
        sym = r.get("symbol")
        if isinstance(sym, str) and sym.strip():
            out[sym.strip().upper()] = r
    return out


def read_context(caps=True):
    """Every view as it is, and None when it is cold - never an empty stand-in:
    ``checks`` marks a None input as a missing view, which keeps the summary out
    of "Clear" (an empty dict would let it read Clear with a check missing).

    ``caps=False`` does not READ the owner's ledger caps at all and passes None:
    for a caller that draws no Paper book line (the public Calculator's rating,
    whose candidates carry ``_allow_paper`` False, so ``checks._book`` never
    reads them). Every private caller takes the default.

    **Blocking** (four locked Redis reads): call it through ``run.io_bound``,
    never on the event loop."""
    return {"matrix": _index_board(_gated(MATRIX_VIEW)), "regime": _gated(REGIME_VIEW),
            "calibration": _gated(CALIBRATION_VIEW),
            "caps": _gated(CAPS_VIEW) if caps else None}


def checks_for(row, ctx):
    """The checklist for one row against a :func:`read_context` result."""
    if not isinstance(row, dict):
        return []
    from . import checks
    ctx = ctx if isinstance(ctx, dict) else {}
    sym = row.get("symbol")
    sym = sym.strip().upper() if isinstance(sym, str) else ""
    board = ctx.get("matrix")
    matrix_row = board.get(sym) if isinstance(board, dict) and sym else None
    return checks.build_checks(row, matrix_row, ctx.get("regime"),
                               ctx.get("calibration"), ctx.get("caps"))

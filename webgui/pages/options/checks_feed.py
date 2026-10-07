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

    ⚠ On the PUBLIC origin the caps are never read, whatever the caller asks
    for. The Market Scanner is published there (Option Signals), and its Trade
    detail panel reads a context of its own when the page holds none yet, so
    the refusal lives here rather than at each caller.

    **Blocking** (four locked Redis reads): call it through ``run.io_bound``,
    never on the event loop."""
    import shell as _shell               # lazy: this module stays light to import
    caps = caps and not _shell.is_public()
    return {"matrix": _index_board(_gated(MATRIX_VIEW)), "regime": _gated(REGIME_VIEW),
            "calibration": _gated(CALIBRATION_VIEW),
            "caps": _gated(CAPS_VIEW) if caps else None}


def quotes_withheld():
    """True on the PUBLIC origin while the site's quotes switch is off.

    ``config/finder_public.toml [display] show_leg_quotes`` is the one switch
    for figures read off a contract's own quote, off until Schwab's
    redistribution terms are settled (roadmap decision D2). The public
    Calculator and Simulator draw no delta under it; the Market Scanner,
    published as Option Signals, follows the same switch rather than growing
    one of its own. Always False on the private app.

    ⚠ It withholds what is NAMED here and in ``detail._CONTRACT_GREEKS``, not
    everything a quote feeds. A credit spread's probability of profit is one
    minus its short delta, and a single-leg row's debit is that option's own
    price; the public Finder prints both, and so does Option Signals. Whether
    they should follow this switch too is the owner's open decision (D2)."""
    import shell as _shell               # lazy, as in read_context
    if not _shell.is_public():
        return False
    from shared import public_scan
    return not public_scan.show_leg_quotes()


# What a candidate carries that is read off a contract's own bid and ask.
_QUOTE_FIELDS = ("friction_pct",)


def checks_for(row, ctx):
    """The checklist for one row against a :func:`read_context` result.

    While :func:`quotes_withheld`, the row is judged without its bid-ask
    friction, so the cost-to-trade line reads as not measured: the round trip
    as a share of the credit, printed beside the credit, is the spread's
    bid-ask width. A copy - the row is the caller's."""
    if not isinstance(row, dict):
        return []
    if quotes_withheld():
        row = {k: v for k, v in row.items() if k not in _QUOTE_FIELDS}
    from . import checks
    ctx = ctx if isinstance(ctx, dict) else {}
    sym = row.get("symbol")
    sym = sym.strip().upper() if isinstance(sym, str) else ""
    board = ctx.get("matrix")
    matrix_row = board.get(sym) if isinstance(board, dict) and sym else None
    return checks.build_checks(row, matrix_row, ctx.get("regime"),
                               ctx.get("calibration"), ctx.get("caps"))

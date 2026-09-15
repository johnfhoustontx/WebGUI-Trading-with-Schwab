"""Live context for the checklist, read with version-gated memos.

Separate from ``checks`` so that module stays pure. The Opportunity Board view
updates every minute; the tables re-stamp on the REFRESH_VIEWS changing or a
5-minute timer (operator decision), while the detail panel and the Paper dialog
call ``read_context`` on every open.
"""
import bus_client

# NOTE view names carry no `cache:` prefix - bus_client.read* adds it.
MATRIX_VIEW = "options:matrix"
REGIME_VIEW = "sentiment:regime"
CALIBRATION_VIEW = "options:calibration"
CAPS_VIEW = "options:ledger_caps"
REFRESH_VIEWS = (CAPS_VIEW, REGIME_VIEW)
TABLE_REFRESH_SEC = 300.0

# Module-level on purpose, shared by every tab in this process (the same shape
# as detail.py's calibration memo): the webgui is single-user, and a version
# probe per view is all an unchanged read costs whoever asks next.
_memos = {MATRIX_VIEW: {}, REGIME_VIEW: {}, CALIBRATION_VIEW: {}, CAPS_VIEW: {}}


def _gated(view):
    try:
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


def read_context():
    """Every view as it is, and None when it is cold - never an empty stand-in:
    ``checks`` marks a None input as a missing view, which keeps the summary out
    of "Clear" (an empty dict would let it read Clear with a check missing)."""
    return {"matrix": _index_board(_gated(MATRIX_VIEW)), "regime": _gated(REGIME_VIEW),
            "calibration": _gated(CALIBRATION_VIEW), "caps": _gated(CAPS_VIEW)}


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

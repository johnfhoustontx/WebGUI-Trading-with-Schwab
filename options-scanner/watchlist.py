"""
Options Scanner - Scan Watchlist
Version: 1.0.0
Last Updated: 2026-06-07

Version 1.0.0 Changes:
- Initial implementation

Single source of truth for the scanned symbol universe: the three benchmark
indices unioned with Column A of data/Top 20.xlsx (deduped, order-preserving).
Live-read with an mtime cache and a safe fallback to the base indices so a scan
never crashes over the watchlist.
"""

import logging
from pathlib import Path

log = logging.getLogger(__name__)

#############################################
# CONFIGURATION
#############################################

BASE_SYMBOLS = ["$SPX", "SPY", "QQQ"]
WATCHLIST_PATH = Path(__file__).parent / "data" / "Top 20.xlsx"

_cache = {"mtime": None, "symbols": None}

# Last watchlist load problem: None when the file read cleanly, else a short
# human-readable message describing why we fell back to BASE_SYMBOLS.
LAST_LOAD_ERROR = None


def get_load_error():
    """Return the last watchlist load error message, or None if it loaded
    cleanly. Reflects the most recent read_symbols/get_scan_symbols call."""
    return LAST_LOAD_ERROR

#############################################
# WATCHLIST READ
#############################################

def _read_column_a(path):
    """Read Column A of the first worksheet as a list of normalized symbols."""
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb.worksheets[0]
        out = []
        for (cell,) in ws.iter_rows(min_col=1, max_col=1, values_only=True):
            if cell is None:
                continue
            s = str(cell).strip().upper()
            if s:
                out.append(s)
        return out
    finally:
        wb.close()


def read_symbols(path=WATCHLIST_PATH):
    """Return BASE_SYMBOLS unioned with Column A of the workbook (deduped,
    order-preserving). On any failure, return BASE_SYMBOLS only."""
    global LAST_LOAD_ERROR
    path = Path(path)
    try:
        col_a = _read_column_a(path)
    except FileNotFoundError:
        LAST_LOAD_ERROR = f"Watchlist file not found: {path.name}"
        return list(BASE_SYMBOLS)
    except Exception as e:  # corrupt / locked / openpyxl error
        LAST_LOAD_ERROR = f"Watchlist read failed: {e}"
        log.warning("watchlist read failed (%s); using base symbols only", e)
        return list(BASE_SYMBOLS)

    LAST_LOAD_ERROR = None
    result = list(BASE_SYMBOLS)
    seen = set(result)
    for s in col_a:
        if s not in seen:
            seen.add(s)
            result.append(s)
    return result


def get_scan_symbols():
    """Cached accessor used by the scanners. Re-reads only when the file mtime
    changes; falls back to base symbols if the file is unavailable."""
    global LAST_LOAD_ERROR
    try:
        mtime = WATCHLIST_PATH.stat().st_mtime
    except OSError:
        LAST_LOAD_ERROR = f"Watchlist file not accessible: {WATCHLIST_PATH.name}"
        return list(BASE_SYMBOLS)
    if _cache["mtime"] != mtime or _cache["symbols"] is None:
        _cache["symbols"] = read_symbols(WATCHLIST_PATH)
        _cache["mtime"] = mtime
    return list(_cache["symbols"])

"""Rotating file logging for the webgui (Tier 1's equivalent of the services' R3a).

``main.py`` logs through ``logging.getLogger("webgui")`` throughout, but nothing
installed a handler, so output went to the console only - lost when a Windows
Terminal tab closes, and lost on exit for a manual ``python webgui\\main.py``.
Only ``start_all_wt.bat nowindow`` captured it, and only by shell redirection.

Deliberately a small local copy of ``services/_scaffold._install_file_logging``
rather than an import of it: **Tier 1's import allow-list does not include
`services.*`**, and that helper drags in FastAPI and the Bus. The duplication is
~30 lines of stdlib boilerplate with no shared state, which is the cheaper side
of the trade.

Writes to ``logs/webgui.log`` at the repo root - the same directory the
nowindow launcher and ``tools/restart_one.bat`` already redirect into, so there
is one place to look.
"""
import logging
import pathlib
import sys
from logging.handlers import RotatingFileHandler

_MAX_BYTES = 10 * 1024 * 1024   # ~10 MB per file
_BACKUP_COUNT = 5               # ~50 MB ceiling, mirroring the services
_FORMAT = "%(asctime)s [%(levelname)s] %(name)s - %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_INSTALLED: set[str] = set()


def _under_pytest() -> bool:
    return "pytest" in sys.modules


def install_file_logging(log_root=None, *, force: bool = False):
    """Attach a rotating file handler to the ROOT logger. Returns the path, or None.

    Idempotent, and a **no-op under pytest** unless ``force`` - a test run must
    never create ``logs/`` files. Never raises: an unwritable log directory must
    not be able to stop the GUI from starting, so it degrades to console-only.
    """
    if not force and _under_pytest():
        return None
    log_dir = pathlib.Path(log_root) if log_root is not None else _REPO_ROOT / "logs"
    log_path = log_dir / "webgui.log"
    if "webgui" in _INSTALLED:
        return log_path
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            log_path, maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT,
            encoding="utf-8")
        handler.setFormatter(logging.Formatter(_FORMAT, datefmt=_DATEFMT))
        root = logging.getLogger()
        if root.level > logging.INFO or root.level == logging.NOTSET:
            root.setLevel(logging.INFO)
        root.addHandler(handler)
        _INSTALLED.add("webgui")
        logging.getLogger("webgui").info("file logging -> %s", log_path)
        return log_path
    except Exception:   # noqa: BLE001 - logging setup must never crash startup
        return None

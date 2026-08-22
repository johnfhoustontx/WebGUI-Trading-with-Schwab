"""The webgui's own rotating log file.

`webgui/main.py` logs through `logging.getLogger("webgui")` all over, but nothing
ever installed a handler - output went to the console only. With the WT-tabs
launcher that means it is lost the moment the tab closes, and with a manual
`python webgui\\main.py` it is lost on exit; only the nowindow launcher captured
it, by shell redirection. The six services have had rotating file logs since R3a
(`services/_scaffold._install_file_logging`); this is the Tier-1 equivalent.

Deliberately a small local copy rather than importing `services._scaffold`:
Tier 1's import allow-list does not include `services.*`, and that helper drags
in FastAPI and the Bus.
"""
import logging
import pathlib
from logging.handlers import RotatingFileHandler

import logging_setup


def _clean(root):
    for h in list(root.handlers):
        if isinstance(h, RotatingFileHandler):
            root.removeHandler(h)
            h.close()


def test_installs_a_rotating_handler_and_returns_the_path(tmp_path):
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        path = logging_setup.install_file_logging(log_root=tmp_path, force=True)
        assert path == tmp_path / "webgui.log"
        assert any(isinstance(h, RotatingFileHandler) for h in root.handlers)

        logging.getLogger("webgui").warning("hello from the webgui")
        for h in root.handlers:
            h.flush()
        assert "hello from the webgui" in path.read_text(encoding="utf-8")
    finally:
        _clean(root)
        root.handlers[:] = before


def test_is_idempotent(tmp_path):
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        logging_setup.install_file_logging(log_root=tmp_path, force=True)
        n = sum(isinstance(h, RotatingFileHandler) for h in root.handlers)
        logging_setup.install_file_logging(log_root=tmp_path, force=True)
        assert sum(isinstance(h, RotatingFileHandler) for h in root.handlers) == n
    finally:
        _clean(root)
        logging_setup._INSTALLED.clear()
        root.handlers[:] = before


def test_no_op_under_pytest_unless_forced(tmp_path):
    """A test run must never create logs/ files - which is exactly the situation
    this test is running in."""
    assert logging_setup.install_file_logging(log_root=tmp_path) is None
    assert not list(tmp_path.iterdir())


def test_never_raises_when_the_directory_is_unusable(tmp_path):
    """Logging setup must not be able to stop the GUI from starting."""
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")
    assert logging_setup.install_file_logging(
        log_root=blocker / "nested", force=True) is None


def test_tier1_import_purity():
    """It must not reach into services/ or pull a heavy dependency."""
    src = pathlib.Path(logging_setup.__file__).read_text(encoding="utf-8")
    for banned in ("services", "fastapi", "shared.bus", "nicegui"):
        assert f"import {banned}" not in src and f"from {banned}" not in src

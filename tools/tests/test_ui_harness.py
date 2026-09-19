"""The harness must import without starting a server (tests import it)."""
import importlib.util
import pathlib


def test_harness_imports_without_starting_a_server():
    path = pathlib.Path(__file__).resolve().parents[1] / "ui_harness.py"
    spec = importlib.util.spec_from_file_location("ui_harness", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)            # __name__ != "__main__": no ui.run
    assert callable(mod.main)

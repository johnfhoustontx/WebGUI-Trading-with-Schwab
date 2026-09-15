"""Tier 1 may import shared.book_caps because it is pure math.

Run in a FRESH interpreter so a transitive import cannot hide behind a module
some earlier test already loaded.
"""
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]

PROBE = r"""
import sys
sys.path.insert(0, r"%s")
before = set(sys.modules)
import shared.book_caps
new = set(sys.modules) - before
bad = sorted(m for m in new if m.split(".")[0] in {
    "sqlite3", "redis", "fakeredis", "requests", "pandas", "numpy", "nicegui",
    "services", "paper_trader", "paper_concentration", "config_paper",
    "scanner_engine", "tomllib"} or m in {"shared.sectors", "shared.scanner_config",
                                         "shared.config_toml", "repo_paths"})
print("BAD:" + ",".join(bad))
""" % REPO


def test_book_caps_imports_nothing_tier1_forbids():
    out = subprocess.run([sys.executable, "-c", PROBE], capture_output=True,
                         text=True, check=True).stdout
    assert out.strip() == "BAD:", out

"""Tier 1 may import shared.quotes because it is pure arithmetic over a quote.

The same pinned-set rule as test_numeric_tier1.py: importing shared.quotes may
load nothing outside EXPECTED, and must really load shared.quotes. Run in a
FRESH interpreter so a transitive import cannot hide behind a module an earlier
test already loaded. `math` may be absent: an interpreter can preload it.
"""
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]

# If shared.quotes legitimately gains an import, this set and CLAUDE.md's
# allow-list sentence for shared.quotes change together.
EXPECTED = {"math", "__future__", "shared", "shared.numeric", "shared.quotes"}
REQUIRED = {"shared.quotes"}

PROBE = r"""
import sys
sys.path.insert(0, r"%s")
before = set(sys.modules)
import shared.quotes
new = set(sys.modules) - before
print("NEW:" + ",".join(sorted(new)))
""" % REPO


def test_quotes_imports_only_its_pinned_set():
    r = subprocess.run([sys.executable, "-c", PROBE], cwd=REPO,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    line = r.stdout.strip()
    assert line.startswith("NEW:"), r.stdout
    new = set(filter(None, line[len("NEW:"):].split(",")))
    assert new <= EXPECTED, sorted(new - EXPECTED)
    assert REQUIRED <= new, sorted(REQUIRED - new)

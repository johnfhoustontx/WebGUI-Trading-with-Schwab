"""Tier 1 may import shared.book_caps because it is pure math.

The rule is an EXACT import set, not a deny-list: importing shared.book_caps
must load precisely the modules in EXPECTED and nothing else, so any new
import - forbidden or merely unreviewed - fails here and has to be argued for.

Run in a FRESH interpreter so a transitive import cannot hide behind a module
some earlier test already loaded.
"""
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]

# repo_paths is on the Tier-1 allow-list, but book_caps must not need it - it
# never reads config. If book_caps legitimately gains an import, this set and
# CLAUDE.md's allow-list sentence for shared.book_caps change together.
EXPECTED = {"math", "shared", "shared.book_caps", "shared.driver_policy"}

PROBE = r"""
import sys
sys.path.insert(0, r"%s")
before = set(sys.modules)
import shared.book_caps
new = set(sys.modules) - before
print("NEW:" + ",".join(sorted(new)))
""" % REPO


def test_book_caps_imports_exactly_its_pinned_set():
    r = subprocess.run([sys.executable, "-c", PROBE], cwd=REPO,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    line = r.stdout.strip()
    assert line.startswith("NEW:"), r.stdout
    new = set(filter(None, line[len("NEW:"):].split(",")))
    assert new == EXPECTED, sorted(new ^ EXPECTED)

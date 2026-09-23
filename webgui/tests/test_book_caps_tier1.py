"""Tier 1 may import shared.book_caps because it is pure math.

The rule is a pinned import set, not a deny-list: importing shared.book_caps
may load nothing outside EXPECTED, so any new import - forbidden or merely
unreviewed - fails here and has to be argued for. It must also really load
shared.book_caps, so the probe cannot pass vacuously.
`math` may legitimately be ABSENT from what the import loads: an interpreter
can preload it at startup (a stdlib module some CPython builds import during
site setup), and then it is already in sys.modules before the probe runs.

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
# `math` is allowed but not required: an interpreter that preloads it at
# startup loads it before the probe's snapshot, so it never shows up as new.
EXPECTED = {"math", "shared", "shared.book_caps"}
REQUIRED = {"shared.book_caps"}

PROBE = r"""
import sys
sys.path.insert(0, r"%s")
before = set(sys.modules)
import shared.book_caps
new = set(sys.modules) - before
print("NEW:" + ",".join(sorted(new)))
""" % REPO


def test_book_caps_imports_only_its_pinned_set():
    r = subprocess.run([sys.executable, "-c", PROBE], cwd=REPO,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    line = r.stdout.strip()
    assert line.startswith("NEW:"), r.stdout
    new = set(filter(None, line[len("NEW:"):].split(",")))
    assert new <= EXPECTED, sorted(new - EXPECTED)
    assert REQUIRED <= new, sorted(REQUIRED - new)

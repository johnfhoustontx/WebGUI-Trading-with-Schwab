"""shared.public_tools: the public Calculator/Simulator requests."""
import datetime as dt
import pathlib
import subprocess
import sys

import pytest  # noqa: F401  (used by the builder tests)

from shared import public_tools as pt

REPO = pathlib.Path(__file__).resolve().parents[2]
TODAY = dt.date(2026, 9, 21)
EXP = "2026-10-16"


def test_two_streams_neither_the_owners_nor_rescues():
    from shared import public_rescue, public_scan
    assert pt.TOOLS_STREAM == "cmd:tools_public"
    assert pt.MATH_STREAM == "cmd:tools_public_math"
    assert len({pt.TOOLS_STREAM, pt.MATH_STREAM, public_rescue.STREAM,
                public_scan.STREAM, "cmd:options"}) == 5


def test_every_request_kind_is_on_exactly_one_stream():
    assert set(pt.TOOLS_KINDS) & set(pt.MATH_KINDS) == set()
    assert set(pt.TOOLS_KINDS) == {"chain", "expiry", "rate", "sim_snapshot",
                                   "sim_expiry"}
    assert set(pt.MATH_KINDS) == {"price", "iv", "sweep"}


def test_the_shipped_file_matches_the_defaults():
    import tomllib
    shipped = tomllib.loads((REPO / "config" / "tools_public.toml")
                            .read_text(encoding="utf-8"))
    for section, values in pt.DEFAULTS.items():
        assert shipped[section] == values, section


EXPECTED = {"repo_paths", "shared", "shared.config_toml", "shared.public_rescue",
            "shared.public_tools", "shared.symbols", "tzdata"}

PROBE = r"""
import sys
sys.path.insert(0, r"%s")
before = set(sys.modules)
import shared.public_tools
new = set(sys.modules) - before
print("NEW:" + ",".join(sorted(m for m in new
                              if m.split(".")[0] not in sys.stdlib_module_names)))
""" % REPO


def test_public_tools_imports_only_config_and_validators():
    r = subprocess.run([sys.executable, "-c", PROBE], cwd=REPO,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    new = set(filter(None, r.stdout.strip()[len("NEW:"):].split(",")))
    stray = {m for m in new if m not in EXPECTED and m.split(".")[0] != "tzdata"}
    assert not stray, sorted(stray)

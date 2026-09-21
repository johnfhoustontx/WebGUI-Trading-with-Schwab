"""shared.public_scan: the one request the public origin may make, and its config."""
import pathlib
import subprocess
import sys

import pytest

from shared import public_scan as ps

REPO = pathlib.Path(__file__).resolve().parents[2]


# ── the request ─────────────────────────────────────────────────────────────

def test_the_stream_is_its_own_and_never_the_options_stream():
    """The live ACL user's write selector names exactly this key. cmd:options
    carries paper creates, rescue applies and paid Claude calls."""
    assert ps.STREAM == "cmd:finder_public"
    assert ps.STREAM != "cmd:options"


def test_a_request_carries_one_validated_symbol_and_nothing_else():
    cmd = ps.request_command(" spy ")
    assert cmd == {"type": ps.COMMAND_TYPE, "args": {"symbol": "SPY"}}


@pytest.mark.parametrize("raw", ["", None, "spy; flushall", "TOOLONGSYM",
                                 "../x", "a b", "*", "SP Y"])
def test_an_invalid_symbol_builds_no_request(raw):
    assert ps.request_command(raw) is None


def test_an_index_symbol_is_accepted():
    assert ps.request_command("$spx")["args"]["symbol"] == "$SPX"


def test_the_result_key_is_per_symbol_and_never_the_private_slot():
    assert ps.result_key("spy") == "cache:options:swing_pub:SPY"
    assert ps.result_key("SPY") != "cache:options:swing"
    assert ps.result_view("SPY") == "options:swing_pub:SPY"
    assert ps.STATUS_KEY == "cache:options:finder_public_status"
    assert ps.STATUS_VIEW == "options:finder_public_status"


def test_every_outcome_has_words():
    """The page words each outcome; an outcome without words is a blank."""
    for code in ps.OUTCOMES:
        assert ps.OUTCOME_TEXT[code].strip()
    assert set(ps.OUTCOME_TEXT) == set(ps.OUTCOMES)


# ── the config ──────────────────────────────────────────────────────────────

def test_the_scan_pin_is_the_roadmaps():
    pin = ps.scan_pin()
    assert (pin["dte_min"], pin["dte_max"]) == (0, 90)
    assert (pin["put_d_min"], pin["put_d_max"]) == (-0.20, -0.10)
    assert (pin["call_d_min"], pin["call_d_max"]) == (0.10, 0.20)
    assert pin["min_cr_fraction"] == 0.10
    assert set(pin) == {"dte_min", "dte_max", "put_d_min", "put_d_max",
                        "call_d_min", "call_d_max", "min_cr_fraction"}


def test_limits_have_the_shipped_values():
    lim = ps.limits()
    assert lim == {"result_ttl_min": 15, "dedup_sec": 60, "negative_ttl_min": 240,
                   "max_wait_sec": 180, "daily_budget": 200,
                   "result_keep_hours": 24}


def test_a_visitor_gets_ten_scans_an_hour():
    assert ps.scans_per_hour() == 10


def test_leg_quotes_ship_off_until_d2_is_settled():
    assert ps.show_leg_quotes() is False


def test_a_bad_value_degrades_to_the_default(monkeypatch):
    monkeypatch.setattr(ps, "load", lambda: {"limits": {"daily_budget": "lots",
                                                        "dedup_sec": -5}})
    lim = ps.limits()
    assert lim["daily_budget"] == 200 and lim["dedup_sec"] == 60


def test_the_shipped_file_matches_the_defaults():
    """The TOML overrides defaults; shipped equal, so a missing file changes nothing."""
    import tomllib
    shipped = tomllib.loads((REPO / "config" / "finder_public.toml")
                            .read_text(encoding="utf-8"))
    for section, values in ps.DEFAULTS.items():
        assert shipped[section] == values, section


# ── Tier 1 may import it ────────────────────────────────────────────────────

# The project and third-party modules importing it may load. Stdlib is free;
# anything else - an engine, the bus, redis, requests - fails here. tzdata is
# zoneinfo's data package on hosts without a system tz database (Windows).
EXPECTED = {"repo_paths", "shared", "shared.config_toml", "shared.public_scan",
            "shared.symbols", "tzdata"}

PROBE = r"""
import sys
sys.path.insert(0, r"%s")
before = set(sys.modules)
import shared.public_scan
new = set(sys.modules) - before
print("NEW:" + ",".join(sorted(m for m in new
                              if m.split(".")[0] not in sys.stdlib_module_names)))
""" % REPO


def test_public_scan_imports_only_config_and_the_symbol_allow_list():
    """Tier 1 imports this module, so its import set is pinned: no engine, no
    bus, no Schwab. Run in a FRESH interpreter so a transitive import cannot
    hide behind a module an earlier test already loaded."""
    r = subprocess.run([sys.executable, "-c", PROBE], cwd=REPO,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    new = set(filter(None, r.stdout.strip()[len("NEW:"):].split(",")))
    stray = {m for m in new if m not in EXPECTED and m.split(".")[0] != "tzdata"}
    assert not stray, sorted(stray)
    assert {"shared.public_scan", "shared.symbols"} <= new

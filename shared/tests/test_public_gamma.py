"""shared.public_gamma: the public Gamma page's one request, its keys, its config."""
import pathlib
import subprocess
import sys

import pytest

from shared import public_gamma as pg

REPO = pathlib.Path(__file__).resolve().parents[2]


# ── the request ─────────────────────────────────────────────────────────────

def test_the_stream_is_its_own():
    """The live ACL user's write selector names exactly this key."""
    from shared import public_rescue, public_scan, public_tools
    assert pg.STREAM == "cmd:gamma_public"
    assert pg.STREAM not in ("cmd:options", public_scan.STREAM, public_rescue.STREAM,
                             public_tools.TOOLS_STREAM, public_tools.MATH_STREAM)


def test_a_request_carries_one_validated_symbol_and_nothing_else():
    assert pg.request_command(" nvda ") == {"type": pg.COMMAND_TYPE,
                                            "args": {"symbol": "NVDA"}}
    assert pg.request_command("$spx")["args"]["symbol"] == "$SPX"


@pytest.mark.parametrize("raw", ["", None, "spy; flushall", "TOOLONGSYM",
                                 "../x", "a b", "*"])
def test_an_invalid_symbol_builds_no_request(raw):
    assert pg.request_command(raw) is None


def test_the_keys_are_written_for_the_public_page():
    assert pg.STATUS_KEY == "cache:options:gamma_public_status"
    assert pg.SYMBOLS_KEY == "cache:options:gamma_pub_symbols"
    assert pg.SYMBOLS_KEY != "cache:options:gamma_symbols"     # never the private list


def test_the_page_has_no_term_view():
    """Decision D6: a Term grid costs extra Schwab calls for most names."""
    assert pg.HISTORY_VIEWS == ("GEX", "Charm", "DEX", "Vanna")
    assert "Term" not in pg.HISTORY_VIEWS


def test_every_outcome_has_words():
    for code in pg.OUTCOMES:
        assert pg.OUTCOME_TEXT[code].strip()
    assert set(pg.OUTCOME_TEXT) == set(pg.OUTCOMES)


# ── the config ──────────────────────────────────────────────────────────────

def test_the_shipped_values():
    assert pg.hot() == {"cap": 8, "lease_min": 15, "keep_min": 30}
    assert pg.renew_min() == 5
    assert pg.max_wait_sec() == 120
    assert pg.picks_per_hour() == 30


def test_the_page_renews_before_the_lease_runs_out():
    assert pg.renew_min() < pg.hot()["lease_min"]


def test_a_cap_of_zero_is_allowed_and_turns_hot_symbols_off(monkeypatch):
    monkeypatch.setattr(pg, "load", lambda: {"hot": {"cap": 0}})
    assert pg.hot()["cap"] == 0


def test_a_bad_value_degrades_to_the_default(monkeypatch):
    monkeypatch.setattr(pg, "load", lambda: {"hot": {"cap": "lots", "lease_min": 0,
                                                     "keep_min": True},
                                             "limits": {"max_wait_sec": 1}})
    assert pg.hot() == {"cap": 8, "lease_min": 15, "keep_min": 30}
    assert pg.max_wait_sec() == 120


def test_the_shipped_file_matches_the_defaults():
    import tomllib
    shipped = tomllib.loads((REPO / "config" / "gamma_public.toml")
                            .read_text(encoding="utf-8"))
    for section, values in pg.DEFAULTS.items():
        assert shipped[section] == values, section


# ── Tier 1 may import it ────────────────────────────────────────────────────

EXPECTED = {"repo_paths", "shared", "shared.config_toml", "shared.public_gamma",
            "shared.symbols", "tzdata"}

PROBE = r"""
import sys
sys.path.insert(0, r"%s")
before = set(sys.modules)
import shared.public_gamma
new = set(sys.modules) - before
print("NEW:" + ",".join(sorted(m for m in new
                              if m.split(".")[0] not in sys.stdlib_module_names)))
""" % REPO


def test_public_gamma_imports_only_config_and_the_symbol_allow_list():
    """Tier 1 imports this module, so its import set is pinned, in a FRESH
    interpreter so a transitive import cannot hide behind an earlier test's."""
    r = subprocess.run([sys.executable, "-c", PROBE], cwd=REPO,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    new = set(filter(None, r.stdout.strip()[len("NEW:"):].split(",")))
    stray = {m for m in new if m not in EXPECTED and m.split(".")[0] != "tzdata"}
    assert not stray, sorted(stray)
    assert {"shared.public_gamma", "shared.symbols"} <= new

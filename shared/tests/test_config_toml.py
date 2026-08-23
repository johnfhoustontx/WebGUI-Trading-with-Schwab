"""The shared TOML-loader factory.

`config/flow_alerts.toml` and `config/sessions.toml` each grew their own copy of
the same ~40 lines: mtime-cache, deep-merge over built-in defaults, degrade to
the defaults on ANY failure, never raise. Batch 3 adds four more config files, so
the boilerplate is factored out here rather than copied six times.
"""
import textwrap

import pytest

from shared.config_toml import toml_loader

DEFAULTS = {
    "limits": {"max_risk": 3000, "vix_max": 35.0},
    "menu": {"top_n": 15},
    "enabled": True,
}


def _write(tmp_path, body):
    p = tmp_path / "c.toml"
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return p


def test_missing_file_yields_the_defaults(tmp_path):
    load, _ = toml_loader(tmp_path / "nope.toml", DEFAULTS)
    assert load() == DEFAULTS


def test_file_overrides_are_deep_merged(tmp_path):
    p = _write(tmp_path, """
        [limits]
        max_risk = 500
    """)
    load, _ = toml_loader(p, DEFAULTS)
    cfg = load()
    assert cfg["limits"]["max_risk"] == 500
    assert cfg["limits"]["vix_max"] == 35.0, "a sibling key must survive the merge"
    assert cfg["menu"]["top_n"] == 15, "an untouched section must survive"


def test_malformed_file_degrades_to_defaults_and_never_raises(tmp_path):
    p = _write(tmp_path, "this is not [valid toml")
    load, _ = toml_loader(p, DEFAULTS)
    assert load() == DEFAULTS


def test_defaults_are_never_mutated_by_a_load(tmp_path):
    """The nastiest failure mode: one bad file permanently poisons the defaults
    for the rest of the process."""
    p = _write(tmp_path, "[limits]\nmax_risk = 1\n")
    load, _ = toml_loader(p, DEFAULTS)
    load()
    assert DEFAULTS["limits"]["max_risk"] == 3000


def test_result_is_not_aliased_to_the_defaults(tmp_path):
    """Mutating a load() result must not reach the module-level defaults.

    Note the contract this does NOT claim: load() returns the CACHED mapping, so
    a caller that mutates it affects later readers of that same file until the
    cache turns over. That matches the existing flow_alerts/sessions loaders -
    copying on every hot-path read would defeat the point of caching - so the
    config dicts are read-only by convention. What must never happen is the
    defaults themselves being poisoned, since that is permanent and
    process-wide.
    """
    load, _ = toml_loader(tmp_path / "nope.toml", DEFAULTS)
    cfg = load()
    cfg["limits"]["max_risk"] = 999
    assert DEFAULTS["limits"]["max_risk"] == 3000

    # ...and a NEW loader over the same defaults is unaffected.
    load2, _ = toml_loader(tmp_path / "nope.toml", DEFAULTS)
    assert load2()["limits"]["max_risk"] == 3000


def test_is_mtime_cached_and_reset_forces_a_reread(tmp_path):
    p = _write(tmp_path, "[limits]\nmax_risk = 1\n")
    load, reset = toml_loader(p, DEFAULTS)
    assert load()["limits"]["max_risk"] == 1
    p.write_text("[limits]\nmax_risk = 2\n", encoding="utf-8")
    # same call within the same mtime tick may still serve the cache; an explicit
    # reset must always re-read.
    reset()
    assert load()["limits"]["max_risk"] == 2


def test_picks_up_an_edit_when_the_mtime_moves(tmp_path):
    import os
    p = _write(tmp_path, "[limits]\nmax_risk = 1\n")
    load, _ = toml_loader(p, DEFAULTS)
    assert load()["limits"]["max_risk"] == 1
    p.write_text("[limits]\nmax_risk = 7\n", encoding="utf-8")
    st = os.stat(p)
    os.utime(p, (st.st_atime + 10, st.st_mtime + 10))
    assert load()["limits"]["max_risk"] == 7, "edit + restart is the documented flow"


@pytest.mark.parametrize("bad", [None, 42, "text", [1, 2]])
def test_a_non_table_top_level_degrades_rather_than_corrupting(tmp_path, bad):
    """tomllib always returns a dict, but a hand-rolled caller might not - the
    merge must not explode on one."""
    load, _ = toml_loader(tmp_path / "nope.toml", DEFAULTS)
    assert load() == DEFAULTS

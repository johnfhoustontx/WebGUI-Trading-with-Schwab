"""C2: which profit-lock ladder is in force — gap assessment C2.

Design: docs/plans/2026-09-12-profit-lock-ladder-design.md.

``[trail].ratchet_ladder`` has existed, tested, with no caller. The mechanism
needed two things from a caller — ``trail_ladder`` and ``peak_pnl_frac`` — and got
neither. ``active`` names which ladder is in force, so the two stay side by side
in the TOML and switching back is one word rather than an array edit.

Measured over 281 closed captured signals replayed on their own mark series, the
ratchet beats the plain break-even stop it replaces by **+$383 (+4.7%)** at a 10%
slippage haircut, and wins in every month and both scanner types. It reverses only
at an implausible 50% haircut.
"""
import pytest

from shared import trade_mgmt as tm


@pytest.fixture(autouse=True)
def _fresh():
    tm.reset_cache()
    yield
    tm.reset_cache()


def test_the_two_ladders_are_still_both_readable():
    """Switching is a name change, not an array edit, so both must survive."""
    assert tm.default_trail_ladder() == [(0.50, 0.0)]
    assert tm.ratchet_trail_ladder() == [(0.50, 0.0), (0.65, 0.25), (0.80, 0.50)]


def test_the_active_ladder_is_the_ratchet_as_shipped():
    """The measured decision: it beats the break-even stop it replaces in every
    month and both scanner types, and structurally cannot increase loss exposure —
    rule 3 takes ``max(be_level, locked_level)``, so the ladder only ever raises a
    stop that is already above break-even."""
    assert tm.active_trail_ladder() == tm.ratchet_trail_ladder()


def test_naming_the_default_ladder_gives_back_todays_behaviour(tmp_path,
                                                               monkeypatch):
    """One word reverts it. The default ladder is a single break-even rung, which
    is exactly the plain break-even stop."""
    toml = tmp_path / "tm.toml"
    toml.write_text('[trail]\nactive = "default"\n', encoding="utf-8")
    from shared import config_toml
    load, _ = config_toml.toml_loader(toml, tm.DEFAULTS, label="test")
    monkeypatch.setattr(tm, "load", load)
    tm.reset_cache()
    assert tm.active_trail_ladder() == [(0.50, 0.0)]


@pytest.mark.parametrize("bad", ['"nonsense"', "42", "true", '""'])
def test_an_unknown_active_name_falls_back_to_the_INERT_ladder(bad, tmp_path,
                                                              monkeypatch):
    """⚠ The safe direction is today's behaviour, not the richer ladder. A typo in
    a risk config must never silently switch on an untried policy — the same rule
    that makes a missing `[structures.*]` table fall back to plain `[stops]`."""
    toml = tmp_path / "tm.toml"
    toml.write_text("[trail]\nactive = %s\n" % bad, encoding="utf-8")
    from shared import config_toml
    load, _ = config_toml.toml_loader(toml, tm.DEFAULTS, label="test")
    monkeypatch.setattr(tm, "load", load)
    tm.reset_cache()
    assert tm.active_trail_ladder() == [(0.50, 0.0)]


def test_a_missing_trail_section_falls_back_to_the_inert_ladder(tmp_path,
                                                               monkeypatch):
    from shared import config_toml
    load, _ = config_toml.toml_loader(tmp_path / "gone.toml",
                                      {"trail": {}}, label="test")
    monkeypatch.setattr(tm, "load", load)
    tm.reset_cache()
    assert tm.active_trail_ladder() == [(0.50, 0.0)]


def test_the_shipped_toml_and_the_defaults_agree_on_active():
    """``config_toml`` deep-merges, so a key in the defaults and absent from the
    file is invisible to an operator editing the file."""
    import tomllib

    from repo_paths import TRADE_MGMT_TOML
    raw = tomllib.loads(TRADE_MGMT_TOML.read_text(encoding="utf-8"))
    assert (raw.get("trail") or {}).get("active") == "ratchet"
    assert tm.DEFAULTS["trail"]["active"] == "ratchet"


def test_the_recommender_exposes_the_active_ladder():
    """``signal_recommender`` binds its ladders at import, matching the rest of
    that module's edit-and-restart contract — so the constant must exist and match."""
    import pathlib
    import sys

    from repo_paths import OPTIONS_SCANNER
    sys.path.insert(0, str(OPTIONS_SCANNER))
    try:
        import signal_recommender as sr
    finally:
        sys.path.remove(str(OPTIONS_SCANNER))
    assert sr.ACTIVE_TRAIL_LADDER == tm.active_trail_ladder()
    assert pathlib.Path(OPTIONS_SCANNER).exists()

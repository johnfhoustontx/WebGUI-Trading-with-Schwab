"""shared.paper_limits: the paper books' per-trade caps from config/paper.toml."""
import math

import pytest

from shared import paper_limits as pl


@pytest.fixture
def toml(tmp_path, monkeypatch):
    """Point the loader at a scratch file; returns a writer."""
    path = tmp_path / "paper.toml"
    from shared.config_toml import toml_loader

    load, reset = toml_loader(path, pl.DEFAULTS, label="paper.toml")
    monkeypatch.setattr(pl, "load", load)

    def write(text):
        path.write_text(text, encoding="utf-8")
        reset()
    return write


def test_shipped_values_are_750_each():
    assert pl.max_risk_per_trade() == 750.0
    assert pl.ledger_max_risk_per_trade() == 750.0


def test_the_file_overrides_each_cap(toml):
    toml("[risk]\nmax_risk_per_trade = 300\nledger_max_risk_per_trade = 900.5\n")
    assert pl.max_risk_per_trade() == 300.0
    assert pl.ledger_max_risk_per_trade() == 900.5


@pytest.mark.parametrize("bad", ["0", "-5", "nan", "inf", "true", '"x"'])
def test_an_unusable_cap_falls_back_to_the_default(toml, bad):
    # A zero cap refuses every trade; a NaN makes every ">" False and switches
    # the cap off. Neither may bind.
    toml(f"[risk]\nmax_risk_per_trade = {bad}\n")
    assert pl.max_risk_per_trade() == 750.0


def test_a_missing_file_is_the_defaults(toml):
    toml("")
    assert math.isclose(pl.ledger_max_risk_per_trade(), 750.0)


def test_the_shipped_file_matches_the_defaults():
    import tomllib

    from repo_paths import PAPER_TOML

    with open(PAPER_TOML, "rb") as fh:
        shipped = tomllib.load(fh)
    assert shipped == pl.DEFAULTS

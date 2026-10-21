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


# --- trade ideas in the Paper Ledger ([idea_trades]) -------------------------

_IDEA_DEFAULTS = {"enabled": True, "quantity": 1, "mark_interval_min": 5,
                  "debit_stop_frac": 0.60, "clean_mae_pct": 25.0, "min_n": 5,
                  "review_model": "sonnet"}


def test_idea_trades_defaults():
    assert pl.idea_trades() == _IDEA_DEFAULTS


def test_idea_trades_has_no_review_length_setting(toml):
    """The weekly review runs on the Claude Code CLI, which takes no output cap,
    so a length setting would do nothing: there is none, and a stale one in an
    operator's file is ignored."""
    assert "review_max_tokens" not in pl.DEFAULTS["idea_trades"]
    toml("[idea_trades]\nreview_max_tokens = 6000\n")
    assert pl.idea_trades() == _IDEA_DEFAULTS


def test_idea_trades_file_overrides_each_key(toml):
    toml("[idea_trades]\nenabled = false\nquantity = 2\nmark_interval_min = 15\n"
         "debit_stop_frac = 0.5\nclean_mae_pct = 40\nmin_n = 8\n"
         'review_model = "haiku"\n')
    assert pl.idea_trades() == {"enabled": False, "quantity": 2,
                                "mark_interval_min": 15, "debit_stop_frac": 0.5,
                                "clean_mae_pct": 40.0, "min_n": 8,
                                "review_model": "haiku"}


@pytest.mark.parametrize("key,bad", [
    ("quantity", 0), ("quantity", True), ("quantity", 1.5),
    ("mark_interval_min", -5), ("mark_interval_min", 0), ("mark_interval_min", 61),
    ("debit_stop_frac", 1.5), ("debit_stop_frac", 0), ("debit_stop_frac", float("nan")),
    ("debit_stop_frac", float("inf")),
    ("clean_mae_pct", 0), ("clean_mae_pct", 101), ("clean_mae_pct", float("nan")),
    ("min_n", "x"), ("min_n", 0), ("min_n", False),
    ("enabled", "yes"), ("enabled", 1),
    ("review_model", ""), ("review_model", "  "), ("review_model", 5),
])
def test_idea_trades_bad_value_falls_back(monkeypatch, key, bad):
    monkeypatch.setattr(pl, "load", lambda: {"idea_trades": {key: bad}})
    assert pl.idea_trades()[key] == pl.DEFAULTS["idea_trades"][key]


def test_idea_trades_keeps_the_good_keys_beside_a_bad_one(monkeypatch):
    monkeypatch.setattr(pl, "load", lambda: {"idea_trades": {"quantity": 0, "min_n": 9}})
    got = pl.idea_trades()
    assert got["quantity"] == 1 and got["min_n"] == 9


def test_idea_trades_boundaries_are_accepted(monkeypatch):
    monkeypatch.setattr(pl, "load", lambda: {"idea_trades": {
        "mark_interval_min": 60, "debit_stop_frac": 1, "clean_mae_pct": 100}})
    got = pl.idea_trades()
    assert (got["mark_interval_min"], got["debit_stop_frac"], got["clean_mae_pct"]) == (60, 1.0, 100.0)


def test_idea_trades_survives_a_table_that_is_not_a_table(monkeypatch):
    monkeypatch.setattr(pl, "load", lambda: {"idea_trades": "oops"})
    assert pl.idea_trades() == _IDEA_DEFAULTS


def test_the_idea_review_slot_ships_at_1530():
    from datetime import time

    from shared import market_calendar as mc
    assert mc.slot_times("idea_review")["at"] == time(15, 30)
    assert mc._DEFAULTS["slots"]["idea_review"] == {"at": "15:30"}


# --- the zero-bid mark bound (audit AC-08) -----------------------------------

def test_the_zero_bid_bound_ships_at_a_quarter():
    from shared import paper_limits
    assert paper_limits.zero_bid_max_ask() == 0.25


def test_an_unusable_zero_bid_bound_reads_as_the_default(monkeypatch):
    import pytest
    from shared import paper_limits
    for bad in (-1, float("nan"), "0.1", True, None):
        monkeypatch.setattr(paper_limits, "load", lambda b=bad: {"marks": {"zero_bid_max_ask": b}})
        assert paper_limits.zero_bid_max_ask() == 0.25, bad
    monkeypatch.setattr(paper_limits, "load", lambda: {"marks": {"zero_bid_max_ask": 0}})
    assert paper_limits.zero_bid_max_ask() == 0.0
    monkeypatch.setattr(paper_limits, "load", lambda: {"marks": "oops"})
    assert paper_limits.zero_bid_max_ask() == pytest.approx(0.25)

"""D3: the four DEBIT structures' exit rules, and the two keys only they use.

``exit_dte`` is a time exit that is NOT profit-conditional (unlike the credit
side's ``manage_dte``) and ``debit_stop_frac`` is the debit path's only loss-side
knob — ``loss_rules``/``stop_mult`` are credit-denominated and are not read for a
debit, since *2x a debit* is a loss that cannot happen.
"""
import tomllib

import pytest

from repo_paths import TRADE_MGMT_TOML
from shared import structures, trade_mgmt

_DEBITS = ("LONG_CALL", "LONG_PUT", "BULL_CALL", "BEAR_PUT")


@pytest.fixture(autouse=True)
def _fresh():
    trade_mgmt.reset_cache()
    yield
    trade_mgmt.reset_cache()


def _raw():
    with open(TRADE_MGMT_TOML, "rb") as fh:
        return tomllib.load(fh)


# ── the shipped TOML, which is what the operator edits ─────────────────────

@pytest.mark.parametrize("name", _DEBITS)
def test_each_debit_structure_has_a_shipped_table(name):
    """Not a duplicate of the accessor tests: those pass while the values live
    only in DEFAULTS."""
    table = _raw().get("structures", {}).get(name)
    assert table is not None, f"[structures.{name}] is missing from the TOML"
    assert table["exit_dte"] == 21


@pytest.mark.parametrize("name", _DEBITS)
def test_no_debit_structure_ships_a_LOSS_STOP(name):
    """⚠ Sourced: the practitioners close debit spreads before expiry rather than
    stopping them out, so a level here would be invention. It must ship absent or
    explicitly off — never a number someone guessed."""
    table = _raw().get("structures", {}).get(name) or {}
    assert table.get("debit_stop_frac") in (None, 0), table


@pytest.mark.parametrize("name", _DEBITS)
def test_each_debit_table_is_the_CANONICAL_spelling(name):
    """A table keyed on an alternate spelling looks authoritative and is ignored."""
    assert structures.canonical(name) == name


# ── the accessor ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", _DEBITS)
def test_the_accessor_resolves_the_time_exit(name):
    assert trade_mgmt.structure_rules(name)["exit_dte"] == 21


@pytest.mark.parametrize("name", _DEBITS)
def test_the_loss_stop_is_OFF_through_the_accessor(name):
    assert trade_mgmt.structure_rules(name)["debit_stop_frac"] is None


@pytest.mark.parametrize("name", _DEBITS)
def test_a_debit_structure_INHERITS_the_global_profit_target(name):
    """0.50 — both the lower of the two sourced numbers (tastylive 50% /
    TradingBlock ~80%) and the value every other structure here uses. The
    DENOMINATOR is what differs per structure, and that lives in
    ``signal_recommender._debit_target_base``, not in this table."""
    rules = trade_mgmt.structure_rules(name)
    assert rules["tp_frac"] == trade_mgmt.DEFAULTS["stops"]["tp_frac"] == 0.50


# ── the keys must not leak onto the credit structures ─────────────────────

@pytest.mark.parametrize("name", ["PCS", "CCS", "IC", "IRON_CONDOR",
                                  "SHORT_PUT", "COVERED_CALL"])
def test_no_CREDIT_structure_gains_a_time_exit_or_a_debit_stop(name):
    """⚠ The control. ``exit_dte`` fires regardless of profit, so leaking it onto
    a credit spread would close every one of them at 21 DTE — a change to how
    the entire book exits, which is a separate measurable decision."""
    rules = trade_mgmt.structure_rules(name)
    assert rules["exit_dte"] is None
    assert rules["debit_stop_frac"] is None


def test_an_UNKNOWN_structure_gets_neither():
    rules = trade_mgmt.structure_rules("CALENDAR")
    assert rules["exit_dte"] is None and rules["debit_stop_frac"] is None
    assert rules["loss_rules"] is True      # unchanged: the safe end


def test_a_TOML_edit_to_one_debit_table_leaves_its_siblings_alone(
        monkeypatch, tmp_path):
    """The deep-merge contract, on the keys D3 added."""
    import tomli_w

    raw = _raw()
    raw["structures"]["LONG_CALL"]["exit_dte"] = 7
    path = tmp_path / "trade_mgmt.toml"
    with open(path, "wb") as fh:
        tomli_w.dump(raw, fh)
    loader, resetter = trade_mgmt.toml_loader(path, trade_mgmt.DEFAULTS,
                                              label="trade_mgmt.toml")
    # ⚠ BOTH through monkeypatch. A bare ``trade_mgmt.reset_cache = resetter``
    # would leave the tmp_path loader's resetter bound for the rest of the
    # session, and the autouse fixture above calls it — so the next test would
    # "reset" a cache that no longer exists and read the real TOML through a
    # stale one.
    monkeypatch.setattr(trade_mgmt, "load", loader)
    monkeypatch.setattr(trade_mgmt, "reset_cache", resetter)

    assert trade_mgmt.structure_rules("LONG_CALL")["exit_dte"] == 7
    assert trade_mgmt.structure_rules("LONG_PUT")["exit_dte"] == 21
    assert trade_mgmt.structure_rules("LONG_CALL")["tp_frac"] == 0.50

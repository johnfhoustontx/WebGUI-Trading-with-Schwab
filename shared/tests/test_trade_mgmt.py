"""Trade-management rules are config, and the mirror is now structural."""
import pytest

from shared import trade_mgmt


@pytest.fixture(autouse=True)
def _fresh():
    trade_mgmt.reset_cache()
    yield
    trade_mgmt.reset_cache()


def test_shipped_toml_matches_the_documented_rules():
    st = trade_mgmt.stops()
    assert st["tp_frac"] == 0.50
    assert st["stop_mult"] == 2.0
    assert st["delta_drift"] == 0.12
    assert st["delta_hard_ceiling"] == 0.45
    assert st["delta_abs_fallback"] == 0.35
    assert st["cut_dte"] == 2
    assert st["recovery_dte_min"] == 5
    assert st["recovery_min_cushion"] == 0.015


def test_ladders_come_back_as_tuples():
    assert trade_mgmt.default_trail_ladder() == [(0.50, 0.0)]
    assert trade_mgmt.ratchet_trail_ladder() == [(0.50, 0.0), (0.65, 0.25), (0.80, 0.50)]


def test_a_malformed_rung_is_dropped_not_fatal(monkeypatch):
    monkeypatch.setattr(trade_mgmt, "load", lambda: {
        "trail": {"default_ladder": [[0.5, 0.0], "junk", [0.9]]}})
    assert trade_mgmt.default_trail_ladder() == [(0.5, 0.0)]


def test_an_entirely_junk_ladder_falls_back(monkeypatch):
    monkeypatch.setattr(trade_mgmt, "load",
                        lambda: {"trail": {"default_ladder": ["junk"]}})
    assert trade_mgmt.default_trail_ladder() == [(0.50, 0.0)]


# --- the mirror ------------------------------------------------------------

MIRRORED = {
    "delta_critical": "delta_hard_ceiling",
    "delta_drift": "delta_drift",
    "money_tested_mult": "stop_mult",
    "dte_urgent": "cut_dte",
}


def test_rescue_thresholds_derive_the_shared_four_from_stops():
    rt, st = trade_mgmt.rescue_thresholds(), trade_mgmt.stops()
    for rescue_key, stop_key in MIRRORED.items():
        assert rt[rescue_key] == st[stop_key]


def test_moving_a_stop_moves_the_rescue_board_with_it(monkeypatch):
    """The discriminating test. Asserting the two are equal today proves nothing
    - they were equal before this change too, as two hand-copied literals. Move
    the source and the mirror must follow."""
    moved = {**trade_mgmt.DEFAULTS["stops"],
             "delta_hard_ceiling": 0.99, "delta_drift": 0.88,
             "stop_mult": 7.0, "cut_dte": 42}
    monkeypatch.setattr(trade_mgmt, "stops", lambda: moved)
    rt = trade_mgmt.rescue_thresholds()
    assert rt["delta_critical"] == 0.99
    assert rt["delta_drift"] == 0.88
    assert rt["money_tested_mult"] == 7.0
    assert rt["dte_urgent"] == 42


def test_rescue_only_bands_are_unaffected_by_stops():
    rt = trade_mgmt.rescue_thresholds()
    assert rt["delta_warn"] == 0.30
    assert rt["money_warn_mult"] == 1.0
    assert rt["money_critical_mult"] == 3.0
    assert rt["dte_manage"] == 21
    assert rt["proximity_watch_pct"] == 0.03
    assert rt["proximity_tested_pct"] == 0.01


def test_the_toml_does_not_restate_the_derived_four():
    """If someone adds delta_critical back into [rescue] it would look
    authoritative and be silently ignored - worse than not being there."""
    import tomllib

    from repo_paths import TRADE_MGMT_TOML

    with open(TRADE_MGMT_TOML, "rb") as fh:
        raw = tomllib.load(fh)
    for key in MIRRORED:
        assert key not in raw.get("rescue", {}), (
            f"[rescue].{key} is DERIVED from [stops] - listing it here would be "
            "ignored while looking authoritative")


# --- the per-structure rule table (gap assessment B1) ----------------------
# A structure's table OVERLAYS [stops], so it names only what differs. An
# unlisted structure therefore resolves to [stops] unchanged with the loss-side
# rules on - which is exactly what every spread already did, making the table
# additive by construction.

def test_a_spread_gets_the_global_rules_and_every_loss_side_stop():
    r = trade_mgmt.structure_rules("PCS")
    st = trade_mgmt.stops()
    assert r["loss_rules"] is True
    assert r["manage_dte"] is None
    for key, value in st.items():
        assert r[key] == value


def test_an_unlisted_structure_is_treated_exactly_like_a_spread():
    assert trade_mgmt.structure_rules("SOMETHING_NEW") == trade_mgmt.structure_rules("PCS")


def test_no_strategy_at_all_still_returns_the_global_rules():
    """``recommend()`` is called with a ctx that may carry no ``strategy`` — the
    pre-B1 callers. They must keep every rule."""
    assert trade_mgmt.structure_rules(None) == trade_mgmt.structure_rules("PCS")


@pytest.mark.parametrize("strategy", ["SHORT_PUT", "NAKED_PUT", "COVERED_CALL"])
def test_the_income_structures_carry_no_loss_side_rule(strategy):
    r = trade_mgmt.structure_rules(strategy)
    assert r["loss_rules"] is False
    assert r["manage_dte"] == 21


@pytest.mark.parametrize("strategy", ["SHORT_PUT", "NAKED_PUT", "COVERED_CALL"])
def test_the_income_structures_keep_the_global_profit_target(strategy):
    """B1 deliberately did NOT move the target to TradingBlock's 0.90/0.95 —
    that pairs with an automatic roll this app cannot do. The knob exists; the
    default does not use it."""
    assert trade_mgmt.structure_rules(strategy)["tp_frac"] == trade_mgmt.stops()["tp_frac"]


def test_both_short_put_spellings_resolve_to_the_same_rules():
    """The table is keyed on the canonical name, so the two spellings cannot be
    given different exit rules by an edit to one of them."""
    assert trade_mgmt.structure_rules("NAKED_PUT") == trade_mgmt.structure_rules("SHORT_PUT")


def test_a_structure_table_overlays_stops_rather_than_replacing_them(monkeypatch):
    """The discriminating test: a table naming ONE key must inherit the rest, not
    blank them. A dict-replace would drop ``tp_frac`` and every stop.

    Uses a structure with NO built-in table (a call credit spread, the plausible
    "manage my spreads at 14 DTE too" edit) so the inherited values are the ones
    a real TOML edit would actually produce. Naming ``SHORT_PUT`` here would
    assert an unreachable state instead: ``toml_loader`` deep-merges, so a real
    file naming one key inside ``[structures.SHORT_PUT]`` keeps the shipped
    ``loss_rules = false`` beside it."""
    monkeypatch.setattr(trade_mgmt, "load", lambda: {
        "stops": {**trade_mgmt.DEFAULTS["stops"], "tp_frac": 0.77},
        "structures": {"CCS": {"manage_dte": 14}}})
    r = trade_mgmt.structure_rules("CCS")
    assert r["manage_dte"] == 14
    assert r["tp_frac"] == 0.77                      # inherited from [stops]
    assert r["stop_mult"] == trade_mgmt.DEFAULTS["stops"]["stop_mult"]
    assert r["loss_rules"] is True                   # not named -> the default


def test_a_real_toml_edit_keeps_the_shipped_siblings(monkeypatch, tmp_path):
    """The converse, driven through the REAL loader rather than a stubbed
    ``load``. A file naming only ``manage_dte`` must not silently re-arm the
    money stop on a covered call."""
    toml = tmp_path / "trade_mgmt.toml"
    toml.write_text("[structures.COVERED_CALL]\nmanage_dte = 14\n", encoding="utf-8")
    load, _reset = trade_mgmt.toml_loader(toml, trade_mgmt.DEFAULTS,
                                          label="trade_mgmt.toml")
    monkeypatch.setattr(trade_mgmt, "load", load)
    r = trade_mgmt.structure_rules("COVERED_CALL")
    assert r["manage_dte"] == 14
    assert r["loss_rules"] is False


def test_a_structure_can_override_the_profit_target(monkeypatch):
    """TradingBlock's ~90% short put / ~95% covered call is a one-line edit."""
    monkeypatch.setattr(trade_mgmt, "load", lambda: {
        "stops": trade_mgmt.DEFAULTS["stops"],
        "structures": {"COVERED_CALL": {"tp_frac": 0.95}}})
    assert trade_mgmt.structure_rules("COVERED_CALL")["tp_frac"] == 0.95
    assert trade_mgmt.structure_rules("PCS")["tp_frac"] == 0.50


def test_a_malformed_structure_table_degrades_to_the_BUILT_IN_one(monkeypatch):
    """Degrading to the global rules here would re-arm the money stop on a
    covered call because of a typo — the exact rule this table exists to remove.
    So a junk table falls back to the shipped one, which is also the loader's own
    contract ("the built-in defaults are the real values")."""
    monkeypatch.setattr(trade_mgmt, "load",
                        lambda: {"structures": {"SHORT_PUT": "junk"}})
    assert trade_mgmt.structure_rules("SHORT_PUT")["loss_rules"] is False


def test_a_malformed_structures_section_is_not_fatal(monkeypatch):
    monkeypatch.setattr(trade_mgmt, "load", lambda: {"structures": ["junk"]})
    assert trade_mgmt.structure_rules("SHORT_PUT")["loss_rules"] is False
    assert trade_mgmt.structure_rules("PCS")["loss_rules"] is True


def test_a_junk_table_on_an_unknown_structure_still_gives_the_global_rules():
    """There is nothing to fall back TO for a structure with no shipped table,
    so it lands on the global rules — with every stop on, which is the safe end
    for something the app does not recognise."""
    assert trade_mgmt.structure_rules("SOMETHING_NEW")["loss_rules"] is True


def test_the_shipped_toml_is_the_source_of_the_income_rules():
    """Not a duplicate of the accessor tests: those would still pass if the
    values lived only in DEFAULTS. The TOML is what the operator edits."""
    import tomllib

    from repo_paths import TRADE_MGMT_TOML

    with open(TRADE_MGMT_TOML, "rb") as fh:
        raw = tomllib.load(fh)
    for name in ("SHORT_PUT", "COVERED_CALL"):
        table = raw.get("structures", {}).get(name)
        assert table is not None, f"[structures.{name}] is missing from the TOML"
        assert table["loss_rules"] is False
        assert table["manage_dte"] == 21


def test_the_toml_does_not_key_a_structure_by_an_alternate_spelling():
    """``[structures.NAKED_PUT]`` would look authoritative and be silently
    ignored — the table is keyed on the canonical name."""
    import tomllib

    from repo_paths import TRADE_MGMT_TOML
    from shared import structures

    with open(TRADE_MGMT_TOML, "rb") as fh:
        raw = tomllib.load(fh)
    for name in raw.get("structures", {}):
        assert structures.canonical(name) == name, (
            f"[structures.{name}] is not the canonical spelling — it would be "
            f"ignored. Use [structures.{structures.canonical(name)}].")


# --- the consumers actually read it ----------------------------------------
# signal_recommender lives in options-scanner and is NOT importable from here
# without putting a hyphenated app dir on sys.path (the documented `scoring`
# collision). Its half of this lives in options-scanner/tests/.

def test_rescue_module_reads_the_config():
    from services.options_svc import rescue

    assert rescue.RESCUE_THRESHOLDS == trade_mgmt.rescue_thresholds()


def test_rescue_actually_READS_it_rather_than_agreeing_by_luck(monkeypatch):
    """rescue.RESCUE_THRESHOLDS matched trade_mgmt before this change too - both
    were the same hand-copied literals. Move the config and require it to follow.
    It is a module constant resolved at import (the "edit + restart" contract),
    so this reloads."""
    import importlib

    from services.options_svc import rescue

    monkeypatch.setattr(trade_mgmt, "rescue_thresholds",
                        lambda: {"delta_warn": 0.11, "sentinel": True})
    try:
        importlib.reload(rescue)
        assert rescue.RESCUE_THRESHOLDS.get("sentinel") is True,             "options_svc/rescue.py is not reading config/trade_mgmt.toml"
    finally:
        monkeypatch.undo()
        importlib.reload(rescue)

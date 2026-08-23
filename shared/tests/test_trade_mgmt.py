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

"""Thresholds: defaults, round-trip persistence, partial/corrupt file handling."""
from src.thresholds import Thresholds, load_thresholds, save_thresholds


def test_defaults():
    t = Thresholds()
    assert t.weight_cap == 0.10
    assert t.sector_lag_pct == 0.05
    assert t.exit_loss_pct == 0.15
    assert t.dd_trigger == 0.10
    assert t.atr_mult == 2.0
    assert t.take_profit_pct == 0.25
    assert t.bottom_quartile == 0.25
    assert t.max_position_loss_pct == 0.02


def test_round_trip(tmp_path):
    path = tmp_path / "eval_settings.json"
    t = Thresholds(weight_cap=0.07, atr_mult=3.0)
    save_thresholds(t, path)
    assert load_thresholds(path) == t


def test_load_missing_file_returns_defaults(tmp_path):
    assert load_thresholds(tmp_path / "nope.json") == Thresholds()


def test_load_partial_file_fills_defaults(tmp_path):
    path = tmp_path / "eval_settings.json"
    path.write_text('{"weight_cap": 0.08}')
    t = load_thresholds(path)
    assert t.weight_cap == 0.08
    assert t.atr_mult == 2.0  # default fills the gap


def test_load_corrupt_file_returns_defaults(tmp_path):
    path = tmp_path / "eval_settings.json"
    path.write_text("not json{")
    assert load_thresholds(path) == Thresholds()


def test_load_ignores_unknown_keys(tmp_path):
    path = tmp_path / "eval_settings.json"
    path.write_text('{"weight_cap": 0.08, "bogus": 1}')
    assert load_thresholds(path).weight_cap == 0.08


def test_load_non_dict_json_returns_defaults(tmp_path):
    path = tmp_path / "eval_settings.json"
    path.write_text("[1, 2]")
    assert load_thresholds(path) == Thresholds()


def test_load_non_numeric_value_falls_back_to_default(tmp_path):
    path = tmp_path / "eval_settings.json"
    path.write_text('{"weight_cap": "abc", "atr_mult": 3.0}')
    t = load_thresholds(path)
    assert t.weight_cap == 0.10  # garbage value -> field default
    assert t.atr_mult == 3.0  # valid sibling still applied

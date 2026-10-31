"""config/futures_chain.toml: the /ES and /NQ 0-DTE chain feed's tunables."""
import ast
import pathlib

import pytest

from shared import futures_chain_config as cfg


def test_the_two_instruments_ship_with_their_measured_values():
    ins = cfg.instruments()
    assert list(ins) == ["ES", "NQ"]
    es, nq = ins["ES"], ins["NQ"]
    assert (es["symbol"], es["label"], es["exchange"]) == ("FOP.ES", "/ES", "XCME")
    assert (nq["symbol"], nq["label"], nq["exchange"]) == ("FOP.NQ", "/NQ", "XCME")
    assert (es["multiplier"], es["strike_step"], es["parity_limit_pts"]) == (50, 5, 15)
    assert (nq["multiplier"], nq["strike_step"], nq["parity_limit_pts"]) == (20, 10, 60)
    assert es["strike_width_pct"] == nq["strike_width_pct"] == 5


def test_contract_roots_are_the_ones_that_returned_data():
    es, nq = cfg.instruments()["ES"], cfg.instruments()["NQ"]
    assert es["weekday_roots"] == ["E{w}A", "E{w}B", "E{w}C", "E{w}D"]
    assert (es["friday_root"], es["eom_root"], es["quarterly_root"]) == ("EW{w}", "EW", "ES")
    assert nq["weekday_roots"] == ["Q{w}A", "Q{w}B", "Q{w}C", "Q{w}D"]
    assert (nq["friday_root"], nq["eom_root"], nq["quarterly_root"]) == ("QN{w}", "QNE", "NQ")


def test_the_capture_defaults():
    assert cfg.capture() == {
        "interval_sec": 300, "max_age_sec": 900, "stale_after_sec": 660,
        "max_future_skew_sec": 30, "min_fill_fraction": 0.8, "frozen_repeats": 3,
        "fill_step_sec": 120, "frozen_before_expiry_min": 60, "alert_repeat_min": 240,
        "max_snapshot_kb": 512, "parity_strikes": 5, "show_in_dropdown": False}
    assert isinstance(cfg.capture()["min_fill_fraction"], float)
    assert cfg.capture()["show_in_dropdown"] is False


def test_the_session_defaults():
    assert cfg.session() == {"open_ct": "17:00", "close_ct": "15:00"}


def test_the_shipped_file_carries_the_defaults():
    """The file and the built-in defaults are two copies of one set of numbers."""
    import tomllib

    from repo_paths import FUTURES_CHAIN_TOML
    with open(FUTURES_CHAIN_TOML, "rb") as fh:
        assert tomllib.load(fh) == cfg.DEFAULTS


def test_an_override_is_read(monkeypatch):
    """The proof a value is READ: the accessor sees a patched loader."""
    monkeypatch.setattr(cfg, "load", lambda: {
        "capture": {"interval_sec": 120, "show_in_dropdown": True,
                    "min_fill_fraction": 0.5},
        "session": {"open_ct": "18:00", "close_ct": "14:30"},
        "instruments": {"ES": {"multiplier": 25, "strike_step": 2.5,
                               "weekday_roots": ["A{w}", "B{w}", "C{w}", "D{w}"]}}})
    assert cfg.capture()["interval_sec"] == 120
    assert cfg.capture()["show_in_dropdown"] is True
    assert cfg.capture()["min_fill_fraction"] == 0.5
    assert cfg.session() == {"open_ct": "18:00", "close_ct": "14:30"}
    es = cfg.instruments()["ES"]
    assert es["multiplier"] == 25 and es["strike_step"] == 2.5
    assert es["weekday_roots"] == ["A{w}", "B{w}", "C{w}", "D{w}"]
    assert es["parity_limit_pts"] == 15          # untouched key keeps its default
    assert cfg.instruments()["NQ"]["multiplier"] == 20


@pytest.mark.parametrize("key,bad", [
    ("interval_sec", 0), ("interval_sec", 99999), ("interval_sec", True),
    ("interval_sec", "300"), ("interval_sec", 12.5),
    ("max_age_sec", -1), ("stale_after_sec", 0), ("max_future_skew_sec", -5),
    ("min_fill_fraction", 0), ("min_fill_fraction", 1.5), ("min_fill_fraction", True),
    ("frozen_repeats", 1), ("fill_step_sec", 0), ("frozen_before_expiry_min", -1),
    ("alert_repeat_min", 0), ("max_snapshot_kb", 0), ("max_snapshot_kb", 10 ** 6),
    ("parity_strikes", 0), ("parity_strikes", 51), ("parity_strikes", 2.5),
    ("parity_strikes", True), ("parity_strikes", "5"),
    ("show_in_dropdown", 1), ("show_in_dropdown", "true"), ("show_in_dropdown", None),
])
def test_a_bad_capture_value_falls_back_to_the_default(monkeypatch, key, bad):
    monkeypatch.setattr(cfg, "load", lambda: {"capture": {key: bad}})
    assert cfg.capture()[key] == cfg.DEFAULTS["capture"][key]


def test_a_non_table_section_is_the_defaults(monkeypatch):
    monkeypatch.setattr(cfg, "load", lambda: {"capture": "x", "session": 5,
                                              "instruments": [1]})
    assert cfg.capture() == cfg.DEFAULTS["capture"]
    assert cfg.session() == cfg.DEFAULTS["session"]
    assert cfg.instruments() == cfg.DEFAULTS["instruments"]


@pytest.mark.parametrize("bad", ["25:00", "9", "", "17:60", 1700, None, True, "5pm"])
def test_a_bad_session_time_falls_back(monkeypatch, bad):
    monkeypatch.setattr(cfg, "load", lambda: {"session": {"open_ct": bad, "close_ct": bad}})
    assert cfg.session() == cfg.DEFAULTS["session"]


def test_a_session_time_is_normalised(monkeypatch):
    monkeypatch.setattr(cfg, "load", lambda: {"session": {"open_ct": "7:05"}})
    assert cfg.session()["open_ct"] == "07:05"


@pytest.mark.parametrize("key,bad", [
    ("multiplier", 0), ("multiplier", -50), ("multiplier", 2.5), ("multiplier", True),
    ("multiplier", "50"),
    ("strike_step", 0), ("strike_step", -5), ("strike_step", True), ("strike_step", "5"),
    ("strike_width_pct", 0), ("strike_width_pct", 80), ("strike_width_pct", True),
    ("parity_limit_pts", 0), ("parity_limit_pts", -1), ("parity_limit_pts", None),
    ("symbol", "ES"), ("symbol", "FOP.ES; DEL"), ("symbol", "FOP.TOOLONGNAME"),
    ("symbol", 5), ("label", ""), ("label", 3), ("exchange", ""), ("exchange", "x y"),
    ("weekday_roots", ["E{w}A"]), ("weekday_roots", "E1A"), ("weekday_roots", [1, 2, 3, 4]),
    ("weekday_roots", ["E{w}A", "E{w}B", "E{w}C", ""]),
    ("friday_root", ""), ("friday_root", "E W"), ("eom_root", None),
    ("quarterly_root", ["ES"]), ("friday_root", "A" * 30),
])
def test_a_bad_instrument_value_falls_back_to_that_instruments_default(
        monkeypatch, key, bad):
    monkeypatch.setattr(cfg, "load", lambda: {"instruments": {"ES": {key: bad}}})
    assert cfg.instruments()["ES"][key] == cfg.DEFAULTS["instruments"]["ES"][key]


def test_only_the_shipped_instruments_exist(monkeypatch):
    """A table for another root is not a feed: the symbol, the keys and the page
    all follow code that knows the two."""
    monkeypatch.setattr(cfg, "load", lambda: {"instruments": {"CL": {
        **cfg.DEFAULTS["instruments"]["ES"], "symbol": "FOP.CL"}}})
    assert list(cfg.instruments()) == ["ES", "NQ"]


def test_a_returned_instrument_is_a_copy(monkeypatch):
    """Callers may sort or edit what they get without moving the cached config."""
    got = cfg.instruments()
    got["ES"]["weekday_roots"].append("zzz")
    got["ES"]["multiplier"] = 1
    assert cfg.instruments()["ES"]["multiplier"] == 50
    assert cfg.instruments()["ES"]["weekday_roots"] == ["E{w}A", "E{w}B", "E{w}C", "E{w}D"]


def test_it_imports_only_the_config_loader_and_the_paths():
    """Tier 1 imports this module, so its import set is pinned (CLAUDE.md, the
    Tier-1 allow-list): shared.config_toml and repo_paths, as gamma_heat_config."""
    tree = ast.parse(pathlib.Path(cfg.__file__).read_text(encoding="utf-8"))
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            mods.add(node.module)
    assert mods == {"repo_paths", "shared.config_toml"}

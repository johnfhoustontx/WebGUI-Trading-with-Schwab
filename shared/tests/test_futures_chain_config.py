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
        "fill_step_sec": 300, "outage_after_sec": 450, "max_outage_columns": 288,
        "frozen_before_expiry_min": 60, "alert_repeat_min": 240,
        "max_snapshot_kb": 512, "parity_strikes": 5, "show_in_dropdown": False,
        "min_read_fraction": 0.25, "fill_settle_sec": 5, "fill_wait_max_sec": 20,
        "probe_round_steps": 10, "probe_reach": 3, "recentre_share": 0.5,
        "ssh_timeout_sec": 60, "no_plan_retry_sec": 60, "plan_retry_max_sec": 900,
        "hang_limit_sec": 600, "hang_exit_after_sec": 240, "store_busy_wait_sec": 30}
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
    ("min_read_fraction", 0), ("min_read_fraction", 1.5), ("fill_settle_sec", 0),
    ("fill_wait_max_sec", 1), ("probe_round_steps", 0), ("probe_reach", 0),
    ("probe_reach", 2.5), ("recentre_share", 0), ("recentre_share", 2),
    ("ssh_timeout_sec", 1), ("no_plan_retry_sec", 0), ("plan_retry_max_sec", 1),
    ("hang_limit_sec", 1), ("hang_limit_sec", True),
])
def test_a_bad_capture_value_falls_back_to_the_default(monkeypatch, key, bad):
    monkeypatch.setattr(cfg, "load", lambda: {"capture": {key: bad}})
    assert cfg.capture()[key] == cfg.DEFAULTS["capture"][key]


@pytest.mark.parametrize("bad", [float("inf"), float("-inf"), float("nan")])
@pytest.mark.parametrize("key", ["interval_sec", "fill_step_sec", "min_fill_fraction"])
def test_a_value_that_is_not_finite_is_the_default_and_never_raises(monkeypatch, key, bad):
    """TOML can spell ``inf`` and ``nan``, and ``int()`` raises on both. A whole-number
    key and a fraction key alike."""
    monkeypatch.setattr(cfg, "load", lambda: {"capture": {key: bad}})
    assert cfg.capture()[key] == cfg.DEFAULTS["capture"][key]


@pytest.mark.parametrize("bad", [float("inf"), float("nan")])
def test_a_multiplier_that_is_not_finite_is_the_default(monkeypatch, bad):
    monkeypatch.setattr(cfg, "load", lambda: {"instruments": {"ES": {"multiplier": bad}}})
    assert cfg.instruments()["ES"]["multiplier"] == 50


@pytest.mark.parametrize("bad", [59, 3601, True, "300"])
def test_the_exit_after_a_hang_refuses_a_bad_value(monkeypatch, bad):
    monkeypatch.setattr(cfg, "load", lambda: {"capture": {"hang_exit_after_sec": bad}})
    assert cfg.capture()["hang_exit_after_sec"] == 240


@pytest.mark.parametrize("bad", [0, 301, True, "30"])
def test_the_stores_busy_wait_refuses_a_bad_value(monkeypatch, bad):
    monkeypatch.setattr(cfg, "load", lambda: {"capture": {"store_busy_wait_sec": bad}})
    assert cfg.capture()["store_busy_wait_sec"] == 30


# ── how far apart a column is, and what counts as an outage ──────────────────

@pytest.mark.parametrize("interval,step,column", [
    (300, 300, 300),     # as shipped: one column per capture
    (120, 300, 120),     # rows are 120 apart, whatever the step says
    (300, 100, 100),     # each capture repeated to the step
    (300, 600, 300),
])
def test_a_column_is_the_smaller_of_the_step_and_the_interval(monkeypatch, interval, step, column):
    monkeypatch.setattr(cfg, "load", lambda: {
        "capture": {"interval_sec": interval, "fill_step_sec": step}})
    assert cfg.column_step() == column


def test_the_shipped_outage_tolerance_is_450():
    assert cfg.outage_after() == 450


@pytest.mark.parametrize("interval,after", [
    (120, 180),    # one and a half intervals: a capture missed at 120 is a gap of 240
    (600, 900),    # and a late capture at 600 (660) is not an outage
    (60, 90),
    (30, 60),      # rows are floored to the minute: every row would be an outage at 45
])
def test_the_outage_tolerance_follows_the_interval(monkeypatch, interval, after):
    monkeypatch.setattr(cfg, "load", lambda: {"capture": {"interval_sec": interval}})
    assert cfg.outage_after() == after


def test_a_changed_tolerance_is_read_and_scales_too(monkeypatch):
    monkeypatch.setattr(cfg, "load", lambda: {
        "capture": {"interval_sec": 300, "outage_after_sec": 700}})
    assert cfg.outage_after() == 700
    monkeypatch.setattr(cfg, "load", lambda: {
        "capture": {"interval_sec": 150, "outage_after_sec": 700}})
    assert cfg.outage_after() == 350


def test_a_tolerance_below_the_interval_reads_as_the_interval(monkeypatch):
    monkeypatch.setattr(cfg, "load", lambda: {"capture": {"outage_after_sec": 30}})
    assert cfg.outage_after() == 300


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

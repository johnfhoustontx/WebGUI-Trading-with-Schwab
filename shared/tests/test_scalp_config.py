"""config/scalp.toml: the Opening Scalp's tunables.

Every accessor must return its built-in default for a missing file, key or
unusable value and never raise (a typo in a config file must not stop the
morning call), and the shipped file must agree with those defaults."""
import math
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from shared import scalp_config as sc
from shared import symbols as _symbols

_BIG10 = list(_symbols.big10())


def _point_at(monkeypatch, tmp_path, text=None):
    """Aim the loader at a test file (or a missing one) and drop its cache."""
    path = tmp_path / "scalp.toml"
    if text is not None:
        path.write_text(text, encoding="utf-8")
    load, reset = sc.toml_loader(path, sc.DEFAULTS, label="scalp.toml")
    monkeypatch.setattr(sc, "load", load)
    monkeypatch.setattr(sc, "reset_cache", reset)
    reset()


def test_defaults_when_file_missing(monkeypatch, tmp_path):
    _point_at(monkeypatch, tmp_path)
    assert sc.target_pct() == 50
    assert sc.stop_pct() == 50
    assert sc.time_stop_min() == 60
    assert sc.otm_steps() == 0
    assert sc.width("$SPX") == 5
    assert sc.width("SPY") == 1
    assert sc.width("QQQ") == 1
    assert sc.strike_step("$SPX") == 5
    assert sc.strike_step("SPY") == 1
    assert sc.strike_step("QQQ") == 1
    assert sc.iv_scale() == 1.0
    assert sc.iv_scales() == [0.8, 1.0, 1.25, 1.5]
    assert sc.buckets() == [0.4, 0.7]
    assert sc.symbols() == ["$SPX", "SPY", "QQQ"]


def test_the_shipped_file_matches_the_defaults():
    sc.reset_cache()
    assert (sc.target_pct(), sc.stop_pct(), sc.time_stop_min(), sc.otm_steps(),
            sc.iv_scale(), sc.min_premium(), sc.chain_strikes()) == (50, 50, 60, 0, 1.0, 0.05, 10)
    assert (sc.dead_zone_pct(), sc.dead_zone_frac(), sc.open_dead_zone_pct(),
            sc.buckets()) == (0.05, 0.1, 0.03, [0.4, 0.7])
    assert (sc.iv_scales(), sc.min_samples(), sc.scorecard_days(),
            sc.news_items()) == ([0.8, 1.0, 1.25, 1.5], 20, 20, 5)
    assert sc.symbols() == ["$SPX", "SPY", "QQQ"]
    # the big ten follow the original thirteen (intraday calls read their bars)
    assert sc.bar_symbols() == ["$SPX", "SPY", "QQQ", "/ES", "/NQ", "IGV", "SMH",
                                "EWY", "XLE", "XLK", "XLV", "$VIX", "$VXN",
                                *_BIG10]


def test_the_shipped_file_has_exactly_the_default_keys():
    # A key in the file the loader does not default is a key nothing reads; a
    # default the file lacks is a setting the operator cannot see.
    import tomllib
    import repo_paths
    with open(repo_paths.SCALP_TOML, "rb") as fh:
        shipped = tomllib.load(fh)
    assert shipped == sc.DEFAULTS


def test_the_two_new_paths_are_where_the_design_puts_them():
    import repo_paths
    assert repo_paths.SCALP_TOML.name == "scalp.toml"
    assert repo_paths.SCALP_TOML.parent.name == "config"
    assert repo_paths.SCALP_DB.name == "scalp.db"
    assert repo_paths.SCALP_DB.parent.parts[-2:] == ("options_svc", "data")


def test_bad_values_fall_back(monkeypatch, tmp_path):
    _point_at(monkeypatch, tmp_path, """
[trade]
target_pct = "x"
stop_pct = -5
time_stop_min = 2.5
otm_steps = true
iv_scale = 0
min_premium = -1
chain_strikes = 0
[direction]
dead_zone_pct = "wide"
buckets = [0.9, 0.1]
[study]
iv_scales = []
min_samples = 0
scorecard_days = "many"
[width]
SPY = -3
[strike_step]
SPY = 0
""")
    assert sc.target_pct() == 50
    assert sc.stop_pct() == 50
    assert sc.time_stop_min() == 60
    assert sc.otm_steps() == 0
    assert sc.iv_scale() == 1.0
    assert sc.min_premium() == 0.05
    assert sc.chain_strikes() == 10
    assert sc.dead_zone_pct() == 0.05
    assert sc.buckets() == [0.4, 0.7]
    assert sc.iv_scales() == [0.8, 1.0, 1.25, 1.5]
    assert sc.min_samples() == 20
    assert sc.scorecard_days() == 20
    assert sc.width("SPY") == 1
    assert sc.strike_step("SPY") == 1


@pytest.mark.parametrize("raw", [[0.4], [0.4, 0.7, 0.9], [0.0, 0.5], [0.5, 1.0],
                                 [0.7, 0.4], [0.5, 0.5], ["a", "b"], [True, 0.5],
                                 [float("nan"), 0.5], 0.5, "0.4,0.7"])
def test_unusable_buckets_are_the_default(monkeypatch, raw):
    monkeypatch.setattr(sc, "load", lambda: {"direction": {"buckets": raw}})
    assert sc.buckets() == [0.4, 0.7]


def test_good_buckets_are_read(monkeypatch):
    monkeypatch.setattr(sc, "load", lambda: {"direction": {"buckets": [0.3, 0.6]}})
    assert sc.buckets() == [0.3, 0.6]


@pytest.mark.parametrize("raw", [[], [1.0, 0], [1.0, -2], [1.0, "x"], [True], 1.25,
                                 [1.0, float("inf")], [float("nan")]])
def test_unusable_iv_scales_are_the_default(monkeypatch, raw):
    monkeypatch.setattr(sc, "load", lambda: {"study": {"iv_scales": raw}})
    assert sc.iv_scales() == [0.8, 1.0, 1.25, 1.5]


def test_good_iv_scales_are_read_as_floats(monkeypatch):
    monkeypatch.setattr(sc, "load", lambda: {"study": {"iv_scales": [1, 2.5]}})
    out = sc.iv_scales()
    assert out == [1.0, 2.5] and all(isinstance(v, float) for v in out)


def test_iv_scales_hands_back_a_copy(monkeypatch):
    # The config dict is the cached mapping; a caller that mutates its answer
    # must not change what the next caller reads.
    sc.reset_cache()
    first = sc.iv_scales()
    first.append(9.0)
    assert sc.iv_scales() == [0.8, 1.0, 1.25, 1.5]
    b = sc.buckets()
    b[0] = 0.99
    assert sc.buckets() == [0.4, 0.7]


def test_inputs_for_qqq_reads_nq_and_tech_etfs():
    sc.reset_cache()
    got = sc.inputs_for("QQQ")
    assert got["etfs"] == ["IGV", "SMH", "EWY", "XLK"]
    assert got["futures"] == "/NQ"
    assert got["vol"] == "$VXN"


@pytest.mark.parametrize("sym", ["SPY", "$SPX"])
def test_inputs_for_the_s_and_p_symbols(sym):
    sc.reset_cache()
    assert sc.inputs_for(sym) == {"futures": "/ES", "etfs": ["XLE", "XLK", "XLV"],
                                  "vol": "$VIX"}


def test_inputs_for_an_unknown_symbol_is_absent_not_borrowed():
    sc.reset_cache()
    assert sc.inputs_for("IWM") == {"futures": None, "etfs": [], "vol": None}


def test_inputs_for_hands_back_a_copy():
    sc.reset_cache()
    sc.inputs_for("QQQ")["etfs"].append("ZZZ")
    assert sc.inputs_for("QQQ")["etfs"] == ["IGV", "SMH", "EWY", "XLK"]


def test_inputs_for_drops_a_bad_entry_to_absent(monkeypatch):
    monkeypatch.setattr(sc, "load", lambda: {"inputs": {"QQQ": {
        "futures": 7, "etfs": ["xlk", 3, "", "XLK", "smh"], "vol": ""}}})
    assert sc.inputs_for("qqq") == {"futures": None, "etfs": ["XLK", "SMH"], "vol": None}


def test_per_symbol_lookup_ignores_case_and_space(monkeypatch):
    monkeypatch.setattr(sc, "load", lambda: {"width": {"$SPX": 10}, "strike_step": {"SPY": 0.5}})
    assert sc.width(" $spx ") == 10
    assert sc.strike_step("spy") == 0.5


def test_an_unknown_symbol_reads_as_the_spy_like_default(monkeypatch):
    monkeypatch.setattr(sc, "load", lambda: {})
    assert sc.width("IWM") == 1
    assert sc.strike_step("IWM") == 1
    assert sc.width(None) == 1


def test_an_empty_universe_is_the_default(monkeypatch):
    monkeypatch.setattr(sc, "load", lambda: {"universe": {"symbols": []}})
    assert sc.symbols() == ["$SPX", "SPY", "QQQ"]


def test_symbols_are_cleaned_and_deduplicated(monkeypatch):
    monkeypatch.setattr(sc, "load", lambda: {"universe": {"symbols": ["spy", "SPY", 3, " qqq "]}})
    assert sc.symbols() == ["SPY", "QQQ"]


def test_bar_symbols_adds_an_input_the_list_forgot(monkeypatch):
    monkeypatch.setattr(sc, "load", lambda: {
        "universe": {"symbols": ["SPY"]},
        "bars": {"symbols": ["SPY", "/ES"]},
        "inputs": {"SPY": {"futures": "/ES", "etfs": ["XLE", "XLK"], "vol": "$VIX"}}})
    assert sc.bar_symbols() == ["SPY", "/ES", "XLE", "XLK", "$VIX", *_BIG10]


def test_bar_symbols_always_holds_the_symbols_themselves(monkeypatch):
    monkeypatch.setattr(sc, "load", lambda: {
        "universe": {"symbols": ["SPY", "QQQ"]}, "bars": {"symbols": ["QQQ"]},
        "inputs": {}})
    assert sc.bar_symbols() == ["QQQ", "SPY", *_BIG10]


@pytest.mark.parametrize("raw", [-1, "x", None, True, float("nan"), float("inf")])
def test_a_bad_dead_zone_is_the_default(monkeypatch, raw):
    monkeypatch.setattr(sc, "load", lambda: {"direction": {
        "dead_zone_pct": raw, "dead_zone_frac": raw, "open_dead_zone_pct": raw}})
    assert (sc.dead_zone_pct(), sc.dead_zone_frac(), sc.open_dead_zone_pct()) == (0.05, 0.1, 0.03)


def test_a_zero_dead_zone_is_allowed(monkeypatch):
    monkeypatch.setattr(sc, "load", lambda: {"direction": {"dead_zone_pct": 0}})
    assert sc.dead_zone_pct() == 0.0


def test_every_float_accessor_is_finite_whatever_the_file_says(monkeypatch):
    monkeypatch.setattr(sc, "load", lambda: {"trade": {"target_pct": float("nan"),
                                                       "stop_pct": float("inf")}})
    assert math.isfinite(sc.target_pct()) and math.isfinite(sc.stop_pct())


def test_a_whole_number_float_is_read_for_an_int_setting(monkeypatch):
    monkeypatch.setattr(sc, "load", lambda: {"trade": {"time_stop_min": 45.0}})
    assert sc.time_stop_min() == 45 and isinstance(sc.time_stop_min(), int)


# ── the intraday calls (design 2026-10-20-scalp-intraday-calls) ──────────────
CT = ZoneInfo("America/Chicago")
_DAY = date(2026, 10, 20)


def test_intraday_slots_are_21_from_0851_to_1351():
    sc.reset_cache()
    slots = sc.intraday_slots(_DAY)
    assert len(slots) == 21
    assert slots[0] == "08:51" and slots[-1] == "13:51"
    assert slots[:3] == ["08:51", "09:06", "09:21"]
    assert slots == sorted(set(slots))


def test_entry_ts_floors_to_the_5_minute_boundary():
    sc.reset_cache()
    ts = sc.intraday_entry_ts(_DAY, "08:51")
    assert ts == int(datetime(2026, 10, 20, 8, 50, tzinfo=CT).timestamp())
    assert ts % 300 == 0
    # a slot already on a boundary stays put; one at :59 floors to :55
    assert sc.intraday_entry_ts(_DAY, "09:05") == int(
        datetime(2026, 10, 20, 9, 5, tzinfo=CT).timestamp())
    assert sc.intraday_entry_ts(_DAY, "09:59") == int(
        datetime(2026, 10, 20, 9, 55, tzinfo=CT).timestamp())


def test_slot_time_is_an_aware_central_datetime():
    got = sc.intraday_slot_time(_DAY, "13:51")
    assert got.tzinfo is not None and got.utcoffset().total_seconds() == -5 * 3600
    assert (got.hour, got.minute, got.second) == (13, 51, 0)


def test_blocks_cover_every_slot_once():
    sc.reset_cache()
    seen = {"morning": [], "midday": [], "afternoon": []}
    for slot in sc.intraday_slots(_DAY):
        block = sc.intraday_block(slot)
        assert block in seen, slot
        seen[block].append(slot)
    assert [len(v) for v in seen.values()] == [5, 10, 6]
    assert seen["morning"][0] == "08:51" and seen["morning"][-1] == "09:51"
    assert seen["midday"][0] == "10:06" and seen["midday"][-1] == "12:21"
    assert seen["afternoon"][0] == "12:36" and seen["afternoon"][-1] == "13:51"


def test_a_slot_outside_every_block_has_none():
    sc.reset_cache()
    assert sc.intraday_block("08:36") is None
    assert sc.intraday_block("14:06") is None
    assert sc.intraday_block("nonsense") is None
    assert sc.intraday_block(None) is None


def test_slots_keep_ct_across_dst():
    # Daylight time ended on 2026-11-01: the same wall-clock slot is a different
    # UTC instant, and still 08:51 on the Central clock both days.
    before = sc.intraday_slot_time(date(2026, 10, 30), "08:51")
    after = sc.intraday_slot_time(date(2026, 11, 2), "08:51")
    assert (before.hour, before.minute) == (8, 51)
    assert (after.hour, after.minute) == (8, 51)
    assert before.utcoffset().total_seconds() == -5 * 3600
    assert after.utcoffset().total_seconds() == -6 * 3600
    for d in (date(2026, 10, 30), date(2026, 11, 2)):
        ts = sc.intraday_entry_ts(d, "08:51")
        back = datetime.fromtimestamp(ts, CT)
        assert (back.hour, back.minute, back.second) == (8, 50, 0)


def test_bar_symbols_include_the_big10():
    sc.reset_cache()
    out = sc.bar_symbols()
    assert len(out) == len(set(out))
    assert all(sym in out for sym in _BIG10)
    # the existing list keeps its order and comes first
    assert out[:13] == ["$SPX", "SPY", "QQQ", "/ES", "/NQ", "IGV", "SMH", "EWY",
                        "XLE", "XLK", "XLV", "$VIX", "$VXN"]


def test_bar_symbols_do_not_duplicate_a_big10_member_already_listed(monkeypatch):
    monkeypatch.setattr(sc, "load", lambda: {
        "universe": {"symbols": ["SPY"]},
        "bars": {"symbols": ["SPY", _BIG10[0]]}, "inputs": {}})
    out = sc.bar_symbols()
    assert out == ["SPY", _BIG10[0], *_BIG10[1:]]


def test_the_intraday_defaults_are_the_specs_values():
    sc.reset_cache()
    assert (sc.intraday_grace_min(), sc.intraday_dead_zone_pct(),
            sc.breadth_agree()) == (5, 0.03, 0.6)
    assert sc.confirm_weights() == {"flow": 1.0, "netprem": 1.0, "dealer": 1.0,
                                    "sentiment": 0.5}
    assert (sc.flow_window_min(), sc.flow_margin(), sc.netprem_margin(),
            sc.confirm_max_age_min(), sc.promotion_sessions()) == (30, 2, 0.1, 5, 40)
    assert sc.confirm_cuts() == (-0.34, 0.34)
    assert sc.sides_majority() == 0.6


def test_bad_intraday_values_fall_back(monkeypatch):
    monkeypatch.setattr(sc, "load", lambda: {
        "intraday": {"start": "8:5x", "end": 7, "interval_min": 0, "grace_min": -1,
                     "dead_zone_pct": "wide", "breadth_agree": 2,
                     "promotion_sessions": 1.5,
                     "blocks": {"morning": {"start": "08:51"}}},
        "confirm": {"flow_window_min": 0, "flow_margin": True, "netprem_margin": -0.1,
                    "max_age_min": "soon", "cuts": [0.5, 0.2], "sides_majority": 0.5,
                    "weights": {"flow": -1, "netprem": "x", "dealer": float("nan"),
                                "sentiment": True}}})
    assert len(sc.intraday_slots(_DAY)) == 21
    assert sc.intraday_slots(_DAY)[0] == "08:51"
    assert sc.intraday_grace_min() == 5
    assert sc.intraday_dead_zone_pct() == 0.03
    assert sc.breadth_agree() == 0.6
    assert sc.promotion_sessions() == 40
    assert sc.flow_window_min() == 30
    assert sc.flow_margin() == 2
    assert sc.netprem_margin() == 0.1
    assert sc.confirm_max_age_min() == 5
    assert sc.sides_majority() == 0.6              # 0.5 is a tie, not a majority
    assert sc.confirm_cuts() == (-0.34, 0.34)
    assert sc.confirm_weights() == {"flow": 1.0, "netprem": 1.0, "dealer": 1.0,
                                    "sentiment": 0.5}
    # the block table was half-given, so the defaults apply whole
    assert [sc.intraday_block(s) for s in ("08:51", "10:06", "12:36")] == [
        "morning", "midday", "afternoon"]


def test_sides_majority_must_be_above_a_tie(monkeypatch):
    for bad in (0.4, 0.5, 1.2, True, "most", float("nan")):
        monkeypatch.setattr(sc, "load", lambda bad=bad: {"confirm": {"sides_majority": bad}})
        assert sc.sides_majority() == 0.6
    monkeypatch.setattr(sc, "load", lambda: {"confirm": {"sides_majority": 1}})
    assert sc.sides_majority() == 1.0


def test_breadth_min_members_is_a_whole_number_of_at_least_one(monkeypatch):
    sc.reset_cache()
    assert sc.breadth_min_members() == 5
    for bad in (0, -2, 2.5, True, "five", float("nan")):
        monkeypatch.setattr(sc, "load", lambda bad=bad: {"confirm": {"breadth_min_members": bad}})
        assert sc.breadth_min_members() == 5
    monkeypatch.setattr(sc, "load", lambda: {"confirm": {"breadth_min_members": 3}})
    assert sc.breadth_min_members() == 3


def test_a_start_after_the_end_is_the_default_schedule(monkeypatch):
    monkeypatch.setattr(sc, "load", lambda: {"intraday": {"start": "13:51", "end": "08:51"}})
    slots = sc.intraday_slots(_DAY)
    assert slots[0] == "08:51" and slots[-1] == "13:51" and len(slots) == 21


def test_good_intraday_values_are_read(monkeypatch):
    monkeypatch.setattr(sc, "load", lambda: {
        "intraday": {"start": "09:00", "end": "10:00", "interval_min": 30,
                     "grace_min": 3, "dead_zone_pct": 0.05, "breadth_agree": 0.7,
                     "promotion_sessions": 60},
        "confirm": {"flow_window_min": 20, "flow_margin": 3, "netprem_margin": 0.2,
                    "max_age_min": 4, "cuts": [-0.5, 0.25], "sides_majority": 0.75,
                    "weights": {"flow": 2, "netprem": 0, "dealer": 1.5, "sentiment": 0.25}}})
    assert sc.intraday_slots(_DAY) == ["09:00", "09:30", "10:00"]
    assert (sc.intraday_grace_min(), sc.intraday_dead_zone_pct(), sc.breadth_agree(),
            sc.promotion_sessions()) == (3, 0.05, 0.7, 60)
    assert (sc.flow_window_min(), sc.flow_margin(), sc.netprem_margin(),
            sc.confirm_max_age_min()) == (20, 3, 0.2, 4)
    assert sc.confirm_cuts() == (-0.5, 0.25)
    assert sc.sides_majority() == 0.75
    assert sc.confirm_weights() == {"flow": 2.0, "netprem": 0.0, "dealer": 1.5,
                                    "sentiment": 0.25}


def test_all_zero_weights_are_the_default_weights(monkeypatch):
    # a zero total would be a divide-by-zero in the scorer
    monkeypatch.setattr(sc, "load", lambda: {"confirm": {"weights": {
        "flow": 0, "netprem": 0, "dealer": 0, "sentiment": 0}}})
    assert sc.confirm_weights() == {"flow": 1.0, "netprem": 1.0, "dealer": 1.0,
                                    "sentiment": 0.5}


def test_good_blocks_are_read_and_overlapping_ones_are_not(monkeypatch):
    good = {"morning": {"start": "09:00", "end": "09:30"},
            "midday": {"start": "09:45", "end": "11:00"},
            "afternoon": {"start": "11:15", "end": "12:00"}}
    monkeypatch.setattr(sc, "load", lambda: {"intraday": {"blocks": good}})
    assert sc.intraday_block("09:15") == "morning"
    assert sc.intraday_block("10:00") == "midday"
    assert sc.intraday_block("08:51") is None
    overlap = dict(good, midday={"start": "09:20", "end": "11:00"})
    monkeypatch.setattr(sc, "load", lambda: {"intraday": {"blocks": overlap}})
    assert sc.intraday_block("08:51") == "morning"      # defaults whole
    assert sc.intraday_block("10:06") == "midday"


def test_intraday_config_hands_back_copies(monkeypatch):
    sc.reset_cache()
    sc.confirm_weights()["flow"] = 99.0
    assert sc.confirm_weights()["flow"] == 1.0
    sc.intraday_slots(_DAY).append("99:99")
    assert "99:99" not in sc.intraday_slots(_DAY)


def test_min_brier_margin_default_and_validation(monkeypatch):
    sc.reset_cache()
    assert sc.min_brier_margin() == 0.005
    for bad in (-0.001, 0.25, 0.3, "x", True, None, float("nan"), float("inf")):
        monkeypatch.setattr(sc, "load", lambda bad=bad: {"intraday": {"min_brier_margin": bad}})
        assert sc.min_brier_margin() == 0.005
    for good in (0, 0.01, 0.2499):
        monkeypatch.setattr(sc, "load", lambda good=good: {"intraday": {"min_brier_margin": good}})
        assert sc.min_brier_margin() == good


def test_vwap_max_missing_default_and_validation(monkeypatch):
    sc.reset_cache()
    assert sc.vwap_max_missing() == 0.25
    for bad in (-0.1, 1, 1.5, "x", True, None, float("nan")):
        monkeypatch.setattr(sc, "load", lambda bad=bad: {"intraday": {"vwap_max_missing": bad}})
        assert sc.vwap_max_missing() == 0.25
    for good in (0, 0.4, 0.999):
        monkeypatch.setattr(sc, "load", lambda good=good: {"intraday": {"vwap_max_missing": good}})
        assert sc.vwap_max_missing() == good


# --- the slot schedule's own checks (final review M4) ---------------------------

@pytest.mark.parametrize("intraday,why", [
    ({"start": "08:31", "end": "13:51"}, "before 08:45"),         # inside the 08:36 call
    ({"start": "08:21", "end": "13:51", "interval_min": 15}, "08:36 is a slot"),
    ({"start": "08:51", "end": "14:21"}, "14:20 + 60 passes 15:00"),
    ({"start": "13:51", "end": "08:51"}, "start after end"),
])
def test_an_unusable_schedule_is_the_default_schedule(monkeypatch, intraday, why):
    monkeypatch.setattr(sc, "load", lambda: {"intraday": intraday})
    assert sc.intraday_slots(_DAY) == [f"{h:02d}:{m:02d}" for h in range(8, 14)
                                       for m in (6, 21, 36, 51)
                                       if (8, 51) <= (h, m) <= (13, 51)], why


def test_schedule_problem_names_each_rule():
    m = sc._minutes
    assert sc.schedule_problem(m("08:51"), m("13:51"), 15, 60) is None
    assert sc.schedule_problem(m("08:45"), m("14:00"), 15, 60) is None   # 14:00+60 = 15:00
    assert "before 08:45" in sc.schedule_problem(m("08:40"), m("13:51"), 15, 60)
    assert "08:36" in sc.schedule_problem(m("08:36"), m("13:51"), 15, 60)
    assert "08:36" in sc.schedule_problem(m("08:21"), m("13:51"), 15, 60)
    assert "15:00" in sc.schedule_problem(m("08:51"), m("13:51"), 15, 90)
    # the last slot is the last one the step reaches, not the configured end
    assert sc.schedule_problem(m("08:51"), m("14:05"), 15, 60) is None   # last 13:51
    assert sc.schedule_problem(m("08:51"), m("14:10"), 15, 60) is not None  # 14:06
    assert "after the last" in sc.schedule_problem(m("10:00"), m("09:00"), 15, 60)


def test_a_longer_time_stop_makes_the_configured_schedule_unusable(monkeypatch):
    monkeypatch.setattr(sc, "load", lambda: {
        "intraday": {"start": "09:00", "end": "10:00", "interval_min": 30},
        "trade": {"time_stop_min": 330}})                  # 10:00 + 330 min = 15:30
    assert sc.intraday_slots(_DAY)[0] == "08:51"


# --- half days (final review M3) ------------------------------------------------

def test_early_close_defaults():
    sc.reset_cache()
    assert sc.early_close_days() == {date(2026, 11, 27), date(2026, 12, 24)}
    assert sc.early_close_time() == "12:00"
    assert sc.early_close_on(date(2026, 11, 27)) == datetime(
        2026, 11, 27, 12, 0, tzinfo=ZoneInfo("America/Chicago"))
    assert sc.early_close_on(_DAY) is None


@pytest.mark.parametrize("raw", ["2026-11-27", ["2026-13-01"], ["11/27/2026"], [20261127],
                                 None])
def test_unusable_early_close_days_are_the_defaults(monkeypatch, raw):
    monkeypatch.setattr(sc, "load", lambda: {"intraday": {"early_close_days": raw}})
    assert sc.early_close_days() == {date(2026, 11, 27), date(2026, 12, 24)}


def test_early_close_days_are_read_and_may_be_empty(monkeypatch):
    monkeypatch.setattr(sc, "load", lambda: {"intraday": {"early_close_days": ["2027-07-02"]}})
    assert sc.early_close_days() == {date(2027, 7, 2)}
    monkeypatch.setattr(sc, "load", lambda: {"intraday": {"early_close_days": []}})
    assert sc.early_close_days() == set()


@pytest.mark.parametrize("raw", ["8:00", "08:30", "15:01", "noon", 12, None])
def test_an_unusable_early_close_time_is_the_default(monkeypatch, raw):
    monkeypatch.setattr(sc, "load", lambda: {"intraday": {"early_close_time": raw}})
    assert sc.early_close_time() == "12:00"


def test_a_slot_is_cut_only_when_its_trade_passes_the_early_close(monkeypatch):
    sc.reset_cache()
    half = date(2026, 11, 27)
    # 10:51 enters on 10:50; + 60 = 11:50, inside. 11:06 enters on 11:05: 12:05.
    assert not sc.intraday_slot_cut(half, "10:51")
    assert sc.intraday_slot_cut(half, "11:06")
    assert sc.intraday_slot_cut(half, "13:51")
    assert not sc.intraday_slot_cut(_DAY, "13:51")             # a full session
    assert not sc.intraday_slot_cut(half, "11:06", stop_min=50)  # 11:05 + 50 = 11:55
    monkeypatch.setattr(sc, "load", lambda: {"intraday": {"early_close_time": "11:00"}})
    assert sc.intraday_slot_cut(half, "10:06")                 # 10:05 + 60 = 11:05
    # the schedule itself is the same every session: the cut is the caller's
    assert sc.intraday_slots(half) == sc.intraday_slots(_DAY)


def test_min_sources_default_and_validation(monkeypatch):
    sc.reset_cache()
    assert sc.confirm_min_sources() == 2
    for bad in (0, 5, 1.5, True, "two", float("nan")):
        monkeypatch.setattr(sc, "load", lambda bad=bad: {"confirm": {"min_sources": bad}})
        assert sc.confirm_min_sources() == 2
    for good in (1, 3, 4):
        monkeypatch.setattr(sc, "load", lambda good=good: {"confirm": {"min_sources": good}})
        assert sc.confirm_min_sources() == good


# ── [prob]: the fitted chance of target (design 2026-10-22) ──────────────────
_PROB_PRIORS = {"slope_5": 0.15, "slope_15": 0.10, "slope_60": 0.05,
                "rsi_5": 0.10, "rsi_15": 0.07, "rsi_60": 0.04,
                "room": 0.15, "breadth_room": 0.05, "flip_side": 0.0,
                "netprem": 0.10, "flow": 0.10, "sentiment": 0.05}


def _prob_defaults():
    return (sc.prob_ema_period(), sc.prob_slope_bars(), sc.prob_rsi_period(),
            sc.prob_timeframes(), sc.prob_lookback_sessions(), sc.prob_cap(),
            sc.prob_room_cap(), sc.prob_flow_cap(), sc.prob_priors(),
            sc.prob_prior_sd(), sc.prob_min_train_weeks(),
            sc.prob_min_brier_margin(), sc.prob_refit_weekday(), sc.prob_show(),
            sc.prob_assumed_share())


_PROB_DEFAULTS = (20, 3, 14, [5, 15, 60], 10, 3.0, 3.0, 3, _PROB_PRIORS,
                  {"wide": 1.0, "tight": 0.10}, 8, 0.005, 5, True, 0.5)


def test_prob_defaults(monkeypatch, tmp_path):
    _point_at(monkeypatch, tmp_path)                    # no file at all
    assert _prob_defaults() == _PROB_DEFAULTS


def test_the_shipped_prob_section_is_the_defaults():
    sc.reset_cache()
    assert _prob_defaults() == _PROB_DEFAULTS


def test_prob_bad_values_read_as_defaults(monkeypatch):
    bad = {"ema_period": 0, "slope_bars": 2.5, "rsi_period": True,
           "timeframes": [7, 15], "lookback_sessions": -1, "cap": 0,
           "room_cap": float("nan"), "flow_cap": "three",
           "priors": {"slope_5": "x", "room": float("inf"), "netprem": True},
           "prior_sd": {"wide": 0},
           "min_train_weeks": 0, "min_brier_margin": 0.25, "refit_weekday": 9,
           "show": "yes", "assumed_share": 0}
    monkeypatch.setattr(sc, "load", lambda: {"prob": bad})
    assert _prob_defaults() == _PROB_DEFAULTS


def test_prob_timeframes_must_be_ascending_multiples_of_five(monkeypatch):
    for bad in ([], [15, 5], [5, 5], [5, 12], [0, 5], [5.5], "5", [True, 15], [-5, 5]):
        monkeypatch.setattr(sc, "load", lambda bad=bad: {"prob": {"timeframes": bad}})
        assert sc.prob_timeframes() == [5, 15, 60]
    monkeypatch.setattr(sc, "load", lambda: {"prob": {"timeframes": [5, 30, 120]}})
    assert sc.prob_timeframes() == [5, 30, 120]
    monkeypatch.setattr(sc, "load", lambda: {"prob": {"timeframes": [10.0, 20]}})
    assert sc.prob_timeframes() == [10, 20]


def test_prob_prior_sd_needs_both_spreads_positive(monkeypatch):
    for bad in ({"wide": 0}, {"wide": 1.0, "tight": -1}, {"tight": 0.2},
                {"wide": "a", "tight": 0.1}, "no", {"wide": float("nan")}):
        monkeypatch.setattr(sc, "load", lambda bad=bad: {"prob": {"prior_sd": bad}})
        assert sc.prob_prior_sd() == {"wide": 1.0, "tight": 0.10}
    monkeypatch.setattr(sc, "load", lambda: {"prob": {"prior_sd": {"wide": 2, "tight": 0.2}}})
    assert sc.prob_prior_sd() == {"wide": 2.0, "tight": 0.2}


def test_prob_refit_weekday_is_zero_to_six(monkeypatch):
    for bad in (-1, 7, 9, 5.5, True, "5", float("nan")):
        monkeypatch.setattr(sc, "load", lambda bad=bad: {"prob": {"refit_weekday": bad}})
        assert sc.prob_refit_weekday() == 5
    for good in (0, 3, 6):
        monkeypatch.setattr(sc, "load", lambda good=good: {"prob": {"refit_weekday": good}})
        assert sc.prob_refit_weekday() == good


def test_prob_priors_unknown_key_kept_out(monkeypatch, tmp_path):
    _point_at(monkeypatch, tmp_path, "[prob.priors]\nfoo = 1\nroom = 0.3\n")
    got = sc.prob_priors()
    assert "foo" not in got
    assert got == dict(_PROB_PRIORS, room=0.3)
    assert set(got) == set(_PROB_PRIORS)


def test_prob_priors_allow_zero_and_negative_but_not_junk(monkeypatch):
    # a prior is an assumed push: zero means "no push assumed", a negative one is
    # a push against the call; neither is the default's business
    monkeypatch.setattr(sc, "load", lambda: {"prob": {"priors": {
        "slope_5": 0, "rsi_5": -0.2, "room": "x", "flow": float("nan")}}})
    got = sc.prob_priors()
    assert got["slope_5"] == 0.0 and got["rsi_5"] == -0.2
    assert got["room"] == 0.15 and got["flow"] == 0.10


def test_good_prob_values_are_read(monkeypatch):
    monkeypatch.setattr(sc, "load", lambda: {"prob": {
        "ema_period": 9, "slope_bars": 4, "rsi_period": 7, "lookback_sessions": 3,
        "cap": 2, "room_cap": 1.5, "flow_cap": 2, "min_train_weeks": 12,
        "min_brier_margin": 0, "refit_weekday": 6, "show": False,
        "assumed_share": 0.75}})
    assert (sc.prob_ema_period(), sc.prob_slope_bars(), sc.prob_rsi_period(),
            sc.prob_lookback_sessions(), sc.prob_cap(), sc.prob_room_cap(),
            sc.prob_flow_cap(), sc.prob_min_train_weeks(),
            sc.prob_min_brier_margin(), sc.prob_refit_weekday(), sc.prob_show(),
            sc.prob_assumed_share()) == (9, 4, 7, 3, 2.0, 1.5, 2, 12, 0.0, 6, False, 0.75)


def test_prob_assumed_share_is_strictly_between_zero_and_one(monkeypatch):
    for bad in (0, 1, 1.5, -0.1, True, "0.5", float("nan"), None):
        monkeypatch.setattr(sc, "load", lambda bad=bad: {"prob": {"assumed_share": bad}})
        assert sc.prob_assumed_share() == 0.5
    for good in (0.01, 0.3, 0.99):
        monkeypatch.setattr(sc, "load", lambda good=good: {"prob": {"assumed_share": good}})
        assert sc.prob_assumed_share() == good


def test_prob_config_hands_back_copies(monkeypatch):
    sc.reset_cache()
    sc.prob_priors()["room"] = 99.0
    sc.prob_prior_sd()["wide"] = 99.0
    sc.prob_timeframes().append(999)
    assert sc.prob_priors()["room"] == 0.15
    assert sc.prob_prior_sd()["wide"] == 1.0
    assert sc.prob_timeframes() == [5, 15, 60]


def test_the_scalp_prob_slot_is_in_the_shipped_calendar():
    from shared import market_calendar as mc
    assert mc.slot_grace_min("scalp_prob") == 180
    (name, at), = mc.slot_times("scalp_prob").items()
    assert name == "refit" and (at.hour, at.minute) == (8, 0)

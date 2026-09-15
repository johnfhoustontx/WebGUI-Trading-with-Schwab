"""Tests for the shared Trade detail panel pure helpers."""
from pages.options import detail, theme


def test_pop_color_thresholds():
    assert detail.pop_color(75) == detail.GREEN
    assert detail.pop_color(60) == detail.AMBER
    assert detail.pop_color(40) == detail.RED


def test_pop_color_returns_state_class_tokens():
    assert detail.pop_color(75) == theme.TXT_POS
    assert detail.pop_color(60) == theme.TXT_WARN
    assert detail.pop_color(40) == theme.TXT_NEG
    assert detail.pop_color("n/a") == theme.TXT_NEUTRAL


def test_flag_class_returns_state_token_classes():
    # flag_class is now the only place the panel maps a state to a color on a
    # PERSISTENT element (the header flag labels are rebuilt, but pop_color still
    # colors a body row). Both must stay inside the finite token set so a class
    # can never be built from a runtime value. The tile color-fns this replaces
    # are gone with the 2x2 grid; pop_color's own coverage is the test above.
    allowed = set(theme.STATE_TEXT_CLASSES.split())
    for state in ("tripped", "unmeasured", "unavailable", "", None):
        assert detail.flag_class(state) in allowed


def test_flag_badge_text_hides_zero():
    assert detail.flag_badge_text(0) == ""
    assert detail.flag_badge_text(2) == "2"
    assert detail.flag_badge_text(12) == "9+"


def test_flag_badge_text_rejects_non_counts():
    # flag_count feeds this, but the badge must not render junk if it ever gets
    # something else — and bool is an int subclass, so True must not read as "1".
    for junk in (None, -1, "3", 1.5, True, False):
        assert detail.flag_badge_text(junk) == ""


def test_factor_rows_returns_11_for_non_ic():
    fs = {"rr": 80, "pop": 62, "theta": 60, "iv": 94, "iv_hv": 46, "vega": 75,
          "em": 19, "liq": 0, "trend": 75, "gex": 100, "dex": 83}
    rows = detail.factor_rows(fs, "PCS")
    labels = [label for label, _val, _known in rows]
    assert "R:R" in labels and "DEX" in labels
    assert len(rows) == 11
    # every factor was supplied, so every row is a measured reading
    assert all(known for _label, _val, known in rows)


def test_factor_rows_ic_variant():
    rows = detail.factor_rows({"pcs_leg": 70, "ccs_leg": 65, "delta_bonus": -2}, "IC")
    labels = [label for label, _val, _known in rows]
    assert "Put leg" in labels and "Call leg" in labels


def test_factor_rows_missing_values_are_unknown_not_zero():
    rows = detail.factor_rows({}, "PCS")
    assert all(known is False and val is None for _label, val, known in rows)


def test_factor_rows_marks_absent_factor_as_unknown():
    rows = detail.factor_rows({"rr": 80}, "PCS")
    by_label = {label: (val, known) for label, val, known in rows}
    assert by_label["R:R"] == (80, True)
    assert by_label["PoP"][1] is False       # absent -> unknown, NOT 0


def test_factor_rows_respects_explicit_unavailable_list():
    # Tier 2 may emit factors_unavailable; a present-but-sentinel value is then
    # known to be missing rather than merely suspected.
    rows = detail.factor_rows({"liq": 50}, "PCS", unavailable=["liq"])
    by_label = {label: known for label, _val, known in rows}
    assert by_label["Liquidity"] is False


def test_factor_rows_keeps_a_genuine_zero_known():
    rows = detail.factor_rows({"liq": 0}, "PCS")
    by_label = {label: (val, known) for label, val, known in rows}
    assert by_label["Liquidity"] == (0, True)   # a real wide-spread reading


# ── factor_rows: the THIRD vocabulary (swing / Strategy Finder) ─────────────
# strategy_scoring.py:664 scores every multi-family structure on
# {fit_dir, fit_vol, q_rr, q_be, q_pop, q_liq} -- reached from /options/swing AND
# the scanner's Directional subtab. factor_rows knew only the scanner's eleven and
# the IC's three, so it looked up keys that do not exist and rendered an
# all-em-dash card while six real, computed quality factors sat in the payload.
SWING_FS = {"fit_dir": 72.0, "fit_vol": 41.5, "q_rr": 88.0,
            "q_be": 55.0, "q_pop": 63.0, "q_liq": 90.0}


def test_factor_rows_swing_vocabulary():
    rows = detail.factor_rows(SWING_FS, "LONG_CALL")
    assert len(rows) == 6
    by_label = {label: (val, known) for label, val, known in rows}
    assert by_label == {
        "R:R": (88.0, True),
        "Breakeven vs move": (55.0, True),
        "PoP": (63.0, True),
        "Liquidity": (90.0, True),
        "Direction fit": (72.0, True),
        "Volatility fit": (41.5, True),
    }


def test_factor_rows_swing_orders_quality_before_fit():
    # Quality dominates the composite (0.7*quality + 0.3*fit) and within each
    # group the rows follow that group's own weights, so the card reads in order
    # of actual influence.
    labels = [label for label, _v, _k in detail.factor_rows(SWING_FS, "LONG_CALL")]
    assert labels == ["R:R", "Breakeven vs move", "PoP", "Liquidity",
                      "Direction fit", "Volatility fit"]


def test_factor_rows_swing_detected_by_shape_not_trade_type():
    """The misroute the shape-based check exists to prevent.

    A swing signal's ``type`` can be "PCS" or "IC" (adapt_credit_spread /
    adapt_iron_condor re-score existing scanner structures on the swing model), so
    branching on the type would send a swing-scored iron condor into the IC branch
    and render three em-dashes for pcs_leg/ccs_leg/delta_bonus that it never had.
    """
    for trade_type in ("IC", "PCS", None, ""):
        rows = detail.factor_rows(SWING_FS, trade_type)
        labels = [label for label, _v, _k in rows]
        assert labels[0] == "R:R" and len(rows) == 6, trade_type
        assert "Put leg" not in labels


def test_factor_rows_swing_partial_dict_still_routes_to_swing():
    # One q_*/fit_* key is proof of the shape; the absent ones stay unknown rather
    # than falling back to the scanner's eleven.
    rows = detail.factor_rows({"q_liq": 70.0}, "PCS")
    by_label = {label: (val, known) for label, val, known in rows}
    assert len(rows) == 6
    assert by_label["Liquidity"] == (70.0, True)
    assert by_label["PoP"] == (None, False)      # absent -> unknown, NOT 0


def test_factor_rows_swing_keeps_provenance_semantics():
    # A present 0 is a real reading; an explicit factors_unavailable entry is not.
    rows = detail.factor_rows({**SWING_FS, "q_liq": 0}, "LONG_PUT")
    assert dict((l, k) for l, _v, k in rows)["Liquidity"] is True
    assert dict((l, v) for l, v, _k in rows)["Liquidity"] == 0
    rows = detail.factor_rows(SWING_FS, "LONG_PUT", unavailable=["q_liq"])
    by_label = {label: (val, known) for label, val, known in rows}
    assert by_label["Liquidity"] == (None, False)


def test_factor_value_text_shows_dash_for_unknown():
    assert detail.factor_value_text(80, True) == "80"
    assert detail.factor_value_text(None, False) == "—"


def test_flag_inside_expected_move():
    flags = detail.flags_for({"factor_scores": {"em": 30}})
    assert any(f["key"] == "em" and f["state"] == "tripped" for f in flags)


def test_no_em_flag_when_outside_the_move():
    flags = detail.flags_for({"factor_scores": {"em": 70}})
    assert not any(f["key"] == "em" for f in flags)


def test_flag_thin_liquidity_only_when_measured():
    sig = {"factor_scores": {"liq": 20}, "bid": 1.10, "ask": 1.20}
    assert any(f["key"] == "liq" and f["state"] == "tripped"
               for f in detail.flags_for(sig))


def test_liquidity_unmeasured_when_bid_ask_absent():
    flags = detail.flags_for({"factor_scores": {"liq": 50}})
    liq = [f for f in flags if f["key"] == "liq"]
    assert liq and liq[0]["state"] == "unmeasured"


def test_flag_thin_credit_uses_rr_pct():
    # rr_pct always rides on a SCORED signal -- every scanner spread and IC
    # carries factor_scores too -- so the fixture keeps a scanner-shaped score.
    # A bare {"rr_pct": ...} is the score-less shape, which is silent by design.
    def sig(rr):
        return {"rr_pct": rr, "factor_scores": {"liq": 95}, "bid": 1.10, "ask": 1.12}
    assert any(f["key"] == "rr" for f in detail.flags_for(sig(12)))
    assert not any(f["key"] == "rr" for f in detail.flags_for(sig(35)))


def test_flag_near_gamma_wall():
    assert any(f["key"] == "gex" for f in detail.flags_for({"factor_scores": {"gex": 10}}))


def test_clean_signal_has_no_flags():
    sig = {"rr_pct": 40, "bid": 1.10, "ask": 1.12,
           "factor_scores": {"em": 80, "liq": 95, "trend": 90, "gex": 90, "dex": 90}}
    assert detail.flags_for(sig) == []


def test_flags_never_raise_on_garbage():
    for bad in (None, {}, {"factor_scores": None}, {"factor_scores": {"em": "x"}},
                {"rr_pct": "n/a"}):
        assert isinstance(detail.flags_for(bad), list)


def test_flag_count_counts_only_tripped_and_unmeasured():
    assert detail.flag_count({"factor_scores": {"em": 30, "gex": 10}}) >= 2


# --- flags fire only on REAL dealbreakers -----------------------------------
# The inferred "== 50.0 means unmeasured" sentinel was removed: a realistic
# swing signal lands on exactly 50.0 for em/gex/dex (no walls, no sized EM) AND
# for trend (a genuine NEUTRAL reading), so it raised four "not measured" chips
# on an ordinary trade -- flags became wallpaper.

def test_realistic_swing_signal_raises_no_flags():
    # Values taken verbatim from the REAL scoring.calc_composite_score output for
    # an ordinary 7-DTE PCS, so this pins actual scorer behavior, not a guess.
    sig = {"type": "PCS", "rr_pct": 30, "bid": 1.10, "ask": 1.13,
           "factor_scores": {"rr": 60.0, "pop": 55.6, "theta": 100.0, "iv": 60,
                             "iv_hv": 50.0, "vega": 84.0, "em": 50.0, "liq": 57.7,
                             "trend": 50.0, "gex": 50.0, "dex": 50.0}}
    assert detail.flags_for(sig) == []


def test_neutral_trend_raises_no_trend_flag():
    # norm_trend returns exactly 50.0 for a real NEUTRAL trend (scoring.py:250),
    # which means "not against the structure" -- so silence is the right answer.
    sig = {"factor_scores": {"trend": 50}, "bid": 1.1, "ask": 1.12}
    assert not any(f["key"] == "trend" for f in detail.flags_for(sig))


def test_explicit_unavailable_still_reports_unmeasured():
    # The sentinel is gone but the EXACT Tier-2 path must survive. Value 80 would
    # read as measured-and-fine on its own; factors_unavailable overrides it.
    flags = detail.flags_for({"factor_scores": {"em": 80},
                              "factors_unavailable": ["em"],
                              "bid": 1.1, "ask": 1.12})
    em = [f for f in flags if f["key"] == "em"]
    assert em and em[0]["state"] == "unmeasured"


# --- iron condors ------------------------------------------------------------
# A real IC carries factor_scores {pcs_leg, ccs_leg, delta_bonus} only
# (scanner_engine.py:1654), so em/trend/gex/dex/liq cannot be checked at all. A
# flagless IC must never read as a clean IC.

def test_iron_condor_reports_checks_unavailable():
    sig = {"type": "IC", "rr_pct": 30, "bid": 1.10, "ask": 1.13,
           "factor_scores": {"pcs_leg": 70, "ccs_leg": 65, "delta_bonus": -2}}
    flags = detail.flags_for(sig)
    assert len(flags) == 1
    assert flags[0]["key"] == "ic" and flags[0]["state"] == "unavailable"
    assert detail.flag_count(sig) >= 1


def test_iron_condor_detected_without_a_type_field():
    # pcs_leg in factor_scores is proof of the IC shape even if `type` is absent.
    sig = {"factor_scores": {"pcs_leg": 70, "ccs_leg": 65, "delta_bonus": -2}}
    assert any(f["key"] == "ic" for f in detail.flags_for(sig))


def test_iron_condor_still_checks_credit_vs_risk():
    # rr_pct IS present on IC signals (scanner_engine.py:1065), so it is the one
    # dealbreaker that genuinely works for them.
    sig = {"type": "IC", "rr_pct": 12,
           "factor_scores": {"pcs_leg": 70, "ccs_leg": 65, "delta_bonus": -2}}
    keys = [f["key"] for f in detail.flags_for(sig)]
    assert "ic" in keys and "rr" in keys


# --- swing / Strategy Finder shape ------------------------------------------
# Third vocabulary: {fit_dir, fit_vol, q_rr, q_be, q_pop, q_liq}
# (strategy_scoring.py:664). Its thresholds are NOT the scanner's -- q_be rewards
# a breakeven INSIDE the expected move, the opposite of norm_em_buffer -- so
# flags come from the engine's own per-family gates via grade_reason.

_SWING_FS = {"fit_dir": 70.0, "fit_vol": 60.0, "q_rr": 55.0,
             "q_be": 40.0, "q_pop": 65.0, "q_liq": 80.0}


def test_swing_failed_gates_become_tripped_flags():
    sig = {"type": "LONG_CALL", "factor_scores": dict(_SWING_FS),
           "grade_reason": "Fails: liquidity, R:R"}
    flags = detail.flags_for(sig)
    keys = [f["key"] for f in flags]
    assert keys == ["gate_liquidity", "gate_rr"]
    assert all(f["state"] == "tripped" for f in flags)


def test_swing_naked_capital_efficiency_gate_becomes_a_readable_flag():
    """evaluate_gates names a NAKED reward failure "capital efficiency", not
    "R:R" (a naked short has no R:R). It must map to a real chip rather than
    falling through to the generated "Fails ... quality gate" label."""
    sig = {"type": "SHORT_PUT", "factor_scores": dict(_SWING_FS),
           "grade_reason": "Fails: capital efficiency"}
    flags = detail.flags_for(sig)
    assert [f["key"] for f in flags] == ["gate_rr"]
    assert flags[0]["label"] == "Reward too thin for the capital tied up"


def test_swing_passing_gates_raise_no_flags():
    for reason in ("Passes all quality gates", "Excellent on all quality gates",
                   "Fillable but middling quality"):
        sig = {"type": "BULL_CALL", "factor_scores": dict(_SWING_FS),
               "grade_reason": reason}
        assert detail.flags_for(sig) == [], reason


def test_swing_unscored_reports_unavailable():
    sig = {"type": "LONG_PUT", "factor_scores": dict(_SWING_FS),
           "grade_reason": "unscored"}
    flags = detail.flags_for(sig)
    assert len(flags) == 1
    assert flags[0]["key"] == "unscored" and flags[0]["state"] == "unavailable"


def test_swing_pop_gate_flag():
    sig = {"factor_scores": dict(_SWING_FS), "grade_reason": "Fails: PoP"}
    assert [f["key"] for f in detail.flags_for(sig)] == ["gate_pop"]


def test_swing_shape_never_uses_scanner_thresholds():
    # q_be 40 would trip the scanner's `em < 50` bar, but q_be rewards a
    # breakeven INSIDE the move -- inverted. A passing swing signal must stay
    # silent no matter how low q_be is.
    sig = {"factor_scores": dict(_SWING_FS, q_be=5.0, q_liq=1.0, q_rr=2.0),
           "grade_reason": "Passes all quality gates"}
    assert detail.flags_for(sig) == []


# --- score-less rows (captured / paper) --------------------------------------
# Fourth shape: synth_from_captured and synth_from_trade emit NO factor_scores
# and NO grade_reason. There is nothing to triage on an already-open position.

def test_paper_trade_shape_raises_no_flags():
    sig = {"symbol": "SPY", "credit": 1.55, "max_loss": 3.45,
           "short_strike": 480, "long_strike": 475, "dte": 7}
    assert detail.flags_for(sig) == []
    assert detail.flag_count(sig) == 0


def test_scoreless_iron_condor_also_raises_no_flags():
    # Both synth adapters set `type` from `strategy`, so a captured/paper IC
    # carries type == "IC" with no factor_scores. The IC note is for a TRIAGEABLE
    # signal whose checks vanished -- an open position has no checks either way.
    sig = {"symbol": "SPX", "type": "IC", "credit": 2.10, "dte": 3}
    assert detail.flags_for(sig) == []


def test_flag_class_warns_for_unavailable_and_unmeasured():
    assert detail.flag_class("tripped") == theme.TXT_NEG
    assert detail.flag_class("unmeasured") == theme.TXT_WARN
    assert detail.flag_class("unavailable") == theme.TXT_WARN


def test_render_returns_handle_with_update():
    # render() needs a NiceGUI context; just assert the API surface exists.
    assert callable(detail.render)


def test_detail_header_uses_the_svg_score_bar_not_a_chart():
    """The header's score is `svg.score_bar_svg` (2026-08-25), replacing the
    Highcharts angular gauge that replaced an earlier SVG speedometer.

    The `ui.highchart` assertion is the load-bearing half. The gauge was the ONLY
    chart on all four pages that mount this panel, so those pages no longer load
    Highcharts at all — and a `ui.highchart` created after first render on a page
    that had none dies with "Failed to resolve module specifier
    nicegui-highcharts". Reintroducing one here would fail in the browser while
    every test stayed green, so the guard lives at source level.

    Checked by AST, not substring: the module's own comment EXPLAINS why there
    is no `ui.highchart`, and a substring scan reads that explanation as a
    violation. Attribute access is what matters, so that is what is inspected.
    """
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(detail))
    attrs = {f"{n.value.id}.{n.attr}"
             for n in ast.walk(tree)
             if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)}
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert "svg.score_bar_svg" in attrs
    assert "ui.highchart" not in attrs
    assert "gauge_figure" not in names
    assert "speedometer_svg" not in names


def test_the_four_explore_expansions_are_in_the_agreed_order():
    """Expected Move, Greeks, Implied volatility, Score factors — set 2026-08-25
    by operator preference. Order is a deliberate choice here, not an accident of
    how the code grew, so it gets a guard."""
    import inspect
    import re
    src = inspect.getsource(detail)
    found = re.findall(r'ui\.expansion\("([^"]+)"\)', src)
    assert found == ["Expected Move", "Greeks", "Implied volatility",
                     "Score factors"]


def test_per_contract_multiplies_per_share_by_100():
    assert detail.per_contract(1.55) == 155.0
    assert detail.per_contract(3.45) == 345.0


def test_per_contract_passes_through_none_and_junk():
    assert detail.per_contract(None) is None
    assert detail.per_contract("n/a") is None


def test_money_per_contract_labels_its_unit():
    assert detail.money_per_contract(1.55) == "$155.00 per contract"
    assert detail.money_per_contract(None) == "—"


def test_breakevens_parses_iron_condor_string():
    assert detail.breakevens("5900.5/6010.2") == [5900.5, 6010.2]


def test_breakevens_accepts_a_plain_number():
    assert detail.breakevens(398.45) == [398.45]


def test_breakevens_returns_empty_for_missing_or_junk():
    assert detail.breakevens(None) == []
    assert detail.breakevens("") == []
    assert detail.breakevens("not/a/number") == []



def test_breakevens_accepts_the_ledgers_legacy_comma_separator():
    """Debit ledger rows written before 2026-09-13 joined two breakevens with
    ", ". A thousands separator (no space) is not a separator, so it stays junk
    rather than splitting one number in two."""
    assert detail.breakevens("96.0, 104.0") == [96.0, 104.0]
    assert detail.breakevens("96.0 / 104.0") == [96.0, 104.0]
    assert detail.breakevens("1,234.5") == []

def test_breakeven_text_formats_both_sides():
    assert detail.breakeven_text("5900.5/6010.2") == "$5,900.50 / $6,010.20"
    assert detail.breakeven_text(398.45) == "$398.45"
    assert detail.breakeven_text(None) == "—"


def test_contract_lines_put_credit_spread():
    sig = {"type": "PCS", "short_strike": 400, "long_strike": 395, "width": 5}
    assert detail.contract_lines(sig) == ["Sell 400 P  /  Buy 395 P", "5 wide"]


def test_contract_lines_call_credit_spread():
    sig = {"type": "CCS", "short_strike": 420, "long_strike": 425, "width": 5}
    assert detail.contract_lines(sig)[0] == "Sell 420 C  /  Buy 425 C"


def test_contract_lines_iron_condor_has_two_legs():
    sig = {"type": "IC", "short_strike": 390, "long_strike": 385,
           "call_short": 420, "call_long": 425}
    lines = detail.contract_lines(sig)
    assert "Sell 390 P  /  Buy 385 P" in lines
    assert "Sell 420 C  /  Buy 425 C" in lines


def test_contract_lines_empty_when_no_strikes():
    assert detail.contract_lines({"type": "PCS"}) == []


# --- the canonical ``legs`` path (every natively-built swing family) ----------
# strategy_scanner._assemble emits ``legs`` and NO short_strike/long_strike, so
# the strike-key path rendered NOTHING for LONG_CALL/BULL_CALL/... .

def test_contract_lines_long_call_from_legs():
    sig = {"type": "LONG_CALL",
           "legs": [{"kind": "call", "side": "long", "strike": 420.0, "qty": 1}]}
    assert detail.contract_lines(sig) == ["Buy 420 C"]


def test_contract_lines_short_put_from_legs_names_the_right_side():
    sig = {"type": "SHORT_PUT",
           "legs": [{"kind": "put", "side": "short", "strike": 385.0, "qty": 1}]}
    assert detail.contract_lines(sig) == ["Sell 385 P"]


def test_contract_lines_bull_call_keeps_emitted_long_then_short_order():
    # build_debit_verticals emits [long, short] -- "Buy the 400, sell the 410".
    sig = {"type": "BULL_CALL", "legs": [
        {"kind": "call", "side": "long", "strike": 400.0, "qty": 1},
        {"kind": "call", "side": "short", "strike": 410.0, "qty": 1},
    ]}
    assert detail.contract_lines(sig) == ["Buy 400 C  /  Sell 410 C"]


def test_contract_lines_bear_put_is_labelled_as_puts():
    # The old `startswith("CC")` test mislabelled everything non-CCS as a put;
    # here the legs' own ``kind`` must decide.
    sig = {"type": "BEAR_PUT", "legs": [
        {"kind": "put", "side": "long", "strike": 400.0, "qty": 1},
        {"kind": "put", "side": "short", "strike": 390.0, "qty": 1},
    ]}
    assert detail.contract_lines(sig) == ["Buy 400 P  /  Sell 390 P"]


def test_contract_lines_prefers_legs_when_both_shapes_are_present():
    # An adapted swing PCS carries BOTH legs and the source strike keys.
    sig = {"type": "PCS", "short_strike": 400, "long_strike": 395, "width": 5,
           "legs": [{"kind": "put", "side": "short", "strike": 400.0, "qty": 1},
                    {"kind": "put", "side": "long", "strike": 395.0, "qty": 1}]}
    assert detail.contract_lines(sig) == ["Sell 400 P  /  Buy 395 P", "5 wide"]


def test_contract_lines_iron_condor_legs_split_by_kind():
    sig = {"type": "IC", "legs": [
        {"kind": "put", "side": "short", "strike": 390.0, "qty": 1},
        {"kind": "put", "side": "long", "strike": 385.0, "qty": 1},
        {"kind": "call", "side": "short", "strike": 420.0, "qty": 1},
        {"kind": "call", "side": "long", "strike": 425.0, "qty": 1},
    ]}
    assert detail.contract_lines(sig) == ["Sell 390 P  /  Buy 385 P",
                                          "Sell 420 C  /  Buy 425 C"]


def test_contract_lines_shows_quantity_above_one():
    # A butterfly body trades at 2x -- invisible qty would misstate the position.
    sig = {"type": "BUTTERFLY", "legs": [
        {"kind": "call", "side": "long", "strike": 400.0, "qty": 1},
        {"kind": "call", "side": "short", "strike": 410.0, "qty": 2},
        {"kind": "call", "side": "long", "strike": 420.0, "qty": 1},
    ]}
    # The marker is "2×", the same one the Strategy Finder's Legs cell prints
    # (strategy_table.legs_summary) - it read "2x" here until 2026-09-13.
    assert detail.contract_lines(sig) == [
        "Buy 400 C  /  Sell 2× 410 C  /  Buy 420 C"]


def test_contract_lines_empty_legs_falls_back_to_strike_keys():
    sig = {"type": "PCS", "short_strike": 400, "long_strike": 395, "legs": []}
    assert detail.contract_lines(sig) == ["Sell 400 P  /  Buy 395 P"]


def test_contract_lines_ignores_legs_missing_a_strike():
    sig = {"type": "LONG_CALL",
           "legs": [{"kind": "call", "side": "long", "strike": None, "qty": 1}]}
    assert detail.contract_lines(sig) == []


# --- the Strategy Finder's share and two-expiry structures --------------------
# Leg shapes copied from options-scanner/strategy_scanner.py: ``_stock_leg`` (a
# strike-less, expiry-less 100-share lot) and ``_leg_from`` (every option leg
# carries its own ``expiration``). A share leg used to be dropped as "no strike",
# so a covered call read as a naked short call.

def _stock(side="long", qty=1, mark=538.2):
    return {"kind": "stock", "side": side, "strike": None, "expiration": None,
            "qty": qty, "mark": mark, "delta": 1.0, "theta": 0.0, "vega": 0.0,
            "gamma": 0.0, "iv": 0.0}


def _opt(kind, side, strike, exp="2026-10-16", qty=1):
    return {"kind": kind, "side": side, "strike": strike, "expiration": exp,
            "qty": qty, "mark": 2.04, "delta": 0.3, "theta": -0.1, "vega": 0.2,
            "gamma": 0.01, "iv": 24.0}


def test_contract_lines_covered_call_shows_the_shares():
    sig = {"type": "COVERED_CALL", "legs": [_stock(), _opt("call", "short", 545.0)]}
    assert detail.contract_lines(sig) == ["Buy 100 shares", "Sell 545 C"]


def test_contract_lines_protective_put_and_collar_keep_leg_order():
    pp = {"type": "PROTECTIVE_PUT", "legs": [_stock(), _opt("put", "long", 520.0)]}
    assert detail.contract_lines(pp) == ["Buy 100 shares", "Buy 520 P"]
    collar = {"type": "COLLAR", "legs": [_stock(), _opt("call", "short", 545.0),
                                         _opt("put", "long", 520.0)]}
    assert detail.contract_lines(collar) == ["Buy 100 shares", "Sell 545 C", "Buy 520 P"]


def test_share_leg_scales_with_lots_and_names_a_short_lot():
    assert detail.contract_lines({"legs": [_stock(qty=2)]}) == ["Buy 200 shares"]
    assert detail.contract_lines({"legs": [_stock(side="short")]}) == ["Sell 100 shares"]


def test_share_leg_with_a_malformed_qty_reads_as_one_lot():
    for bad in (float("nan"), "two", None, True, 0):
        assert detail.contract_lines({"legs": [_stock(qty=bad)]}) == ["Buy 100 shares"]


def test_contract_lines_calendar_dates_each_leg_on_its_own_line():
    # One line, "Sell 100 C  /  Buy 100 C", read as buying and selling the SAME
    # contract. Each leg now names its own expiry.
    sig = {"type": "CALENDAR_CALL", "expiration": "2026-10-16",
           "legs": [_opt("call", "short", 100.0, "2026-10-16"),
                    _opt("call", "long", 100.0, "2026-11-13")]}
    assert detail.contract_lines(sig) == ["Sell 100 C  2026-10-16",
                                          "Buy 100 C  2026-11-13"]


def test_contract_lines_diagonal_dates_each_leg():
    sig = {"type": "DIAGONAL_PUT", "expiration": "2026-10-16",
           "legs": [_opt("put", "short", 95.0, "2026-10-16"),
                    _opt("put", "long", 105.0, "2026-11-13")]}
    assert detail.contract_lines(sig) == ["Sell 95 P  2026-10-16",
                                          "Buy 105 P  2026-11-13"]


def test_contract_lines_share_leg_does_not_make_a_single_expiry_multi():
    sig = {"legs": [_stock(), _opt("call", "short", 545.0, "2026-10-16")]}
    assert detail.contract_lines(sig) == ["Buy 100 shares", "Sell 545 C"]


def test_expiry_caption_single_expiry_is_unchanged():
    assert detail.expiry_caption({"expiration": "2026-10-16"}) == "Exp 2026-10-16"
    sig = {"expiration": "2026-10-16",
           "legs": [_stock(), _opt("call", "short", 545.0, "2026-10-16")]}
    assert detail.expiry_caption(sig) == "Exp 2026-10-16"


def test_expiry_caption_absent_when_the_legs_carry_their_own_dates():
    sig = {"expiration": "2026-10-16",
           "legs": [_opt("call", "short", 100.0, "2026-10-16"),
                    _opt("call", "long", 100.0, "2026-11-13")]}
    assert detail.expiry_caption(sig) is None


def test_expiry_caption_none_without_an_expiration():
    assert detail.expiry_caption({}) is None


def test_money_unit_is_per_position_only_with_a_share_leg():
    assert detail.money_unit({"legs": [_opt("call", "short", 545.0)]}) == "per contract"
    assert detail.money_unit({}) == "per contract"
    assert detail.money_unit({"legs": [_stock(), _opt("call", "short", 545.0)]}) == (
        "per position")


def test_cost_row_and_money_for_say_per_position_for_shares():
    sig = {"net_cost": -536.16,
           "legs": [_stock(), _opt("call", "short", 545.0)]}
    assert detail.cost_row(sig) == ("Debit", "$53,616.00 per position")
    assert detail.money_for(sig, 536.16) == "$53,616.00 per position"
    assert detail.money_for(sig, None) == "—"


def test_money_for_an_option_only_signal_matches_money_per_contract():
    sig = {"legs": [_opt("put", "short", 400.0)]}
    assert detail.money_for(sig, 1.55) == detail.money_per_contract(1.55)


def test_cost_row_labels_credit_and_debit():
    assert detail.cost_row({"net_cost": 1.55}) == ("Credit", "$155.00 per contract")
    assert detail.cost_row({"net_cost": -2.40}) == ("Debit", "$240.00 per contract")
    assert detail.cost_row({}) == ("Credit", "—")


def test_cost_row_falls_back_to_credit_field():
    assert detail.cost_row({"credit": 1.55}) == ("Credit", "$155.00 per contract")


def test_cost_row_rejects_non_numeric_and_bools():
    assert detail.cost_row({"net_cost": "2.40"}) == ("Credit", "—")
    assert detail.cost_row({"net_cost": True}) == ("Credit", "—")


def test_gauge_metric_prefers_composite_score():
    m = detail.gauge_metric({"composite_score": 72, "grade": "Good"})
    assert m == {"value": 72, "caption": "Composite score", "grade": "Good"}


def test_gauge_metric_falls_back_to_pop_and_relabels():
    m = detail.gauge_metric({"pop_pct": 68})
    assert m["value"] == 68
    assert m["caption"] == "Probability of profit"
    assert m["grade"] == ""


def test_gauge_metric_when_nothing_is_available():
    """`value` is None, NOT 0.

    The Highcharts gauge it was written for had no way to draw absence, so 0 was
    the only option and the caption carried the meaning. The SVG score bar that
    replaced it on 2026-08-25 CAN draw absence -- a bare track and a dash -- and
    a 0 there would render a confident worst-possible score under a caption
    saying there is no score. The renderer got the ability, so the producer must
    stop flattening it."""
    m = detail.gauge_metric({})
    assert m["value"] is None
    assert m["caption"] == "No score available"
    assert m["grade"] == ""


def test_gauge_metric_treats_a_real_zero_as_a_score():
    """0 is a legitimate composite score and must not read as absence."""
    m = detail.gauge_metric({"composite_score": 0, "grade": "Weak"})
    assert m["value"] == 0 and m["caption"] == "Composite score"


def test_gauge_metric_rejects_a_non_finite_score(self=None):
    """A NaN clamps to the HIGH bound in this repo's scorers -- here it would
    slip past the isinstance check and reach the bar as a real reading."""
    m = detail.gauge_metric({"composite_score": float("nan")})
    assert m["value"] is None


def test_iv_marker_suppressed_without_current_iv():
    # Falling back to the 52w LOW drew the marker at the bottom of the range,
    # reading as "IV is dirt cheap" when the truth was "unknown".
    assert detail.iv_marker_value({"iv_low_52w": 10, "iv_high_52w": 40}) is None


def test_iv_marker_uses_current_iv_when_present():
    assert detail.iv_marker_value(
        {"iv_low_52w": 10, "iv_high_52w": 40, "current_iv": 22}) == 22


def test_dte_text_distinguishes_live_from_entry():
    assert detail.dte_text({"dte": 12}) == "12 DTE"
    assert detail.dte_text({"dte": 12, "dte_is_entry": True}) == "12 DTE at entry"
    assert detail.dte_text({}) == "—"


def _panel_column(handle):
    return handle._header.parent_slot.parent


def test_panel_opens_by_default_and_can_be_collapsed_then_opened():
    """The Strategy Finder starts the panel collapsed and opens it on the first
    selection. The DEFAULT stays open, since three other pages mount it."""
    from nicegui import ui

    with ui.card():
        h = detail.render()
    col = _panel_column(h)
    assert h.is_open is True and "w-[360px]" in col._classes

    h.collapse()
    assert h.is_open is False
    assert "w-11" in col._classes and "w-[360px]" not in col._classes

    h.collapse()                      # idempotent: no toggle back open
    assert h.is_open is False

    h.open()
    assert h.is_open is True
    assert "w-[360px]" in col._classes and "w-11" not in col._classes

    h.open()                          # idempotent
    assert h.is_open is True


def test_toggling_the_panel_keeps_exactly_one_tooltip():
    """``Element.tooltip()`` builds a NEW Tooltip on every call, so a toggle that
    called it per open/collapse piled up contradictory tooltips."""
    from nicegui import ui

    with ui.card() as card:
        h = detail.render()
    tips = lambda: [e for e in card.descendants() if isinstance(e, ui.tooltip)]
    assert len(tips()) == 1 and tips()[0].text == "Collapse panel"
    for _ in range(3):
        h.collapse()
        h.open()
    h.collapse()
    assert len(tips()) == 1 and tips()[0].text == "Expand panel"
    h.open()
    assert len(tips()) == 1 and tips()[0].text == "Collapse panel"


# ── the checklist at the top of the panel (design 2026-09-15) ────────────────
_LIMITS = {"max_positions_per_symbol": 3, "max_risk_per_symbol": 750.0,
           "max_positions_per_expiry": 5, "max_positions_per_sector": 5,
           "max_risk_per_sector": 1500.0, "max_deployed_risk_pct": 0.2,
           "max_risk_per_trade": 250.0}
_CAPS = {"limits": _LIMITS, "equity": 25000.0, "open": [], "sectors": {"ORCL": "IT"},
         "unmapped_prefix": "?"}
_BOARD = {"symbol": "ORCL", "spot": 110.0, "put_wall": 102.0, "call_wall": 120.0,
          "gex_regime": "above", "trend_dir": 0.4, "trend_state": "up"}
_CTX = {"matrix": {"ORCL": _BOARD}, "regime": {"direction": 1},
        "calibration": {"by_bucket": {}}, "caps": _CAPS}
_COLD = {"matrix": None, "regime": None, "calibration": None, "caps": None}


def _scanner_pcs(**over):
    """A stamped Market Scanner PCS signal, as the options service publishes it."""
    sig = {"id": "ORCL_PCS_2026-10-17_100_97.5", "symbol": "ORCL", "type": "PCS",
           "trade_type": "SWING", "expiration": "2026-10-17", "dte": 12,
           "short_strike": 100.0, "long_strike": 97.5, "width": 2.5,
           "credit": 0.60, "max_loss": 1.90, "pop_pct": 72.0,
           "iv_rank": 55.0, "iv_rank_known": True, "vol_floor": 30,
           "friction_pct": 8.0, "em_to_expiry": 6.0,
           "earnings_status": "none_scheduled", "earnings_date": None,
           "ledger_risk_per_contract": 190.0, "ledger_risk_basis": {"per_contract": 190.0},
           "underlying_price": 110.0, "composite_score": 70, "grade": "Good"}
    sig.update(over)
    return sig


def _finder_pcs(**over):
    """A stamped Strategy Finder PCS: normalized legs, per-contract dollars and a
    ``fit_score`` (which is what omits the track-record line)."""
    sig = {"id": "f1", "symbol": "ORCL", "type": "PCS", "group": "SPREADS",
           "expiration": "2026-10-17", "dte": 32, "fit_score": 64.0,
           "legs": [{"side": "short", "kind": "put", "strike": 100.0, "qty": 1},
                    {"side": "long", "kind": "put", "strike": 97.5, "qty": 1}],
           "net_credit": 60.0, "max_profit": 60.0, "max_loss": 190.0, "net_vega": -0.2,
           "iv_rank": 55.0, "iv_rank_known": True, "vol_floor": 30,
           "friction_pct": 8.0, "em_to_expiry": 6.0,
           "earnings_status": "none_scheduled", "earnings_date": None,
           "ledger_risk_per_contract": 190.0, "ledger_risk_basis": {"per_contract": 190.0},
           "underlying_price": 110.0, "composite_score": 70, "grade": "Good"}
    sig.update(over)
    return sig


def test_checklist_candidate_hands_the_rows_paper_gate_across_without_mutating():
    sig = _scanner_pcs()
    cand = detail.checklist_candidate(sig, True)
    assert cand["_allow_paper"] is True and cand["id"] == sig["id"]
    assert "_allow_paper" not in sig
    assert detail.checklist_candidate(None, True) is None


def test_checks_for_a_stamped_scanner_row_runs_every_check_in_order():
    from pages.options import checks_feed
    items = checks_feed.checks_for(detail.checklist_candidate(_scanner_pcs(), True), _CTX)
    assert [c["key"] for c in items] == ["book", "earnings", "vol", "cost", "em",
                                         "wall", "gamma", "direction", "record"]


def test_checks_for_a_stamped_finder_row_has_no_record_line():
    from pages.options import checks_feed, strategy_table
    sig = _finder_pcs()
    items = checks_feed.checks_for(
        detail.checklist_candidate(sig, sig["type"] in strategy_table._PAPER_TYPES), _CTX)
    assert [c["key"] for c in items] == ["book", "earnings", "vol", "cost", "em",
                                         "wall", "gamma", "direction"]


def test_the_finder_detail_signal_keeps_the_checklist_stamps():
    from pages.options import strategy_table
    out = strategy_table.detail_signal(_finder_pcs())
    for key in ("fit_score", "ledger_risk_basis", "ledger_risk_per_contract",
                "friction_pct", "em_to_expiry", "earnings_status", "vol_floor"):
        assert out[key] == _finder_pcs()[key]


def test_checklist_view_is_the_summary_then_one_line_per_check():
    from pages.options import checks
    items = [{"key": "book", "label": "Paper book", "tone": "pos", "text": "Fits"},
             {"key": "cost", "label": "Bid-ask", "tone": "muted", "text": "Not measured"}]
    view = detail.checklist_view(items)
    assert view["summary"] == checks.summary(items)
    assert view["lines"] == [
        {"label": "Paper book", "text": "Fits", "class": checks.TONE_CLASS["pos"]},
        {"label": "Bid-ask", "text": "Not measured", "class": checks.TONE_CLASS["muted"]}]
    assert detail.checklist_view([]) is None
    # An unknown tone reads grey rather than raising on a repaint.
    odd = detail.checklist_view([{"label": "X", "text": "y", "tone": "??"}])
    assert odd["lines"][0]["class"] == checks.TONE_CLASS["muted"]


def _no_loop_reads(monkeypatch):
    """The panel must never read the context on the event loop: fail if it tries."""
    from pages.options import checks_feed

    def boom():
        raise AssertionError("read_context called on the event loop")
    monkeypatch.setattr(checks_feed, "read_context", boom)


def _capture_spawn(monkeypatch):
    spawned = []
    monkeypatch.setattr(detail, "_spawn", lambda coro: spawned.append(coro))
    return spawned


def _texts(card):
    from nicegui import ui
    return [e.text for e in card.descendants() if isinstance(e, ui.label)]


def _verdicts(texts):
    return [t for t in texts
            if t.startswith(("Partly checked", "Clear", "Blocked", "unchecked", "Unchecked"))
            or t.endswith(("caution", "cautions"))]


def test_a_candidate_with_the_pages_context_shows_the_list_above_the_contract(monkeypatch):
    from nicegui import ui
    from pages.options import checks
    _no_loop_reads(monkeypatch)
    with ui.card() as card:
        h = detail.render()
    sig = _scanner_pcs()
    h.update(sig, candidate=detail.checklist_candidate(sig, True), ctx=_CTX)
    texts = _texts(card)
    assert h.checklist_id == sig["id"]
    (verdict,) = _verdicts(texts)
    assert verdict.startswith("Clear")
    for key in checks.ORDER:
        assert checks.LABELS[key] in texts
    assert texts.index(verdict) < texts.index(detail.contract_lines(sig)[0])


def test_a_cold_context_shows_a_partly_checked_list_with_every_line(monkeypatch):
    """All views None: the summary cannot read Clear, and a grey line still shows."""
    from nicegui import ui
    _no_loop_reads(monkeypatch)
    with ui.card() as card:
        h = detail.render()
    sig = _scanner_pcs()
    h.update(sig, candidate=detail.checklist_candidate(sig, True), ctx=_COLD)
    texts = _texts(card)
    (verdict,) = _verdicts(texts)
    assert verdict.startswith("Partly checked")
    for label in ("Paper book", "Earnings", "Walls", "Dealer gamma"):
        assert label in texts


def test_no_held_context_says_checking_then_paints_from_an_off_loop_read(monkeypatch):
    """A page with no context yet: never a verdict and never a read on the loop.
    The read goes through run.io_bound and the list paints when it lands."""
    import asyncio
    from nicegui import run, ui
    from pages.options import checks_feed
    _no_loop_reads(monkeypatch)
    spawned = _capture_spawn(monkeypatch)
    with ui.card() as card:
        h = detail.render()
    sig = _scanner_pcs()
    h.update(sig, candidate=detail.checklist_candidate(sig, True))
    texts = _texts(card)
    assert detail.CHECKING_TEXT in texts and not _verdicts(texts)
    assert len(spawned) == 1

    off_loop = []

    async def fake_io_bound(fn, *args, **kwargs):
        off_loop.append(fn)
        return _CTX
    monkeypatch.setattr(run, "io_bound", fake_io_bound)
    asyncio.run(spawned[0])
    assert off_loop == [checks_feed.read_context]
    texts = _texts(card)
    assert detail.CHECKING_TEXT not in texts
    assert _verdicts(texts)[0].startswith("Clear")


def test_an_off_loop_read_for_an_old_selection_is_dropped(monkeypatch):
    import asyncio
    from nicegui import run, ui
    _no_loop_reads(monkeypatch)
    spawned = _capture_spawn(monkeypatch)
    with ui.card() as card:
        h = detail.render()
    sig = _scanner_pcs()
    h.update(sig, candidate=detail.checklist_candidate(sig, True))
    # The reader picks another row, with the page's context, before the read lands.
    other = _scanner_pcs(id="other")
    h.update(other, candidate=detail.checklist_candidate(other, False), ctx=_COLD)

    async def fake_io_bound(fn, *args, **kwargs):
        return _CTX
    monkeypatch.setattr(run, "io_bound", fake_io_bound)
    asyncio.run(spawned[0])
    texts = _texts(card)
    (verdict,) = _verdicts(texts)
    assert verdict.startswith("Partly checked") and "Paper book" not in texts


def test_no_candidate_means_no_checklist(monkeypatch):
    """A position already held (Paper Ledger, Captured Signals) is not a Go/No-Go
    decision: without an explicit candidate the panel shows no checklist."""
    from nicegui import ui
    from pages.options import checks
    _no_loop_reads(monkeypatch)
    spawned = _capture_spawn(monkeypatch)
    with ui.card() as card:
        h = detail.render()
    h.update(_scanner_pcs())
    texts = _texts(card)
    assert not _verdicts(texts) and detail.CHECKING_TEXT not in texts
    assert not set(checks.LABELS.values()) & set(texts)
    assert h.checklist_id is None and spawned == []


def test_refresh_repaints_the_list_in_place_against_the_pages_new_context(monkeypatch):
    from nicegui import ui
    _no_loop_reads(monkeypatch)
    with ui.card() as card:
        h = detail.render()
    sig = _scanner_pcs()
    h.update(sig, candidate=detail.checklist_candidate(sig, True), ctx=_CTX)
    assert _verdicts(_texts(card))[0].startswith("Clear")
    h.refresh_checks({**_CTX, "matrix": None})       # the board went cold
    texts = _texts(card)
    (verdict,) = _verdicts(texts)
    assert verdict.startswith("Partly checked")
    assert detail.contract_lines(sig)[0] in texts      # the rest was not rebuilt


def test_refresh_takes_a_fresh_candidate_for_the_same_id_only(monkeypatch):
    from nicegui import ui
    _no_loop_reads(monkeypatch)
    with ui.card() as card:
        h = detail.render()
    sig = _scanner_pcs()
    h.update(sig, candidate=detail.checklist_candidate(sig, True), ctx=_CTX)
    assert "Paper book" in _texts(card)
    # The signal went stale: its Paper gate closed, so the book line goes.
    h.refresh_checks(_CTX, candidate=detail.checklist_candidate(sig, False))
    assert "Paper book" not in _texts(card)
    # Another id's candidate is not this panel's: ignored.
    h.refresh_checks(_CTX, candidate=detail.checklist_candidate(_scanner_pcs(id="x"), True))
    assert "Paper book" not in _texts(card)


def test_refresh_without_a_candidate_is_a_no_op_and_clear_forgets_it(monkeypatch):
    from nicegui import ui
    _no_loop_reads(monkeypatch)
    spawned = _capture_spawn(monkeypatch)
    with ui.card() as card:
        h = detail.render()
    h.refresh_checks(_CTX)                       # nothing selected - nothing built
    assert "Paper book" not in _texts(card)
    sig = _scanner_pcs()
    h.update(sig, candidate=detail.checklist_candidate(sig, True), ctx=_CTX)
    h.clear()
    assert h.checklist_id is None
    h.refresh_checks(_CTX)
    h.refresh_checks(None)
    assert "Paper book" not in _texts(card) and spawned == []


def test_the_position_pages_never_hand_the_panel_a_candidate():
    import inspect
    from pages.options import captured, paper
    for mod in (captured, paper):
        src = inspect.getsource(mod)
        assert "detail_panel.update(" in src
        assert "candidate=" not in src and "refresh_checks" not in src


def test_the_panel_never_reads_the_context_on_the_loop_at_source_level():
    import inspect
    src = inspect.getsource(detail)
    assert "run.io_bound(checks_feed.read_context)" in src
    assert src.count("read_context") == src.count("run.io_bound(checks_feed.read_context)")


def test_spawn_without_a_running_app_loop_closes_the_read_instead_of_raising(monkeypatch):
    """No NiceGUI loop (the app is not running): nothing could paint the result,
    so the read is dropped and the line stays Checking - never a raise out of a
    row click."""
    from nicegui import core
    monkeypatch.setattr(core, "loop", None)
    ran = []

    async def read():
        ran.append(True)
    coro = read()
    detail._spawn(coro)
    assert ran == [] and coro.cr_frame is None      # closed, never started


# ── review follow-ups: a row that left, no checks, casing, wrapping, repaints ─
def _open_on(monkeypatch, sig=None, ctx=None, allow=True):
    from nicegui import ui
    _no_loop_reads(monkeypatch)
    with ui.card() as card:
        h = detail.render()
    sig = sig or _scanner_pcs()
    h.update(sig, candidate=detail.checklist_candidate(sig, allow), ctx=ctx or _CTX)
    return card, h, sig


def test_a_row_that_left_the_page_shows_one_muted_line_and_no_verdict(monkeypatch):
    """The page's lookup no longer finds the open id (a cap eviction, a new day's
    first scan): the panel must not repaint the old candidate against the new
    context and show a fresh Clear for a signal the table no longer lists."""
    from pages.options import checks
    card, h, sig = _open_on(monkeypatch)
    h.set_candidate_source(lambda _id: None, gone_text=detail.GONE_SCAN_TEXT)
    h.refresh_checks(_CTX)
    texts = _texts(card)
    assert detail.GONE_SCAN_TEXT in texts
    assert not _verdicts(texts)
    assert not set(checks.LABELS.values()) & set(texts)
    (gone,) = [e for e in card.descendants()
               if getattr(e, "text", None) == detail.GONE_SCAN_TEXT]
    assert detail.MUTED.split()[0] in gone._classes
    # It stays gone: a later refresh has no candidate to repaint.
    assert h.checklist_id is None
    h.refresh_checks(_CTX)
    assert detail.GONE_SCAN_TEXT in _texts(card) and not _verdicts(_texts(card))


def test_the_candidate_source_supplies_the_fresh_row_for_the_open_id(monkeypatch):
    card, h, sig = _open_on(monkeypatch)
    asked = []
    h.set_candidate_source(
        lambda i: (asked.append(i), detail.checklist_candidate(sig, False))[1])
    h.refresh_checks(_CTX)
    assert asked == [sig["id"]]
    assert "Paper book" not in _texts(card)          # the gate the row carries now


def test_a_gone_row_drops_a_read_still_in_flight(monkeypatch):
    import asyncio
    from nicegui import run
    _no_loop_reads(monkeypatch)
    spawned = _capture_spawn(monkeypatch)
    from nicegui import ui
    with ui.card() as card:
        h = detail.render()
    sig = _scanner_pcs()
    h.update(sig, candidate=detail.checklist_candidate(sig, True))
    h.set_candidate_source(lambda _id: None)
    h.refresh_checks(_CTX)

    async def fake_io_bound(fn, *args, **kwargs):
        return _CTX
    monkeypatch.setattr(run, "io_bound", fake_io_bound)
    asyncio.run(spawned[0])
    texts = _texts(card)
    assert detail.GONE_SCAN_TEXT in texts and not _verdicts(texts)


def test_no_checks_applying_says_so_never_checking(monkeypatch):
    from pages.options import checks_feed
    monkeypatch.setattr(checks_feed, "checks_for", lambda row, ctx: [])
    card, h, _sig = _open_on(monkeypatch)
    texts = _texts(card)
    assert detail.NO_CHECKS_TEXT in texts
    assert detail.CHECKING_TEXT not in texts and not _verdicts(texts)


def test_the_panel_headline_is_capitalised_but_the_table_chip_is_not():
    from pages.options import checks
    items = [{"key": "cost", "label": "Cost to trade", "tone": "muted",
              "text": "Bid-ask not measured"}]
    view = detail.checklist_view(items)
    assert view["summary"]["text"] == "Unchecked"
    assert view["summary"]["state"] == "muted"
    assert checks.verdict(items)["text"] == "unchecked"      # the chip is unchanged
    assert checks.verdict(items)["short"] == "unchecked"


def test_the_check_text_wraps_long_tokens_in_its_no_wrap_row(monkeypatch):
    card, _h, _sig = _open_on(monkeypatch)
    from nicegui import ui
    (earn,) = [e for e in card.descendants()
               if isinstance(e, ui.label) and e.text == "No report scheduled"]
    assert "min-w-0" in earn._classes and "break-words" in earn._classes
    assert "no-wrap" in earn.parent_slot.parent._classes


def test_an_identical_refresh_keeps_the_rendered_elements(monkeypatch):
    from nicegui import ui
    card, h, sig = _open_on(monkeypatch)

    def label_ids():
        return [id(e) for e in card.descendants() if isinstance(e, ui.label)]
    before = label_ids()
    h.refresh_checks(dict(_CTX))                        # equal view, new dict
    assert label_ids() == before
    h.refresh_checks({**_CTX, "matrix": None})          # a different view repaints
    assert label_ids() != before

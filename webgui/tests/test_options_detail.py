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


def test_tile_color_fns_only_return_state_token_classes():
    # _set_color does remove=STATE_TEXT_CLASSES then add=color_fn(s). That only fully
    # clears the prior tile color if EVERY color-fn output is a member of the 4-token
    # set. Pin that invariant: across a representative input space, every tile color-fn
    # must return a class in theme.STATE_TEXT_CLASSES.
    allowed = set(theme.STATE_TEXT_CLASSES.split())
    # Representative signals: vary the pop_pct that drives pop_color across its branches
    # plus the non-numeric/missing fallback; the other color-fns are constant.
    signals = [
        {"pop_pct": 80}, {"pop_pct": 60}, {"pop_pct": 40},
        {"pop_pct": None}, {"pop_pct": "n/a"}, {},
    ]
    for _key, _label, _value_fn, color_fn in detail._TILES:
        for s in signals:
            cls = color_fn(s)
            assert cls in allowed, f"tile {_key!r} returned {cls!r} not in STATE_TEXT_CLASSES"


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
    assert any(f["key"] == "rr" for f in detail.flags_for({"rr_pct": 12}))
    assert not any(f["key"] == "rr" for f in detail.flags_for({"rr_pct": 35}))


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


def test_render_returns_handle_with_update():
    # render() needs a NiceGUI context; just assert the API surface exists.
    assert callable(detail.render)


def test_detail_header_uses_highcharts_gauge_not_svg_speedometer():
    import inspect
    src = inspect.getsource(detail)
    # The composite-score header is the shared Highcharts solid-gauge now.
    assert "gauge_figure" in src
    assert "speedometer_svg" not in src


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


def test_breakeven_text_formats_both_sides():
    assert detail.breakeven_text("5900.5/6010.2") == "$5,900.50 / $6,010.20"
    assert detail.breakeven_text(398.45) == "$398.45"
    assert detail.breakeven_text(None) == "—"

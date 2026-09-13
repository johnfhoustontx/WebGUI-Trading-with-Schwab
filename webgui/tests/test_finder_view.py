"""Pure view model for the redesigned Strategy Finder (``finder_view.py``)."""
from pages.options import finder_view as fv


def test_module_is_pure():
    """No widget library: every builder here is testable without a browser."""
    import ast
    import pathlib
    tree = ast.parse(pathlib.Path(fv.__file__).read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            names.add(node.module or "")
    assert names, "parsed no imports at all - the check is vacuous"
    assert not {n for n in names if n.split(".")[0] == "nicegui"}
    # strategy_table would be the natural import, but it pulls scanner -> nicegui.
    assert not {n for n in names if n.endswith("strategy_table") or n.endswith("scanner")}


def test_money_formats_by_size():
    assert fv.money(54057.72) == "$54,058"
    assert fv.money(195.34) == "$195"
    assert fv.money(4.5) == "$4.50"
    assert fv.money(None) == "—"
    assert fv.money(float("nan")) == "—"


def test_money_keeps_a_leading_minus():
    assert fv.money(-300.0) == "-$300"
    assert fv.money(-4.5) == "-$4.50"
    assert fv.money(True) == "—"
    assert fv.money(99.996) == "$100"


def test_cost_text_names_credit_debit_and_shares():
    assert fv.cost_text({"net_debit": 195.34}) == "$195 debit"
    assert fv.cost_text({"net_credit": 803.67}) == "$804 credit"
    shares = {"net_debit": 54057.72, "legs": [{"kind": "stock"}]}
    assert fv.cost_text(shares) == "$54,058 debit for 100 shares"
    assert fv.cost_text({}) == "—"


def test_cost_text_counts_share_lots():
    two = {"net_debit": 1000.0, "legs": [{"kind": "stock", "qty": 2}]}
    assert fv.cost_text(two) == "$1,000 debit for 200 shares"


def test_expiry_text():
    assert fv.expiry_text({"expiration": "2026-10-16", "dte": 8}) == "Oct 16 · 8d"
    assert fv.expiry_text({}) == "—"


def test_expiry_text_without_dte_or_with_a_bad_date():
    assert fv.expiry_text({"expiration": "2026-01-02"}) == "Jan 2"
    assert fv.expiry_text({"expiration": "garbage", "dte": 3}) == "—"


def test_expiry_presets_round_trip_and_detect_custom():
    assert [p[0] for p in fv.EXPIRY_PRESETS] == ["1–2 wk", "2–6 wk", "1–3 mo", "Any"]
    for label, lo, hi in fv.EXPIRY_PRESETS:
        assert fv.expiry_preset_for(lo, hi) == label
    assert fv.expiry_preset_for(3, 9) is None


def test_risk_styles_match_todays_default_as_balanced():
    assert fv.RISK_DEFAULT == "Balanced"
    b = fv.risk_bands("Balanced")
    assert b == {"put_d_min": -0.20, "put_d_max": -0.10, "call_d_min": 0.10, "call_d_max": 0.20}
    assert fv.risk_style_for(**b) == "Balanced"
    assert fv.risk_style_for(**fv.risk_bands("Aggressive")) == "Aggressive"
    assert fv.risk_style_for(-0.30, -0.12, 0.10, 0.20) == "Custom"


def test_risk_style_for_tolerates_float_noise_and_junk():
    assert fv.risk_style_for(-0.1 - 0.1, -0.1, 0.1, 0.30000000000000004 - 0.1) == "Balanced"
    assert fv.risk_style_for(None, -0.10, 0.10, 0.20) == "Custom"


def _sig(t, g, score, **kw):
    return {"id": t, "type": t, "group": g, "composite_score": score, **kw}


def test_groups_cover_the_seven_build_groups_in_order():
    assert [c for c, _ in fv.GROUPS] == [
        "DIRECTIONAL", "VERTICAL", "NEUTRAL", "STRADDLE", "BUTTERFLY", "CALENDAR", "STOCK"]


def test_chip_counts_follow_group_order_and_skip_empty():
    sigs = [_sig("A", "VERTICAL", 70), _sig("B", "VERTICAL", 60), _sig("C", "CALENDAR", 65)]
    assert fv.chip_counts(sigs) == [("VERTICAL", "Spreads", 2), ("CALENDAR", "Calendars", 1)]


def test_filter_by_groups():
    sigs = [_sig("A", "VERTICAL", 70), _sig("C", "CALENDAR", 65)]
    assert [s["id"] for s in fv.filter_groups(sigs, None)] == ["A", "C"]
    assert [s["id"] for s in fv.filter_groups(sigs, {"CALENDAR"})] == ["C"]
    assert fv.filter_groups(sigs, set()) == []


def test_top_picks_take_the_best_of_distinct_groups():
    sigs = [_sig("A", "VERTICAL", 80), _sig("B", "VERTICAL", 79), _sig("C", "CALENDAR", 70),
            _sig("D", "STOCK", 75), _sig("E", "BUTTERFLY", 60), _sig("F", "STRADDLE", 55)]
    assert [s["id"] for s in fv.top_picks(sigs, k=4)] == ["A", "D", "C", "E"]
    assert [s["id"] for s in fv.top_picks(sigs[:2], k=4)] == ["A"]


def test_top_picks_put_unscored_last_and_break_ties_by_id():
    sigs = [_sig("Z", "VERTICAL", None), _sig("B", "CALENDAR", 50), _sig("A", "STOCK", 50)]
    assert [s["id"] for s in fv.top_picks(sigs, k=4)] == ["A", "B", "Z"]


def test_summary_facts():
    payload = {"symbol": "SPY", "filtered_out": 6, "vol_filtered": 0,
               "view": {"direction": "neutral", "conviction": 0.1, "vol_regime": "mid"},
               "signals": [{"underlying_price": 540.12, "iv_rank": 55.0}] * 16}
    f = fv.summary_facts(payload)
    assert f["symbol"] == "SPY" and f["price"] == "$540.12"
    assert f["pills"] == ["Neutral", "Conviction low", "Volatility mid"]
    assert f["vol_rank"] == "Vol Rank 55"
    assert f["counts"] == "16 ideas · 6 below the quality bar"
    assert fv.summary_facts({}) is None


def test_summary_facts_conviction_bands_and_cheap_premium():
    def conv(c):
        return fv.summary_facts({"symbol": "X", "view": {"conviction": c}})["pills"]
    assert conv(0.34) == ["Conviction medium"]
    assert conv(0.67) == ["Conviction high"]
    f = fv.summary_facts({"symbol": "X", "vol_filtered": 3, "signals": []})
    assert f["counts"] == "0 ideas · 3 where premium is too cheap to sell"
    assert f["price"] is None and f["vol_rank"] is None
    one = fv.summary_facts({"symbol": "X", "signals": [{"underlying_price": 1.0}]})
    assert one["counts"] == "1 idea"


def test_top_picks_with_no_room_returns_nothing():
    assert fv.top_picks([_sig("A", "VERTICAL", 80)], k=0) == []


# ------------------------------------------------------------ bars and payoff SVG

def test_split_bar_scales_loss_and_profit_to_the_larger():
    b = fv.risk_reward_bar({"max_loss": 200.0, "max_profit": 800.0})
    assert b["loss_class"] == "w-[25%]" and b["profit_class"] == "w-full"
    b = fv.risk_reward_bar({"max_loss": 53817.0, "max_profit": 1960.0})
    assert b["loss_class"] == "w-full" and b["profit_class"] == "w-[5%]"


def test_split_bar_labels_are_money():
    b = fv.risk_reward_bar({"max_loss": 200.0, "max_profit": 800.0})
    assert b["loss_label"] == "$200" and b["profit_label"] == "$800"


def test_split_bar_unbounded_profit_is_full_and_marked():
    b = fv.risk_reward_bar({"max_loss": 500.0, "max_profit": None, "unbounded_profit": True})
    assert b["profit_class"] == "w-full" and b["profit_label"] == "∞"


def test_split_bar_unbounded_loss_is_full_and_marked():
    """A naked short's max_loss is a MARGIN PROXY, not a cap - drawing it to scale
    would read as a bounded risk."""
    b = fv.risk_reward_bar({"max_loss": 10866.0, "max_profit": 240.0, "unbounded_loss": True})
    assert b["loss_class"] == "w-full" and b["loss_label"] == "∞"
    assert b["profit_class"] == "w-[5%]"


def test_split_bar_one_side_missing_draws_that_side_empty():
    b = fv.risk_reward_bar({"max_loss": 300.0, "max_profit": None})
    assert b["loss_class"] == "w-full" and b["profit_class"] == "w-0"
    assert b["profit_label"] == "—"


def test_split_bar_missing_numbers_draws_nothing():
    assert fv.risk_reward_bar({}) is None


def test_width_classes_snap_to_five_and_never_hide_a_positive():
    assert fv._snap(0) == 0 and fv._snap(0.4) == 5 and fv._snap(2.4) == 5
    assert fv._snap(97.6) == 100 and fv._snap(140) == 100 and fv._snap(-3) == 0
    assert set(fv._WIDTH) == set(range(0, 101, 5))


def test_pop_bar_snaps_and_colours_by_band():
    assert fv.pop_bar(46.4) == {"class": "w-[45%]", "tone": "neutral", "label": "46%"}
    assert fv.pop_bar(31.0)["tone"] == "warn"
    assert fv.pop_bar(72.0)["tone"] == "pos"
    assert fv.pop_bar(None) is None


def test_pop_bar_band_edges():
    assert fv.pop_bar(40.0)["tone"] == "neutral"
    assert fv.pop_bar(60.0)["tone"] == "neutral"
    assert fv.pop_bar(120.0)["class"] == "w-full"


_CURVE = [[90.0, -300.0], [100.0, -300.0], [110.0, 700.0]]


def test_payoff_svg_is_fixed_size_and_colours_by_sign():
    curve = _CURVE
    svg = fv.payoff_svg(curve, spot=100.0, width=120, height=32)
    assert svg.startswith("<svg") and 'width="120"' in svg and 'height="32"' in svg
    assert "preserveAspectRatio" not in svg and "vector-effect" not in svg
    assert fv.PROFIT_STROKE in svg and fv.LOSS_STROKE in svg
    assert fv.payoff_svg(None, 100.0) == "" and fv.payoff_svg([[1, 2]], 100.0) == ""


def test_payoff_svg_geometry():
    svg = fv.payoff_svg(_CURVE, spot=100.0, width=120, height=32)
    assert 'viewBox="0 0 120 32"' in svg and "style=" not in svg
    # a flat loss, a crossing segment split in two, a dashed zero line, a spot tick
    assert svg.count("<line ") == 5
    assert svg.count(f'stroke="{fv.LOSS_STROKE}"') == 2
    assert svg.count(f'stroke="{fv.PROFIT_STROKE}"') == 1
    assert "stroke-dasharray" in svg and f'stroke="{fv.SPOT_STROKE}"' in svg


def test_payoff_svg_skips_the_spot_tick_off_the_curve_and_junk_points():
    svg = fv.payoff_svg(_CURVE + [[float("nan"), 1.0], None, [1]], spot=500.0)
    assert svg.count("<line ") == 4 and fv.SPOT_STROKE not in svg
    assert fv.payoff_svg([[100.0, 1.0], [100.0, 2.0]], 100.0) == ""


def test_payoff_svg_emits_nothing_dompurify_would_strip():
    _assert_dompurify_clean(fv.payoff_svg(_CURVE, spot=100.0))


def _assert_dompurify_clean(svg):
    import re
    from test_rings import _dompurify_allowlist
    allow = _dompurify_allowlist()
    tags = set(re.findall(r"<([a-zA-Z][\w-]*)", svg))
    attrs = set(re.findall(r'\s([a-zA-Z][\w:-]*)="', svg))
    stripped = sorted(n for n in tags | attrs if n.lower() not in allow)
    assert not stripped, f"DOMPurify would strip: {stripped}"
    # Non-vacuity: we really parsed an SVG and really checked its attributes.
    assert {"svg", "line"} <= tags
    assert {"viewBox", "x1", "stroke", "stroke-dasharray"} <= attrs


def _segments(svg):
    import re
    return [dict(re.findall(r'([\w-]+)="([^"]*)"', m))
            for m in re.findall(r"<line ([^>]*)/>", svg)
            if 'stroke-dasharray' not in m]


def test_payoff_svg_changes_colour_exactly_at_break_even():
    curve = [[90.0, -100.0], [110.0, 100.0]]
    svg = fv.payoff_svg(curve, spot=None, width=120, height=32)
    red, green = _segments(svg)
    assert red["stroke"] == fv.LOSS_STROKE and green["stroke"] == fv.PROFIT_STROKE
    # P&L 0 at price 100 -> x = 2 + 0.5 * 116 = 60; zero line at y = 2 + 0.5 * 28 = 16
    assert (red["x2"], red["y2"]) == ("60", "16") == (green["x1"], green["y1"])
    assert (red["x1"], green["x2"]) == ("2", "118")
    _assert_dompurify_clean(svg)


def test_payoff_svg_segment_touching_zero_takes_its_other_end():
    up = _segments(fv.payoff_svg([[90.0, 0.0], [110.0, 100.0]], spot=None))
    down = _segments(fv.payoff_svg([[90.0, -100.0], [110.0, 0.0]], spot=None))
    assert [s["stroke"] for s in up] == [fv.PROFIT_STROKE]
    assert [s["stroke"] for s in down] == [fv.LOSS_STROKE]

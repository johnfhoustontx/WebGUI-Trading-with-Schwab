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
    assert [s["id"] for s in fv.top_picks(sigs[:2], k=4)] == ["A", "B"]


def test_top_picks_fill_every_slot_when_a_filter_leaves_one_group():
    """Clicking one chip (say Calendars) must still show up to four cards."""
    sigs = [_sig(i, "CALENDAR", sc) for i, sc in
            (("P", 61), ("Q", 90), ("R", 75), ("S", 82), ("T", 40))]
    assert [s["id"] for s in fv.top_picks(sigs, k=4)] == ["Q", "S", "R", "P"]


def test_top_picks_take_distinct_groups_first_then_fill_by_score():
    sigs = [_sig("A", "VERTICAL", 80), _sig("B", "VERTICAL", 79),
            _sig("C", "CALENDAR", 50), _sig("D", "VERTICAL", 60)]
    # The one calendar beats the second and third spread to a card ...
    assert [s["id"] for s in fv.top_picks(sigs, k=3)] == ["A", "C", "B"]
    # ... and the remaining slots fill in score order, never duplicating a card.
    assert [s["id"] for s in fv.top_picks(sigs, k=4)] == ["A", "C", "B", "D"]
    assert [s["id"] for s in fv.top_picks(sigs, k=9)] == ["A", "C", "B", "D"]


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


# ------------------------------------------------------------ review nits (Task 5)

def test_pop_bar_colour_follows_the_rounded_label():
    """The label and the colour must agree: a bar reading "40%" is not amber."""
    b = fv.pop_bar(39.6)
    assert b["label"] == "40%" and b["tone"] == "neutral"
    b = fv.pop_bar(60.4)
    assert b["label"] == "60%" and b["tone"] == "neutral"
    assert fv.pop_bar(39.4)["tone"] == "warn"
    assert fv.pop_bar(60.6)["tone"] == "pos"


def test_halves_round_up_everywhere():
    """Python's round() is banker's rounding: round(42.5) == 42, round(12.5/5) == 2.
    One rule for every number the page shows."""
    assert fv.pop_bar(42.5)["label"] == "43%"
    assert fv.pop_bar(41.5)["label"] == "42%"
    assert fv._snap(12.5) == 15 and fv._snap(22.5) == 25
    f = fv.summary_facts({"symbol": "X", "signals": [{"iv_rank": 42.5}]})
    assert f["vol_rank"] == "Vol Rank 43"


def test_money_never_prints_a_negative_zero():
    assert fv.money(-0.001) == "$0.00"
    assert fv.money(-0.0) == "$0.00"
    assert fv.money(-0.004) == "$0.00"
    assert fv.money(-0.006) == "-$0.01"


def test_summary_facts_reports_both_drops_separately():
    """Moved from swing.status_text (B2): a volatility drop is a statement about the
    environment, not the candidate, so it keeps its own count and sentence."""
    f = fv.summary_facts({"symbol": "X", "signals": [{"id": "a"}], "filtered_out": 3,
                          "vol_filtered": 2})
    assert f["counts"] == ("1 idea · 3 below the quality bar · "
                           "2 where premium is too cheap to sell")
    # A payload written before vol_filtered existed renders as it always did.
    old = fv.summary_facts({"symbol": "X", "signals": [{"id": "a"}], "filtered_out": 2})
    assert old["counts"] == "1 idea · 2 below the quality bar"


# ------------------------------------------------------------ controls wiring

def test_expiry_range_for_a_preset_and_not_for_anything_else():
    assert fv.expiry_range_for("2–6 wk") == (14, 42)
    assert fv.expiry_range_for("Any") == (0, 120)
    assert fv.expiry_range_for(None) is None
    assert fv.expiry_range_for("Custom") is None


def test_bands_for_choice_never_raises_on_custom():
    """``risk_bands("Custom")`` raises KeyError; the toggle's handler goes through
    this so a Custom (or cleared) value writes nothing."""
    assert fv.bands_for_choice("Aggressive") == fv.risk_bands("Aggressive")
    assert fv.bands_for_choice(fv.RISK_CUSTOM) is None
    assert fv.bands_for_choice(None) is None
    assert fv.bands_for_choice("nonsense") is None


def test_risk_toggle_value_is_none_for_custom():
    """The toggle offers only the three styles; a hand-edited band shows NO
    selection plus a read-only Custom marker, never a "Custom" option value."""
    b = fv.risk_bands("Balanced")
    assert fv.risk_toggle_value(b["put_d_min"], b["put_d_max"],
                                b["call_d_min"], b["call_d_max"]) == "Balanced"
    assert fv.risk_toggle_value(-0.30, -0.12, 0.10, 0.20) is None


# ------------------------------------------------------------ chips

def test_toggle_chip_membership_and_all():
    assert fv.toggle_chip(None, "VERTICAL") == {"VERTICAL"}
    assert fv.toggle_chip({"VERTICAL"}, "CALENDAR") == {"VERTICAL", "CALENDAR"}
    assert fv.toggle_chip({"VERTICAL", "CALENDAR"}, "VERTICAL") == {"CALENDAR"}
    # Removing the last chip goes back to All rather than an empty page.
    assert fv.toggle_chip({"VERTICAL"}, "VERTICAL") is None
    assert fv.toggle_chip({"VERTICAL"}, fv.ALL_CHIP) is None


def test_toggle_chip_does_not_mutate_its_input():
    active = {"VERTICAL"}
    fv.toggle_chip(active, "CALENDAR")
    assert active == {"VERTICAL"}


def test_chip_is_active():
    assert fv.chip_is_active(None, fv.ALL_CHIP) is True
    assert fv.chip_is_active(None, "VERTICAL") is False
    assert fv.chip_is_active({"VERTICAL"}, "VERTICAL") is True
    assert fv.chip_is_active({"VERTICAL"}, fv.ALL_CHIP) is False


def test_carry_chips_survives_a_repaint_and_resets_on_a_new_symbol():
    sigs = [_sig("A", "VERTICAL", 70), _sig("C", "CALENDAR", 65)]
    assert fv.carry_chips({"VERTICAL"}, "SPY", "SPY", sigs) == {"VERTICAL"}
    assert fv.carry_chips({"VERTICAL"}, "SPY", "QQQ", sigs) is None
    assert fv.carry_chips(None, "SPY", "SPY", sigs) is None
    # A chosen group that the new scan did not produce is dropped ...
    assert fv.carry_chips({"VERTICAL", "STOCK"}, "SPY", "SPY", sigs) == {"VERTICAL"}
    # ... and if nothing chosen survives, back to All.
    assert fv.carry_chips({"STOCK"}, "SPY", "SPY", sigs) is None


# ------------------------------------------------------------ list + cards

def test_finder_columns_fit_without_the_old_extras():
    names = [c["name"] for c in fv.finder_columns()]
    assert names == ["strategy", "composite_score", "strikes", "expiry", "cost",
                     "max_profit", "max_loss", "pop", "grade", "actions"]


def test_the_strikes_column_sits_beside_expiry_and_shows_its_text():
    """Operator request: a row names its strikes and expiration without opening
    the detail panel. Legs text does not sort meaningfully, so it does not sort."""
    cols = {c["name"]: c for c in fv.finder_columns()}
    assert cols["strikes"]["label"] == "Strikes"
    assert cols["strikes"]["field"] == "strikes"
    assert not cols["strikes"]["sortable"]


def test_the_two_long_text_columns_wrap_so_the_actions_stay_on_screen():
    """Measured in the local harness at the app's 1440 px content width: with
    Strikes on one line a condor's legs and a collar's cost pushed the table 200 px
    past its box, hiding the action buttons - the redesign's first complaint.
    Letting those two cells wrap brings it back inside. Quasar's own td rule is
    nowrap; a Tailwind class beats it (NiceGUI layers Quasar below utilities)."""
    cols = {c["name"]: c for c in fv.finder_columns()}
    wrapping = {n for n, c in cols.items()
                if "whitespace-normal" in c.get("classes", "").split()}
    assert wrapping == {"strikes", "cost"}
    # A minimum width each, or the browser squeezes them to one leg per line
    # (a condor four lines tall, measured). Strikes gets room for two legs.
    assert "min-w-[10.5rem]" in cols["strikes"]["classes"].split()
    assert "min-w-[7rem]" in cols["cost"]["classes"].split()


def test_every_header_may_wrap_to_give_the_legs_room():
    """A header's label, not its numbers, set three columns' width ("Probability of
    profit" held 187 px over a 110 px bar); wrapping headers hands that to Strikes."""
    for col in fv.finder_columns():
        assert "whitespace-normal" in col.get("headerClasses", "").split(), col["name"]


def test_finder_columns_sort_numbers_by_number():
    """A formatted "$1,234" sorts as text; the sortable money / odds / expiry
    columns point their ``field`` at a numeric twin and a slot shows the text."""
    cols = {c["name"]: c for c in fv.finder_columns()}
    assert cols["composite_score"]["sortable"]
    assert cols["composite_score"]["field"] == "composite_score"
    assert cols["expiry"]["field"] == "_dte" and cols["expiry"]["sortable"]
    assert cols["max_profit"]["field"] == "_max_profit_n"
    assert cols["max_loss"]["field"] == "_max_loss_n"
    assert cols["pop"]["field"] == "_pop_n"
    assert not cols["cost"].get("sortable") and not cols["actions"].get("sortable")


_FLY = {"id": "x", "type": "BUTTERFLY_CALL", "group": "BUTTERFLY",
        "strategy_label": "Call Butterfly", "composite_score": 72.1, "grade": "Good",
        "grade_reason": "ok", "expiration": "2026-10-16", "dte": 30,
        "net_debit": 120.0, "max_profit": 374.8, "max_loss": 125.2, "pop_pct": 31.5,
        "payoff_curve": [[90, -120], [100, 380], [110, -120]], "underlying_price": 100.0}

_HOOKS = {"score_class": lambda s: f"score-{s}", "grade_class": lambda g: f"grade-{g}",
          "paper_types": {"BUTTERFLY_CALL"},
          "legs_text": lambda legs: f"legs-{len(legs or [])}"}


def test_finder_rows_carry_shape_bars_and_paper_gate():
    row = fv.finder_rows([_FLY], **_HOOKS)[0]
    assert row["id"] == "x"
    assert row["strategy"] == "Call Butterfly" and row["cost"] == "$120 debit"
    assert row["_payoff_svg"].startswith("<svg") and row["_allow_paper"] is True
    assert 'width="72"' in row["_payoff_svg"] and 'height="20"' in row["_payoff_svg"]
    assert row["_pop"]["label"] == "32%" and row["pop"] == "32%"
    assert row["_pop_fill"] == fv.POP_FILL["warn"]
    assert row["expiry"] == "Oct 16 · 30d" and row["_dte"] == 30
    assert row["max_profit"] == "$375" and row["max_loss"] == "$125"
    assert row["_max_profit_n"] == 374.8 and row["_max_loss_n"] == 125.2
    assert "_rr" not in row            # no list slot draws the split bar
    assert row["score_text"] == "72"
    assert row["_score_class"] == "score-72.1" and row["_grade_class"] == "grade-Good"
    assert row["grade"] == "Good" and row["grade_reason"] == "ok"
    assert row["_undefined_risk"] is False
    assert row["strikes"] == "legs-0"          # _FLY here carries no legs


def test_finder_rows_paper_gate_and_hooks_are_injected():
    row = fv.finder_rows([_FLY], score_class=str, grade_class=str, paper_types=set(),
                         legs_text=lambda legs: "L 1C")[0]
    assert row["_allow_paper"] is False
    assert row["strikes"] == "L 1C"


def test_a_wrapped_strikes_cell_breaks_between_legs_never_inside_one():
    """The cell wraps at spaces, and a leg reads "S 530P" - so a plain space would
    strand the "S" on one line and the strike on the next. Inside a leg the spaces
    are non-breaking, and so is the space BEFORE each slash, so a line ends "S 560C /"
    rather than the next one starting "/ L 540P"."""
    hooks = {**_HOOKS, "legs_text": lambda legs: "L 100 shares / S 560C / L 540P 10/19"}
    (row,) = fv.finder_rows([_FLY], **hooks)
    nb = " "
    assert row["strikes"] == f"L{nb}100{nb}shares{nb}/ S{nb}560C{nb}/ L{nb}540P{nb}10/19"
    assert row["strikes"].replace(nb, " ") == "L 100 shares / S 560C / L 540P 10/19"


def test_an_empty_legs_line_passes_through():
    (row,) = fv.finder_rows([_FLY], **{**_HOOKS, "legs_text": lambda legs: "—"})
    assert row["strikes"] == "—"


def test_finder_rows_hand_the_legs_to_the_legs_hook():
    seen = []
    legs = [{"side": "long", "kind": "call", "strike": 100.0}]
    fv.finder_rows([{**_FLY, "legs": legs}], **{**_HOOKS,
                   "legs_text": lambda l: seen.append(l) or "x"})
    assert seen == [legs]


def test_finder_rows_mark_unbounded_sides():
    long_call = {"id": "lc", "type": "LONG_CALL", "net_debit": 300.0, "max_profit": None,
                 "unbounded_profit": True, "max_loss": 300.0}
    naked = {"id": "sp", "type": "SHORT_PUT", "net_credit": 240.0, "max_profit": 240.0,
             "max_loss": 10866.0, "unbounded_loss": True}
    rows = {r["id"]: r for r in fv.finder_rows([long_call, naked], **_HOOKS)}
    assert rows["lc"]["max_profit"] == "∞" and rows["lc"]["_undefined_risk"] is False
    assert rows["lc"]["_max_profit_n"] > 1e9
    assert rows["sp"]["max_loss"] == "∞" and rows["sp"]["_undefined_risk"] is True
    assert rows["sp"]["_max_loss_n"] > 1e9


def test_finder_rows_degrade_on_a_bare_signal():
    row = fv.finder_rows([{"id": "b"}], **_HOOKS)[0]
    assert row["strategy"] == "" and row["cost"] == "—" and row["expiry"] == "—"
    assert row["pop"] == "—" and row["_pop"] is None and row["_pop_fill"] == ""
    assert row["_payoff_svg"] == "" and row["score_text"] == "—"
    assert row["max_profit"] == "—" and row["_max_profit_n"] is None
    assert row["_pop_n"] is None and row["_dte"] is None


def test_finder_rows_are_ranked_best_first():
    sigs = [_sig("A", "VERTICAL", 50), _sig("B", "CALENDAR", 80), _sig("C", "STOCK", None)]
    assert [r["id"] for r in fv.finder_rows(sigs, **_HOOKS)] == ["B", "A", "C"]


def test_card_facts_for_a_top_pick():
    c = fv.card_facts(_FLY)
    assert c["title"] == "Call Butterfly" and c["score"] == 72.1 and c["grade"] == "Good"
    assert c["expiry"] == "Oct 16 · 30d" and c["cost"] == "$120 debit"
    # A card is ~300px wide: the shape fills it rather than sitting in a corner.
    assert 'width="280"' in c["payoff_svg"] and 'height="56"' in c["payoff_svg"]
    _assert_dompurify_clean(c["payoff_svg"])
    assert c["rr"] == fv.risk_reward_bar(_FLY) and c["pop"] == fv.pop_bar(31.5)
    assert c["pop_fill"] == fv.POP_FILL["warn"]
    assert c["score_text"] == "72"


def test_card_facts_degrade():
    c = fv.card_facts({})
    assert c["title"] == "—" and c["score"] is None and c["score_text"] == "—"
    assert c["payoff_svg"] == "" and c["pop"] is None and c["pop_fill"] == ""


def test_pop_fill_is_a_fixed_class_per_tone():
    assert set(fv.POP_FILL) == {"warn", "neutral", "pos"}
    assert all(v.startswith("bg-[#") for v in fv.POP_FILL.values())


def test_pop_fill_neutral_is_the_theme_accent_not_grey():
    from pages.options import theme
    assert fv.POP_FILL["neutral"] == f"bg-[{theme.THEME['palette']['primary']}]"
    assert fv.POP_FILL["warn"] == "bg-[#fbbf24]" and fv.POP_FILL["pos"] == "bg-[#34d399]"
    assert len(set(fv.POP_FILL.values())) == 3


# ------------------------------------------------------------ review fixes (Task 5)

def test_payload_answers_scan_only_for_the_symbol_being_scanned():
    msft = {"symbol": "MSFT"}
    assert fv.payload_answers_scan(None, {"symbol": "AAPL"}) is True
    assert fv.payload_answers_scan(msft, {"symbol": "AAPL"}) is False
    assert fv.payload_answers_scan(msft, {"symbol": "msft"}) is True
    assert fv.payload_answers_scan(msft, None) is False
    assert fv.payload_answers_scan(msft, {}) is False
    # A blank request names no symbol, so any answer is its answer.
    assert fv.payload_answers_scan({"symbol": ""}, {"symbol": "SPY"}) is True


def test_payload_answers_scan_compares_the_request_not_only_the_symbol():
    """SPY 1-2 wk then SPY 1-3 mo: the first result must not paint as the second.
    The handler echoes the request back as ``payload.params``."""
    wk = {"symbol": "SPY", "dte_min": 7, "dte_max": 14, "put_d_min": -0.2,
          "put_d_max": -0.1, "call_d_min": 0.1, "call_d_max": 0.2, "min_cr_fraction": 0.1}
    mo = {**wk, "dte_min": 30, "dte_max": 90}
    assert fv.payload_answers_scan(mo, {"symbol": "SPY", "params": wk}) is False
    assert fv.payload_answers_scan(mo, {"symbol": "SPY", "params": mo}) is True
    # Numbers compare as numbers (JSON round-trips 7 as 7.0 elsewhere).
    assert fv.payload_answers_scan(mo, {"symbol": "SPY",
                                        "params": {**mo, "dte_min": 30.0}}) is True
    aggressive = {**mo, "call_d_max": 0.3}
    assert fv.payload_answers_scan(aggressive, {"symbol": "SPY", "params": mo}) is False
    # A payload that echoes no params can only be matched on its symbol.
    assert fv.payload_answers_scan(mo, {"symbol": "SPY"}) is True


def test_no_data_label_names_the_reason_in_the_pages_voice():
    assert fv.no_data_label({"filtered_out": 4}) == (
        "No strategies cleared the quality bar for this symbol.")
    assert fv.no_data_label({"filtered_out": 4, "vol_filtered": 2}) == (
        "No strategies cleared the quality bar for this symbol.")
    assert fv.no_data_label({"vol_filtered": 3}) == (
        "No strategies to show — premium is too cheap to sell for this symbol.")
    assert fv.no_data_label({}) == (
        "No strategies could be built for this symbol in this expiry range.")
    assert fv.no_data_label(None) == fv.no_data_label({})


def test_payoff_svg_carries_nothing_but_numbers_and_fixed_colours():
    """The list slot renders ``_payoff_svg`` through Vue's v-html, which is NOT
    sanitised. Safe only because every value in it is a number we formatted or a
    fixed constant - so feed it label-like junk and check the output grammar."""
    import re
    junk = [["<script>alert(1)</script>", "1"], ['90" onload="x', -5],
            [95.0, "-1e999"], ["100", "200"], [110.0, -50.0], [120, "</svg><img src=x>"]]
    svg = fv.payoff_svg(junk, spot='"><img onerror=alert(1)>', width=72, height=20)
    assert svg.startswith("<svg") and svg.count("<line ") >= 2   # really drew
    assert re.fullmatch(r'[A-Za-z0-9 <>/="#.:\-]+', svg)
    assert set(re.findall(r"<(/?[A-Za-z]+)", svg)) <= {"svg", "/svg", "line"}
    value = re.compile(r"-?\d+(\.\d+)?|#[0-9a-f]{6}|round|\d+ \d+"
                       r"|0 0 \d+ \d+|http://www\.w3\.org/2000/svg")
    pairs = re.findall(r'([A-Za-z-]+)="([^"]*)"', svg)
    assert pairs
    for name, val in pairs:
        assert value.fullmatch(val), (name, val)
    for bad in ("script", "img", "onload", "onerror", "alert"):
        assert bad not in svg


def test_finder_rows_show_the_score_the_card_shows():
    """One rounding rule for the list badge and the card: 72.5 reads 73 on both,
    while the list still sorts on the raw score."""
    sig = {**_FLY, "composite_score": 72.5}
    row = fv.finder_rows([sig], **_HOOKS)[0]
    assert row["score_text"] == fv.card_facts(sig)["score_text"] == "73"
    assert row["composite_score"] == 72.5
    assert {c["name"]: c for c in fv.finder_columns()}["composite_score"]["field"] == \
        "composite_score"


# ------------------------------------------------ scan bar from the cached scan
# The Expiry and Risk style controls start on the params the cached result was
# scanned with, so the bar never describes a different scan than the ideas below.

_PAGE_PARAMS = {"symbol": "NVDA", "dte_min": 7, "dte_max": 14,
                "put_d_min": -0.30, "put_d_max": -0.20,
                "call_d_min": 0.20, "call_d_max": 0.30, "min_cr_fraction": 0.15}


def test_scan_controls_follow_the_cached_params():
    got = fv.scan_controls_from({"symbol": "NVDA", "params": _PAGE_PARAMS})
    assert got["dte_min"] == 7 and got["dte_max"] == 14
    assert (got["put_d_min"], got["put_d_max"]) == (-0.30, -0.20)
    assert (got["call_d_min"], got["call_d_max"]) == (0.20, 0.30)
    assert got["min_credit_pct"] == 15.0
    assert fv.expiry_preset_for(got["dte_min"], got["dte_max"]) == "1–2 wk"
    assert fv.risk_style_for(got["put_d_min"], got["put_d_max"],
                             got["call_d_min"], got["call_d_max"]) == "Aggressive"


def test_scan_controls_default_with_nothing_cached():
    want = {"dte_min": 0, "dte_max": 120, **fv.risk_bands(fv.RISK_DEFAULT),
            "min_credit_pct": 10.0}
    for payload in (None, {}, {"symbol": "SPY"}, {"params": None},
                    {"params": "junk"}, {"params": {}}):
        assert fv.scan_controls_from(payload) == want


def test_the_credit_floor_converts_without_float_noise():
    got = fv.scan_controls_from({"params": {"min_cr_fraction": 0.1}})
    assert got["min_credit_pct"] == 10.0


def test_a_bad_dte_pair_falls_back_as_a_pair():
    default = (0, 120)
    for dte in ({"dte_min": 7}, {"dte_max": 14},
                {"dte_min": 30, "dte_max": 7},
                {"dte_min": -1, "dte_max": 14},
                {"dte_min": 0, "dte_max": 0},
                {"dte_min": float("nan"), "dte_max": 14},
                {"dte_min": True, "dte_max": 14},
                {"dte_min": 7.5, "dte_max": 14}):
        got = fv.scan_controls_from({"params": dte})
        assert (got["dte_min"], got["dte_max"]) == default, dte


def test_dte_values_come_back_as_whole_days():
    got = fv.scan_controls_from({"params": {"dte_min": 7.0, "dte_max": 14.0}})
    assert (got["dte_min"], got["dte_max"]) == (7, 14)
    assert all(isinstance(got[k], int) for k in ("dte_min", "dte_max"))


def test_bands_fall_back_as_a_set_when_any_is_missing_or_bad():
    default = fv.risk_bands(fv.RISK_DEFAULT)
    partial = {k: v for k, v in _PAGE_PARAMS.items() if k != "call_d_max"}
    nan = {**_PAGE_PARAMS, "put_d_min": float("nan")}
    for params in (partial, nan):
        got = fv.scan_controls_from({"params": params})
        assert {k: got[k] for k in default} == default


def test_a_hand_edited_band_is_kept_and_reads_custom():
    params = {**_PAGE_PARAMS, "put_d_min": -0.27}
    got = fv.scan_controls_from({"params": params})
    assert got["put_d_min"] == -0.27
    assert fv.risk_style_for(got["put_d_min"], got["put_d_max"],
                             got["call_d_min"], got["call_d_max"]) == fv.RISK_CUSTOM


def test_a_bad_credit_floor_falls_back_alone():
    for frac in (-0.05, float("nan"), None, True):
        got = fv.scan_controls_from({"params": {**_PAGE_PARAMS, "min_cr_fraction": frac}})
        assert got["min_credit_pct"] == 10.0
        assert got["dte_min"] == 7            # the other groups still follow

"""Pure view model for the redesigned Strategy Finder (``finder_view.py``)."""
import datetime

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
    assert f["price"] == "Price unavailable" and f["vol_rank"] is None
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
    assert fv.expiry_range_for("All") == (0, None)
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
    want = {"dte_min": 0, "dte_max": None, **fv.risk_bands(fv.RISK_DEFAULT),
            "min_credit_pct": 10.0}
    for payload in (None, {}, {"symbol": "SPY"}, {"params": None},
                    {"params": "junk"}, {"params": {}}):
        assert fv.scan_controls_from(payload) == want


def test_the_credit_floor_converts_without_float_noise():
    got = fv.scan_controls_from({"params": {"min_cr_fraction": 0.1}})
    assert got["min_credit_pct"] == 10.0


def test_a_bad_dte_pair_falls_back_as_a_pair():
    default = (0, None)
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


# ------------------------------------------------ the whole chain (2026-09-14)
# Design: docs/plans/2026-09-14-strategy-finder-whole-chain-design.md

def test_presets_reach_past_120_days_and_all_has_no_upper_limit():
    assert fv.EXPIRY_PRESETS == [("1–2 wk", 7, 14), ("2–6 wk", 14, 42),
                                 ("1–3 mo", 30, 90), ("3–12 mo", 90, 365),
                                 ("1 yr+", 365, None), ("All", 0, None)]
    for label, lo, hi in fv.EXPIRY_PRESETS:
        assert fv.expiry_range_for(label) == (lo, hi)
        assert fv.expiry_preset_for(lo, hi) == label
    assert fv.expiry_preset_for(0, None) == "All"
    assert fv.expiry_preset_for(365, None) == "1 yr+"
    assert fv.DEFAULT_DTE == (0, None)


def test_the_old_any_range_is_now_a_hand_typed_range():
    assert fv.expiry_preset_for(0, 120) is None
    assert fv.expiry_range_for("Any") is None


def test_an_unreadable_upper_bound_is_not_no_limit():
    """Only a real absence means no limit; junk in the field matches no preset."""
    assert fv.expiry_preset_for(0, float("nan")) is None
    assert fv.expiry_preset_for(0, "junk") is None
    assert fv.expiry_preset_for(None, None) is None


def _dte(got):
    return got["dte_min"], got["dte_max"]


def test_scan_controls_follow_a_cached_scan_with_no_upper_limit():
    assert _dte(fv.scan_controls_from({"params": {"dte_min": 0, "dte_max": None}})) == (0, None)
    assert _dte(fv.scan_controls_from({"params": {"dte_min": 90, "dte_max": None}})) == (90, None)
    got = fv.scan_controls_from({"params": {"dte_min": 365.0, "dte_max": None}})
    assert _dte(got) == (365, None) and isinstance(got["dte_min"], int)
    assert fv.expiry_preset_for(*_dte(got)) == "1 yr+"


def test_no_upper_limit_still_needs_a_whole_lower_bound():
    for dte in ({"dte_max": None}, {"dte_min": None, "dte_max": None},
                {"dte_min": -1, "dte_max": None}, {"dte_min": 7.5, "dte_max": None},
                {"dte_min": float("nan"), "dte_max": None},
                {"dte_min": True, "dte_max": None},
                {"dte_min": 7}):                  # a MISSING max is not no limit
        assert _dte(fv.scan_controls_from({"params": dte})) == (0, None), dte


def test_payload_answers_a_scan_with_no_upper_limit():
    want = {"symbol": "SPY", "dte_min": 0, "dte_max": None}
    assert fv.payload_answers_scan(want, {"symbol": "SPY", "params": dict(want)}) is True
    assert fv.payload_answers_scan(want, {"symbol": "SPY",
                                          "params": {**want, "dte_max": 120}}) is False
    assert fv.payload_answers_scan({**want, "dte_max": 120},
                                   {"symbol": "SPY", "params": want}) is False


# -------------------------------------------------------------------- earnings

def _this_year(month_day):
    return f"{datetime.date.today().year}-{month_day}"


def test_earnings_text_names_the_report_date():
    sig = {"spans_earnings": True, "earnings_date": "2026-11-19"}
    assert fv.earnings_text(sig, today=datetime.date(2026, 9, 14)) == "Earnings Nov 19"


def test_earnings_text_names_the_year_when_the_report_is_in_another_year():
    sig = {"spans_earnings": True, "earnings_date": "2027-01-22"}
    assert fv.earnings_text(sig, today=datetime.date(2026, 9, 14)) == (
        "Earnings Jan 22, 2027")


def test_earnings_text_is_none_without_a_stamp_or_a_date():
    today = datetime.date(2026, 9, 14)
    assert fv.earnings_text({"earnings_date": "2026-11-19"}, today=today) is None
    assert fv.earnings_text({"spans_earnings": False, "earnings_date": "2026-11-19"},
                            today=today) is None
    # earnings_status rides on EVERY row; it is not the stamp.
    assert fv.earnings_text({"earnings_status": "found", "earnings_date": "2026-11-19"},
                            today=today) is None
    for bad in (None, "", "garbage", 20261119):
        assert fv.earnings_text({"spans_earnings": True, "earnings_date": bad},
                                today=today) is None
    assert fv.earnings_text({"spans_earnings": True}, today=today) is None
    assert fv.earnings_text(None) is None


def test_card_and_row_carry_the_earnings_tag():
    stamped = {**_FLY, "spans_earnings": True, "earnings_date": _this_year("11-19")}
    assert fv.card_facts(stamped)["earnings"] == "Earnings Nov 19"
    assert fv.finder_rows([stamped], **_HOOKS)[0]["earnings"] == "Earnings Nov 19"
    assert fv.card_facts(_FLY)["earnings"] is None
    assert fv.finder_rows([_FLY], **_HOOKS)[0]["earnings"] is None


# ------------------------------------------------------ price on every answer

def test_summary_shows_the_spot_on_a_zero_idea_answer():
    f = fv.summary_facts({"symbol": "SPY", "signals": [], "spot": 764.48,
                          "filtered_out": 18})
    assert f["price"] == "$764.48"
    assert f["counts"] == "0 ideas · 18 below the quality bar"


def test_summary_price_prefers_the_payload_spot_over_a_row():
    f = fv.summary_facts({"symbol": "SPY", "spot": 764.48,
                          "signals": [{"underlying_price": 700.0}]})
    assert f["price"] == "$764.48"
    # A payload written before spot existed still takes the first row's price.
    old = fv.summary_facts({"symbol": "SPY", "signals": [{"underlying_price": 700.0}]})
    assert old["price"] == "$700.00"


def test_summary_says_price_unavailable_never_a_zero():
    for spot in (None, float("nan"), "junk", True):
        f = fv.summary_facts({"symbol": "SPY", "signals": [], "spot": spot})
        assert f["price"] == "Price unavailable", spot
    f = fv.summary_facts({"symbol": "SPY", "spot": float("nan"),
                          "signals": [{"underlying_price": float("nan")}]})
    assert f["price"] == "Price unavailable"


def test_summary_counts_expirations_that_could_not_be_loaded():
    base = {"symbol": "SPY", "signals": [{"id": "a"}]}
    assert fv.summary_facts({**base, "expiries_failed": 3})["counts"] == (
        "1 idea · 3 expirations could not be loaded")
    assert fv.summary_facts({**base, "expiries_failed": 1})["counts"] == (
        "1 idea · 1 expiration could not be loaded")
    # None is "not counted", not zero: no line either way.
    for n in (None, 0):
        assert fv.summary_facts({**base, "expiries_failed": n})["counts"] == "1 idea"


def test_summary_counts_the_ideas_not_shown():
    base = {"symbol": "$SPX", "signals": [{"id": "a"}]}
    assert fv.summary_facts({**base, "not_shown": 1040})["counts"] == (
        "1 idea · 1,040 lower-scoring ideas not shown")
    assert fv.summary_facts({**base, "not_shown": 1})["counts"] == (
        "1 idea · 1 lower-scoring idea not shown")
    assert fv.summary_facts({**base, "not_shown": 0})["counts"] == "1 idea"


def test_summary_counts_every_reason_in_order():
    f = fv.summary_facts({"symbol": "SPY", "signals": [{"id": "a"}] * 2,
                          "filtered_out": 18, "vol_filtered": 2, "not_shown": 5,
                          "expiries_failed": 2})
    assert f["counts"] == ("2 ideas · 18 below the quality bar · "
                           "2 where premium is too cheap to sell · "
                           "5 lower-scoring ideas not shown · "
                           "2 expirations could not be loaded")


# ---------------------------------------------------------- the empty answer

_SPY = {"symbol": "SPY", "signals": [], "spot": 764.48}


def test_no_data_label_a_failed_scan_comes_first():
    for err in ("TypeError", "ConnectionError"):
        p = {**_SPY, "error": err, "chain_missing": True, "filtered_out": 3}
        assert fv.no_data_label(p) == (
            "The scan for SPY failed. Check System Status and scan again.")
    # A falsy error is no error.
    assert "failed" not in fv.no_data_label({**_SPY, "error": ""})


def test_no_data_label_no_chain():
    p = {**_SPY, "chain_missing": True, "no_expiries_in_range": True, "filtered_out": 3}
    assert fv.no_data_label(p) == "No option chain came back for SPY at $764.48."


def test_no_data_label_no_expiries_in_range():
    p = {**_SPY, "no_expiries_in_range": True, "filtered_out": 3}
    assert fv.no_data_label(p) == "SPY at $764.48 has no expirations in this expiry range."


def test_no_data_label_quality_cut_names_symbol_and_price():
    p = {**_SPY, "filtered_out": 4, "vol_filtered": 2}
    assert fv.no_data_label(p) == "No strategies cleared the quality bar for SPY at $764.48."


def test_no_data_label_too_cheap_names_symbol_and_price():
    assert fv.no_data_label({**_SPY, "vol_filtered": 3}) == (
        "No strategies for SPY at $764.48 — premium is too cheap to sell.")


def test_no_data_label_built_nothing_names_symbol_and_price():
    assert fv.no_data_label(_SPY) == (
        "No strategies could be built for SPY at $764.48 in this expiry range.")


def test_no_data_label_without_a_price_names_the_symbol_alone():
    bare = {"symbol": "SPY", "signals": [], "spot": None}
    assert fv.no_data_label({**bare, "chain_missing": True}) == (
        "No option chain came back for SPY.")
    assert fv.no_data_label({**bare, "no_expiries_in_range": True}) == (
        "SPY has no expirations in this expiry range.")
    assert fv.no_data_label({**bare, "filtered_out": 1}) == (
        "No strategies cleared the quality bar for SPY.")
    assert fv.no_data_label({**bare, "vol_filtered": 1}) == (
        "No strategies for SPY — premium is too cheap to sell.")
    assert fv.no_data_label({**bare, "spot": float("nan")}) == (
        "No strategies could be built for SPY in this expiry range.")


def test_no_data_label_without_a_symbol_keeps_the_generic_sentences():
    assert fv.no_data_label({"error": "TypeError"}) == (
        "The scan failed. Check System Status and scan again.")
    assert fv.no_data_label({"chain_missing": True, "spot": 1.0}) == (
        "No option chain came back for this symbol.")
    assert fv.no_data_label({"no_expiries_in_range": True}) == (
        "This symbol has no expirations in this expiry range.")


# ----------------------------------------------------------- the running count

def test_scan_timeout_text_counts_whole_seconds():
    assert fv.scan_timeout_text("SPY", 12) == "Scanning SPY… 12 s"
    assert fv.scan_timeout_text("SPY", 12.9) == "Scanning SPY… 12 s"
    assert fv.scan_timeout_text(" $spx ", 0.2) == "Scanning $SPX… 0 s"
    assert fv.scan_timeout_text("", 31) == "Scanning… 31 s"
    assert fv.scan_timeout_text(None, 31) == "Scanning… 31 s"
    assert fv.scan_timeout_text("SPY", -1) == "Scanning SPY… 0 s"
    assert fv.scan_timeout_text("SPY", float("nan")) == "Scanning SPY…"
    assert fv.scan_timeout_text("SPY", None) == "Scanning SPY…"


def test_a_failed_scan_counts_nothing():
    """A failed scan read no ideas and no cuts - "0 ideas" would be a count nobody
    read. The price still shows when it was read."""
    f = fv.summary_facts({"symbol": "SPY", "signals": [], "spot": 764.48,
                          "error": "TypeError", "filtered_out": 3, "vol_filtered": 1,
                          "not_shown": 4, "expiries_failed": 2})
    assert f["counts"] == "Scan failed"
    assert f["price"] == "$764.48"
    # A falsy error is no error.
    assert fv.summary_facts({"symbol": "SPY", "signals": [], "error": ""})["counts"] == (
        "0 ideas")


def test_no_data_label_uppercases_the_symbol():
    assert fv.no_data_label({"symbol": " spy ", "spot": 764.48, "chain_missing": True}) == (
        "No option chain came back for SPY at $764.48.")
    assert fv.no_data_label({"symbol": "spy", "error": "TypeError"}) == (
        "The scan for SPY failed. Check System Status and scan again.")


# ---------------------------------------------------- the large-chain chooser

_CHOICES = [
    {"key": "next_30", "label": "Next 30 days", "count": 23, "est_seconds": 17},
    {"key": "next_90", "label": "Next 90 days", "count": 35, "est_seconds": 26},
    {"key": "monthly", "label": "Monthlies only", "count": 19, "est_seconds": 14},
    {"key": "all", "label": "Everything", "count": 56, "est_seconds": 42},
]
_ASK = {"symbol": "$SPX", "spot": 6512.25, "signals": [], "needs_choice": True,
        "expiration_count": 56, "choices": _CHOICES, "expiry_choice": None,
        "params": {"symbol": "$SPX"}}


def test_choice_label_is_a_fixed_map_of_the_four_keys():
    assert fv.choice_label("next_30") == "Next 30 days"
    assert fv.choice_label("next_90") == "Next 90 days"
    assert fv.choice_label("monthly") == "Monthlies only"
    assert fv.choice_label("all") == "Everything"
    for junk in ("weekly", "", None, 3, "ALL", ["all"]):
        assert fv.choice_label(junk) is None


def test_chooser_facts_for_a_large_chain():
    f = fv.chooser_facts(_ASK)
    assert f["title"] == "$SPX lists 56 expirations in this range."
    assert f["prompt"] == "Choose what to scan:"
    assert f["buttons"] == [
        {"key": "next_30", "text": "Next 30 days · 23 · ~17 s", "enabled": True},
        {"key": "next_90", "text": "Next 90 days · 35 · ~26 s", "enabled": True},
        {"key": "monthly", "text": "Monthlies only · 19 · ~14 s", "enabled": True},
        {"key": "all", "text": "Everything · 56 · ~42 s", "enabled": True},
    ]


def test_chooser_facts_is_none_unless_the_answer_asks():
    assert fv.chooser_facts(None) is None
    assert fv.chooser_facts({}) is None
    # An old payload, and an answer that scanned, carry no chooser.
    assert fv.chooser_facts({"symbol": "SPY", "signals": [{"id": "a"}]}) is None
    assert fv.chooser_facts({**_ASK, "needs_choice": False}) is None
    # A failed scan wins over everything.
    assert fv.chooser_facts({**_ASK, "error": "TypeError"}) is None


def test_chooser_facts_keeps_the_payload_order():
    f = fv.chooser_facts({**_ASK, "choices": list(reversed(_CHOICES))})
    assert [b["key"] for b in f["buttons"]] == ["all", "monthly", "next_90", "next_30"]


def test_chooser_title_never_prints_a_count_it_did_not_read():
    for junk in (None, "lots", float("nan"), True, -3, 2.5):
        f = fv.chooser_facts({**_ASK, "expiration_count": junk})
        assert f["title"] == "$SPX lists many expirations in this range.", junk
    assert fv.chooser_facts({**_ASK, "expiration_count": 1})["title"] == (
        "$SPX lists 1 expiration in this range.")
    assert fv.chooser_facts({**_ASK, "expiration_count": 1200})["title"] == (
        "$SPX lists 1,200 expirations in this range.")
    assert fv.chooser_facts({**_ASK, "symbol": " spx "})["title"].startswith("SPX lists")
    # No symbol: a pick could scan nothing, so there is no chooser at all.
    for blank in ("", "   ", None):
        assert fv.chooser_facts({**_ASK, "symbol": blank}) is None, blank


def test_a_choice_with_nothing_in_it_is_disabled_and_says_zero():
    zero = {"key": "monthly", "label": "Monthlies only", "count": 0, "est_seconds": 0}
    f = fv.chooser_facts({**_ASK, "choices": [zero]})
    # Nothing to scan takes no time worth quoting, so the estimate part goes.
    assert f["buttons"] == [{"key": "monthly", "text": "Monthlies only · 0",
                             "enabled": False}]


def test_a_choice_drops_the_parts_it_could_not_read():
    def button(**kw):
        entry = {"key": "all", "label": "Everything", "count": 56, "est_seconds": 42}
        entry.update(kw)
        return fv.chooser_facts({**_ASK, "choices": [entry]})["buttons"][0]
    assert button(count=None) == {"key": "all", "text": "Everything · ~42 s",
                                  "enabled": False}
    assert button(count=float("nan"))["enabled"] is False
    assert button(count=True)["text"] == "Everything · ~42 s"
    assert button(count=-2)["text"] == "Everything · ~42 s"
    assert button(est_seconds=None)["text"] == "Everything · 56"
    assert button(est_seconds="soon")["text"] == "Everything · 56"
    assert button(count=None, est_seconds=None)["text"] == "Everything"
    assert button(count=1500, est_seconds=1125)["text"] == "Everything · 1,500 · ~1,125 s"
    assert button(est_seconds=16.5)["text"] == "Everything · 56 · ~17 s"


def test_a_choice_label_falls_back_to_the_fixed_map():
    for junk in (None, "", "   ", 7):
        entry = {"key": "next_90", "label": junk, "count": 35, "est_seconds": 26}
        f = fv.chooser_facts({**_ASK, "choices": [entry]})
        assert f["buttons"][0]["text"] == "Next 90 days · 35 · ~26 s", junk
    entry = {"key": "next_90", "count": 35, "est_seconds": 26}
    assert fv.chooser_facts({**_ASK, "choices": [entry]})["buttons"][0]["text"] == (
        "Next 90 days · 35 · ~26 s")


def test_malformed_choices_are_skipped_and_none_left_is_no_chooser():
    junk = [None, "all", {"label": "Everything", "count": 4},
            {"key": "weekly", "label": "Weeklies", "count": 4}, {"key": None}]
    f = fv.chooser_facts({**_ASK, "choices": junk + [_CHOICES[2]]})
    assert [b["key"] for b in f["buttons"]] == ["monthly"]
    assert fv.chooser_facts({**_ASK, "choices": junk}) is None
    for bad in (None, [], {"key": "all"}, "all", 4):
        assert fv.chooser_facts({**_ASK, "choices": bad}) is None


def test_summary_on_an_answer_that_asks():
    f = fv.summary_facts(_ASK)
    assert f["symbol"] == "$SPX" and f["price"] == "$6,512.25"
    assert f["counts"] == "56 expirations — choose what to scan"
    assert f["can_change"] is False
    for junk in (None, float("nan"), "lots", True):
        assert fv.summary_facts({**_ASK, "expiration_count": junk})["counts"] == (
            "Choose what to scan")
    assert fv.summary_facts({**_ASK, "expiration_count": 1})["counts"] == (
        "1 expiration — choose what to scan")
    # A failed scan still wins.
    failed = fv.summary_facts({**_ASK, "error": "TypeError"})
    assert failed["counts"] == "Scan failed" and failed["can_change"] is False


def test_summary_names_the_choice_a_scan_applied():
    base = {"symbol": "$SPX", "spot": 6512.25, "signals": [{"id": "a"}] * 3,
            "needs_choice": False, "expiration_count": 56, "expirations_scanned": 19,
            "choices": _CHOICES, "expiry_choice": "monthly", "expiries_failed": 1}
    f = fv.summary_facts(base)
    assert f["counts"] == ("3 ideas · 1 expiration could not be loaded · "
                           "Scanned 19 of 56 expirations · Monthlies only")
    assert f["can_change"] is True
    one = fv.summary_facts({**base, "expirations_scanned": 1, "expiries_failed": 0})
    assert one["counts"] == "3 ideas · Scanned 1 of 56 expirations · Monthlies only"
    big = fv.summary_facts({**base, "expirations_scanned": 1100, "expiration_count": 2400,
                            "expiries_failed": 0, "expiry_choice": "all"})
    assert big["counts"] == "3 ideas · Scanned 1,100 of 2,400 expirations · Everything"


def test_summary_adds_no_scanned_line_without_a_known_choice_and_both_counts():
    base = {"symbol": "$SPX", "signals": [{"id": "a"}], "expiration_count": 56,
            "expirations_scanned": 19, "expiry_choice": "monthly"}
    assert fv.summary_facts(base)["can_change"] is True
    for change in ({"expiry_choice": None}, {"expiry_choice": "weekly"},
                   {"expirations_scanned": None}, {"expiration_count": None},
                   {"expirations_scanned": float("nan")}, {"expiration_count": "lots"},
                   {"expiration_count": True}):
        f = fv.summary_facts({**base, **change})
        assert f["counts"] == "1 idea", change
        assert f["can_change"] is False, change


def test_an_old_payload_renders_exactly_as_before():
    old = {"symbol": "SPY", "filtered_out": 6, "spot": 540.12,
           "view": {"direction": "neutral"}, "signals": [{"iv_rank": 55.0}] * 2}
    assert fv.summary_facts(old) == {
        "symbol": "SPY", "price": "$540.12", "pills": ["Neutral"],
        "vol_rank": "Vol Rank 55", "counts": "2 ideas · 6 below the quality bar",
        "can_change": False}
    assert fv.no_data_label({**_SPY, "filtered_out": 2}) == (
        "No strategies cleared the quality bar for SPY at $764.48.")
    assert fv.chooser_facts(old) is None


def test_no_data_label_on_an_answer_that_asks():
    assert fv.no_data_label(_ASK) == "Choose which expirations to scan for $SPX."
    # Ahead of every other reason but a failure.
    assert fv.no_data_label({**_ASK, "chain_missing": True, "filtered_out": 3}) == (
        "Choose which expirations to scan for $SPX.")
    # With no symbol there is no chooser to point at: the ordinary line.
    assert fv.no_data_label({**_ASK, "symbol": None}) == (
        "No strategies could be built for this symbol in this expiry range.")
    assert fv.no_data_label({**_ASK, "error": "TypeError"}) == (
        "The scan for $SPX failed. Check System Status and scan again.")


def test_whole_count_reads_a_numeric_string_like_every_other_count():
    # _fmt.num reads "56" as 56.0, as it does for the module's other counts.
    assert fv._whole_count("56") == 56
    assert fv.chooser_facts({**_ASK, "expiration_count": "56"})["title"] == (
        "$SPX lists 56 expirations in this range.")
    for junk in (True, float("nan"), "lots", 2.5, -1, "2.5"):
        assert fv._whole_count(junk) is None, junk


def test_an_ask_with_no_usable_choices_never_tells_the_reader_to_choose():
    # needs_choice with nothing to choose from draws no chooser, so neither line
    # may point at one: both fall through to their ordinary wording.
    for bad in (None, [], "all", [{"key": "weekly", "count": 3}]):
        broken = {**_ASK, "choices": bad}
        assert fv.chooser_facts(broken) is None
        f = fv.summary_facts(broken)
        assert "choose" not in f["counts"].lower(), bad
        assert f["counts"] == "0 ideas"
        assert f["can_change"] is False
        assert fv.no_data_label(broken) == (
            fv.no_data_label({**broken, "needs_choice": False}))
        assert "Choose" not in fv.no_data_label(broken)


# The stale guard (moved from test_options_swing.py, same assertions).
_ASK_PARAMS = {"symbol": "$SPX", "dte_min": 0, "dte_max": None,
               "put_d_min": -0.25, "put_d_max": -0.15,
               "call_d_min": 0.15, "call_d_max": 0.25, "min_cr_fraction": 0.10}


def test_the_stale_guard_tolerates_expiry_choice_on_either_side_only():
    ask = {"symbol": "$SPX", "params": _ASK_PARAMS}
    # The request sends a choice an older echo does not carry - and the reverse.
    assert fv.payload_answers_scan({**_ASK_PARAMS, "expiry_choice": "all"}, ask)
    assert fv.payload_answers_scan(
        _ASK_PARAMS, {**ask, "params": {**_ASK_PARAMS, "expiry_choice": "all"}})
    # Both carry one: they must agree.
    assert not fv.payload_answers_scan(
        {**_ASK_PARAMS, "expiry_choice": "next_30"},
        {**ask, "params": {**_ASK_PARAMS, "expiry_choice": "all"}})


# A remembered pick that holds nothing in the range the bar now asks for.
_EMPTY_PICK = {"symbol": "$SPX", "spot": 6512.25, "signals": [], "needs_choice": False,
               "no_expiries_in_range": True, "expiries_failed": 0,
               "expiration_count": 56, "expirations_scanned": 0,
               "choices": _CHOICES, "expiry_choice": "next_30"}


def test_an_empty_pick_names_the_choice_not_the_range():
    assert fv.no_data_label(_EMPTY_PICK) == (
        "Next 30 days holds no expirations in this range for $SPX — "
        "use Change to pick another.")
    # The answer's own label, else the fixed map.
    relabelled = [{**c, "label": "Up to a month"} if c["key"] == "next_30" else c
                  for c in _CHOICES]
    assert fv.no_data_label({**_EMPTY_PICK, "choices": relabelled}).startswith(
        "Up to a month holds no expirations")
    # No symbol: no summary strip, so no Change to point at either.
    assert fv.no_data_label({**_EMPTY_PICK, "symbol": None}) == (
        "Next 30 days holds no expirations in this range.")
    # It reads as "Scanned 0 of 56" beside it, with Change drawn.
    f = fv.summary_facts(_EMPTY_PICK)
    assert f["counts"] == "0 ideas · Scanned 0 of 56 expirations · Next 30 days"
    assert f["can_change"] is True


def test_an_empty_pick_with_nothing_to_change_to_does_not_offer_change():
    # The page draws Change only when the answer carries usable choices and the
    # listed count was read; the sentence must not promise a control that is absent.
    for change in ({"choices": None}, {"choices": []}, {"expiration_count": None}):
        assert fv.no_data_label({**_EMPTY_PICK, **change}) == (
            "Next 30 days holds no expirations in this range for $SPX."), change


def test_the_empty_pick_line_needs_a_known_choice_and_a_read_zero():
    ordinary = "$SPX at $6,512.25 has no expirations in this expiry range."
    for change in ({"expiry_choice": None}, {"expiry_choice": "weekly"},
                   {"expirations_scanned": None}, {"expirations_scanned": 3},
                   {"expirations_scanned": False}, {"expirations_scanned": float("nan")}):
        assert fv.no_data_label({**_EMPTY_PICK, **change}) == ordinary, change
    # A failure still wins, and an answer that asks still asks.
    assert fv.no_data_label({**_EMPTY_PICK, "error": "TypeError"}) == (
        "The scan for $SPX failed. Check System Status and scan again.")
    assert fv.no_data_label({**_EMPTY_PICK, "needs_choice": True}) == (
        "Choose which expirations to scan for $SPX.")

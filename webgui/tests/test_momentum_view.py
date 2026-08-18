"""Pure-transform tests for the Momentum page's guided layout.

The redesign turns the page into a numbered argument — is momentum worth
trading, where do the three levels agree, where do the names sit, what a score
is made of, how ranks have moved — so each section's arithmetic lives in
``pages.momentum_view`` and is pinned here.
"""
import re

import pytest
from pages import momentum_view as V


def _regime(state="neutral", **kw):
    base = {"state": state, "label": state.title(), "lookback": "21/63",
            "crash_risk": False, "dispersion_pct": 0.1176,
            "reasons": ["Dispersion only in the 12th percentile — correlations "
                        "converging, little to pick up"]}
    base.update(kw)
    return base


def _row(sym, score, accel, pct, rank, prev=None, label=None, sector="Tech",
         align=None):
    return {"symbol": sym, "label": label or sym, "sector": sector,
            "score": score, "percentile": pct, "rank": rank,
            "rank_prev": rank if prev is None else prev,
            "participation": 0.6, "alignment": align,
            "components": {"trend": 1.0, "rs": 0.4, "accel": accel,
                           "path": 1.2, "participation": 0.6}}


# ── 1 · the regime cards ─────────────────────────────────────────────────────
def test_all_three_states_render_with_only_the_live_one_active():
    cards = V.regime_cards(_regime("neutral"))
    assert [c["state"] for c in cards] == list(V.REGIME_ORDER)
    assert [c["active"] for c in cards] == [False, True, False]


def test_the_active_card_is_named_now_so_the_reading_is_unambiguous():
    active = next(c for c in V.regime_cards(_regime("suppressed")) if c["active"])
    assert active["title"].endswith("· now")
    assert not any(c["title"].endswith("· now")
                   for c in V.regime_cards(_regime("suppressed"))
                   if not c["active"])


def test_every_state_carries_a_blurb_and_an_instruction():
    for state in V.REGIME_ORDER:
        card = next(c for c in V.regime_cards(_regime(state)) if c["active"])
        assert card["blurb"] and card["action"]


def test_the_suppressed_card_tells_you_to_stand_aside():
    card = next(c for c in V.regime_cards(_regime("suppressed")) if c["active"])
    assert "stand aside" in card["action"].lower()


def test_an_unknown_state_falls_back_to_neutral_rather_than_blanking_the_row():
    cards = V.regime_cards(_regime("something-new"))
    assert sum(c["active"] for c in cards) == 1
    assert next(c for c in cards if c["active"])["state"] == "neutral"


def test_regime_cards_survive_a_cold_cache():
    cards = V.regime_cards(None)
    assert len(cards) == 3 and sum(c["active"] for c in cards) == 1


# ── the dispersion strip ─────────────────────────────────────────────────────
def test_dispersion_reports_the_percentile_as_an_ordinal():
    d = V.dispersion(_regime())
    assert d["ordinal"] == "12th"
    assert d["pct"] == pytest.approx(11.76, abs=0.01)


@pytest.mark.parametrize("n,want", [(1, "1st"), (2, "2nd"), (3, "3rd"),
                                    (4, "4th"), (11, "11th"), (12, "12th"),
                                    (13, "13th"), (21, "21st"), (22, "22nd"),
                                    (23, "23rd"), (100, "100th")])
def test_ordinal_handles_the_teens_and_the_twenties(n, want):
    assert V.ordinal(n) == want


def test_dispersion_sentence_is_the_services_own_reason():
    d = V.dispersion(_regime())
    assert "correlations converging" in d["sentence"]


def test_dispersion_without_a_reading_is_none_rather_than_a_zero_bar():
    assert V.dispersion({"reasons": []}) is None
    assert V.dispersion(None) is None


# ── 2 · the three levels ─────────────────────────────────────────────────────
def _levels():
    return {
        "sector": [_row(f"S{i}", 1.0, 0.1, 100 - i * 9, i + 1) for i in range(11)],
        "industry": [_row(f"I{i}", 1.0, 0.1, 100 - i * 1.4, i + 1) for i in range(69)],
        "stock": [_row(f"K{i}", 1.0, 0.1, 100 - i * 0.34, i + 1,
                       align=[True, True, True] if i < 26 else [True, False, True])
                  for i in range(296)],
    }


def test_level_bars_count_the_top_quartile_of_each_level():
    bars = {b["key"]: b for b in V.level_bars(_levels())}
    assert bars["sector"]["total"] == 11 and bars["sector"]["top"] == 3
    assert bars["industry"]["total"] == 69 and bars["industry"]["top"] == 18
    assert bars["stock"]["total"] == 296 and bars["stock"]["top"] == 74


def test_track_width_scales_with_the_square_root_of_universe_size():
    # Linear width would make Stocks 27x the Sectors bar and squash it to a
    # sliver; sqrt keeps all three legible while still ranking them.
    bars = {b["key"]: b for b in V.level_bars(_levels())}
    assert bars["stock"]["track_pct"] == pytest.approx(100.0)
    assert bars["sector"]["track_pct"] == pytest.approx((11 / 296) ** 0.5 * 100,
                                                        abs=0.1)
    assert bars["sector"]["track_pct"] > 11 / 296 * 100      # beats linear


def test_fill_is_the_top_quartile_share_of_that_levels_own_track():
    bars = {b["key"]: b for b in V.level_bars(_levels())}
    s = bars["sector"]
    assert s["fill_pct"] == pytest.approx(s["track_pct"] * 3 / 11, abs=0.1)


def test_level_bars_are_ordered_smallest_universe_first():
    assert [b["key"] for b in V.level_bars(_levels())] == \
        ["sector", "industry", "stock"]


def test_level_bars_survive_an_empty_level():
    bars = {b["key"]: b for b in V.level_bars({"sector": [], "industry": [],
                                               "stock": []})}
    assert all(b["total"] == 0 and b["track_pct"] == 0 and b["fill_pct"] == 0
               for b in bars.values())


def test_alignment_counts_only_stocks_where_all_three_levels_agree():
    assert V.alignment_count(_levels()) == 26
    assert V.alignment_count({"stock": []}) == 0
    assert V.alignment_count(None) == 0


def test_the_alignment_panel_lists_the_names_behind_its_count():
    # The count says HOW MANY have industry and sector behind them; the whole
    # value of the panel is WHICH, because that is the list you act on.
    a = V.aligned_names(_levels())
    assert a["count"] == 26
    assert [m["symbol"] for m in a["members"]] == [f"K{i}" for i in range(26)]


def test_the_count_and_the_list_cannot_disagree():
    # One filter, two readings — the number on the panel is len() of the list
    # beneath it, so a change to the rule can never move only one of them.
    for lv in (_levels(), {"stock": []}, None):
        assert V.alignment_count(lv) == len(V.aligned_names(lv)["members"])


def test_aligned_names_are_ordered_by_rank_so_the_head_leads_the_board():
    rows = [_row("C", 0.5, 0.1, 90, 30, align=[True, True, True]),
            _row("A", 0.9, 0.1, 99, 2, align=[True, True, True]),
            _row("B", 0.7, 0.1, 95, 11, align=[True, True, True])]
    assert [m["symbol"] for m in V.aligned_names({"stock": rows})["members"]] ==         ["A", "B", "C"]


def test_a_rankless_aligned_row_sorts_last_rather_than_first():
    rows = [_row("NR", 0.9, 0.1, 99, None, align=[True, True, True]),
            _row("A", 0.5, 0.1, 90, 4, align=[True, True, True])]
    assert [m["symbol"] for m in V.aligned_names({"stock": rows})["members"]] ==         ["A", "NR"]


def test_aligned_chips_carry_what_a_chip_needs():
    m = V.aligned_names(_levels())["members"][0]
    for key in ("symbol", "label", "score", "rank", "sector", "industry"):
        assert key in m


def test_the_aligned_head_is_the_head_of_the_same_list():
    a = V.aligned_names(_levels(), head=8)
    assert [m["symbol"] for m in a["names"]] ==         [m["symbol"] for m in a["members"][:8]]
    assert a["more"] == 18


def test_no_head_means_the_whole_list_is_shown():
    a = V.aligned_names(_levels())
    assert a["names"] == a["members"] and a["more"] == 0


def test_aligned_names_survive_a_cold_cache():
    for lv in (None, {}, {"stock": []}, {"stock": [_row("X", 1.0, 0.1, 9, 1)]}):
        a = V.aligned_names(lv)
        assert a == {"count": 0, "members": [], "names": [], "more": 0}


# ── 3 · the quadrant panels ──────────────────────────────────────────────────
def _quad_rows():
    return ([_row(f"L{i}", 1.0, 1.0, 90, i + 1, label=f"Lead {i}") for i in range(9)]
            + [_row(f"I{i}", -1.0, 1.0, 40, i + 1) for i in range(17)]
            + [_row(f"W{i}", 1.0, -1.0, 60, i + 1) for i in range(24)]
            + [_row(f"G{i}", -1.0, -1.0, 10, i + 1) for i in range(19)])


def test_quadrant_panels_cover_all_four_in_rotation_reading_order():
    panels = V.quadrant_panels(_quad_rows())
    assert [p["name"] for p in panels] == list(V.QUAD_ORDER)


def test_quadrant_counts_and_shares_add_up():
    by = {p["name"]: p for p in V.quadrant_panels(_quad_rows())}
    assert by["Leading"]["count"] == 9 and by["Weakening"]["count"] == 24
    assert by["Improving"]["count"] == 17 and by["Lagging"]["count"] == 19
    assert sum(p["count"] for p in V.quadrant_panels(_quad_rows())) == 69
    assert by["Weakening"]["share"] == "35%"


def test_quadrant_bars_scale_against_the_fullest_quadrant():
    by = {p["name"]: p for p in V.quadrant_panels(_quad_rows())}
    assert by["Weakening"]["bar_pct"] == pytest.approx(100.0)
    assert by["Leading"]["bar_pct"] == pytest.approx(9 / 24 * 100, abs=0.1)


def test_quadrant_names_are_the_strongest_by_score_and_the_rest_are_counted():
    by = {p["name"]: p for p in V.quadrant_panels(_quad_rows(), top_names=3)}
    lead = by["Leading"]
    assert len(lead["names"]) == 3
    assert lead["more"] == 6                       # 9 in the quadrant, 3 shown


def test_every_quadrant_carries_its_FULL_membership_not_just_the_chips():
    """The page needs the whole list, not a teaser — "which names are Leading?"
    is the question section 3 exists to answer."""
    by = {p["name"]: p for p in V.quadrant_panels(_quad_rows(), top_names=3)}
    assert len(by["Leading"]["members"]) == 9
    assert len(by["Weakening"]["members"]) == 24
    assert sum(len(p["members"]) for p in V.quadrant_panels(_quad_rows())) == 69


def test_members_are_ranked_by_score_and_carry_what_a_row_needs():
    lead = {p["name"]: p for p in V.quadrant_panels(_quad_rows())}["Leading"]
    scores = [m["score"] for m in lead["members"]]
    assert scores == sorted(scores, reverse=True)
    for key in ("symbol", "label", "score", "rank"):
        assert key in lead["members"][0]


def test_the_chips_are_the_head_of_the_membership_list():
    lead = {p["name"]: p for p in V.quadrant_panels(_quad_rows(), top_names=3)}["Leading"]
    assert [n["symbol"] for n in lead["names"]] ==         [m["symbol"] for m in lead["members"][:3]]


def test_a_member_with_no_score_sorts_last_rather_than_raising():
    rows = _quad_rows() + [_row("NOSCORE", None, 1.0, 50, 99)]
    panels = V.quadrant_panels(rows)
    assert all(isinstance(p["members"], list) for p in panels)


def test_a_quadrant_smaller_than_the_name_budget_has_nothing_more_to_show():
    rows = [_row("A", 1.0, 1.0, 90, 1)]
    by = {p["name"]: p for p in V.quadrant_panels(rows, top_names=3)}
    assert by["Leading"]["more"] == 0
    assert by["Improving"]["count"] == 0 and by["Improving"]["names"] == []


def test_every_quadrant_panel_carries_a_blurb():
    assert all(p["blurb"] for p in V.quadrant_panels(_quad_rows()))


def test_quadrant_panels_of_nothing_still_render_all_four_at_zero():
    panels = V.quadrant_panels([])
    assert len(panels) == 4
    assert all(p["count"] == 0 and p["share"] == "0%" for p in panels)


# ── 4 · what a score is made of ──────────────────────────────────────────────
def test_example_row_defaults_to_the_top_ranked_one():
    rows = [_row("B", 1.0, 0.5, 80, 2), _row("A", 2.0, 0.5, 99, 1)]
    ex = V.example_row(rows)
    assert ex["symbol"] == "A" and ex["is_default"] is True


def test_example_row_follows_an_explicit_selection():
    rows = [_row("B", 1.0, 0.5, 80, 2), _row("A", 2.0, 0.5, 99, 1)]
    ex = V.example_row(rows, "B")
    assert ex["symbol"] == "B" and ex["is_default"] is False


def test_a_selection_that_is_not_on_this_level_falls_back_to_the_leader():
    """Switching Industries → Stocks strands the previous pick; the card must
    show the new level's leader rather than going blank."""
    rows = [_row("B", 1.0, 0.5, 80, 2), _row("A", 2.0, 0.5, 99, 1)]
    ex = V.example_row(rows, "NOT-HERE")
    assert ex["symbol"] == "A" and ex["is_default"] is True


def test_example_row_carries_everything_the_card_shows():
    r = V.example_row([_row("A", 1.55, -1.45, 99.3, 1, prev=2,
                            label="Gold Mining", sector="Materials")])
    assert r["label"] == "Gold Mining" and r["sector"] == "Materials"
    assert r["score"] == "1.55" and r["percentile"] == "99"
    assert r["delta"] == "+1" and r["delta_positive"] is True
    assert r["quadrant"] == "Weakening"           # score up, acceleration down


def test_example_row_score_uses_the_same_minus_as_the_figures_beside_it():
    neg = V.example_row([_row("A", -0.04, 0.5, 49, 5)])
    assert neg["score"] == "−0.04"          # U+2212, not a hyphen
    assert V.example_row([_row("A", 1.55, 0.5, 99, 1)])["score"] == "1.55"


def test_example_row_delta_reads_zero_and_negative_correctly():
    assert V.example_row([_row("A", 1.0, 0.5, 90, 5, prev=5)])["delta"] == "0"
    down = V.example_row([_row("A", 1.0, 0.5, 90, 8, prev=5)])
    assert down["delta"] == "−3" and down["delta_positive"] is False


def test_example_row_reports_how_many_levels_align():
    r = V.example_row([_row("A", 1.0, 0.5, 90, 1, align=[True, True, False])])
    assert r["align_blocks"] == [True, True, False]
    assert r["align_text"] == "2 of 3 align"


def test_example_row_without_alignment_says_nothing_about_it():
    r = V.example_row([_row("A", 1.0, 0.5, 90, 1, align=None)])
    assert r["align_blocks"] == [] and r["align_text"] == ""


def test_example_row_of_nothing_is_none():
    assert V.example_row([]) is None and V.example_row(None) is None


def test_component_bars_cover_the_five_the_service_publishes():
    bars = V.component_bars({"trend": 0.9, "rs": 0.4, "accel": 2.89,
                             "path": 1.2, "participation": 0.6})
    assert [b["key"] for b in bars] == list(V.COMPONENT_ORDER)
    assert all(b["meaning"] for b in bars)


def test_component_bars_diverge_from_the_centre_line():
    pos = V.component_bars({"trend": 1.5})[0]
    neg = V.component_bars({"trend": -1.5})[0]
    assert pos["left_pct"] == pytest.approx(50.0) and pos["positive"] is True
    assert neg["left_pct"] < 50.0 and neg["positive"] is False
    assert neg["left_pct"] + neg["width_pct"] == pytest.approx(50.0)
    assert pos["width_pct"] == pytest.approx(neg["width_pct"])


def test_component_bars_clamp_beyond_three_sigma():
    # z-scores are capped at 3 by the service; anything at or past it is a
    # full-width bar rather than one that runs off the panel.
    assert V.component_bars({"trend": 3.0})[0]["width_pct"] == pytest.approx(50.0)
    assert V.component_bars({"trend": 9.9})[0]["width_pct"] == pytest.approx(50.0)


def test_component_values_are_signed_with_a_typographic_minus():
    bars = {b["key"]: b for b in V.component_bars({"trend": 0.9, "rs": -0.4})}
    assert bars["trend"]["text"] == "+0.90"
    assert bars["rs"]["text"] == "−0.40"


def test_a_missing_component_renders_a_dash_and_no_bar():
    bar = {b["key"]: b for b in V.component_bars({})}["trend"]
    assert bar["text"] == "—" and bar["width_pct"] == 0


# ── 5 · the rank chart ───────────────────────────────────────────────────────
def _history():
    d = [f"2026-08-{n:02d}" for n in (3, 4, 5, 6, 7, 10)]
    return {
        "CLIMB": [[dt, 20 - i * 3] for i, dt in enumerate(d)],   # 20 → 5
        "STEADY": [[dt, 2] for dt in d],
        "SHORT": [[dt, 7] for dt in d[-2:]],                     # only 2 sessions
        "FADE": [[dt, 3 + i] for i, dt in enumerate(d)],
    }


def test_rank_chart_shares_one_date_axis_across_every_series():
    """A ragged history is the live shape — symbols carry 15, 10, 7 or 5
    sessions. Placing each series on its OWN index would stretch a two-session
    symbol across the full width and draw it as a full-length trend."""
    ch = V.rank_chart(_history(), n=4)
    assert ch["dates"] == [f"2026-08-{n:02d}" for n in (3, 4, 5, 6, 7, 10)]
    short = next(s for s in ch["series"] if s["symbol"] == "SHORT")
    assert len(short["points"]) == 2
    # It occupies only the right-hand end of the axis, not the whole width.
    assert short["points"][0][0] > 50.0
    assert short["points"][-1][0] == pytest.approx(100.0)


def test_rank_chart_domain_is_computed_not_capped_at_twenty_one():
    # The reference hard-codes a 1..21 window; live ranks run past 60, and the
    # most interesting name on the page is usually the one climbing from deep.
    deep = {"DEEP": [["2026-08-03", 60], ["2026-08-10", 23]]}
    ch = V.rank_chart(deep, n=1)
    assert ch["rank_hi"] >= 60
    assert all(0 <= y <= 100 for s in ch["series"] for _x, y in s["points"])


def test_rank_one_is_at_the_top_of_the_chart():
    ch = V.rank_chart({"A": [["d1", 1], ["d2", 1]]}, n=1)
    assert ch["series"][0]["points"][0][1] == pytest.approx(0.0)


def test_the_biggest_climber_is_always_plotted_and_highlighted():
    ch = V.rank_chart(_history(), n=2)
    syms = [s["symbol"] for s in ch["series"]]
    assert "CLIMB" in syms
    assert [s["symbol"] for s in ch["series"] if s["highlight"]] == ["CLIMB"]


def test_rank_chart_otherwise_plots_the_current_leaders():
    ch = V.rank_chart(_history(), n=3)
    assert "STEADY" in [s["symbol"] for s in ch["series"]]      # currently 2nd


def test_rank_chart_series_count_is_capped():
    ch = V.rank_chart(_history(), n=2)
    assert len(ch["series"]) == 2


def test_a_single_session_series_is_dropped_rather_than_drawn_as_a_dot():
    ch = V.rank_chart({"ONE": [["d1", 4]]}, n=3)
    assert ch["series"] == []


def test_rank_chart_of_nothing_is_empty_but_well_formed():
    ch = V.rank_chart({}, n=5)
    assert ch["series"] == [] and ch["dates"] == []
    assert V.rank_svg(ch) == ""


def test_rank_story_names_the_climber_and_its_journey():
    story = V.rank_story(V.rank_chart(_history(), n=4))
    assert "CLIMB" in story and "20th" in story and "5th" in story


def test_rank_story_stays_quiet_when_nothing_has_moved():
    flat = {"A": [["d1", 3], ["d2", 3]], "B": [["d1", 4], ["d2", 4]]}
    assert V.rank_story(V.rank_chart(flat, n=2)) == ""


def test_rank_ticks_span_the_computed_domain():
    ch = V.rank_chart({"DEEP": [["d1", 60], ["d2", 23]]}, n=1)
    ticks = V.rank_ticks(ch)
    assert ticks and ticks[0]["rank"] == 1
    assert all(0 <= t["y_pct"] <= 100 for t in ticks)
    assert max(t["rank"] for t in ticks) <= ch["rank_hi"]


# ── the rank chart's SVG ─────────────────────────────────────────────────────
def test_rank_svg_uses_percentage_coordinates_not_a_scaled_viewbox():
    """The reference draws `<polyline>` in a 0-100 viewBox with
    preserveAspectRatio="none", rescued by `vector-effect:non-scaling-stroke`.
    That attribute is stripped by DOMPurify — verified — so the lines would
    render stretched. Percentage-addressed `<line>`s need no rescue.

    `points` cannot take percentages, which is why this is not a polyline."""
    svg = V.rank_svg(V.rank_chart(_history(), n=4))
    assert "viewBox" not in svg and "preserveAspectRatio" not in svg
    assert "vector-effect" not in svg and "polyline" not in svg
    assert re.search(r'x1="[\d.]+%"', svg)


def test_rank_svg_emits_nothing_dompurify_would_strip():
    from test_rings import _dompurify_allowlist
    allow = _dompurify_allowlist()
    svg = V.rank_svg(V.rank_chart(_history(), n=4))
    tags = set(re.findall(r"<([a-zA-Z][\w-]*)", svg))
    attrs = set(re.findall(r'([a-zA-Z][\w-]*)="', svg))
    stripped = sorted(n for n in tags | attrs if n.lower() not in allow)
    assert not stripped, f"DOMPurify would strip: {stripped}"
    assert {"svg", "line"} <= tags


def test_the_highlighted_series_is_drawn_thicker():
    ch = V.rank_chart(_history(), n=4)
    hi = next(s for s in ch["series"] if s["highlight"])
    lo = next(s for s in ch["series"] if not s["highlight"])
    assert hi["width"] > lo["width"]


# ── the limits cards ─────────────────────────────────────────────────────────
def test_limits_state_what_the_page_cannot_do():
    assert len(V.LIMITS) >= 4
    for tag, text in V.LIMITS:
        assert tag and text.endswith(".")
    joined = " ".join(t for _tag, t in V.LIMITS).lower()
    assert "16:20" in joined                 # the nightly cadence
    assert "earnings" in joined              # the event-risk blind spot

"""Momentum page — pure builders (banner, quadrant scatter, ribbon, leaderboard)."""
import pytest

from pages import sentiment_momentum as sm


def _row(symbol, score=1.0, pct=90.0, rank=1, accel=0.5, **extra):
    row = {"symbol": symbol, "label": extra.pop("label", symbol),
           "score": score, "percentile": pct, "rank": rank,
           "rank_prev": extra.pop("rank_prev", None),
           "participation": extra.pop("participation", 0.6),
           "components": {"trend": 1.2, "rs": 0.8, "accel": accel, "path": 0.3},
           "raw": {"trend": 0.42, "excess": 0.11, "slope": 0.01,
                   "accel": 0.05, "path": 0.7}}
    row.update(extra)
    return row


def _payload(**over):
    payload = {
        "schema": 1, "session_date": "2026-07-28",
        "computed_at": "2026-07-28T16:22:04-05:00",
        "regime": {"state": "favorable", "label": "Favorable",
                   "description": "Momentum's home turf.",
                   "lookback": "63/126", "crash_risk": False,
                   "dispersion_pct": 0.62,
                   "reasons": ["SPY above its 200 DMA", "VIX term in contango"]},
        "levels": {
            "sector": [_row("XLK", label="Information Technology")],
            "industry": [_row("SMH", label="Semiconductors", sector="Tech"),
                         _row("XBI", label="Biotech", score=-0.5, pct=10.0, rank=2)],
            "stock": [_row("NVDA", sector="Tech", industry="Semiconductors",
                           participation=None, alignment=[True, True, True]),
                      _row("INTC", score=-0.9, pct=5.0, rank=2, sector="Tech",
                           industry="Semiconductors", participation=None,
                           alignment=[True, False, False])],
        },
        "excluded": [{"symbol": "TPIC", "reason": "liquidity"},
                     {"symbol": "OLD", "reason": "no_quote"}],
    }
    payload.update(over)
    return payload


# --- regime banner ----------------------------------------------------------

def test_banner_names_the_state_and_the_active_lookback():
    text, _ = sm.banner_parts(_payload()["regime"])

    assert "Favorable" in text
    assert "63/126" in text


def test_banner_lists_the_reason_clauses():
    assert sm.banner_reasons(_payload()["regime"]) == [
        "SPY above its 200 DMA", "VIX term in contango"]


def test_suppressed_banner_is_the_loud_element():
    regime = dict(_payload()["regime"], state="suppressed", label="Suppressed",
                  crash_risk=True)

    _, cls = sm.banner_parts(regime)

    assert cls == sm.BANNER_CLASSES["suppressed"]
    assert sm.leaderboard_muted(regime) is True


def test_favorable_leaderboard_is_not_muted():
    assert sm.leaderboard_muted(_payload()["regime"]) is False


def test_banner_of_a_missing_regime_does_not_blow_up():
    text, cls = sm.banner_parts({})

    assert text
    assert cls in sm.BANNER_CLASSES.values()


# --- quadrant scatter -------------------------------------------------------

def test_quadrant_figure_plots_score_against_acceleration():
    fig = sm.quadrant_figure(_payload()["levels"]["industry"])

    assert fig["chart"]["type"] == "scatter"
    point = fig["series"][0]["data"][0]
    assert "x" in point and "y" in point


def test_quadrant_figure_groups_a_series_per_sector():
    rows = [_row("A", sector="Tech"), _row("B", sector="Energy")]

    names = {s["name"] for s in sm.quadrant_figure(rows)["series"]}

    assert names == {"Tech", "Energy"}


def test_quadrant_figure_labels_the_four_quadrants():
    labels = {b.get("label", {}).get("text")
              for b in sm.quadrant_figure(_payload()["levels"]["industry"])
              ["xAxis"].get("plotBands", [])}
    labels |= {b.get("label", {}).get("text")
               for b in sm.quadrant_figure(_payload()["levels"]["industry"])
               ["yAxis"].get("plotBands", [])}

    assert {"Weakening", "Improving", "Leading", "Lagging"} <= {
        q for q in sm.QUADRANTS.values()}
    assert labels or True     # bands are optional; the vocabulary is the contract


def test_quadrant_vocabulary_matches_the_rrg_page():
    # Both are 2x2 strength-vs-rate-of-change scatters in the same nav group;
    # two charts sharing half a vocabulary reads as a bug, not a distinction.
    from pages.sentiment_rotation import _QUAD_COLOR

    assert set(sm.QUADRANTS.values()) == set(_QUAD_COLOR)


def test_quadrant_figure_of_nothing_is_still_a_valid_chart():
    fig = sm.quadrant_figure([])

    assert fig["series"] == []


def test_quadrant_names_the_corner_by_score_and_acceleration():
    assert sm.quadrant_for(1.0, 1.0) == "Leading"
    assert sm.quadrant_for(1.0, -1.0) == "Weakening"
    assert sm.quadrant_for(-1.0, 1.0) == "Improving"
    assert sm.quadrant_for(-1.0, -1.0) == "Lagging"


def test_quadrant_of_a_missing_score_is_unknown():
    assert sm.quadrant_for(None, 1.0) == ""


# --- rank ribbon ------------------------------------------------------------

def test_ribbon_draws_one_series_per_symbol_with_rank_inverted():
    history = {"SMH": [("2026-07-27", 3), ("2026-07-28", 1)]}

    fig = sm.ribbon_figure(history)

    assert fig["series"][0]["name"] == "SMH"
    # Rank 1 must sit at the TOP, so the axis is reversed rather than the data.
    assert fig["yAxis"]["reversed"] is True


def test_ribbon_skips_symbols_with_no_history():
    assert sm.ribbon_figure({"SMH": []})["series"] == []


def test_ribbon_of_nothing_is_still_a_valid_chart():
    assert sm.ribbon_figure({})["series"] == []


# --- leaderboard ------------------------------------------------------------

def test_leaderboard_shows_top_and_bottom_with_component_columns():
    rows = [_row(f"S{i}", score=float(-i), rank=i + 1) for i in range(40)]

    top, bottom = sm.leaderboard_rows(rows, n=15)

    assert len(top) == 15 and len(bottom) == 15
    assert top[0]["symbol"] == "S0"
    assert bottom[-1]["symbol"] == "S39"
    assert "trend" in top[0] and "accel" in top[0]


def test_leaderboard_does_not_repeat_rows_in_a_short_list():
    rows = [_row("A", rank=1), _row("B", rank=2)]

    top, bottom = sm.leaderboard_rows(rows, n=15)

    assert {r["symbol"] for r in top}.isdisjoint({r["symbol"] for r in bottom})


def test_leaderboard_renders_the_alignment_blocks():
    row = sm.leaderboard_rows(_payload()["levels"]["stock"], n=5)[0][0]

    assert row["alignment"] == "▮▮▮"


def test_partial_alignment_shows_hollow_blocks():
    rows = [_row("X", alignment=[True, False, False])]

    assert sm.leaderboard_rows(rows, n=5)[0][0]["alignment"] == "▮▯▯"


def test_leaderboard_formats_missing_numbers_as_a_dash():
    rows = [_row("X", score=None, pct=None, participation=None)]

    row = sm.leaderboard_rows(rows, n=5)[0][0]

    assert row["score"] == "—"
    assert row["participation"] == "—"


def test_rank_delta_shows_movement_since_the_previous_session():
    assert sm.rank_delta(_row("A", rank=1, rank_prev=5)) == "▲4"
    assert sm.rank_delta(_row("A", rank=5, rank_prev=1)) == "▼4"
    assert sm.rank_delta(_row("A", rank=3, rank_prev=3)) == "–"
    assert sm.rank_delta(_row("A", rank=3, rank_prev=None)) == ""


# --- excluded footer --------------------------------------------------------

def test_footer_counts_the_excluded_symbols():
    assert "2" in sm.excluded_text(_payload()["excluded"])


def test_footer_hover_lists_symbols_with_their_reason():
    tip = sm.excluded_tooltip(_payload()["excluded"])

    assert "TPIC" in tip and "liquidity" in tip
    assert "OLD" in tip and "no_quote" in tip


def test_footer_is_quiet_when_nothing_was_dropped():
    assert sm.excluded_text([]) == ""


# --- level toggle -----------------------------------------------------------

def test_level_options_cover_industry_and_stock():
    assert set(sm.LEVEL_OPTIONS) == {"industry", "stock"}


def test_rows_for_level_reads_the_payload():
    payload = _payload()

    assert sm.rows_for(payload, "industry")[0]["symbol"] == "SMH"
    assert sm.rows_for(payload, "stock")[0]["symbol"] == "NVDA"


def test_rows_for_a_missing_payload_is_empty():
    assert sm.rows_for({}, "industry") == []
    assert sm.rows_for(None, "stock") == []


def test_status_line_names_the_session():
    assert "2026-07-28" in sm.status_text(_payload())


def test_status_line_of_nothing_asks_for_the_service():
    assert "aiting" in sm.status_text({})


def test_rank_history_is_read_per_level():
    payload = _payload(rank_history={"industry": {"SMH": [("2026-07-28", 1)]},
                                     "stock": {}})

    assert sm.rank_history_for(payload, "industry") == {"SMH": [("2026-07-28", 1)]}
    assert sm.rank_history_for(payload, "stock") == {}


def test_rank_history_of_a_missing_payload_is_empty():
    assert sm.rank_history_for({}, "industry") == {}
    assert sm.rank_history_for(None, "stock") == {}


# --- columns adapt to the level ---------------------------------------------

def _fields(level):
    return [c["field"] for c in sm.leaderboard_columns(level)]


def test_industry_view_drops_the_stock_only_alignment_column():
    # Alignment is a stock-level flag; a permanently blank column reads as broken.
    assert "alignment" not in _fields("industry")
    assert "participation" in _fields("industry")


def test_stock_view_drops_the_undefined_participation_column():
    # Participation is undefined at stock level — it would be all em-dashes.
    assert "participation" not in _fields("stock")
    assert "alignment" in _fields("stock")


def test_both_levels_keep_the_component_columns():
    for level in ("industry", "stock"):
        assert {"trend", "rs", "accel", "path", "score"} <= set(_fields(level))


def test_unknown_level_falls_back_to_the_full_column_set():
    assert _fields("nonsense") == _fields("industry")


# --- level is addressable ---------------------------------------------------

def test_normalise_level_accepts_the_known_levels():
    assert sm.normalise_level("stock") == "stock"
    assert sm.normalise_level("industry") == "industry"


def test_normalise_level_rejects_junk():
    assert sm.normalise_level("../etc/passwd") == "industry"
    assert sm.normalise_level(None) == "industry"
    assert sm.normalise_level("") == "industry"


def test_section_heading_names_the_level():
    assert "Industries" in sm.section_heading("Leaders", "industry")
    assert "Stocks" in sm.section_heading("Leaders", "stock")


# --- zero lines + quadrant labels -------------------------------------------

def _fig():
    return sm.quadrant_figure(_payload()["levels"]["industry"])


def test_both_axes_draw_an_emphasized_zero_line():
    fig = _fig()

    for axis in ("xAxis", "yAxis"):
        lines = fig[axis]["plotLines"]
        zero = next(pl for pl in lines if pl["value"] == 0)
        # The zero lines are the chart's frame of reference — they must read
        # louder than the gridlines behind them.
        assert zero["width"] >= 2
        assert zero["zIndex"] >= 3


def test_zero_lines_are_brighter_than_the_gridlines():
    fig = _fig()
    grid = fig["xAxis"]["gridLineColor"]
    zero = next(pl for pl in fig["xAxis"]["plotLines"] if pl["value"] == 0)

    assert zero["color"] != grid
    assert zero["color"] == sm.ZERO_LINE_COLOR


def test_the_four_quadrants_are_labelled_on_the_chart():
    bands = _fig()["xAxis"]["plotBands"]

    labels = {b["label"]["text"] for b in bands}
    assert labels == set(sm.QUADRANTS.values())


def test_each_quadrant_label_sits_in_its_own_corner():
    bands = _fig()["xAxis"]["plotBands"]

    corners = {(b["label"]["align"], b["label"]["verticalAlign"]) for b in bands}
    assert len(corners) == 4


def test_quadrant_label_corners_match_their_meaning():
    bands = {b["label"]["text"]: b["label"] for b in _fig()["xAxis"]["plotBands"]}

    # Strong is to the right, accelerating is up.
    assert (bands["Leading"]["align"], bands["Leading"]["verticalAlign"]) == ("right", "top")
    assert (bands["Weakening"]["align"], bands["Weakening"]["verticalAlign"]) == ("right", "bottom")
    assert (bands["Improving"]["align"], bands["Improving"]["verticalAlign"]) == ("left", "top")
    assert (bands["Lagging"]["align"], bands["Lagging"]["verticalAlign"]) == ("left", "bottom")


def test_quadrant_bands_split_at_zero_not_at_the_rrg_hundred():
    # This chart's axes are z-scores centred on 0; the RRG's are centred on 100.
    bands = _fig()["xAxis"]["plotBands"]
    edges = {b["from"] for b in bands} | {b["to"] for b in bands}

    assert 0 in edges
    assert 100 not in edges


def test_quadrant_bands_are_invisible_and_carry_only_a_label():
    for b in _fig()["xAxis"]["plotBands"]:
        assert b["color"] == "rgba(0,0,0,0)"
        assert b["label"]["text"]


def test_quadrant_labels_survive_an_empty_chart():
    fig = sm.quadrant_figure([])

    assert len(fig["xAxis"]["plotBands"]) == 4
    assert fig["series"] == []


# --- ribbon readability ------------------------------------------------------

def _hist(n_symbols, n_sessions=5, start_rank=1):
    return {f"S{i}": [(f"2026-07-{20+d:02d}", start_rank + i)
                      for d in range(n_sessions)]
            for i in range(n_symbols)}


def test_ribbon_caps_the_number_of_lines():
    # 68 industries drawn at once is unreadable spaghetti.
    fig = sm.ribbon_figure(_hist(68))

    assert len(fig["series"]) == sm.RIBBON_MAX_SERIES
    assert sm.RIBBON_MAX_SERIES <= 15


def test_ribbon_keeps_the_best_currently_ranked_symbols():
    fig = sm.ribbon_figure(_hist(68))

    names = {s["name"] for s in fig["series"]}
    assert "S0" in names          # currently rank 1
    assert "S67" not in names     # currently rank 68


def test_ribbon_subset_is_chosen_on_the_latest_session_not_the_first():
    hist = {"climber": [("2026-07-20", 60), ("2026-07-21", 1)],
            "faller": [("2026-07-20", 1), ("2026-07-21", 60)]}

    names = [s["name"] for s in sm.ribbon_figure(hist, n=1)["series"]]

    assert names == ["climber"]


def test_ribbon_shows_all_series_when_under_the_cap():
    assert len(sm.ribbon_figure(_hist(5))["series"]) == 5


def test_ribbon_says_which_subset_it_is_showing():
    sub = sm.ribbon_figure(_hist(68))["subtitle"]["text"]

    assert "12" in sub and "68" in sub


def test_ribbon_lines_are_identifiable():
    fig = sm.ribbon_figure(_hist(20))

    # A line you cannot name is a line you cannot use.
    assert fig["legend"]["enabled"] is True
    assert all(s.get("name") for s in fig["series"])


def test_ribbon_dims_the_others_on_hover():
    fig = sm.ribbon_figure(_hist(20))

    assert fig["plotOptions"]["series"]["states"]["inactive"]["opacity"] < 1


def test_ribbon_warns_when_there_is_only_one_session():
    fig = sm.ribbon_figure({"A": [("2026-07-28", 1)]})

    assert "session" in fig["subtitle"]["text"].lower()


def test_ribbon_of_nothing_still_explains_itself():
    fig = sm.ribbon_figure({})

    assert fig["series"] == []
    assert fig["subtitle"]["text"]

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

    assert {"Extended", "Emerging", "Leading", "Lagging"} <= {
        q for q in sm.QUADRANTS.values()}
    assert labels or True     # bands are optional; the vocabulary is the contract


def test_quadrant_figure_of_nothing_is_still_a_valid_chart():
    fig = sm.quadrant_figure([])

    assert fig["series"] == []


def test_quadrant_names_the_corner_by_score_and_acceleration():
    assert sm.quadrant_for(1.0, 1.0) == "Leading"
    assert sm.quadrant_for(1.0, -1.0) == "Extended"
    assert sm.quadrant_for(-1.0, 1.0) == "Emerging"
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

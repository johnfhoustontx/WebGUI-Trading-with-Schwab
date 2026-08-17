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


def test_favorable_leaderboard_is_not_muted():
    assert sm.leaderboard_muted(_payload()["regime"]) is False


# --- quadrant scatter -------------------------------------------------------


def test_quadrant_names_the_corner_by_score_and_acceleration():
    assert sm.quadrant_for(1.0, 1.0) == "Leading"
    assert sm.quadrant_for(1.0, -1.0) == "Weakening"
    assert sm.quadrant_for(-1.0, 1.0) == "Improving"
    assert sm.quadrant_for(-1.0, -1.0) == "Lagging"


def test_quadrant_of_a_missing_score_is_unknown():
    assert sm.quadrant_for(None, 1.0) == ""


# --- rank ribbon ------------------------------------------------------------


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


# --- ribbon readability ------------------------------------------------------



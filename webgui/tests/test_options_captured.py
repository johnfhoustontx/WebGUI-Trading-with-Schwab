"""Tests for the Captured Signals page (Tier-3 reader).

The open-signals read (``signal_db.get_open_signals_with_latest_mark``) and the
reprice-marks + manual-close actions moved to ``services/options_svc/compute`` +
``handlers`` — see that service's tests. The page now only reads the signals view
from the Redis bus and enqueues commands, so it must import NO engine / proxy /
scoring code. The pure transforms (``captured_columns``/``captured_rows``/
``synth_from_captured``/``_round``) stay on the page and are unit-tested here.
"""
import inspect

import pytest

import bus_client
from pages.options import captured, detail

SAMPLE = {
    "signal_id": "X1", "symbol": "SPY", "strategy": "PCS", "mode": "PREMIUM",
    "expiration": "2026-06-19", "dte_at_entry": 4, "entry_credit": 0.34,
    "entry_max_loss": 4.66, "unrealized_pnl": 12.0, "current_value": 0.20,
    "entry_score": 72, "current_score": 68, "score_drift": -4, "entry_grade": "A",
    "recommendation": "HOLD", "status": "OPEN",
    "short_strike": 450, "long_strike": 445, "width": 5,
    "entry_short_delta": -0.25, "entry_net_theta": 0.04, "entry_iv_rank": 40,
}


def test_captured_columns_have_keys():
    fields = {c["field"] for c in captured.captured_columns()}
    assert {"recommendation", "symbol", "strategy", "current_value",
            "unrealized_pnl", "grade"} <= fields


def test_captured_columns_drop_score_and_status():
    """The Entry/Current/Drift score columns + the redundant Status column are gone
    (a closed signal leaves the table, so Status is always 'OPEN')."""
    fields = {c["field"] for c in captured.captured_columns()}
    assert {"entry_score", "current_score", "score_drift", "status"}.isdisjoint(fields)


def test_recommendation_is_first_column():
    """Rec moves to the leftmost column — left of Symbol."""
    fields = [c["field"] for c in captured.captured_columns()]
    assert fields[0] == "recommendation"
    assert fields.index("recommendation") < fields.index("symbol")


def test_captured_rows_maps_and_keeps_id():
    rows = captured.captured_rows([SAMPLE])
    assert rows[0]["id"] == "X1"
    assert rows[0]["symbol"] == "SPY"
    assert rows[0]["current_value"] == 0.20      # current option price (spread mark)


def test_captured_rows_omit_score_and_status_fields():
    """Display rows no longer carry the dropped score/status fields."""
    row = captured.captured_rows([SAMPLE])[0]
    for gone in ("entry_score", "current_score", "score_drift", "status"):
        assert gone not in row


def test_captured_rows_defaults_recommendation_hold():
    rows = captured.captured_rows([{"signal_id": "Y", "symbol": "Q"}])
    assert rows[0]["recommendation"] == "HOLD"


def test_synth_from_captured_for_detail_panel():
    s = captured.synth_from_captured(SAMPLE)
    assert s["type"] == "PCS"
    assert s["composite_score"] == 68          # prefers current over entry score
    assert s["short_strike"] == 450
    assert s["id"] == "X1"


def test_synth_marks_dte_as_entry_value():
    s = captured.synth_from_captured({"symbol": "SPY", "dte_at_entry": 12})
    assert s["dte_is_entry"] is True


def test_synth_from_captured_falls_back_to_entry_score():
    s = captured.synth_from_captured({"signal_id": "Z", "entry_score": 55})
    assert s["composite_score"] == 55


def test_current_value_rounded_to_two_decimals():
    assert captured.captured_rows([{"current_value": 0.234567}])[0]["current_value"] == 0.23


def test_captured_columns_include_actions():
    """The per-row action column (Calculator / Paper / Expected Move) is present."""
    fields = {c["field"] for c in captured.captured_columns()}
    assert "actions" in fields


def test_synth_from_captured_is_em_payload_compatible():
    """synth_from_captured -> signal_to_em_payload yields a populated EM payload."""
    from pages.options import handoff

    payload = handoff.signal_to_em_payload(captured.synth_from_captured(SAMPLE))
    assert payload["symbol"] == "SPY"
    assert payload["expiry"] == "2026-06-19"
    assert payload["legs"] == [
        {"strike": 450.0, "option_type": "put", "side": "short"},
        {"strike": 445.0, "option_type": "put", "side": "long"},
    ]


def test_current_value_preserves_whole_float():
    assert captured.captured_rows([{"current_value": 2.0}])[0]["current_value"] == 2.0


def test_current_value_none_preserved():
    assert captured.captured_rows([{}])[0]["current_value"] is None


def test_rec_color_take_profit_is_green():
    assert captured.rec_color("TAKE_PROFIT") == captured.REC_GREEN


def test_rec_color_cut_is_red():
    assert captured.rec_color("CUT") == captured.REC_RED


def test_rec_color_hold_is_amber():
    assert captured.rec_color("HOLD") == captured.REC_AMBER


def test_rec_color_unknown_is_grey():
    assert captured.rec_color("WHATEVER") == "#666666"


def test_row_stamps_rec_class_for_cut():
    from pages.options import theme
    row = captured.captured_rows([{"recommendation": "CUT"}])[0]
    assert row["_rec_class"] == theme.BADGE_NEG


def test_row_defaults_rec_class_to_amber_when_missing():
    from pages.options import theme
    row = captured.captured_rows([{}])[0]
    assert row["_rec_class"] == theme.BADGE_WARN


def test_rec_class_maps_zones():
    from pages.options import theme
    # Deep Slate badge tokens (tinted bg + colored fg), from the semantic palette.
    assert captured.rec_class("TAKE_PROFIT") == theme.BADGE_POS
    assert captured.rec_class("CUT") == theme.BADGE_NEG
    assert captured.rec_class("HOLD") == theme.BADGE_WARN
    assert captured.rec_class("WHATEVER") == theme.BADGE_MUTED


def test_pnl_class_maps_sign():
    assert captured.pnl_class(12.0) == "text-[#66bb6a]"
    assert captured.pnl_class(-5.0) == "text-[#ef5350]"
    assert captured.pnl_class(0) == ""
    assert captured.pnl_class(None) == ""


def test_row_stamps_rec_and_pnl_class():
    from pages.options import theme
    row = captured.captured_rows([{"recommendation": "CUT", "unrealized_pnl": -3.0}])[0]
    assert row["_rec_class"] == theme.BADGE_NEG
    assert row["_pnl_class"] == "text-[#ef5350]"


def test_row_stamps_rescue_class_empty_for_normal():
    assert captured.captured_rows([{}])[0]["_rescue_class"] == ""


def test_pnl_color_profit_is_green():
    assert captured.pnl_color(12.0) == captured.PNL_GREEN


def test_pnl_color_loss_is_red():
    assert captured.pnl_color(-5.0) == captured.PNL_RED


def test_pnl_color_zero_or_missing_is_blank():
    assert captured.pnl_color(0) == ""
    assert captured.pnl_color(None) == ""
    assert captured.pnl_color("x") == ""


def test_row_stamps_pnl_class():
    assert captured.captured_rows([{"unrealized_pnl": 3.0}])[0]["_pnl_class"] == "text-[#66bb6a]"
    assert captured.captured_rows([{"unrealized_pnl": -3.0}])[0]["_pnl_class"] == "text-[#ef5350]"


def test_fmt_opened_iso_to_date_and_hhmm():
    assert captured.fmt_opened("2026-06-17T13:49:49.898534-05:00") == "2026-06-17 13:49"


def test_fmt_opened_blank_when_missing():
    assert captured.fmt_opened(None) == ""
    assert captured.fmt_opened("") == ""


def test_opened_column_present_and_populated():
    fields = {c["field"] for c in captured.captured_columns()}
    assert "opened" in fields
    row = captured.captured_rows([{"signal_id": "X1", "first_seen_ts":
                                   "2026-06-17T13:49:49.898534-05:00"}])[0]
    assert row["opened"] == "2026-06-17 13:49"


# ── default order: newest capture first ──────────────────────────────────────
def _sig(sid, ts):
    return {"signal_id": sid, "first_seen_ts": ts}


def test_captured_rows_default_to_newest_first():
    rows = captured.captured_rows([
        _sig("old", "2026-06-15T09:31:00.000000-05:00"),
        _sig("new", "2026-06-17T13:49:49.898534-05:00"),
        _sig("mid", "2026-06-16T15:02:11.000000-05:00"),
    ])
    assert [r["id"] for r in rows] == ["new", "mid", "old"]


def test_captured_rows_order_by_the_full_timestamp_not_the_display_minute():
    # ``opened`` is truncated to HH:MM, so two captures inside one minute display
    # identically — the sort must still separate them by the stored seconds.
    rows = captured.captured_rows([
        _sig("first", "2026-06-17T13:49:01.000000-05:00"),
        _sig("second", "2026-06-17T13:49:59.000000-05:00"),
    ])
    assert [r["id"] for r in rows] == ["second", "first"]


def test_captured_rows_sort_respects_the_utc_offset():
    # Same wall-clock text, different offsets: -06:00 is the LATER instant.
    rows = captured.captured_rows([
        _sig("cdt", "2026-06-17T13:00:00-05:00"),
        _sig("cst", "2026-06-17T13:00:00-06:00"),
    ])
    assert [r["id"] for r in rows] == ["cst", "cdt"]


def test_captured_rows_put_undated_signals_last_in_given_order():
    rows = captured.captured_rows([
        _sig("no_ts", None),
        _sig("bad_ts", "not-a-timestamp"),
        _sig("dated", "2026-06-15T09:31:00.000000-05:00"),
    ])
    assert [r["id"] for r in rows] == ["dated", "no_ts", "bad_ts"]


def test_captured_rows_handle_no_signals():
    assert captured.captured_rows([]) == []
    assert captured.captured_rows(None) == []


# ── day footer under the table ───────────────────────────────────────────────
def test_open_pnl_sums_the_unrealized_column():
    assert captured.open_pnl([{"unrealized_pnl": 12.0}, {"unrealized_pnl": -4.5}]) == 7.5


def test_open_pnl_ignores_unpriced_and_non_numeric_signals():
    # A freshly captured signal has no mark yet, so unrealized_pnl is None.
    assert captured.open_pnl([{"unrealized_pnl": None}, {}, {"unrealized_pnl": "x"},
                              {"unrealized_pnl": 3.0}]) == 3.0


def test_open_pnl_of_nothing_is_zero():
    assert captured.open_pnl([]) == 0.0
    assert captured.open_pnl(None) == 0.0


def test_footer_cells_report_the_four_requested_figures():
    day = {"date": "2026-08-19", "opened": 4, "closed": 2, "booked_pnl": 61.5}
    cells = captured.footer_cells(day, [{"unrealized_pnl": 12.0}, {"unrealized_pnl": -4.5}])
    assert [c["label"] for c in cells] == [
        "Opened today", "Closed today", "P&L today (booked)", "P&L today (open)"]
    assert [c["value"] for c in cells] == ["4", "2", "+$61.50", "+$7.50"]


def test_footer_cells_sign_and_colour_the_two_pnl_figures():
    cells = captured.footer_cells({"opened": 0, "closed": 1, "booked_pnl": -120.0},
                                  [{"unrealized_pnl": -3.25}])
    booked, open_ = cells[2], cells[3]
    assert booked["value"] == "-$120.00" and booked["cls"] == captured.pnl_class(-120.0)
    assert open_["value"] == "-$3.25" and open_["cls"] == captured.pnl_class(-3.25)
    # the two COUNTS are never coloured — they carry no direction
    assert cells[0]["cls"] == "" and cells[1]["cls"] == ""


def test_footer_cells_render_a_flat_pnl_uncoloured_and_unsigned():
    cells = captured.footer_cells({"opened": 0, "closed": 0, "booked_pnl": 0.0}, [])
    assert cells[2]["value"] == "$0.00" and cells[2]["cls"] == ""
    assert cells[3]["value"] == "$0.00" and cells[3]["cls"] == ""


def test_footer_cells_survive_a_missing_day_block():
    # A payload published before this shipped, or a cold service, carries no
    # 'day'. The footer must degrade to zeros, never raise on the page build.
    cells = captured.footer_cells(None, [{"unrealized_pnl": 5.0}])
    assert [c["value"] for c in cells] == ["0", "0", "$0.00", "+$5.00"]


def test_footer_cells_coerce_non_numeric_day_fields():
    cells = captured.footer_cells({"opened": None, "closed": "x", "booked_pnl": None}, [])
    assert [c["value"] for c in cells][:3] == ["0", "0", "$0.00"]


def test_footer_open_pnl_reads_a_dash_when_nothing_is_priced_yet():
    """A persisted view carries no marks until 'Refresh marks (live)' runs, so
    every unrealized_pnl is None. Summing that to a confident $0.00 would read
    as 'flat book' when the truth is 'not priced' — the same trap as a NaN
    clamping to a confident extreme."""
    cells = captured.footer_cells({"opened": 0, "closed": 0, "booked_pnl": 0.0},
                                  [{"signal_id": "A"}, {"signal_id": "B"}])
    assert cells[3]["value"] == "—" and cells[3]["cls"] == ""


def test_footer_open_pnl_is_zero_when_there_is_genuinely_nothing_open():
    # No open signals at all IS a flat book — that zero is real, not missing.
    cells = captured.footer_cells({"opened": 0, "closed": 3, "booked_pnl": 0.0}, [])
    assert cells[3]["value"] == "$0.00"


def test_footer_open_pnl_notes_a_partially_priced_book():
    cells = captured.footer_cells(
        {"opened": 0, "closed": 0, "booked_pnl": 0.0},
        [{"unrealized_pnl": 10.0}, {"unrealized_pnl": None}, {"signal_id": "C"}])
    assert cells[3]["value"] == "+$10.00"
    assert cells[3]["note"] == "1 of 3 open signals priced"


def test_footer_open_pnl_has_no_note_when_every_signal_is_priced():
    cells = captured.footer_cells({"opened": 0, "closed": 0, "booked_pnl": 0.0},
                                  [{"unrealized_pnl": 10.0}, {"unrealized_pnl": -2.0}])
    assert cells[3]["value"] == "+$8.00" and cells[3]["note"] == ""
    assert all(c["note"] == "" for c in cells[:3])


def test_footer_is_repainted_on_every_table_repaint():
    """Structural guard: the footer must repaint from the SAME _populate the
    table does, or it silently freezes at its page-build zeros while the rows
    above it update every 2 s."""
    src = inspect.getsource(captured.render)
    call = '_paint_footer(cap.get("day"), sigs)'
    assert call in src, "the footer is never repainted"
    # and it sits INSIDE _populate, not in some once-only build path
    assert src.index("def _populate(") < src.index(call) < src.index("def _select(")


def test_exit_value_default_uses_current_value():
    """The close dialog pre-fills its Exit value with the signal's current price."""
    assert captured.exit_value_default({"current_value": 0.234}) == 0.23


def test_exit_value_default_zero_when_missing():
    assert captured.exit_value_default({}) == 0.0
    assert captured.exit_value_default({"current_value": None}) == 0.0


def test_exit_value_default_handles_non_numeric():
    assert captured.exit_value_default({"current_value": "x"}) == 0.0


def test_exit_value_default_int_to_float():
    assert captured.exit_value_default({"current_value": 2}) == 2.0


def test_render_callable():
    assert callable(captured.render)


def test_page_imports_no_engine_or_proxy():
    """Regression: the Tier-3 page must not pull in engine / proxy / scoring code."""
    for attr in ("proxy", "signal_db", "signal_repricer", "signal_recommender",
                 "OPTIONS_SCANNER", "sys"):
        assert not hasattr(captured, attr), f"captured.py still references {attr}"
    # Also guard the literal import lines so the strings never creep back.
    src = inspect.getsource(captured)
    for forbidden in ("signal_db", "signal_repricer", "signal_recommender",
                      "OPTIONS_SCANNER", "import proxy", "import sys"):
        assert forbidden not in src, f"captured.py must not reference {forbidden!r}"


def test_render_graceful_empty_cache():
    """render() must paint without crashing when the bus cache is empty
    (options service cold) — the Tier-3 graceful-empty path."""
    from nicegui import ui

    bus_client.reset()  # fresh empty fakeredis cache (no service writes)
    assert bus_client.read("options:captured") is None  # confirm empty
    with ui.card():
        captured.render()  # must not raise


#############################################
# Derived breakeven + PoP (the signals table stores NEITHER column)
#############################################

def test_synth_derives_breakeven_for_put_credit_spread():
    # 202.5 short - 0.93 per-share credit. Exact arithmetic, not an estimate.
    s = captured.synth_from_captured(
        {"strategy": "PCS", "short_strike": 202.5, "long_strike": 200.0,
         "entry_credit": 0.93})
    assert s["breakeven"] == pytest.approx(201.57)


def test_synth_derives_breakeven_for_call_credit_spread():
    # A call spread breaks even ABOVE its short strike, so the sign flips.
    s = captured.synth_from_captured(
        {"strategy": "CCS", "short_strike": 420.0, "long_strike": 425.0,
         "entry_credit": 1.10})
    assert s["breakeven"] == pytest.approx(421.10)


def test_synth_iron_condor_breakeven_carries_both_sides():
    s = captured.synth_from_captured(
        {"strategy": "IC", "short_strike": 390.0, "call_short": 420.0,
         "entry_credit": 2.00})
    assert detail.breakeven_text(s["breakeven"]) == "$388.00 / $422.00"


def test_synth_breakeven_none_without_credit():
    s = captured.synth_from_captured({"strategy": "PCS", "short_strike": 202.5})
    assert s["breakeven"] is None


def test_synth_derives_pop_from_short_delta():
    s = captured.synth_from_captured({"entry_short_delta": -0.18})
    assert s["pop_pct"] == pytest.approx(82.0)


def test_synth_pop_none_when_delta_is_the_recorder_zero_default():
    # signal_recorder.py stores short_delta with a DEFAULT OF 0, so a signal that
    # never carried a delta must not be reported as a 100% probability of profit.
    assert captured.synth_from_captured({"entry_short_delta": 0})["pop_pct"] is None

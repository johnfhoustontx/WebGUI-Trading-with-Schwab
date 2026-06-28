"""Tests for the Rescue page pure display builders.

The advisory/candidate computation lives in ``services/options_svc``; this page
is a Tier-3 reader, so only its pure transforms (heat coloring, at-risk row
filtering, cash text, candidate cards, summary line) are exercised here. The
module must import without a NiceGUI app context.
"""
from pages.options import rescue


def test_heat_color_zones():
    # <25 green, 25-50 amber, 50-75 orange, >=75 red
    assert rescue.heat_color(10) == rescue.HEAT_GREEN
    assert rescue.heat_color(30) == rescue.HEAT_AMBER
    assert rescue.heat_color(60) == rescue.HEAT_ORANGE
    assert rescue.heat_color(90) == rescue.HEAT_RED
    # boundaries
    assert rescue.heat_color(25) == rescue.HEAT_AMBER
    assert rescue.heat_color(50) == rescue.HEAT_ORANGE
    assert rescue.heat_color(75) == rescue.HEAT_RED
    # missing / non-numeric -> green (heat 0 default)
    assert rescue.heat_color(None) == rescue.HEAT_GREEN


def test_heat_bg_and_border_classes():
    assert rescue.heat_bg_class(80) == "bg-[#ef5350]"
    assert rescue.heat_bg_class(60) == "bg-[#ff7043]"
    assert rescue.heat_bg_class(40) == "bg-[#ffa726]"
    assert rescue.heat_bg_class(10) == "bg-[#66bb6a]"
    assert rescue.heat_border_class(80) == "border-l-4 border-[#ef5350] bg-[#ef5350]/[.13]"
    assert rescue.heat_border_class(None) == ""   # missing -> no tint


def test_cash_class_maps_sign():
    assert rescue.cash_class(5) == "text-[#66bb6a]"
    assert rescue.cash_class(-5) == "text-[#ef5350]"
    assert rescue.cash_class(0) == "text-[#9e9e9e]"


def _paper_view():
    return {
        "positions": [
            {
                "position_id": "p1", "symbol": "SPY", "strategy": "put_credit_spread",
                "short_strike": 500, "long_strike": 495, "expiration": "2026-07-18",
                "quantity": 1, "current_value": -120.0, "unrealized_pnl": -45.0,
                "current_short_delta": -0.42, "rescue_state": "critical", "heat": 82.0,
                "underlying": 498.0,
            },
            {
                "position_id": "p2", "symbol": "QQQ", "strategy": "call_credit_spread",
                "short_strike": 400, "long_strike": 405, "expiration": "2026-07-25",
                "quantity": 2, "current_value": 30.0, "unrealized_pnl": 20.0,
                "current_short_delta": 0.18, "rescue_state": "ok", "heat": 5.0,
            },
            {
                "position_id": "p3", "symbol": "IWM", "strategy": "iron_condor",
                "short_strike": 200, "long_strike": 195, "expiration": "2026-07-18",
                "quantity": 1, "current_value": -10.0, "unrealized_pnl": -8.0,
                "current_short_delta": -0.30, "rescue_state": "tested", "heat": 55.0,
            },
        ]
    }


def test_at_risk_rows_filters_and_sorts():
    rows = rescue.at_risk_rows(_paper_view(), {"signals": [{"symbol": "AAPL"}]})
    # only critical + tested, not ok; sorted by heat desc
    assert [r["id"] for r in rows] == ["p1", "p3"]
    assert all(r["source"] == "paper" for r in rows)
    top = rows[0]
    assert top["symbol"] == "SPY"
    assert top["strategy"] == "put_credit_spread"
    assert top["strikes"] == "500/495"
    assert top["state"] == "critical"
    assert top["heat"] == 82.0
    assert top["short_delta"] == -0.42
    assert top["pnl"] == -45.0


def test_at_risk_rows_empty_and_none():
    assert rescue.at_risk_rows(None, None) == []
    assert rescue.at_risk_rows({}, {}) == []
    assert rescue.at_risk_rows({"positions": []}, {"signals": []}) == []


def test_at_risk_rows_captured_no_state_excluded():
    captured = {"signals": [{"symbol": "AAPL", "type": "put_credit_spread",
                             "short_strike": 150, "long_strike": 145}]}
    rows = rescue.at_risk_rows({"positions": []}, captured)
    assert rows == []


def test_at_risk_rows_captured_cut_included_keyed_by_signal_id():
    # A captured signal flagged at-risk (e.g. CUT escalated to tested) appears on
    # the board, sourced 'captured' and keyed by signal_id (not symbol).
    captured = {"signals": [{
        "signal_id": "AAPL_0_PCS_150", "symbol": "AAPL", "type": "PCS",
        "short_strike": 150, "long_strike": 145, "expiration": "2026-07-18",
        "current_short_delta": -0.35, "unrealized_pnl": -60.0,
        "rescue_state": "tested", "heat": 60.0,
    }]}
    rows = rescue.at_risk_rows({"positions": []}, captured)
    assert len(rows) == 1
    r = rows[0]
    assert r["id"] == "AAPL_0_PCS_150"   # signal_id, not the symbol
    assert r["source"] == "captured"
    assert r["strategy"] == "PCS"
    assert r["state"] == "tested"
    assert r["heat"] == 60.0


def test_at_risk_rows_pnl_rounded_to_2dp():
    captured = {"signals": [{
        "signal_id": "S1", "symbol": "MRVL", "type": "PCS",
        "short_strike": 285, "long_strike": 280, "expiration": "2026-07-18",
        "unrealized_pnl": -155.99999999999997, "current_short_delta": -0.3499999,
        "rescue_state": "critical", "heat": 60.0,
    }]}
    r = rescue.at_risk_rows({"positions": []}, captured)[0]
    assert r["pnl"] == -156.0
    assert r["short_delta"] == -0.35


def test_at_risk_columns_strike_date_no_underlying():
    fields = [c["field"] for c in rescue.at_risk_columns()]
    labels = [c["label"] for c in rescue.at_risk_columns()]
    assert "underlying_vs_short" not in fields   # nonsensical column removed
    assert "expiration" in fields                # Strike Date uses expiration
    assert "Strike Date" in labels
    assert "DTE" not in labels


def test_cash_text_credit_debit_zero():
    cr = rescue.cash_text(120.0)
    assert cr["text"] == "+$120"
    assert cr["color"] == rescue.CASH_GREEN

    db = rescue.cash_text(-45.0)
    assert db["text"] == "-$45"
    assert db["color"] == rescue.CASH_RED

    z = rescue.cash_text(0)
    assert z["text"] == "$0"
    assert z["color"] == rescue.CASH_NEUTRAL

    n = rescue.cash_text(None)
    assert n["text"] == "$0"
    assert n["color"] == rescue.CASH_NEUTRAL


def _advisory(candidates=None, error=None, apply_result=None):
    adv = {
        "position_id": "p1", "symbol": "SPY", "strategy": "PCS",
        "state": "tested", "heat": 72.0,
        "mark": {"underlying": 498.0, "current_value": -120.0,
                 "unrealized_pnl": -45.0, "short_delta": -0.42, "dte": 6},
        "context": ["short strike tested"],
        "candidates": candidates if candidates is not None else [],
    }
    if error is not None:
        adv["error"] = error
    if apply_result is not None:
        adv["apply_result"] = apply_result
    return adv


def _candidate(**over):
    base = {
        "action": "roll_out", "label": "Roll out 1 week", "apply_kind": "execute",
        "gross_cash": -50.0, "commission": 1.30, "net_cash": -51.30,
        "new_max_loss": -300.0, "new_breakeven": 497.0, "new_short_delta": -0.25,
        "new_width": 5.0, "new_expiry": "2026-07-25", "dte_after": 13,
        "est_fill_legs": [
            {"side": "SELL", "right": "PUT", "strike": 500, "expiry": "2026-07-25",
             "qty": 1, "price": 1.20},
            {"side": "BUY", "right": "PUT", "strike": 495, "expiry": "2026-07-25",
             "qty": 1, "price": 0.60},
        ],
        "rationale": ["reduces delta"], "context": ["debit roll"],
        "warnings": ["uses margin"], "score": 0.81,
    }
    base.update(over)
    return base


def test_candidate_card_rows_two_candidates():
    adv = _advisory(candidates=[_candidate(), _candidate(label="Convert to IC",
                                                         action="convert_ic")])
    rows = rescue.candidate_card_rows(adv)
    assert len(rows) == 2
    c0 = rows[0]
    assert c0["title"] == "Roll out 1 week"
    assert c0["apply_kind"] == "execute"
    assert c0["net_text"]["text"] == "-$51"  # cash_text formatting
    assert c0["score"] == 0.81
    # metrics include formatted entries; delta 2dp, cash $
    joined = " | ".join(c0["metrics"])
    assert "new_short_delta" in joined.lower() or "delta" in joined.lower()
    assert any("-0.25" in m for m in c0["metrics"])
    # legs formatted
    assert c0["legs"][0] == "SELL PUT 500 @1.20"
    assert c0["rationale"] == ["reduces delta"]
    assert c0["context"] == ["debit roll"]
    assert c0["warnings"] == ["uses margin"]


def test_candidate_card_rows_skips_none_metrics():
    adv = _advisory(candidates=[_candidate(new_breakeven=None, new_width=None)])
    rows = rescue.candidate_card_rows(adv)
    metrics = rows[0]["metrics"]
    assert not any("breakeven" in m.lower() for m in metrics)
    assert not any("width" in m.lower() for m in metrics)


def test_candidate_card_rows_error_and_empty():
    assert rescue.candidate_card_rows(_advisory(error="prices moved")) == []
    assert rescue.candidate_card_rows(_advisory(candidates=[])) == []
    assert rescue.candidate_card_rows(None) == []


def test_summary_line_normal():
    adv = _advisory(candidates=[_candidate(), _candidate()])
    line = rescue.summary_line(adv)
    assert "SPY" in line
    assert "PCS" in line
    assert "TESTED" in line
    assert "72" in line
    assert "2" in line  # 2 rescue options


def test_summary_line_error():
    adv = _advisory(error="no chain data")
    assert rescue.summary_line(adv) == "no chain data"


def test_summary_line_with_apply_result_ok():
    adv = _advisory(candidates=[_candidate()],
                    apply_result={"ok": True, "action": "convert_ic"})
    line = rescue.summary_line(adv)
    assert "Applied convert_ic" in line


def test_summary_line_with_apply_result_stale():
    adv = _advisory(candidates=[_candidate()],
                    apply_result={"ok": False, "stale": True, "action": "roll_out"})
    line = rescue.summary_line(adv)
    assert "re-review" in line.lower()

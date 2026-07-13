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
    from pages.options import theme
    # Deep Slate badge tokens: <25 green · 25-50 amber · >=50 red ("red if >=50").
    assert rescue.heat_bg_class(80) == theme.BADGE_NEG
    assert rescue.heat_bg_class(60) == theme.BADGE_NEG
    assert rescue.heat_bg_class(40) == theme.BADGE_WARN
    assert rescue.heat_bg_class(10) == theme.BADGE_POS
    assert rescue.heat_bg_class(None) == theme.BADGE_POS   # missing -> green
    # The row-border tint (separate helper) is unchanged.
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


# ── ad-hoc trade rescue (pure spec mapping from leg-editor legs) ──────────────
def _leg(option_type, side, strike, *, premium=1.0, expiry="2026-07-18", qty=1):
    """A leg-editor leg dict (option_type "call"/"put", side "long"/"short")."""
    return {"option_type": option_type, "side": side, "strike": strike,
            "premium": premium, "expiry": expiry, "qty": qty}


def test_adhoc_spec_from_legs_pcs():
    legs = [_leg("put", "short", 500, premium=1.20),
            _leg("put", "long", 495, premium=0.60)]
    spec = rescue.adhoc_spec_from_legs("spy", legs)
    assert "error" not in spec
    assert spec["symbol"] == "SPY"           # upper-cased
    assert spec["strategy"] == "PCS"
    assert spec["short_strike"] == 500
    assert spec["long_strike"] == 495
    assert spec["expiration"] == "2026-07-18"
    assert spec["quantity"] == 1
    assert round(spec["entry_credit"], 2) == 0.60   # 1.20 short − 0.60 long
    # No call fields for a PCS.
    assert "call_short" not in spec and "call_long" not in spec


def test_adhoc_spec_from_legs_ccs():
    legs = [_leg("call", "short", 400, premium=1.50),
            _leg("call", "long", 405, premium=0.70)]
    spec = rescue.adhoc_spec_from_legs("QQQ", legs)
    assert "error" not in spec
    assert spec["strategy"] == "CCS"
    assert spec["short_strike"] == 400 and spec["long_strike"] == 405
    assert round(spec["entry_credit"], 2) == 0.80
    assert "call_short" not in spec


def test_adhoc_spec_from_legs_ic():
    legs = [_leg("put", "short", 195, premium=1.00),
            _leg("put", "long", 190, premium=0.50),
            _leg("call", "short", 210, premium=1.20),
            _leg("call", "long", 215, premium=0.60)]
    spec = rescue.adhoc_spec_from_legs("IWM", legs)
    assert "error" not in spec
    assert spec["strategy"] == "IC"
    assert spec["short_strike"] == 195 and spec["long_strike"] == 190
    assert spec["call_short"] == 210 and spec["call_long"] == 215
    assert round(spec["entry_credit"], 2) == 1.10   # (1.00+1.20) − (0.50+0.60)


def test_adhoc_spec_from_legs_iron_fly():
    # NBIS-style iron fly: put + call shorts share the 175 strike (allowed).
    legs = [_leg("put", "short", 175, premium=15.70),
            _leg("put", "long", 150, premium=9.05),
            _leg("call", "short", 175, premium=56.97),
            _leg("call", "long", 200, premium=43.15)]
    spec = rescue.adhoc_spec_from_legs("NBIS", legs)
    assert "error" not in spec
    assert spec["strategy"] == "IC"
    assert spec["short_strike"] == 175 and spec["long_strike"] == 150
    assert spec["call_short"] == 175 and spec["call_long"] == 200
    # (15.70+56.97) − (9.05+43.15) = 20.47
    assert round(spec["entry_credit"], 2) == 20.47


def test_adhoc_spec_from_legs_quantity_from_short_leg():
    legs = [_leg("put", "short", 500, premium=1.20, qty=3),
            _leg("put", "long", 495, premium=0.60, qty=3)]
    spec = rescue.adhoc_spec_from_legs("SPY", legs)
    assert spec["quantity"] == 3


def test_adhoc_spec_from_legs_single_leg_errors():
    spec = rescue.adhoc_spec_from_legs("SPY", [_leg("put", "short", 500)])
    assert "error" in spec


def test_adhoc_spec_from_legs_only_shorts_errors():
    legs = [_leg("put", "short", 500), _leg("put", "short", 495)]
    spec = rescue.adhoc_spec_from_legs("SPY", legs)
    assert "error" in spec


def test_adhoc_spec_from_legs_debit_put_spread_errors():
    # short strike BELOW long strike on the put side → debit, not a credit PCS.
    legs = [_leg("put", "short", 495, premium=0.60),
            _leg("put", "long", 500, premium=1.20)]
    spec = rescue.adhoc_spec_from_legs("SPY", legs)
    assert "error" in spec


def test_adhoc_spec_from_legs_multi_expiry_errors():
    legs = [_leg("put", "short", 500, expiry="2026-07-18"),
            _leg("put", "long", 495, expiry="2026-08-15")]
    spec = rescue.adhoc_spec_from_legs("SPY", legs)
    assert "error" in spec
    assert "single expiration" in spec["error"].lower()


def test_adhoc_spec_from_legs_net_debit_errors():
    # A valid PCS structure, but the short premium is below the long → net debit.
    legs = [_leg("put", "short", 500, premium=0.40),
            _leg("put", "long", 495, premium=0.90)]
    spec = rescue.adhoc_spec_from_legs("SPY", legs)
    assert "error" in spec
    assert "credit" in spec["error"].lower()


def test_adhoc_spec_from_legs_missing_strike_errors():
    legs = [_leg("put", "short", None, premium=1.20),
            _leg("put", "long", 495, premium=0.60)]
    spec = rescue.adhoc_spec_from_legs("SPY", legs)
    assert "error" in spec


def test_adhoc_spec_from_legs_empty_errors():
    assert "error" in rescue.adhoc_spec_from_legs("SPY", [])
    assert "error" in rescue.adhoc_spec_from_legs("SPY", None)


def test_render_graceful_empty_cache():
    """render() paints the two-tab page (At-Risk Board + the Calculator-style
    Ad-hoc Trade leg editor) without crashing on a cold/empty cache — the Tier-3
    graceful-empty path. Rendering inside a slot context exercises the widget
    wiring + initial paint (the webgui suite has no NiceGUI User fixture)."""
    import bus_client
    from nicegui import ui

    bus_client.reset()  # fresh empty fakeredis cache (no service writes)
    with ui.card():
        rescue.render()  # must not raise (builds tabs, board + ad-hoc leg editor)

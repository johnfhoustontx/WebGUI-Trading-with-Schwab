"""Unit tests for the multi-strategy swing-scan display builders.

Pure functions in ``pages/options/strategy_table.py`` — no ``ui.`` calls. The
new normalized signal shape (LONG_CALL/LONG_PUT/.../IRON_CONDOR with a ``legs``
list, ``family``, ``bias``, ``net_debit``/``net_credit``, ``breakevens``, …) is
exercised across each type: a long call (unbounded max profit), a debit vertical,
an adapted PCS, and an iron condor.
"""
from pages.options import strategy_table as st
from pages.options.theme import TXT_POS, TXT_NEG, TXT_NEUTRAL


# --- sample normalized signals --------------------------------------------

def _long_call():
    return {
        "id": "lc1", "symbol": "AAPL", "type": "LONG_CALL", "family": "DIRECTIONAL",
        "strategy_label": "Long Call", "bias": "bullish",
        "legs": [{"kind": "call", "side": "long", "strike": 450,
                  "expiration": "2026-08-21", "qty": 1, "mark": 2.50}],
        "expiration": "2026-08-21", "dte": 30,
        "net_debit": 2.50, "net_credit": None, "max_profit": None,  # unbounded
        "max_loss": 250, "capital": 250, "breakevens": [452.5], "pop_pct": 41.0,
        "rr": None, "composite_score": 72.0, "grade": "B",
        "underlying_price": 449.0, "unbounded": True,
    }


def _bull_call_debit():
    return {
        "id": "bc1", "symbol": "MSFT", "type": "BULL_CALL", "family": "VERTICAL",
        "strategy_label": "Bull Call Spread", "bias": "bullish",
        "legs": [{"kind": "call", "side": "long", "strike": 450,
                  "expiration": "2026-08-21", "qty": 1, "mark": 5.00},
                 {"kind": "call", "side": "short", "strike": 455,
                  "expiration": "2026-08-21", "qty": 1, "mark": 2.50}],
        "expiration": "2026-08-21", "dte": 25,
        "net_debit": 2.50, "net_credit": None, "max_profit": 250.0,
        "max_loss": 250.0, "capital": 250.0, "breakevens": [452.5], "pop_pct": 50.0,
        "rr": 1.0, "composite_score": 60.0, "grade": "B",
        "underlying_price": 451.0, "unbounded": False,
    }


def _pcs():
    return {
        "id": "p1", "symbol": "SPY", "type": "PCS", "family": "VERTICAL",
        "strategy_label": "Put Credit Spread", "bias": "bullish",
        "legs": [{"kind": "put", "side": "short", "strike": 440,
                  "expiration": "2026-08-21", "qty": 1, "mark": 1.70},
                 {"kind": "put", "side": "long", "strike": 435,
                  "expiration": "2026-08-21", "qty": 1, "mark": 0.50}],
        "expiration": "2026-08-21", "dte": 20,
        "net_debit": None, "net_credit": 1.70, "max_profit": 170.0,
        "max_loss": 330.0, "capital": 330.0, "breakevens": [438.30], "pop_pct": 68.0,
        "rr": 0.515, "composite_score": 88.0, "grade": "A",
        "underlying_price": 445.0, "unbounded": False,
    }


def _iron_condor():
    return {
        "id": "ic1", "symbol": "QQQ", "type": "IRON_CONDOR", "family": "NEUTRAL",
        "strategy_label": "Iron Condor", "bias": "neutral",
        "legs": [{"kind": "put", "side": "short", "strike": 380,
                  "expiration": "2026-08-21", "qty": 1, "mark": 1.20},
                 {"kind": "put", "side": "long", "strike": 375,
                  "expiration": "2026-08-21", "qty": 1, "mark": 0.60},
                 {"kind": "call", "side": "short", "strike": 420,
                  "expiration": "2026-08-21", "qty": 1, "mark": 1.10},
                 {"kind": "call", "side": "long", "strike": 425,
                  "expiration": "2026-08-21", "qty": 1, "mark": 0.50}],
        "expiration": "2026-08-21", "dte": 28,
        "net_debit": None, "net_credit": 1.20, "max_profit": 120.0,
        "max_loss": 380.0, "capital": 380.0, "breakevens": [378.80, 421.20],
        "pop_pct": 62.0, "rr": 0.315, "composite_score": 55.0, "grade": "C",
        "underlying_price": 400.0, "unbounded": False,
    }


# --- legs_summary ----------------------------------------------------------

def test_legs_summary_two_call_legs():
    assert st.legs_summary(_bull_call_debit()["legs"]) == "L 450C / S 455C"


def test_legs_summary_single_leg():
    assert st.legs_summary(_long_call()["legs"]) == "L 450C"


def test_legs_summary_pcs_puts():
    assert st.legs_summary(_pcs()["legs"]) == "S 440P / L 435P"


def test_legs_summary_iron_condor_four_legs():
    assert st.legs_summary(_iron_condor()["legs"]) == "S 380P / L 375P / S 420C / L 425C"


def test_legs_summary_empty_none():
    assert st.legs_summary([]) == "—"
    assert st.legs_summary(None) == "—"


def test_legs_summary_drops_trailing_zero_on_whole_strike():
    legs = [{"kind": "call", "side": "long", "strike": 450.0}]
    assert st.legs_summary(legs) == "L 450C"


def test_legs_summary_keeps_fractional_strike():
    legs = [{"kind": "put", "side": "short", "strike": 437.5}]
    assert st.legs_summary(legs) == "S 437.5P"


# --- debit_credit_text -----------------------------------------------------

def test_debit_credit_text_debit():
    assert st.debit_credit_text(_long_call()) == "-2.50 debit"


def test_debit_credit_text_credit():
    assert st.debit_credit_text(_pcs()) == "+1.70 credit"


def test_debit_credit_text_neither():
    assert st.debit_credit_text({"net_debit": None, "net_credit": None}) == "—"
    assert st.debit_credit_text({}) == "—"


# --- breakeven_text --------------------------------------------------------

def test_breakeven_text_single():
    assert st.breakeven_text(_pcs()) == "438.30"


def test_breakeven_text_two():
    assert st.breakeven_text(_iron_condor()) == "378.80 / 421.20"


def test_breakeven_text_empty():
    assert st.breakeven_text({"breakevens": []}) == "—"
    assert st.breakeven_text({}) == "—"


# --- strategy_columns ------------------------------------------------------

def test_strategy_columns_shape():
    cols = st.strategy_columns()
    assert isinstance(cols, list)
    # every column has the scanner's exact key shape
    for c in cols:
        assert set(c.keys()) >= {"name", "label", "field", "align"}
    names = [c["name"] for c in cols]
    for expected in ["strategy_label", "bias", "legs", "debit_credit",
                     "max_profit", "max_loss", "rr", "pop_pct", "breakevens",
                     "composite_score", "grade", "actions"]:
        assert expected in names


def test_strategy_columns_actions_centered_and_not_sortable():
    cols = st.strategy_columns()
    actions = next(c for c in cols if c["name"] == "actions")
    assert actions["align"] == "center"
    assert not actions.get("sortable", False)


def test_strategy_columns_data_columns_sortable():
    cols = st.strategy_columns()
    score = next(c for c in cols if c["name"] == "composite_score")
    assert score["sortable"] is True


# --- strategy_rows ---------------------------------------------------------

def test_strategy_rows_sorted_by_score_desc():
    rows = st.strategy_rows([_bull_call_debit(), _pcs(), _long_call(), _iron_condor()])
    scores = [r["composite_score"] for r in rows]
    assert scores == [88.0, 72.0, 60.0, 55.0]


def test_strategy_rows_long_call_unbounded_max_profit():
    row = st.strategy_rows([_long_call()])[0]
    assert row["max_profit"] == "∞"
    assert row["rr"] == "—"
    assert row["debit_credit"] == "-2.50 debit"
    assert row["legs"] == "L 450C"


def test_strategy_rows_debit_spread():
    row = st.strategy_rows([_bull_call_debit()])[0]
    assert row["debit_credit"] == "-2.50 debit"
    assert row["max_profit"] == "250.00"
    assert row["rr"] == "1.00"


def test_strategy_rows_pcs_carries_fields():
    row = st.strategy_rows([_pcs()])[0]
    assert row["id"] == "p1"
    assert row["strategy_label"] == "Put Credit Spread"
    assert row["bias"] == "bullish"
    assert row["debit_credit"] == "+1.70 credit"
    assert row["breakevens"] == "438.30"
    assert row["grade"] == "A"
    assert row["_allow_paper"] is True


def test_strategy_rows_iron_condor():
    row = st.strategy_rows([_iron_condor()])[0]
    assert row["breakevens"] == "378.80 / 421.20"
    assert row["_allow_paper"] is True


def test_strategy_rows_bias_class_mapping():
    rows = {r["id"]: r for r in
            st.strategy_rows([_long_call(), _pcs(), _iron_condor()])}
    assert rows["lc1"]["_bias_class"] == TXT_POS       # bullish
    assert rows["ic1"]["_bias_class"] == TXT_NEUTRAL   # neutral

    bear = _bull_call_debit()
    bear["id"] = "bear1"
    bear["bias"] = "bearish"
    row = st.strategy_rows([bear])[0]
    assert row["_bias_class"] == TXT_NEG               # bearish


def test_strategy_rows_score_class_from_scanner():
    from pages.options import scanner
    row = st.strategy_rows([_pcs()])[0]
    assert row["_score_class"] == scanner.score_zone_class(88.0)


def test_strategy_rows_allow_paper_false_for_directional():
    row = st.strategy_rows([_long_call()])[0]
    assert row["_allow_paper"] is False
    row2 = st.strategy_rows([_bull_call_debit()])[0]
    assert row2["_allow_paper"] is False   # BULL_CALL is a debit spread, not creditable


def test_strategy_rows_robust_to_missing_keys():
    rows = st.strategy_rows([{"id": "x"}])
    assert rows[0]["id"] == "x"
    assert rows[0]["legs"] == "—"
    assert rows[0]["debit_credit"] == "—"
    assert rows[0]["breakevens"] == "—"


def test_strategy_rows_empty():
    assert st.strategy_rows([]) == []
    assert st.strategy_rows(None) == []


# --- view_banner_text ------------------------------------------------------

def test_view_banner_bullish():
    txt = st.view_banner_text({"direction": "bullish", "conviction": 0.60,
                               "vol_regime": "low"})
    assert "Bullish" in txt
    assert "0.60" in txt
    assert "low" in txt
    assert "long / debit" in txt


def test_view_banner_bearish():
    txt = st.view_banner_text({"direction": "bearish", "conviction": 0.40,
                               "vol_regime": "high"})
    assert "Bearish" in txt
    assert "high" in txt


def test_view_banner_neutral():
    txt = st.view_banner_text({"direction": "neutral", "conviction": 0.30,
                               "vol_regime": "mid"})
    assert "Neutral" in txt
    assert "condors" in txt or "flies" in txt or "credit" in txt


def test_view_banner_empty():
    assert "Run a scan" in st.view_banner_text({})
    assert "Run a scan" in st.view_banner_text(None)


# --- detail_signal ---------------------------------------------------------

def test_detail_signal_fills_credit_and_breakeven():
    sig = _pcs()
    out = st.detail_signal(sig)
    assert out["credit"] == 1.70
    assert out["breakeven"] == 438.30
    # input not mutated
    assert "credit" not in sig
    assert "breakeven" not in sig


def test_detail_signal_does_not_fill_credit_from_debit():
    # A debit structure (net_credit None) must NOT show its DEBIT in the green
    # "Credit" tile of the shared detail panel — leave credit unset (→ "—").
    out = st.detail_signal(_long_call())
    assert out.get("credit") is None
    out2 = st.detail_signal(_bull_call_debit())
    assert out2.get("credit") is None


def test_detail_signal_preserves_existing_breakeven():
    sig = dict(_pcs(), breakeven=999.0)
    out = st.detail_signal(sig)
    assert out["breakeven"] == 999.0


def test_detail_signal_handles_empty_breakevens():
    sig = dict(_pcs(), breakevens=[])
    out = st.detail_signal(sig)
    assert out.get("breakeven") is None

"""Unit tests for the multi-strategy swing-scan display builders.

Pure functions in ``pages/options/strategy_table.py`` — no ``ui.`` calls. The
new normalized signal shape (LONG_CALL/LONG_PUT/.../IRON_CONDOR with a ``legs``
list, ``family``, ``bias``, ``net_debit``/``net_credit``, ``breakevens``, …) is
exercised across each type: a long call (unbounded max profit), a debit vertical,
an adapted PCS, and an iron condor.
"""
from pages.options import strategy_table as st
from pages.options.theme import TXT_POS, TXT_NEG, TXT_NEUTRAL, TXT_WARN


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
        # net_credit is per-CONTRACT, like max_profit/max_loss/capital beside it
        # (payoff_metrics multiplies by 100). It read 1.70 — per-SHARE — which
        # made the whole fixture self-inconsistent and masked the credit unit bug.
        "net_debit": None, "net_credit": 170.0, "max_profit": 170.0,
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
    assert st.debit_credit_text(_pcs()) == "+170.00 credit"


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


def test_strategy_columns_include_iv_rank():
    """The multi-strategy table (Strategy Finder + the Scanner's Directional tab)
    carries an IV Rank column, sortable like the other data columns."""
    cols = {c["field"]: c for c in st.strategy_columns()}
    assert cols["iv_rank"]["label"] == "IV Rank"
    assert cols["iv_rank"]["sortable"] is True


def test_strategy_rows_carry_iv_rank_rounded():
    """IV Rank from the signal shows as a whole number; absent → blank (None)."""
    sig = _long_call()
    sig["iv_rank"] = 63.2
    assert st.strategy_rows([sig])[0]["iv_rank"] == 63
    assert st.strategy_rows([_pcs()])[0]["iv_rank"] is None   # fixture has no iv_rank


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
    # long options are now paper-tradeable (defined-risk debit = the premium paid)
    assert row["_allow_paper"] is True


def test_strategy_rows_debit_spread():
    row = st.strategy_rows([_bull_call_debit()])[0]
    assert row["debit_credit"] == "-2.50 debit"
    assert row["max_profit"] == "250.00"
    assert row["rr"] == "1.00"
    assert row["_allow_paper"] is True          # debit vertical → paper-tradeable


def _naked_short_call():
    """A naked short call: profit capped at the credit, loss genuinely unlimited."""
    return {"id": "sc1", "type": "SHORT_CALL", "max_profit": 198.7, "max_loss": 9001.3,
            "unbounded": True, "unbounded_profit": False, "unbounded_loss": True}


def test_fmt_max_profit_naked_short_shows_the_capped_credit_not_infinity():
    """A naked short's profit is capped at the credit — never render it as ∞."""
    assert st._fmt_max_profit(_naked_short_call()) == "198.70"


def test_fmt_max_loss_naked_short_shows_infinity():
    assert st._fmt_max_loss(_naked_short_call()) == "∞"


def test_fmt_long_call_profit_infinite_loss_capped():
    long_call = {"type": "LONG_CALL", "max_profit": None, "max_loss": 201.3,
                 "unbounded": True, "unbounded_profit": True, "unbounded_loss": False}
    assert st._fmt_max_profit(long_call) == "∞"
    assert st._fmt_max_loss(long_call) == "201.30"


def test_fmt_max_profit_absent_value_is_unknown_not_infinite():
    """max_profit=None does NOT imply unbounded profit.

    ``strategy_scanner._normalize_credit`` leaves max_profit None when a credit
    spread's source ``credit`` is missing — a DEFINED-risk structure with an
    unknown reward. Rendering that as ∞ would claim unlimited profit on a
    capped trade, the same lie this fix removes from the naked-short cell.
    """
    unknown_credit = {"type": "PCS", "max_profit": None, "max_loss": 330.0,
                      "unbounded": False, "unbounded_profit": False,
                      "unbounded_loss": False}
    assert st._fmt_max_profit(unknown_credit) == "—"


def test_fmt_max_profit_legacy_signal_without_side_flags():
    """A cached pre-fix signal has `unbounded` but no `unbounded_profit`.

    The legacy flag is trusted ONLY when max_profit is absent, so a legacy long
    call still reads ∞ while a legacy naked short (which carries its capped
    credit) is fixed rather than trusted.
    """
    legacy_long = {"type": "LONG_CALL", "max_profit": None, "unbounded": True}
    legacy_short = {"type": "SHORT_CALL", "max_profit": 198.7, "unbounded": True}
    assert st._fmt_max_profit(legacy_long) == "∞"
    assert st._fmt_max_profit(legacy_short) == "198.70"


def test_fmt_max_loss_stale_naked_short_still_infinite():
    """A signal cached before `unbounded_loss` existed must not read as capped.

    `unbounded` + a FINITE max_profit can only be a naked short: an
    unbounded-profit long carries max_profit=None, and _normalize_credit's
    unknown-reward case carries unbounded=False. Rendering the margin proxy
    (9001.30) here would present an uncapped loss as a risk cap.
    """
    legacy_short = {"type": "SHORT_CALL", "max_profit": 198.7, "max_loss": 9001.3,
                    "unbounded": True}
    assert st._fmt_max_loss(legacy_short) == "∞"


def test_fmt_max_loss_stale_long_call_stays_capped():
    """A legacy long call's loss IS capped at the debit — don't over-flag it."""
    legacy_long = {"type": "LONG_CALL", "max_profit": None, "max_loss": 201.3,
                   "unbounded": True}
    assert st._fmt_max_loss(legacy_long) == "201.30"


def test_fmt_max_loss_unknown_reward_credit_spread_stays_capped():
    """Defined-risk credit spread with an unknown credit: loss is still capped."""
    unknown_credit = {"type": "PCS", "max_profit": None, "max_loss": 330.0,
                      "unbounded": False}
    assert st._fmt_max_loss(unknown_credit) == "330.00"


def test_strategy_rows_flags_undefined_risk_on_stale_naked_short():
    """The badge must survive a stale cache — it is the risk marker."""
    rows = st.strategy_rows([
        {"id": "stale_short", "type": "SHORT_CALL", "max_profit": 198.7,
         "max_loss": 9001.3, "unbounded": True},
        {"id": "stale_long", "type": "LONG_CALL", "max_profit": None,
         "max_loss": 201.3, "unbounded": True},
        {"id": "unknown_credit", "type": "PCS", "max_profit": None,
         "max_loss": 330.0, "unbounded": False},
    ])
    by_id = {r["id"]: r for r in rows}
    assert by_id["stale_short"]["_undefined_risk"] is True
    assert by_id["stale_short"]["max_loss"] == "∞"
    assert by_id["stale_long"]["_undefined_risk"] is False
    assert by_id["unknown_credit"]["_undefined_risk"] is False


def test_strategy_rows_marks_undefined_risk_only_on_unbounded_loss():
    rows = st.strategy_rows([
        {"id": "a", "type": "SHORT_CALL", "max_profit": 198.7, "max_loss": 9001.3,
         "unbounded": True, "unbounded_profit": False, "unbounded_loss": True},
        {"id": "b", "type": "LONG_CALL", "max_profit": None, "max_loss": 201.3,
         "unbounded": True, "unbounded_profit": True, "unbounded_loss": False},
    ])
    by_id = {r["id"]: r for r in rows}
    assert by_id["a"]["_undefined_risk"] is True    # naked short
    assert by_id["b"]["_undefined_risk"] is False   # long call: risk IS defined
    assert by_id["a"]["_allow_paper"] is False      # already gated, pin it


def test_strategy_rows_naked_short_not_paper_tradeable():
    """A naked short (undefined risk) is NOT paper-tradeable — the gate excludes it."""
    row = st.strategy_rows([{"id": "s1", "symbol": "SPY", "type": "SHORT_CALL",
                             "strategy_label": "Short Call", "bias": "bearish",
                             "legs": [{"kind": "call", "side": "short", "strike": 450}],
                             "net_credit": 250.0, "max_loss": 9999.0, "score": 40,
                             "grade": "C"}])[0]
    assert row["_allow_paper"] is False


def test_strategy_rows_pcs_carries_fields():
    row = st.strategy_rows([_pcs()])[0]
    assert row["id"] == "p1"
    assert row["strategy_label"] == "Put Credit Spread"
    assert row["bias"] == "bullish"
    assert row["debit_credit"] == "+170.00 credit"
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


def test_strategy_rows_allow_paper_for_defined_risk_debit():
    # Defined-risk debit structures (long options + debit verticals) are now paper-tradeable.
    assert st.strategy_rows([_long_call()])[0]["_allow_paper"] is True
    assert st.strategy_rows([_bull_call_debit()])[0]["_allow_paper"] is True


def test_strategy_rows_robust_to_missing_keys():
    rows = st.strategy_rows([{"id": "x"}])
    assert rows[0]["id"] == "x"
    assert rows[0]["legs"] == "—"
    assert rows[0]["debit_credit"] == "—"
    assert rows[0]["breakevens"] == "—"


def test_strategy_rows_empty():
    assert st.strategy_rows([]) == []
    assert st.strategy_rows(None) == []


# --- grade_class + grade_reason -------------------------------------------

def test_grade_class_maps_grades():
    from pages.options import theme
    assert st.grade_class("Strong") == theme.TXT_POS
    assert st.grade_class("Good") == theme.TXT_POS
    assert st.grade_class("Marginal") == theme.TXT_WARN
    assert st.grade_class("Weak") == theme.TXT_NEG
    assert st.grade_class("???") == theme.TXT_NEUTRAL


def test_strategy_rows_carry_grade_reason_and_class():
    from pages.options import theme
    row = st.strategy_rows([{"id": "x", "type": "PCS", "grade": "Weak",
                             "grade_reason": "Fails: PoP", "composite_score": 30}])[0]
    assert row["grade_reason"] == "Fails: PoP"
    assert row["_grade_class"] == theme.TXT_NEG


def test_strategy_rows_grade_reason_defaults_empty():
    row = st.strategy_rows([{"id": "y", "type": "PCS", "grade": "Strong",
                             "composite_score": 90}])[0]
    assert row["grade_reason"] == ""
    assert row["_grade_class"] == TXT_POS


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


def test_short_exp_formats_and_handles_missing():
    assert st._short_exp("2026-10-28") == "10/28"
    assert st._short_exp("") == "—"
    assert st._short_exp(None) == "—"


def test_columns_include_exp_and_dte():
    fields = [c["field"] for c in st.strategy_columns()]
    assert "expiration" in fields and "dte" in fields


def test_rows_carry_exp_and_dte():
    row = st.strategy_rows([{"id": "x", "type": "PCS", "expiration": "2026-10-28",
                             "dte": 120, "composite_score": 50}])[0]
    assert row["expiration"] == "10/28"
    assert row["dte"] == 120


# --- signed net cost --------------------------------------------------------
# UNITS: strategy_scanner.payoff_metrics emits net_debit/net_credit in
# per-CONTRACT dollars (``net * _CONTRACT_MULT``, _CONTRACT_MULT = 100.0), always
# POSITIVE, using None to mean "this structure is the other side". ``net_cost``
# is normalized to PER-SHARE dollars — the scale the ``credit`` field and
# detail.money_per_contract already use — so a $240 long call cannot render as
# $24,000.

def test_detail_signal_carries_debit_as_negative_net_cost():
    out = st.detail_signal({"net_debit": 240.0})
    assert out["credit"] is None          # still not mislabelled as a credit
    assert out["net_cost"] == -2.40


def test_detail_signal_credit_is_positive_net_cost():
    out = st.detail_signal({"net_credit": 155.0})
    assert out["net_cost"] == 1.55


def test_detail_signal_net_cost_from_per_share_source_credit():
    # A scanner credit spread carries the per-share ``credit`` and no net_*.
    assert st.detail_signal({"credit": 1.55})["net_cost"] == 1.55


def test_detail_signal_net_cost_absent_when_nothing_priced():
    assert st.detail_signal({"type": "PCS"})["net_cost"] is None


def test_detail_signal_credit_from_net_credit_is_per_share():
    # A NATIVELY-built naked short carries no source ``credit`` (only the adapted
    # credit families preserve one), so ``credit`` is filled from ``net_credit``
    # — which payoff_metrics emits in per-CONTRACT dollars. detail.py's stated
    # convention is that every adapter hands it PER-SHARE, and it renders credit
    # through money_per_contract (x100), so an unnormalized fill double-scales.
    out = st.detail_signal({"type": "SHORT_PUT", "net_credit": 155.0,
                            "net_debit": None})
    assert out["credit"] == 1.55
    # and it stays consistent with the net_cost normalized alongside it
    assert out["net_cost"] == 1.55


def test_detail_signal_does_not_mutate_its_input():
    sig = {"net_debit": 240.0}
    st.detail_signal(sig)
    assert sig == {"net_debit": 240.0}

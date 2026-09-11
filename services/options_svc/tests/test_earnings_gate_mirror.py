"""``swing_scan``'s post-build earnings filter mirrors ``screen_spreads``' gate.

The two halves must agree. ``screen_spreads`` gates the adapted credit spreads
inside the engine; this filter gates the BUILDER families, which the engine
never sees. When they disagree, one candidate set is dropped and the other kept
for the same symbol, expiry and report -- silently, and only for the windows
where the two disagree.

They disagreed as of 2026-09-09: the engine's gate learned that the "0-DTE"
bucket spans DTE 0..4 (so an overnight hold in it can straddle a report) while
this filter still keyed off the bare tuple, which exempts the whole bucket.
"""
import ast
import pathlib

import pytest

from services.options_svc import compute

se = compute.se


def _sig(dte, expiration="2026-09-11"):
    return {"symbol": "ORCL", "type": "PCS", "short_strike": 145.0,
            "long_strike": 143.0, "short_mark": 1.2, "long_mark": 0.6,
            "credit": 0.44, "max_loss": 1.56, "expiration": expiration,
            "dte": dte, "underlying_price": 163.0}


class TestThePredicateIsShared:
    """A source-level guard, because a behaviour test can only cover the windows
    it thinks to try -- and the last drift was in a window nobody tried."""

    def test_the_filter_calls_the_engines_predicate(self):
        src = pathlib.Path(compute.__file__).read_text(encoding="utf-8")
        tree = ast.parse(src)
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "swing_scan")
        called = {ast.unparse(n.func) for n in ast.walk(fn)
                  if isinstance(n, ast.Call)}
        assert "se.earnings_gate_applies" in called, (
            "swing_scan must reach the engine's shared predicate, not restate "
            "the membership test -- that is how the two halves drift")

    def test_the_filter_no_longer_restates_the_membership_test(self):
        src = pathlib.Path(compute.__file__).read_text(encoding="utf-8")
        tree = ast.parse(src)
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "swing_scan")
        compares = [ast.unparse(n) for n in ast.walk(fn)
                    if isinstance(n, ast.Compare)]
        assert not any("EARNINGS_GATED_TRADE_TYPES" in c for c in compares)


class TestTheTwoHalvesAgree:
    """Driven from the PREDICATE rather than a hand-written table, so a window
    added to one side cannot pass by being forgotten here."""

    @pytest.mark.parametrize("trade_type,dte", [
        ("SWING", 9), ("INCOME", 38), ("0-DTE", 0), ("0-DTE", 1), ("0-DTE", 3),
        ("0-DTE", 4), ("DIRECTIONAL", 3),
    ])
    def test_the_filter_drops_exactly_what_the_engine_would(
            self, trade_type, dte, monkeypatch):
        kept = _run_swing_scan(monkeypatch, trade_type=trade_type, dte=dte,
                               earnings_date="2026-09-10")
        expected_dropped = se.earnings_gate_applies(trade_type, dte)
        assert (kept == []) is expected_dropped


class TestTheSeptemberOrclCase:
    def test_a_three_day_hold_in_the_zero_dte_bucket_is_dropped(self, monkeypatch):
        """dte 3, expiry 09-11, report 09-10 -- the shape that was captured
        sixteen times on 2026-09-08."""
        assert _run_swing_scan(monkeypatch, trade_type="0-DTE", dte=3,
                               earnings_date="2026-09-10") == []

    def test_a_same_day_expiry_keeps_its_exemption(self, monkeypatch):
        assert _run_swing_scan(monkeypatch, trade_type="0-DTE", dte=0,
                               earnings_date="2026-09-10") != []

    def test_no_earnings_date_drops_nothing(self, monkeypatch):
        assert _run_swing_scan(monkeypatch, trade_type="0-DTE", dte=3,
                               earnings_date=None) != []


def _run_swing_scan(monkeypatch, *, trade_type, dte, earnings_date):
    """Drive swing_scan over one builder candidate; return the surviving rows."""
    chain = {"putExpDateMap": {}, "callExpDateMap": {}}
    # se.fetch_option_chain unwraps an HTTP response, so patch it directly
    # rather than doubling the schwab-py client's response object.
    monkeypatch.setattr(compute.se, "fetch_option_chain",
                        lambda *a, **kw: chain)
    monkeypatch.setattr(compute._proxy.schwab_client, "get_quote",
                        lambda *a, **kw: {"last": 163.0})
    monkeypatch.setattr(compute.se, "fetch_price_history",
                        lambda *a, **kw: {"hist": True})
    monkeypatch.setattr(compute.se, "calc_technicals",
                        lambda *a, **kw: {"trend": "BULLISH", "rsi14": 60,
                                      "price": 163.0, "sma20": 160.0})
    monkeypatch.setattr(compute, "run_iv_analysis",
                        lambda *a, **kw: {
                            "iv_rank": 50.0,
                            "expected_moves": {"daily": {"move_dollars": 5.0}}})
    monkeypatch.setattr(compute.se, "screen_spreads",
                        lambda *a, **kw: [_sig(dte)])
    monkeypatch.setattr(compute.se, "build_iron_condors", lambda *a, **kw: [])
    # Neutralise the quality cut: this test is about the earnings filter alone.
    monkeypatch.setattr(compute, "_passes_swing_cut", lambda *a, **kw: True)

    out = compute.swing_scan("ORCL", 0, 4, -0.20, -0.10, 0.10, 0.20, 0.10,
                             trade_type=trade_type, earnings_date=earnings_date)
    return out["signals"]


def test_the_harness_itself_produces_a_row(monkeypatch):
    """Guards every assertion above. A broken double yields an empty list, and
    an empty list makes every "was dropped" test pass for the wrong reason --
    which it did on the first run of this file."""
    assert _run_swing_scan(monkeypatch, trade_type="SWING", dte=9,
                           earnings_date=None) != []

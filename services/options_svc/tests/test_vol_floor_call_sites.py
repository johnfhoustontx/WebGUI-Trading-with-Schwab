"""B2: the volatility floor reaches the Strategy Finder and the Income Window.

Design: docs/plans/2026-09-12-volatility-gate-design.md.

``MIN_IV_RANK`` was applied in exactly one place — ``run_full_scan``'s 0-DTE and
Swing lists — so both surfaces served by ``compute.swing_scan`` sold premium at
any volatility at all. Measured on the live board before shipping this, the
Income Window's **top-ranked** candidate was an IREN put credit spread at an IV
rank of **0.1**, with CRWV at 15.3 in fifth.

⚠ The floor must NOT touch the long-premium candidates in the same list. The
Strategy Finder emits debit verticals and long options beside its credit spreads,
and cheap volatility is precisely when those are the right trade — so a blanket
per-list filter would cut hardest exactly where it should not cut at all. Every
test below that asserts a drop has a long-premium sibling asserting a keep.
"""
from services.options_svc import compute  # noqa: F401  (runs the sys.path glue)

import strategy_scanner  # noqa: E402
import strategy_scoring  # noqa: E402

from shared import vol_gate  # noqa: E402


def _sig(kind, vega, score=80.0):
    """A scored candidate, minimal but shaped like the real thing."""
    return {"type": kind, "net_vega": vega, "composite_score": score,
            "grade": "Good", "dte": 35, "expiration": "2026-10-16",
            "symbol": "TEST", "legs": []}


def _stub(monkeypatch, signals, iv_rank):
    """Stub every fetch ``swing_scan`` does, and hand it a fixed scored list."""
    monkeypatch.setattr(compute.se, "fetch_option_chain",
                        lambda *a, **k: {"underlyingPrice": 100.0})
    monkeypatch.setattr(compute._proxy.schwab_client, "get_quote",
                        lambda s: {"last": 100.0})
    monkeypatch.setattr(compute.se, "fetch_price_history", lambda *a, **k: None)
    monkeypatch.setattr(compute.se, "calc_technicals", lambda *a, **k: {})
    monkeypatch.setattr(compute, "run_iv_analysis",
                        lambda *a, **k: {"iv_rank": iv_rank})
    monkeypatch.setattr(strategy_scoring, "infer_market_view",
                        lambda *a, **k: {"vol_regime": "low"})
    monkeypatch.setattr(strategy_scanner, "build_directional", lambda *a, **k: [])
    monkeypatch.setattr(strategy_scanner, "build_debit_verticals",
                        lambda *a, **k: [])
    monkeypatch.setattr(compute.se, "screen_spreads", lambda *a, **k: iter(()))
    monkeypatch.setattr(compute.se, "build_iron_condors", lambda *a, **k: [])
    monkeypatch.setattr(strategy_scoring, "score_all",
                        lambda sigs, *a, **k: list(signals))
    monkeypatch.setattr(compute, "_passes_swing_cut", lambda s: True)


def _run(monkeypatch, signals, iv_rank, **kw):
    _stub(monkeypatch, signals, iv_rank)
    kw.setdefault("trade_type", "SWING")
    return compute.swing_scan("TEST", 30, 45, -0.25, -0.15, 0.15, 0.25, 0.12, **kw)


# ── the Strategy Finder (trade_type="SWING", floor 30) ───────────────────────

def test_a_short_premium_candidate_below_the_floor_is_dropped(monkeypatch):
    out = _run(monkeypatch, [_sig("PCS", -0.31)], iv_rank=12.0)
    assert out["signals"] == []
    assert out["vol_filtered"] == 1


def test_a_short_premium_candidate_above_the_floor_is_kept(monkeypatch):
    out = _run(monkeypatch, [_sig("PCS", -0.31)], iv_rank=80.0)
    assert len(out["signals"]) == 1
    assert out["vol_filtered"] == 0


def test_a_LONG_premium_candidate_below_the_floor_is_KEPT(monkeypatch):
    """The whole reason the gate keys on vega sign: buying cheap vol is correct."""
    out = _run(monkeypatch, [_sig("LONG_CALL", +0.44)], iv_rank=12.0)
    assert len(out["signals"]) == 1
    assert out["vol_filtered"] == 0


def test_a_mixed_list_loses_only_its_short_premium_half(monkeypatch):
    sigs = [_sig("PCS", -0.31), _sig("LONG_CALL", +0.44),
            _sig("SHORT_PUT", -0.22), _sig("DEBIT_CALL_VERTICAL", +0.10)]
    out = _run(monkeypatch, sigs, iv_rank=5.0)
    kept = {s["type"] for s in out["signals"]}
    assert kept == {"LONG_CALL", "DEBIT_CALL_VERTICAL"}
    assert out["vol_filtered"] == 2


# ── the Income Window (trade_type="INCOME") ──────────────────────────────────

def test_income_scan_applies_the_floor(monkeypatch):
    """``income_scan`` is a thin wrapper over ``swing_scan``, so one call site
    covers both surfaces - but only if INCOME is a key the accessor carries."""
    monkeypatch.setattr(compute, "_income_earnings", lambda s: ("none", None))
    _stub(monkeypatch, [_sig("PCS", -0.31)], iv_rank=0.1)
    out = compute.income_scan("IREN")
    assert out["signals"] == []
    assert out["vol_filtered"] == 1


def test_income_scan_keeps_a_candidate_above_its_floor(monkeypatch):
    monkeypatch.setattr(compute, "_income_earnings", lambda s: ("none", None))
    _stub(monkeypatch, [_sig("SHORT_PUT", -0.19)], iv_rank=69.2)
    out = compute.income_scan("XOM")
    assert len(out["signals"]) == 1


def test_the_live_board_that_motivated_this_loses_exactly_two_rows(monkeypatch):
    """Reproduces prod's 2026-09-12 income board: IREN 0.1, SPY 34.9 x2,
    XOM 69.2, CRWV 15.3 against a floor of 30. Per-symbol scans in real life, so
    the readings are driven one at a time and the survivors counted."""
    monkeypatch.setattr(compute, "_income_earnings", lambda s: ("none", None))
    board = [("IREN", 0.1), ("SPY", 34.9), ("SPY", 34.9),
             ("XOM", 69.2), ("CRWV", 15.3)]
    kept = []
    for sym, iv in board:
        _stub(monkeypatch, [_sig("PCS", -0.31)], iv_rank=iv)
        if compute.income_scan(sym)["signals"]:
            kept.append((sym, iv))
    assert kept == [("SPY", 34.9), ("SPY", 34.9), ("XOM", 69.2)]


# ── the counts stay separable ────────────────────────────────────────────────

def test_a_volatility_drop_is_NOT_counted_as_a_quality_drop(monkeypatch):
    """The Strategy Finder renders ``filtered_out`` as "below the quality bar".
    A volatility drop is a statement about the environment, not about the
    candidate, so folding the two would print a sentence that is not true."""
    out = _run(monkeypatch, [_sig("PCS", -0.31)], iv_rank=12.0)
    assert out["vol_filtered"] == 1
    assert out["filtered_out"] == 0


def test_the_quality_cut_still_reports_its_own_drops(monkeypatch):
    _stub(monkeypatch, [_sig("PCS", -0.31), _sig("PCS", -0.30)], iv_rank=80.0)
    monkeypatch.setattr(compute, "_passes_swing_cut", lambda s: False)
    out = compute.swing_scan("TEST", 30, 45, -0.25, -0.15, 0.15, 0.25, 0.12)
    assert out["filtered_out"] == 2
    assert out["vol_filtered"] == 0


def test_both_counts_are_present_on_the_early_return_paths(monkeypatch):
    """A null chain and a null spot both short-circuit before any gate runs; a
    reader doing ``out["vol_filtered"]`` must not KeyError there."""
    monkeypatch.setattr(compute.se, "fetch_option_chain", lambda *a, **k: None)
    out = compute.swing_scan("TEST", 30, 45, -0.25, -0.15, 0.15, 0.25, 0.12)
    assert out["vol_filtered"] == 0 and out["filtered_out"] == 0

    monkeypatch.setattr(compute.se, "fetch_option_chain",
                        lambda *a, **k: {"underlyingPrice": 0})
    monkeypatch.setattr(compute._proxy.schwab_client, "get_quote", lambda s: {})
    out = compute.swing_scan("TEST", 30, 45, -0.25, -0.15, 0.15, 0.25, 0.12)
    assert out["vol_filtered"] == 0 and out["filtered_out"] == 0


# ── the gate is OFF where it has no level ────────────────────────────────────

def test_an_unknown_trade_type_is_ungated_rather_than_refused(monkeypatch):
    """``.get(trade_type)`` -> None -> no floor. Matches the existing
    ``MIN_IV_RANK.get(trade_type, 0)`` contract: a surface nobody has set a level
    for keeps working."""
    out = _run(monkeypatch, [_sig("PCS", -0.31)], iv_rank=0.0,
               trade_type="NOT_A_WINDOW")
    assert len(out["signals"]) == 1


def test_a_symbol_with_no_iv_rank_at_all_is_ungated(monkeypatch):
    """``run_iv_analysis`` returns iv_rank None when HV history is too short. A
    data absence must degrade to ungated, never to refused."""
    out = _run(monkeypatch, [_sig("PCS", -0.31)], iv_rank=None)
    assert len(out["signals"]) == 1


def test_the_shipped_ceiling_is_off_so_long_premium_is_never_cut(monkeypatch):
    """Pins the shipped policy, not just the mechanism: no long-premium outcome
    data exists in this app, so the ceiling must stay disabled until it does."""
    out = _run(monkeypatch, [_sig("LONG_CALL", +0.44)], iv_rank=100.0)
    assert len(out["signals"]) == 1
    assert vol_gate.blocks(100.0, +0.44, ceiling=65) == vol_gate.IV_TOO_HIGH

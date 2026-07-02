"""Tests for the autonomous decision cycle in ``driver_svc.compute`` (Phase 4).

Three additive functions (the existing ``run_morning``/``execute``/
``build_perf_report`` are untouched):

* ``build_packet`` — projects the scanner menu (allowed-only, top-N by composite
  score, stable ids ``m0..``) + day P&L + gap-to-target into the model-facing
  packet (plus a ``menu_by_id`` mapping id → the RAW scanner signal the guardrails
  resolve back for verbatim paper execution). Pure given the cache views.
* ``run_cycle`` — ``build_packet → decider.decide → guardrails.apply_guardrails``;
  defensive (any exception → a stand-down result). Tests monkeypatch
  ``services.driver_svc.decider.decide`` so no model/network is hit.
* ``fetch_market_context`` — VIX/SPX via ``morning_agent.fetch_market_conditions``,
  defensive → ``{}``.

REAL field-name notes (verified against the engines, NOT the plan's guesses):
* the scanner signal's structure code lives in ``type`` (``"PCS"``/``"CCS"``/
  ``"IC"``); ``trade_type`` is the DTE bucket — there is NO ``structure`` key.
* the expiration field is ``expiration`` (not ``expiry``); PoP is ``pop_pct``.
* the paper snapshot's day-P&L field is ``session_pnl`` (``paper_engine.
  account_snapshot``).
"""
from services.driver_svc import compute


def _lim():
    return {"daily_target": 500.0, "per_trade_max_risk": 300.0, "daily_risk_budget": 900.0,
            "max_concurrent": 6, "max_trades_per_cycle": 3, "vix_max": 25.0}


# ---------------------------------------------------------------------------
# Task 4.1 — build_packet
# ---------------------------------------------------------------------------
def test_build_packet_skips_non_dict_signals():
    """A malformed (None/str) signal element is skipped, not crashed on."""
    scan = {"signals_0dte": [None, "junk",
                {"symbol": "QQQ", "type": "PCS", "max_loss": 2,
                 "credit": 60, "pop_pct": 0.85, "composite_score": 78,
                 "expiration": "2026-06-24"}],
            "signals_swing": []}
    pkt = compute.build_packet(scan, {}, target=500.0, limits=_lim(), market={})
    assert len(pkt["menu"]) == 1 and pkt["menu"][0]["structure"] == "PCS"


def test_build_packet_filters_allowed_and_assigns_ids():
    scan = {"signals_0dte": [
                {"symbol": "QQQ", "structure": "put_credit_spread", "max_loss": 200,
                 "credit": 60, "pop": 0.85, "composite_score": 78, "expiry": "2026-06-24"},
                {"symbol": "X", "structure": "naked_put", "max_loss": None}],  # dropped
            "signals_swing": []}
    paper = {"snapshot": {"session_pnl": 120.0}, "positions": [], "has_account": True}
    pkt = compute.build_packet(scan, paper, target=500.0, limits=_lim(), market={"vix": 14.0})
    assert pkt["gap_to_target"] == 380.0
    assert len(pkt["menu"]) == 1 and pkt["menu"][0]["id"] == "m0"
    assert "menu_by_id" in pkt and "m0" in pkt["menu_by_id"]
    # menu_by_id maps the id back to the RAW signal (guardrails resolves it).
    assert pkt["menu_by_id"]["m0"] is scan["signals_0dte"][0]
    assert pkt["vix"] == 14.0 and pkt["day_pnl"] == 120.0
    assert pkt["open_count"] == 0


def test_build_packet_structure_from_real_type_key():
    """A real ``type``-keyed signal (no ``structure`` key) projects structure="PCS".

    The plan's ``_menu_item`` read ``sig.get("structure") or sig.get("trade_type")``
    which would mislabel every real signal as the DTE bucket ("0-DTE"). We reuse
    ``guardrails.signal_structure`` (structure → type → trade_type) instead.
    """
    scan = {"signals_0dte": [
                {"symbol": "SPX", "type": "PCS", "trade_type": "0-DTE",
                 "max_loss": 250.0, "credit": 70, "pop_pct": 82.0,
                 "composite_score": 90, "expiration": "2026-06-24"}],
            "signals_swing": []}
    paper = {"snapshot": {"session_pnl": 0.0}, "positions": []}
    pkt = compute.build_packet(scan, paper, target=500.0, limits=_lim(), market={})
    item = pkt["menu"][0]
    assert item["structure"] == "PCS"          # NOT "0-DTE"
    assert item["symbol"] == "SPX"
    assert item["expiry"] == "2026-06-24"      # read from the real ``expiration`` key
    assert item["pop"] == 82.0                 # read from the real ``pop_pct`` key
    assert item["credit"] == 70 and item["max_loss"] == 250.0
    assert item["score"] == 90


def test_build_packet_sorts_by_composite_score_desc_and_caps_top_n():
    from services.driver_svc import settings as _st
    sigs = [{"symbol": f"S{i}", "type": "PCS", "max_loss": 100.0,
             "composite_score": i} for i in range(_st.MENU_TOP_N + 5)]
    scan = {"signals_0dte": sigs, "signals_swing": []}
    pkt = compute.build_packet(scan, {"snapshot": {}, "positions": []},
                               target=500.0, limits=_lim(), market={})
    assert len(pkt["menu"]) == _st.MENU_TOP_N            # capped to top-N
    scores = [m["score"] for m in pkt["menu"]]
    assert scores == sorted(scores, reverse=True)        # highest score first
    assert scores[0] == _st.MENU_TOP_N + 4               # the highest input score


def test_build_packet_merges_0dte_and_swing():
    scan = {"signals_0dte": [{"symbol": "QQQ", "type": "PCS", "max_loss": 200.0,
                              "composite_score": 60}],
            "signals_swing": [{"symbol": "AAPL", "type": "CCS", "max_loss": 300.0,
                               "composite_score": 70}]}
    pkt = compute.build_packet(scan, {"snapshot": {}, "positions": []},
                               target=500.0, limits=_lim(), market={})
    assert len(pkt["menu"]) == 2
    # Swing (score 70) outranks 0dte (score 60).
    assert pkt["menu"][0]["symbol"] == "AAPL"


def test_build_packet_empty_or_missing_scan_is_safe():
    for scan in ({}, None, {"signals_0dte": [], "signals_swing": []}):
        pkt = compute.build_packet(scan, {"snapshot": {}, "positions": []},
                                   target=500.0, limits=_lim(), market={})
        assert pkt["menu"] == [] and pkt["menu_by_id"] == {}
        assert pkt["gap_to_target"] == 500.0       # day_pnl unknown → gap == target


def test_build_packet_no_snapshot_unknown_day_pnl():
    """A paper_view with no snapshot → day_pnl None, gap == target (not a crash)."""
    for paper in ({}, None, {"positions": []}, {"snapshot": None, "positions": []}):
        pkt = compute.build_packet({"signals_0dte": [], "signals_swing": []}, paper,
                                   target=500.0, limits=_lim(), market={})
        assert pkt["day_pnl"] is None
        assert pkt["gap_to_target"] == 500.0
        assert pkt["open_count"] == 0


def test_build_packet_day_pnl_fallback_keys_and_bad_values():
    # The real key is session_pnl; legacy fallbacks still parse.
    assert compute.build_packet({}, {"snapshot": {"day_pnl": 75.0}},
                                target=500.0, limits=_lim(), market={})["day_pnl"] == 75.0
    # A non-numeric value is ignored (degrades to None), never raises.
    pkt = compute.build_packet({}, {"snapshot": {"session_pnl": "oops"}},
                               target=500.0, limits=_lim(), market={})
    assert pkt["day_pnl"] is None


def test_build_packet_open_positions_prefers_driver_tagged():
    """When positions are source-tagged, only driver positions count toward open."""
    paper = {"snapshot": {"session_pnl": 0.0}, "positions": [
        {"symbol": "QQQ", "source": "driver"},
        {"symbol": "SPY", "source": "manual"},
        {"symbol": "IWM", "source": "driver"}]}
    pkt = compute.build_packet({}, paper, target=500.0, limits=_lim(), market={})
    assert pkt["open_count"] == 2
    assert all(p["source"] == "driver" for p in pkt["open_positions"])


def test_build_packet_open_positions_untagged_falls_back_to_whole_account():
    """v1: when NO position is source-tagged, the whole account counts (dedicated)."""
    paper = {"snapshot": {"session_pnl": 0.0}, "positions": [
        {"symbol": "QQQ"}, {"symbol": "SPY"}]}
    pkt = compute.build_packet({}, paper, target=500.0, limits=_lim(), market={})
    assert pkt["open_count"] == 2
    assert len(pkt["open_positions"]) == 2


def test_build_packet_carries_target_and_limits():
    pkt = compute.build_packet({}, {"snapshot": {}}, target=500.0, limits=_lim(),
                               market={"vix": 14.0})
    assert pkt["target"] == 500.0
    assert pkt["limits"] == _lim()
    assert pkt["vix"] == 14.0


# ---------------------------------------------------------------------------
# Task 4.2 — run_cycle
# ---------------------------------------------------------------------------
def test_run_cycle_returns_executable(monkeypatch):
    scan = {"signals_0dte": [{"symbol": "QQQ", "type": "PCS", "max_loss": 2,
                              "composite_score": 80, "credit": 55, "expiration": "2026-06-24"}],
            "signals_swing": []}
    paper = {"snapshot": {"session_pnl": 0.0}, "positions": [], "has_account": True}
    monkeypatch.setattr("services.driver_svc.decider.decide",
                        lambda packet, **kw: {"stand_down": False,
                            "day_thesis": "t", "confidence": 0.6,
                            "trades": [{"id": "m0", "quantity": 1}]})
    out = compute.run_cycle(scan, paper, target=500.0, limits=_lim(), market={"vix": 14})
    assert out["executable"][0]["signal"]["symbol"] == "QQQ"
    assert out["halted"] is False and out["decision"]["day_thesis"] == "t"
    assert out["day_pnl"] == 0.0


def test_run_cycle_defensive_on_explosion(monkeypatch):
    monkeypatch.setattr("services.driver_svc.decider.decide",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    out = compute.run_cycle({"signals_0dte": [], "signals_swing": []},
                            {"positions": []}, target=500.0, limits=_lim(), market={})
    assert out["executable"] == [] and out["decision"]["stand_down"] is True
    # The shape is always renderable (handler reads these keys unconditionally).
    for k in ("executable", "rejected", "halted", "halt_reason", "day_pnl",
              "open_positions", "decision"):
        assert k in out


def test_run_cycle_model_never_sees_menu_by_id(monkeypatch):
    """The non-JSON ``menu_by_id`` (raw signals) is stripped before the model call."""
    seen = {}
    scan = {"signals_0dte": [{"symbol": "QQQ", "type": "PCS", "max_loss": 2,
                              "composite_score": 80}], "signals_swing": []}

    def _spy(packet, **kw):
        seen["packet"] = packet
        return {"stand_down": True, "trades": []}

    monkeypatch.setattr("services.driver_svc.decider.decide", _spy)
    compute.run_cycle(scan, {"snapshot": {"session_pnl": 0.0}, "positions": []},
                      target=500.0, limits=_lim(), market={})
    assert "menu" in seen["packet"]               # the model still gets the menu
    assert "menu_by_id" not in seen["packet"]     # but NOT the raw-signal mapping


def test_run_cycle_halt_blocks_execution(monkeypatch):
    """A banked-day P&L halts: nothing executes even if the model proposes a trade."""
    scan = {"signals_0dte": [{"symbol": "QQQ", "type": "PCS", "max_loss": 2,
                              "composite_score": 80}], "signals_swing": []}
    paper = {"snapshot": {"session_pnl": 600.0}, "positions": []}   # over target
    monkeypatch.setattr("services.driver_svc.decider.decide",
                        lambda packet, **kw: {"stand_down": False, "day_thesis": "x",
                            "trades": [{"id": "m0", "quantity": 1}]})
    out = compute.run_cycle(scan, paper, target=500.0, limits=_lim(), market={"vix": 14})
    assert out["halted"] is True and out["executable"] == []
    assert out["halt_reason"] and "target" in out["halt_reason"].lower()


def test_run_cycle_clamps_quantity(monkeypatch):
    """The model's quantity is a ceiling; the guardrails clamp to the risk budget."""
    scan = {"signals_0dte": [{"symbol": "QQQ", "type": "PCS", "max_loss": 2,
                              "composite_score": 80}], "signals_swing": []}
    paper = {"snapshot": {"session_pnl": 0.0}, "positions": []}
    monkeypatch.setattr("services.driver_svc.decider.decide",
                        lambda packet, **kw: {"stand_down": False, "day_thesis": "x",
                            "trades": [{"id": "m0", "quantity": 99}]})
    out = compute.run_cycle(scan, paper, target=500.0, limits=_lim(), market={"vix": 14})
    # per_trade_max_risk 300 / per-contract $200 (max_loss 2 * 100) -> floor = 1.
    assert out["executable"][0]["qty"] == 1


def test_run_cycle_passes_vix_to_guardrails(monkeypatch):
    """A high VIX in the market context halts new entries via the guardrails."""
    scan = {"signals_0dte": [{"symbol": "QQQ", "type": "PCS", "max_loss": 2,
                              "composite_score": 80}], "signals_swing": []}
    monkeypatch.setattr("services.driver_svc.decider.decide",
                        lambda packet, **kw: {"stand_down": False, "day_thesis": "x",
                            "trades": [{"id": "m0", "quantity": 1}]})
    out = compute.run_cycle(scan, {"snapshot": {"session_pnl": 0.0}, "positions": []},
                            target=500.0, limits=_lim(), market={"vix": 30.0})
    assert out["halted"] is True and "vix" in out["halt_reason"].lower()


def test_run_cycle_uses_config_daily_max_loss(monkeypatch):
    """The daily loss cap is sourced from the legacy config, not hardcoded 250.

    We patch compute._daily_max_loss to a small value and confirm the halt fires at
    that threshold (proving run_cycle threads the config value into the guardrails).
    """
    monkeypatch.setattr(compute, "_daily_max_loss", lambda: 50.0)
    scan = {"signals_0dte": [], "signals_swing": []}
    paper = {"snapshot": {"session_pnl": -60.0}, "positions": []}   # past the $50 cap
    monkeypatch.setattr("services.driver_svc.decider.decide",
                        lambda packet, **kw: {"stand_down": True, "trades": []})
    out = compute.run_cycle(scan, paper, target=500.0, limits=_lim(), market={"vix": 14})
    assert out["halted"] is True and "loss" in out["halt_reason"].lower()


# ---------------------------------------------------------------------------
# Task 4.3 — fetch_market_context
# ---------------------------------------------------------------------------
def test_fetch_market_context_defensive(monkeypatch):
    monkeypatch.setattr(compute.morning_agent, "fetch_market_conditions",
                        lambda: {"vix": 13.5, "spx_spot": 5500, "vix1d": 12})
    ctx = compute.fetch_market_context()
    assert ctx["vix"] == 13.5
    assert ctx["spx_spot"] == 5500


def test_fetch_market_context_never_raises(monkeypatch):
    monkeypatch.setattr(compute.morning_agent, "fetch_market_conditions",
                        lambda: (_ for _ in ()).throw(RuntimeError()))
    assert compute.fetch_market_context() == {}


def test_fetch_market_context_none_result_is_empty(monkeypatch):
    """A ``None`` from the fetcher degrades to {} (not a crash on dict(None))."""
    monkeypatch.setattr(compute.morning_agent, "fetch_market_conditions", lambda: None)
    assert compute.fetch_market_context() == {}

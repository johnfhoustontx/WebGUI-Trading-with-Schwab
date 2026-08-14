"""Tests for the autonomous decision cycle in ``driver_svc.compute`` (Phase 4).

Three functions:

* ``build_packet`` — projects the scanner menu (allowed-only, top-N by composite
  score, stable ids ``m0..``) + day P&L + gap-to-target into the model-facing
  packet (plus a ``menu_by_id`` mapping id → the RAW scanner signal the guardrails
  resolve back for verbatim paper execution). Pure given the cache views.
* ``run_cycle`` — ``build_packet → decider.decide → guardrails.apply_guardrails``;
  defensive (any exception → a stand-down result). Tests monkeypatch
  ``services.driver_svc.decider.decide`` so no model/network is hit.
* ``fetch_market_context`` — self-contained VIX/SPX/VIX1D fetch straight from the
  proxy (``$VIX,$SPX,$VIX1D`` via ``requests``); defensive → ``{}`` on any failure.

REAL field-name notes (verified against the engines, NOT the plan's guesses):
* the scanner signal's structure code lives in ``type`` (``"PCS"``/``"CCS"``/
  ``"IC"``); ``trade_type`` is the DTE bucket — there is NO ``structure`` key.
* the expiration field is ``expiration`` (not ``expiry``); PoP is ``pop_pct``.
* the paper snapshot's day-P&L field is ``session_pnl`` (``paper_engine.
  account_snapshot``).
"""
import json

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
# Task 3 — market_state surfaced to the decider (context ONLY; guardrails untouched)
# ---------------------------------------------------------------------------
def test_build_packet_includes_market_state_line():
    """The five-state market state (label + evidence) reaches the model-facing packet."""
    market = {"vix": 14.0, "market_state": {
        "state": "lack_of_bearishness", "label": "Lack of Bearishness",
        "evidence": ["put-skew Δ -1.2", "aggression +0.30"]}}
    pkt = compute.build_packet({}, {"snapshot": {}}, target=500.0, limits=_lim(),
                               market=market)
    blob = json.dumps(pkt, default=str)   # the decider serializes the packet this way
    assert "Lack of Bearishness" in blob
    # At least one evidence string is carried through.
    assert ("put-skew Δ -1.2" in blob) or ("aggression +0.30" in blob)


def test_build_packet_market_state_absent_no_empty_line():
    """No market_state (or a blank one) → the packet builds fine, no empty 'Market state:'."""
    for market in ({}, {"vix": 14.0}, None, {"market_state": None},
                   {"market_state": {"label": ""}}, {"market_state": "junk"}):
        pkt = compute.build_packet({}, {"snapshot": {}}, target=500.0, limits=_lim(),
                                   market=market)
        assert "Market state:" not in json.dumps(pkt, default=str)


def test_market_state_is_context_only_not_a_filter():
    """market_state must NOT change the menu / allowed signals — it is decider context,
    not a new hard rule (the guardrails path stays unchanged)."""
    scan = {"signals_0dte": [{"symbol": "QQQ", "type": "PCS", "max_loss": 200.0,
                              "composite_score": 60, "credit": 55,
                              "expiration": "2026-06-24"}],
            "signals_swing": []}
    base = compute.build_packet(scan, {"snapshot": {}}, target=500.0, limits=_lim(),
                                market={})
    with_ms = compute.build_packet(scan, {"snapshot": {}}, target=500.0, limits=_lim(),
                                   market={"market_state": {"label": "Bearish",
                                           "state": "bearish", "evidence": ["x"]}})
    assert with_ms["menu"] == base["menu"]
    assert with_ms["menu_by_id"] == base["menu_by_id"]


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
# Task 4.3 — fetch_market_context (self-contained: fetches $VIX,$SPX,$VIX1D
# straight from the proxy; morning_agent is no longer imported)
# ---------------------------------------------------------------------------
class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_fetch_market_context_parses_index_quotes(monkeypatch):
    """Index quotes nest their values under a 'quote' sub-key — _index_price
    handles both the nested (index) and flat (ETF) shapes."""
    payload = {
        "$VIX": {"assetMainType": "INDEX", "quote": {"lastPrice": 13.5}},
        "$SPX": {"assetMainType": "INDEX", "quote": {"lastPrice": 5500.0}},
        "$VIX1D": {"quote": {"lastPrice": 12.0}},
    }
    monkeypatch.setattr(compute.requests, "get", lambda *a, **k: _Resp(payload))
    ctx = compute.fetch_market_context()
    assert ctx["vix"] == 13.5
    assert ctx["spx_spot"] == 5500.0
    assert ctx["vix1d"] == 12.0


def test_fetch_market_context_missing_symbol_is_none(monkeypatch):
    """A symbol absent from the response degrades to None (never a crash)."""
    monkeypatch.setattr(compute.requests, "get",
                        lambda *a, **k: _Resp({"$VIX": {"quote": {"lastPrice": 20.0}}}))
    ctx = compute.fetch_market_context()
    assert ctx["vix"] == 20.0
    assert ctx["spx_spot"] is None and ctx["vix1d"] is None


def test_fetch_market_context_never_raises(monkeypatch):
    """A proxy failure (requests.get raises) degrades to {} — never raises."""
    def _boom(*a, **k):
        raise RuntimeError("proxy down")

    monkeypatch.setattr(compute.requests, "get", _boom)
    assert compute.fetch_market_context() == {}


def test_fetch_market_context_non_200_is_empty(monkeypatch):
    """A non-200 (raise_for_status raises) degrades to {} rather than crashing."""
    class _Err(_Resp):
        def raise_for_status(self):
            raise RuntimeError("500 Server Error")

    monkeypatch.setattr(compute.requests, "get", lambda *a, **k: _Err({}))
    assert compute.fetch_market_context() == {}


def test_fetch_market_context_includes_etf_spots(monkeypatch):
    """SPY/QQQ are ETFs (flat quote shape) — their spot rides along for the market read."""
    payload = {
        "$VIX": {"quote": {"lastPrice": 13.5}},
        "$SPX": {"quote": {"lastPrice": 5500.0}},
        "$VIX1D": {"quote": {"lastPrice": 12.0}},
        "SPY": {"assetMainType": "EQUITY", "lastPrice": 598.2},   # flat (ETF) shape
        "QQQ": {"assetMainType": "EQUITY", "lastPrice": 521.4},
    }
    monkeypatch.setattr(compute.requests, "get", lambda *a, **k: _Resp(payload))
    ctx = compute.fetch_market_context()
    assert ctx["spy_spot"] == 598.2 and ctx["qqq_spot"] == 521.4
    assert ctx["vix"] == 13.5 and ctx["spx_spot"] == 5500.0    # unchanged


def test_fetch_market_context_missing_etf_spot_is_none(monkeypatch):
    """A symbol absent from the response degrades to None (never a crash)."""
    monkeypatch.setattr(compute.requests, "get",
                        lambda *a, **k: _Resp({"$VIX": {"quote": {"lastPrice": 20.0}}}))
    ctx = compute.fetch_market_context()
    assert ctx["spy_spot"] is None and ctx["qqq_spot"] is None


# ---------------------------------------------------------------------------
# market-read: _dashboard_risk_read (breadth spread + risk-on/off aggregate)
# ---------------------------------------------------------------------------
def test_dashboard_risk_read_breadth_and_risk():
    dash = {"categories": [
        {"category": "Breadth", "tiles": [
            {"display": "$ADVN-$DECN", "last": -465.0, "color_state": "risk_off_mild"},
            {"display": "VIX", "color_state": "risk_off_strong"}]},
        {"category": "Index", "tiles": [
            {"display": "SPX", "color_state": "risk_off_mild"}]}]}
    out = compute._dashboard_risk_read(dash)
    assert out["breadth_spread"] == -465.0
    assert out["risk"] == "risk_off"        # net color_state tilt is negative


def test_dashboard_risk_read_risk_on_and_missing_breadth():
    dash = {"categories": [{"category": "X", "tiles": [
        {"display": "SPY", "color_state": "risk_on_strong"},
        {"display": "XLK", "color_state": "risk_on_mild"}]}]}
    out = compute._dashboard_risk_read(dash)
    assert out["risk"] == "risk_on" and "breadth_spread" not in out


def test_dashboard_risk_read_empty_is_empty():
    for d in ({}, None, {"categories": []}, {"categories": [{"tiles": []}]}, "junk"):
        assert compute._dashboard_risk_read(d) == {}


# ---------------------------------------------------------------------------
# market-read: _pick_latest_briefing (freshest TODAY gamma briefing)
# ---------------------------------------------------------------------------
import datetime as _dt   # noqa: E402


def _brief(slot, gen, bias=-20):
    return {"slot": slot, "generated_at": gen,
            "analysis": {"bias": bias, "regime": "neg gamma",
                         "indices": [{"symbol": "$SPX", "gamma_flip": 6005}]}}


def test_pick_latest_briefing_newest_today():
    today = _dt.date(2026, 7, 8)
    payloads = [_brief("open", "2026-07-08T08:48:00-05:00", bias=-10),
                _brief("midday", "2026-07-08T11:30:00-05:00", bias=-35)]
    out = compute._pick_latest_briefing(payloads, today)
    assert out["bias"] == -35 and out["_slot"] == "midday"      # newest wins
    assert out["_generated_at"].startswith("2026-07-08T11:30")


def test_pick_latest_briefing_drops_prior_day():
    today = _dt.date(2026, 7, 8)
    out = compute._pick_latest_briefing(
        [_brief("close", "2026-07-07T14:58:00-05:00")], today)   # yesterday only
    assert out is None                                           # stale gamma dropped


def test_pick_latest_briefing_skips_no_analysis_and_junk():
    today = _dt.date(2026, 7, 8)
    payloads = [None, "junk", {"slot": "open", "generated_at": "2026-07-08T08:48:00-05:00",
                               "analysis": None},               # degraded page → skip
                _brief("midday", "2026-07-08T11:30:00-05:00")]
    out = compute._pick_latest_briefing(payloads, today)
    assert out and out["_slot"] == "midday"


def test_pick_latest_briefing_empty_is_none():
    assert compute._pick_latest_briefing([], _dt.date(2026, 7, 8)) is None


# ---------------------------------------------------------------------------
# market-read: _market_read assembly + build_packet wiring
# ---------------------------------------------------------------------------
def _market_ctx():
    return {
        "vix": 15.0, "spx_spot": 5980.0, "spy_spot": 598.0, "qqq_spot": 521.0,
        "briefing": {"_slot": "midday", "_generated_at": "2026-07-08T12:30:00-05:00",
            "regime": "negative gamma below flip", "bias": -35, "bias_label": "bearish",
            "headline": "Dealers short gamma.", "indices": [
                {"symbol": "$SPX", "spot": 5975, "gamma_flip": 6005, "put_wall": 5900,
                 "call_wall": 6050, "max_pain": 5975, "expected_move": 46, "pc_ratio": 1.3,
                 "what_if": {"rally": "r", "selloff": "s", "chop": "c"}},
                {"symbol": "SPY", "gamma_flip": 600, "put_wall": 590, "call_wall": 605},
                {"symbol": "QQQ", "gamma_flip": 523, "put_wall": 515, "call_wall": 528}]},
        "dashboard": {"categories": [{"category": "B", "tiles": [
            {"display": "$ADVN-$DECN", "last": -620.0, "color_state": "risk_off_mild"},
            {"display": "VIX", "color_state": "risk_off_strong"}]}]},
        "sentiment": {"score": 4.1, "bias": "bearish"}}


def test_market_read_full_assembly():
    mr = compute._market_read(_market_ctx())
    assert mr["regime"] == "negative gamma below flip" and mr["bias"] == -35
    assert mr["breadth_spread"] == -620.0 and mr["risk"] == "risk_off"
    assert mr["sentiment_score"] == 4.1 and mr["sentiment_bias"] == "bearish"
    spx = next(i for i in mr["indices"] if i["symbol"] == "$SPX")
    assert spx["spot"] == 5980.0                       # LIVE spot overrides briefing 5975
    assert spx["flip"] == 6005 and spx["put_wall"] == 5900
    assert spx["posture"] == "below flip (negative gamma)"
    assert "midday" in mr["as_of"] and "12:30" in mr["as_of"]
    assert mr["summary"]                               # one-line summary present


def test_market_read_posture_above_flip_uses_briefing_spot_when_no_live():
    ctx = {"briefing": {"indices": [
        {"symbol": "$SPX", "spot": 6100, "gamma_flip": 6005}]}}   # no live spx_spot
    mr = compute._market_read(ctx)
    spx = mr["indices"][0]
    assert spx["spot"] == 6100                          # falls back to briefing spot
    assert spx["posture"] == "above flip (positive gamma)"


def test_market_read_degrades_partial():
    # No briefing → no gamma lines, but breadth + sentiment still present.
    mr = compute._market_read({"dashboard": _market_ctx()["dashboard"],
                               "sentiment": {"score": 6.0, "bias": "bullish"}})
    assert "indices" not in mr and "regime" not in mr
    assert mr["breadth_spread"] == -620.0 and mr["sentiment_score"] == 6.0


def test_market_read_all_absent_is_empty():
    for m in ({}, None, {"vix": 15.0}, {"briefing": None, "dashboard": None}):
        assert compute._market_read(m) == {}


# --- structural market regime (cache:sentiment:regime) — CONTEXT ONLY ---------
def _regime_payload(**over):
    p = {"label": "Trending", "committed_label": "trending", "confidence": 0.62,
         "unclear": False,
         "memberships": {"trending": 0.52, "mean_reversion": 0.28, "breakout": 0.08,
                         "choppy": 0.09, "crisis": 0.03},
         "transition": {"from": "mean_reversion", "to": "trending", "progress": 0.6}}
    p.update(over)
    return p


def test_market_read_carries_market_regime():
    """The blended structural regime rides as its OWN key — `regime` is already
    the gamma briefing's regime string, so it must not be clobbered."""
    ctx = dict(_market_ctx(), regime=_regime_payload())
    mr = compute._market_read(ctx)
    assert mr["regime"] == "negative gamma below flip"      # gamma regime intact
    st = mr["market_regime"]
    assert st["label"] == "Trending" and st["confidence"] == 0.62
    # top two memberships, strongest first, as (name, weight) pairs
    # Membership keys are shown to the decider as DISPLAY labels (the internal
    # "crisis" key would read "Stressed"), consistent with the rest of the app.
    assert st["top"][0] == ("Trending", 0.52) and st["top"][1] == ("Balanced", 0.28)
    assert st["transition"] == "Balanced -> Trending 60%"
    assert "Trending" in mr["summary"]                      # surfaced on the log line


def test_market_read_regime_absent_when_no_regime():
    """No regime cache -> no key at all (packet byte-identical to before)."""
    assert "market_regime" not in compute._market_read(_market_ctx())
    assert "market_regime" not in compute._market_read(dict(_market_ctx(), regime=None))


def test_market_read_regime_unclear_and_no_transition():
    mr = compute._market_read(dict(_market_ctx(), regime=_regime_payload(
        label="Unclear", committed_label="", unclear=True, confidence=0.11,
        transition=None)))
    st = mr["market_regime"]
    assert st["label"] == "Unclear" and st["unclear"] is True
    assert st.get("transition") is None            # stable/unknown -> omitted


def test_market_read_regime_survives_junk_payload():
    for junk in ({"memberships": "nope"}, {"label": None}, {"memberships": {}}):
        mr = compute._market_read(dict(_market_ctx(), regime=junk))
        assert mr["regime"] == "negative gamma below flip"   # never breaks the read


def test_guardrails_never_sees_the_market_regime():
    """CONTEXT ONLY: the market regime must not reach guardrails.

    (NB: guardrails uses "structure" for the SPREAD structure PCS/CCS/IC — an
    unrelated concept, which is why the regime key is named `market_regime`.)"""
    import inspect

    from services.driver_svc import guardrails
    src = inspect.getsource(guardrails)
    assert "market_regime" not in src


def test_build_packet_includes_market_read():
    pkt = compute.build_packet({}, {"snapshot": {}}, target=500.0, limits=_lim(),
                               market=_market_ctx())
    assert "market_read" in pkt
    blob = json.dumps(pkt, default=str)
    assert "put_wall" in blob and "negative gamma below flip" in blob


def test_build_packet_market_read_absent_backcompat():
    """No market-read sources → NO market_read key (byte-identical to today)."""
    pkt = compute.build_packet({}, {"snapshot": {}}, target=500.0, limits=_lim(),
                               market={"vix": 14.0})
    assert "market_read" not in pkt


def test_market_read_is_context_only_not_a_filter():
    scan = {"signals_0dte": [{"symbol": "QQQ", "type": "PCS", "max_loss": 200.0,
                              "composite_score": 60, "expiration": "2026-06-24"}],
            "signals_swing": []}
    base = compute.build_packet(scan, {"snapshot": {}}, target=500.0, limits=_lim(), market={})
    withmr = compute.build_packet(scan, {"snapshot": {}}, target=500.0, limits=_lim(),
                                  market=_market_ctx())
    assert withmr["menu"] == base["menu"] and withmr["menu_by_id"] == base["menu_by_id"]


def test_run_cycle_returns_market_read(monkeypatch):
    """run_cycle threads the packet's market_read out so the handler can surface a
    summary on the decision log (observability)."""
    monkeypatch.setattr("services.driver_svc.decider.decide",
                        lambda packet, **kw: {"stand_down": True, "trades": []})
    out = compute.run_cycle({}, {"snapshot": {}}, target=500.0, limits=_lim(),
                            market=_market_ctx())
    assert out["market_read"]["risk"] == "risk_off"
    assert out["market_read"]["summary"]


def test_run_cycle_market_read_none_when_absent(monkeypatch):
    """No market-read sources → run_cycle returns market_read=None (back-compat)."""
    monkeypatch.setattr("services.driver_svc.decider.decide",
                        lambda packet, **kw: {"stand_down": True, "trades": []})
    out = compute.run_cycle({}, {"snapshot": {}}, target=500.0, limits=_lim(),
                            market={"vix": 14})
    assert out.get("market_read") is None


# ── directional gate: _directional_posture + market_read change_pct + run_cycle ─
def test_directional_posture_up_down_neutral():
    up = {"breadth_spread": 500, "indices": [
        {"symbol": "$SPX", "change_pct": 0.6}, {"symbol": "QQQ", "change_pct": 0.4}]}
    down = {"breadth_spread": -500, "indices": [
        {"symbol": "$SPX", "change_pct": -0.6}, {"symbol": "QQQ", "change_pct": -0.4}]}
    mixed = {"breadth_spread": 500, "indices": [   # index vs breadth disagree
        {"symbol": "$SPX", "change_pct": -0.6}, {"symbol": "QQQ", "change_pct": 0.4}]}
    assert compute._directional_posture(up) == "up"
    assert compute._directional_posture(down) == "down"
    assert compute._directional_posture(mixed) == "neutral"
    for bad in (None, {}, {"breadth_spread": 500}, "junk"):
        assert compute._directional_posture(bad) == "neutral"


# an input `market` (pre-market_read) that build_packet turns into an UP posture:
def _up_market():
    return {"vix": 14, "spx_spot": 5980.0, "qqq_spot": 521.0,
            "briefing": {"_slot": "midday", "_generated_at": "2026-07-09T12:30:00-05:00",
                "indices": [{"symbol": "$SPX", "gamma_flip": 6005},
                            {"symbol": "QQQ", "gamma_flip": 523}]},
            "dashboard": {"categories": [{"category": "B", "tiles": [
                {"display": "$ADVN-$DECN", "last": 500.0, "color_state": "risk_on_mild"},
                {"display": "SPX", "change_pct": 0.6, "color_state": "risk_on_mild"},
                {"display": "QQQ", "change_pct": 0.5, "color_state": "risk_on_mild"}]}]}}


def test_market_read_carries_index_change_pct():
    """_market_read enriches each index with change_pct from the dashboard tile."""
    mr = compute._market_read(_up_market())
    spx = next(i for i in mr["indices"] if i["symbol"] == "$SPX")
    assert spx["change_pct"] == 0.6


def _ccs_scan():
    return {"signals_0dte": [{"symbol": "SPY", "type": "CCS", "max_loss": 2.0,
                              "composite_score": 80, "expiration": "2026-07-13"}],
            "signals_swing": []}


def test_run_cycle_gate_inert_when_flag_off(monkeypatch):
    """Flag OFF (default) → posture forced neutral → a CCS in an up tape still executes."""
    monkeypatch.setattr("services.driver_svc.decider.decide",
        lambda p, **k: {"stand_down": False, "trades": [{"id": "m0", "quantity": 1}]})
    out = compute.run_cycle(_ccs_scan(), {"snapshot": {}}, target=500.0, limits=_lim(),
                            market=_up_market())
    assert len(out["executable"]) == 1                       # gate inert


def test_run_cycle_shadow_gate_records_would_block_when_flag_off(monkeypatch):
    """Flag OFF → the CCS fires, but the shadow gate records it as would-have-blocked
    at the decisive up posture, so evidence accrues before the gate is enabled."""
    monkeypatch.setattr("services.driver_svc.decider.decide",
        lambda p, **k: {"stand_down": False, "trades": [{"id": "m0", "quantity": 1}]})
    out = compute.run_cycle(_ccs_scan(), {"snapshot": {}}, target=500.0, limits=_lim(),
                            market=_up_market())
    sg = out["shadow_gate"]
    assert sg["enabled"] is False and sg["posture"] == "up" and sg["n"] == 1
    assert sg["would_block"][0]["structure"] == "CCS"


def test_run_cycle_shadow_gate_empty_when_flag_on(monkeypatch):
    """Flag ON → the wrong-side CCS is rejected outright, so the shadow (over the trades
    that fired) is empty — enabled True with no would-block."""
    monkeypatch.setattr("services.driver_svc.settings.DIRECTIONAL_GATE_ENABLED", True)
    monkeypatch.setattr("services.driver_svc.decider.decide",
        lambda p, **k: {"stand_down": False, "trades": [{"id": "m0", "quantity": 1}]})
    out = compute.run_cycle(_ccs_scan(), {"snapshot": {}}, target=500.0, limits=_lim(),
                            market=_up_market())
    assert out["executable"] == [] and out["shadow_gate"]["enabled"] is True
    assert out["shadow_gate"]["n"] == 0


def test_run_cycle_gate_blocks_wrong_side_when_flag_on(monkeypatch):
    monkeypatch.setattr("services.driver_svc.settings.DIRECTIONAL_GATE_ENABLED", True)
    monkeypatch.setattr("services.driver_svc.decider.decide",
        lambda p, **k: {"stand_down": False, "trades": [{"id": "m0", "quantity": 1}]})
    out = compute.run_cycle(_ccs_scan(), {"snapshot": {}}, target=500.0, limits=_lim(),
                            market=_up_market())
    assert out["executable"] == []
    assert out["rejected"][0]["reason"]                      # blocked wrong-side


def test_run_cycle_blocks_symbol_already_open(monkeypatch):
    """run_cycle derives open_symbols from the driver book and blocks a dup-symbol trade."""
    monkeypatch.setattr("services.driver_svc.decider.decide",
        lambda p, **k: {"stand_down": False, "trades": [{"id": "m0", "quantity": 1}]})
    scan = {"signals_0dte": [{"symbol": "$SPX", "type": "PCS", "max_loss": 2.0,
                              "composite_score": 80}], "signals_swing": []}
    paper = {"snapshot": {"session_pnl": 0.0},
             "positions": [{"symbol": "$SPX", "source": "driver"}]}
    out = compute.run_cycle(scan, paper, target=500.0, limits=_lim(), market={})
    assert out["executable"] == []
    assert out["rejected"][0]["reason"]                      # symbol already open


# ---------------------------------------------------------------------------
# Task 4 — the directional list is invisible to the driver
# ---------------------------------------------------------------------------
def test_build_packet_ignores_signals_directional():
    """The driver must never be offered a single-leg directional trade.

    The scanner builds single-leg directional candidates (LONG_CALL/LONG_PUT/
    SHORT_CALL/SHORT_PUT) into a ``signals_directional`` list on the SAME
    ``cache:options:scan`` view the autonomous driver reads. Naked shorts are
    UNDEFINED RISK (a naked short call has theoretically unlimited loss) and long
    options are not the driver's mandate, so none may reach the model's menu.

    ``build_packet`` merges ``signals_0dte + signals_swing`` only — a third list is
    invisible to it by construction. This pins that. The directional signal is given
    a HIGHER composite_score than the PCS so the score-desc sort + MENU_TOP_N cap
    cannot be what excludes it: only the design can.
    """
    pcs = {"symbol": "QQQ", "type": "PCS", "trade_type": "0-DTE", "max_loss": 2.0,
           "credit": 60, "pop_pct": 85.0, "composite_score": 70,
           "expiration": "2026-07-17"}
    long_call = {"symbol": "NVDA", "type": "LONG_CALL", "max_loss": 5.0,
                 "composite_score": 99, "expiration": "2026-07-17"}
    naked_call = {"symbol": "TSLA", "type": "SHORT_CALL", "max_loss": 8.0,
                  "composite_score": 98, "expiration": "2026-07-17"}
    scan = {"signals_0dte": [pcs], "signals_swing": [],
            "signals_directional": [long_call, naked_call]}

    pkt = compute.build_packet(scan, {"snapshot": {}, "positions": []},
                               target=500.0, limits=_lim(), market={})

    # The credit spread IS offered...
    assert [m["symbol"] for m in pkt["menu"]] == ["QQQ"]
    assert pkt["menu"][0]["structure"] == "PCS"
    # ...and NEITHER directional signal reached the menu or the raw execution map,
    # despite outscoring it (99/98 > 70).
    structures = {m["structure"] for m in pkt["menu"]}
    assert "LONG_CALL" not in structures and "SHORT_CALL" not in structures
    assert long_call not in pkt["menu_by_id"].values()
    assert naked_call not in pkt["menu_by_id"].values()
    assert list(pkt["menu_by_id"].values()) == [pcs]


def test_build_packet_never_reads_signals_directional_key():
    """``build_packet`` merges ``signals_0dte + signals_swing`` ONLY — it never reads
    a third list, whatever is in it.

    This isolates the MERGE from the allowlist. The sibling test above plants REALISTIC
    directional signals and asserts the outcome, but it cannot distinguish the two
    defenses: ``is_allowed`` would reject a LONG_CALL even if the merge DID read the
    list, so that test survives a merge regression. Here the probe is a PCS — a
    structure the allowlist ACCEPTS — parked in ``signals_directional``. The allowlist
    cannot exclude it, so if it reaches the menu the merge read a list it must not.

    (A PCS in ``signals_directional`` is not realistic scanner output; it is a probe
    chosen precisely because only the property under test can reject it.)
    """
    probe = {"symbol": "PROBE", "type": "PCS", "max_loss": 2.0, "credit": 60,
             "composite_score": 99, "expiration": "2026-07-17"}
    scan = {"signals_0dte": [], "signals_swing": [], "signals_directional": [probe]}

    pkt = compute.build_packet(scan, {"snapshot": {}, "positions": []},
                               target=500.0, limits=_lim(), market={})

    assert pkt["menu"] == []
    assert pkt["menu_by_id"] == {}


def test_market_read_regime_labels_carry_the_direction():
    """A committed direction rewords the membership mix for the decider — the
    book's known failure mode was selling call spreads into a rising tape, so
    "Rallying 52%" is materially more actionable than "Trending 52%"."""
    payload = dict(_regime_payload(), direction=1, direction_strong=True)
    st = compute._market_read(dict(_market_ctx(), regime=payload))["market_regime"]
    assert st["top"][0] == ("Rallying", 0.52)
    assert st["transition"] == "Balanced -> Rallying 60%"


def test_market_read_regime_labels_neutral_without_a_direction():
    payload = dict(_regime_payload(), direction=0, direction_strong=False)
    st = compute._market_read(dict(_market_ctx(), regime=payload))["market_regime"]
    assert st["top"][0] == ("Trending", 0.52)


def test_market_read_regime_direction_junk_is_neutral():
    payload = dict(_regime_payload(), direction="up", direction_strong="yes")
    st = compute._market_read(dict(_market_ctx(), regime=payload))["market_regime"]
    assert st["top"][0] == ("Trending", 0.52)

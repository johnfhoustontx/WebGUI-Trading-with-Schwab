"""Tests for the rescue integration layer in compute (Task 6.1).

``_make_leg_pricer`` / ``compute_rescue`` / ``assess_open_positions`` wire the
pure ``rescue`` engine to the live data path (option chains, signal_repricer,
gamma_snapshot, paper_account_db). The heavy deps are monkeypatched so these
tests never touch a proxy or the paper DB — mirroring the patterns in
``test_compute.py`` (monkeypatch the engine names / ``_proxy``).
"""
from services.options_svc import compute, rescue
from shared.contracts.options import RescueAdvisory


def _pos(**kw):
    base = dict(position_id=7, symbol="SPY", strategy="PCS",
                short_strike=500.0, long_strike=495.0, call_short=None,
                call_long=None, width=5.0, expiration="2099-07-31",
                entry_credit=1.00, quantity=2, max_loss_total=800.0,
                status="OPEN", current_value=2.50, unrealized_pnl=-300.0,
                current_short_delta=0.35)
    base.update(kw)
    return base


def _mark(**kw):
    base = dict(current_value=2.50, unrealized_pnl=-300.0,
                current_underlying=501.0, current_short_delta=0.35,
                error=None)
    base.update(kw)
    return base


# ── _make_leg_pricer ─────────────────────────────────────────────────────────

def _fake_chain(strike, bid, ask, right="PUT", expiry="2099-07-31"):
    map_key = "putExpDateMap" if right == "PUT" else "callExpDateMap"
    return {
        map_key: {
            f"{expiry}:30": {
                f"{strike:.1f}": [{"bid": bid, "ask": ask}],
            }
        }
    }


def test_make_leg_pricer_returns_mid(monkeypatch):
    chain = _fake_chain(495.0, 1.00, 1.50)
    monkeypatch.setattr(compute, "_fetch_chain_for_expiry",
                        lambda symbol, expiry: chain)
    price_leg = compute._make_leg_pricer("SPY")
    assert price_leg("SPY", "2099-07-31", "PUT", 495.0) == 1.25


def test_make_leg_pricer_none_on_missing(monkeypatch):
    monkeypatch.setattr(compute, "_fetch_chain_for_expiry",
                        lambda symbol, expiry: _fake_chain(495.0, 1.0, 1.5))
    price_leg = compute._make_leg_pricer("SPY")
    # strike not in chain -> None
    assert price_leg("SPY", "2099-07-31", "PUT", 999.0) is None


def test_make_leg_pricer_none_on_chain_fail(monkeypatch):
    monkeypatch.setattr(compute, "_fetch_chain_for_expiry",
                        lambda symbol, expiry: None)
    price_leg = compute._make_leg_pricer("SPY")
    assert price_leg("SPY", "2099-07-31", "PUT", 495.0) is None


def test_make_leg_pricer_caches_per_expiry(monkeypatch):
    calls = {"n": 0}

    def _fetch(symbol, expiry):
        calls["n"] += 1
        return _fake_chain(495.0, 1.0, 1.5)

    monkeypatch.setattr(compute, "_fetch_chain_for_expiry", _fetch)
    price_leg = compute._make_leg_pricer("SPY")
    price_leg("SPY", "2099-07-31", "PUT", 495.0)
    price_leg("SPY", "2099-07-31", "PUT", 495.0)
    assert calls["n"] == 1  # cached per expiry


# ── compute_rescue ───────────────────────────────────────────────────────────

def _patch_happy(monkeypatch, position=None, reprice=None):
    position = position if position is not None else _pos()
    reprice = reprice if reprice is not None else {
        "current_value": 2.50, "unrealized_pnl": -300.0,
        "current_underlying": 501.0, "current_short_delta": 0.35,
        "error": None,
    }
    monkeypatch.setattr(compute, "_load_position", lambda pid: position)
    monkeypatch.setattr(compute, "reprice_swing", lambda trade, client, today=None: reprice)
    monkeypatch.setattr(compute, "gamma_snapshot",
                        lambda symbol: {"spot": 501.0, "views": {
                            "GEX": {"flip": 505.0, "walls": [490.0, 510.0]}}})
    monkeypatch.setattr(compute, "_rescue_regime", lambda: None)
    monkeypatch.setattr(compute, "_make_leg_pricer", lambda symbol: (lambda *a, **k: 1.0))


def test_compute_rescue_happy_path(monkeypatch):
    _patch_happy(monkeypatch)
    result = compute.compute_rescue(7)
    assert result.get("error") is None
    # validates as the contract
    adv = RescueAdvisory(**result)
    assert adv.position_id == 7
    assert adv.symbol == "SPY"
    assert adv.state in ("ok", "watch", "tested", "critical")
    assert isinstance(adv.heat, float)
    assert adv.candidates, "expected non-empty candidate list"
    assert any(c.action == "close" for c in adv.candidates)


def test_compute_rescue_position_not_found(monkeypatch):
    monkeypatch.setattr(compute, "_load_position", lambda pid: None)
    result = compute.compute_rescue(999)
    assert "error" in result and result["error"]


def test_compute_rescue_defensive_on_reprice_raise(monkeypatch):
    monkeypatch.setattr(compute, "_load_position", lambda pid: _pos())

    def _boom(*a, **k):
        raise RuntimeError("proxy down")

    monkeypatch.setattr(compute, "reprice_swing", _boom)
    monkeypatch.setattr(compute, "gamma_snapshot", lambda symbol: None)
    monkeypatch.setattr(compute, "_rescue_regime", lambda: None)
    monkeypatch.setattr(compute, "_make_leg_pricer", lambda symbol: (lambda *a, **k: None))
    result = compute.compute_rescue(7)
    # Must not raise; falls back to stored marks OR returns an error dict.
    assert isinstance(result, dict)
    # if it produced an advisory it must validate; either way it never raises
    if result.get("error") is None:
        RescueAdvisory(**result)


def test_compute_rescue_gamma_none_ok(monkeypatch):
    _patch_happy(monkeypatch)
    monkeypatch.setattr(compute, "gamma_snapshot", lambda symbol: None)
    result = compute.compute_rescue(7)
    assert result.get("error") is None
    RescueAdvisory(**result)


def test_compute_rescue_drops_one_bad_candidate_not_whole_advisory(monkeypatch):
    # one valid candidate + one invalid (None leg price) -> advisory still
    # returns the valid one (per-candidate construction = defense in depth).
    _patch_happy(monkeypatch)

    def _candidates(*a, **k):
        return [
            {"action": "close", "label": "Close", "apply_kind": "execute",
             "gross_cash": -100.0, "commission": 1.3, "net_cash": -101.3},
            {"action": "roll_down", "label": "Roll", "apply_kind": "execute",
             "gross_cash": 0.0, "commission": 2.6, "net_cash": -2.6,
             "est_fill_legs": [{"side": "BUY", "right": "PUT",
                                "strike": 500.0, "price": None}]},
        ]

    monkeypatch.setattr(compute, "_rescue_candidates", _candidates)
    result = compute.compute_rescue(7)
    assert "error" not in result or result.get("candidates")
    actions = [c["action"] for c in result["candidates"]]
    assert "close" in actions and "roll_down" not in actions


# ── assess_open_positions ────────────────────────────────────────────────────

def test_assess_open_positions_counts(monkeypatch):
    benign = _pos(position_id=1, current_underlying=520.0,
                  current_short_delta=0.10, unrealized_pnl=50.0,
                  current_value=0.5)
    tested = _pos(position_id=2, current_underlying=500.5,
                  current_short_delta=0.40, unrealized_pnl=-300.0,
                  current_value=2.5)
    monkeypatch.setattr(compute, "_load_open_positions",
                        lambda: [benign, tested])
    out = compute.assess_open_positions()
    assert set(out["per_position"].keys()) == {1, 2}
    summ = out["summary"]
    assert summ["n_tested"] >= 1
    assert 2 in summ["position_ids"]
    # each per-position entry has state + heat
    for pid, info in out["per_position"].items():
        assert "state" in info and "heat" in info


def test_assess_open_positions_defensive(monkeypatch):
    def _boom():
        raise RuntimeError("db gone")

    monkeypatch.setattr(compute, "_load_open_positions", _boom)
    out = compute.assess_open_positions()
    assert out["summary"]["n_tested"] == 0
    assert out["per_position"] == {}


def test_assess_open_positions_no_chain_fetch(monkeypatch):
    """assess_open_positions must NOT fetch chains / gamma (cheap pass)."""
    called = {"chain": False, "gamma": False}
    monkeypatch.setattr(compute, "_fetch_chain_for_expiry",
                        lambda *a, **k: called.__setitem__("chain", True))
    monkeypatch.setattr(compute, "gamma_snapshot",
                        lambda *a, **k: called.__setitem__("gamma", True))
    monkeypatch.setattr(compute, "_load_open_positions", lambda: [_pos()])
    compute.assess_open_positions()
    assert called == {"chain": False, "gamma": False}

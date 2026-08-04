"""Run from the repo root with the repo venv:
    .venv\\Scripts\\python -m pytest services\\trade_svc\\tests\\test_deepdive.py -v
(never `pytest services` over all services — cross-app module-name collisions.)"""
import types

from services.trade_svc import compute


_FAKE_RESULT = {
    "symbol": "OKLO", "quote": {"lastPrice": 12.3},
    "technicals": {"last_close": 12.3, "rvol_20d": 55.0},
    "fundamentals": {"pe_ratio": None},
    "options": {"available": False}, "ranks": {}, "takeaways": ["t1"],
}


def test_run_deep_dive_returns_html(monkeypatch):
    # Stub the engine so no proxy/DB is touched: analyze_symbol -> a canned result,
    # render_html -> a sentinel HTML string.
    from services.trade_svc.deepdive import engine
    monkeypatch.setattr(engine, "SchwabClient", lambda *a, **k: object())
    monkeypatch.setattr(engine, "analyze_symbol", lambda *a, **k: dict(_FAKE_RESULT))
    monkeypatch.setattr(engine, "render_html", lambda *a, **k: "<html>DEEP DIVE</html>")
    monkeypatch.setattr(compute, "_open_iv_conn", lambda: None)  # skip real SQLite

    res = compute.run_deep_dive("oklo")
    assert res["symbol"] == "OKLO"
    assert "DEEP DIVE" in res["html"]
    assert res["ts"]


def test_run_deep_dive_bad_symbol_returns_error_html():
    res = compute.run_deep_dive("")
    assert res["symbol"] == "?"
    assert "html" in res and res["html"]  # a friendly error page, never None/raise


def test_build_deep_dive_query_injects_digest(monkeypatch):
    from services.trade_svc.deepdive import engine
    monkeypatch.setattr(engine, "SchwabClient", lambda *a, **k: object())
    monkeypatch.setattr(engine, "analyze_symbol", lambda *a, **k: dict(_FAKE_RESULT))
    monkeypatch.setattr(compute, "_open_iv_conn", lambda: None)

    res = compute.build_deep_dive_query("OKLO")
    md = res["markdown"]
    assert res["symbol"] == "OKLO"
    assert "OKLO" in md                  # {{SYMBOL}} substituted
    assert "{{QUANT_DATA}}" not in md    # placeholder filled
    assert "<!--" not in md              # HOW-TO comment stripped

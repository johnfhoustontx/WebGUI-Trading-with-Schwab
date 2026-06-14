"""End-to-end pure pipeline: apply_tick -> evaluate -> suggest -> rows."""
from src.live import apply_tick
from src.evaluation import evaluate_portfolio
from src.suggestions import suggest
from src.thresholds import Thresholds
from src.view_model import format_performance_rows


def test_tick_flows_through_to_performance_rows():
    model = {"holdings": [{
        "symbol": "ABC", "asset_type": "EQUITY", "sector": "Technology",
        "sector_etf": "XLK", "quantity": 10, "avg_price": 100.0,
        "market_value": 1000.0, "day_pl": 0.0, "total_pl": 0.0,
        "vs_sector_rs": None, "since_purchase_excess": None,
    }], "sectors": [{"sector": "Technology", "weight": 1.0,
                     "benchmark_delta": None, "tailwind": None}]}
    baselines = {"ABC": {
        "symbol": "ABC", "entry_date": "2026-01-05", "entry_price": 100.0,
        "days_held": 100, "ann_vol": 0.20, "atr": 2.0, "peak_close": 115.0,
        "sector_ret": 0.04, "spy_ret": 0.03, "entry_pct": 0.25}}

    model = apply_tick(model, {"symbol": "ABC", "last": 110.0,
                               "net_change": 1.0})
    cards = evaluate_portfolio(model, baselines)
    pv = sum(h["market_value"] for h in model["holdings"])
    ctx = {"portfolio_value": pv, "avg_weight": 1.0, "sector_etf": "XLK",
           "top_sector": None}
    sugs = {s: suggest(c, ctx, Thresholds()) for s, c in cards.items()}
    rows = format_performance_rows(cards, sugs)

    assert rows[0]["symbol"] == "ABC"
    assert rows[0]["ann_return"] != "—"      # live tick produced a real score
    assert rows[0]["top_action"] in {"HOLD", "TRIM", "REVIEW", "SCALE_IN",
                                     "SCALE_OUT", "SET_STOP", "EXIT"}

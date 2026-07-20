"""Options Matrix Display — Tier-1 reader of cache:options:matrix.

Pure builders (columns/rows/color maps) are module-level and NiceGUI-free for testing.
render() (a later task) mounts the sortable table and version-polls.
"""
from __future__ import annotations

VIEW = "options:matrix"

_SIGNAL_CLASS = {
    "buy": "bg-emerald-600/80 text-white",
    "neutral": "bg-slate-600/40 text-slate-200",
    "sell": "bg-rose-600/80 text-white",
}
_REGIME_CLASS = {
    "above": "text-emerald-400",
    "below": "text-rose-400",
    "na": "text-slate-500",
}
_TREND_CLASS = {
    "strong_up": "text-emerald-400", "up": "text-emerald-300",
    "flat": "text-slate-400",
    "down": "text-rose-300", "strong_down": "text-rose-400",
}
_TREND_ARROW = {
    "strong_up": "▲▲", "up": "▲", "flat": "▬",
    "down": "▼", "strong_down": "▼▼",
}
_ACCEL_CLASS = {"hot": "text-emerald-400", "cool": "text-rose-400",
                "steady": "text-slate-400", "flat": "text-slate-500"}
_ACCEL_ARROW = {"hot": "▲", "cool": "▼", "steady": "▬", "flat": "·"}
_SIGNAL_LABEL = {"buy": "Buy", "neutral": "Neutral", "sell": "Sell"}


def signal_class(s):
    return _SIGNAL_CLASS.get(s, _SIGNAL_CLASS["neutral"])


def daypct_class(v):
    if not v:
        return "text-slate-400"
    return "text-emerald-400" if v > 0 else "text-rose-400"


def matrix_columns():
    spec = [
        ("symbol", "Ticker"), ("spot", "Spot"), ("day_pct", "Day %"),
        ("trend", "Trend"), ("call_accel_disp", "Call"), ("put_accel_disp", "Put"),
        ("pc_ratio", "P/C"), ("net_prem_m", "Net $M"), ("gex_regime", "GEX"),
        ("n_signals", "Sig"), ("n_alerts", "Flow"), ("signal_label", "Signal"),
        ("hotness", "Hot"),
    ]
    return [{"name": f, "label": l, "field": f, "sortable": True, "align": "left"}
            for f, l in spec]


def matrix_rows(payload):
    rows = []
    for r in (payload or {}).get("rows") or []:
        t_state = r.get("trend_state", "flat")
        rows.append({
            "symbol": r.get("symbol", ""),
            "spot": r.get("spot"),
            "day_pct": r.get("day_pct"),
            "_daypct_class": daypct_class(r.get("day_pct")),
            "trend": _TREND_ARROW.get(t_state, "▬"),
            "_trend_class": _TREND_CLASS.get(t_state, "text-slate-400"),
            "call_accel_disp": _ACCEL_ARROW.get(r.get("call_accel"), "·"),
            "_call_class": _ACCEL_CLASS.get(r.get("call_accel"), "text-slate-500"),
            "put_accel_disp": _ACCEL_ARROW.get(r.get("put_accel"), "·"),
            "_put_class": _ACCEL_CLASS.get(r.get("put_accel"), "text-slate-500"),
            "pc_ratio": r.get("pc_ratio"),
            "net_prem_m": r.get("net_prem_m"),
            "gex_regime": r.get("gex_regime", "na"),
            "_regime_class": _REGIME_CLASS.get(r.get("gex_regime"), "text-slate-500"),
            "n_signals": r.get("n_signals", 0),
            "n_alerts": r.get("n_alerts", 0),
            "signal_label": _SIGNAL_LABEL.get(r.get("signal"), "Neutral"),
            "_signal_class": signal_class(r.get("signal")),
            "hotness": r.get("hotness", 0),
        })
    return rows

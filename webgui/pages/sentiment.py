"""Sentiment page — composite gauge + components + 30d history + trend regime.

Thin NiceGUI layer over the copied ``history_backfill`` + ``scoring`` engines.
Pure transforms here are unit-tested; ``render()`` wires widgets + timers.
"""
import sys

from repo_paths import SENTIMENT

if str(SENTIMENT) not in sys.path:
    sys.path.insert(0, str(SENTIMENT))

from scoring import WEIGHTS  # noqa: E402
from scoring import composite as scoring_composite  # noqa: E402
from scoring import trend_regime as trend_regime  # noqa: E402

CLR_GREEN = "#66bb6a"
CLR_RED = "#ef5350"
CLR_YELLOW = "#ffd54f"
LINE_COLOR = "#42a5f5"

# (component_scores key, display name, weight or None if out of composite)
COMPONENTS = [
    ("vix_complex", "VIX Complex", WEIGHTS.get("vix_complex")),
    ("put_call",    "Put/Call",    WEIGHTS.get("put_call")),
    ("breadth",     "Breadth",     WEIGHTS.get("breadth")),
    ("rotation",    "Rotation",    WEIGHTS.get("rotation")),
    ("sector_perf", "Sector Perf", WEIGHTS.get("sector_perf")),
    ("credit_pulse", "Credit Pulse", WEIGHTS.get("credit_pulse")),
]


def _safe_float(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def gauge_score(total):
    """0-10 composite -> 0-100 for the svg speedometer."""
    return max(0.0, min(100.0, _safe_float(total) * 10.0))


def bias_color(bias):
    b = (bias or "").lower()
    if "bull" in b:
        return CLR_GREEN
    if "bear" in b:
        return CLR_RED
    return CLR_YELLOW


def composite_series(snapshots):
    """(dates, scores) for snapshots with a positive composite total."""
    dates, scores = [], []
    for s in snapshots:
        v = _safe_float((s.get("composite") or {}).get("total_score"))
        if v > 0:
            dates.append(s.get("date"))
            scores.append(v)
    return dates, scores


def velocity_line(prior_scores, today_score):
    """(text, flag) from scoring.composite.velocity."""
    v = scoring_composite.velocity(list(prior_scores), _safe_float(today_score))
    roc3, roc5, z = v["roc_3d"], v["roc_5d"], v["z_20d"]
    parts = [
        f"3d ROC: {roc3:+.2f}" if roc3 is not None else "3d ROC: —",
        f"5d ROC: {roc5:+.2f}" if roc5 is not None else "5d ROC: —",
        f"20d Z: {z:+.2f}" if z is not None else "20d Z: —",
    ]
    flag = f"REGIME BREAK: {z:+.2f}σ from 20d mean" if v["regime_break"] else ""
    return " | ".join(parts), flag


def divergence_named(snapshot):
    """[(display_name, score)] for confident, scored components."""
    scores = snapshot.get("component_scores") or {}
    confs = snapshot.get("component_confidence") or {}
    out = []
    for key, name, _w in COMPONENTS:
        s = _safe_float(scores.get(key))
        if s > 0 and _safe_float(confs.get(key)) > 0:
            out.append((name, s))
    return out


def build_history_figure(snapshots):
    """Plotly fig dict: composite over time."""
    dates, scores = composite_series(snapshots)
    return {
        "data": [{
            "type": "scatter", "mode": "lines+markers",
            "x": dates, "y": scores,
            "line": {"color": LINE_COLOR, "width": 2},
            "name": "Composite",
        }],
        "layout": {
            "margin": {"l": 36, "r": 12, "t": 8, "b": 28},
            "height": 220,
            "yaxis": {"range": [0, 10], "title": "Composite"},
            "template": "plotly_dark",
            "paper_bgcolor": "rgba(0,0,0,0)",
            "plot_bgcolor": "rgba(0,0,0,0)",
        },
    }


def commit_trend_regime(spy_closes, lookback_days=trend_regime.HYSTERESIS_DAYS + 1):
    """Replay classify + commit_state over the last sessions for faithful
    hysteresis without persisted state. Returns (result, committed, days)."""
    closes = list(spy_closes)
    result = trend_regime.classify(closes)
    committed = None
    history = []
    n = len(closes)
    span = min(lookback_days, max(1, n - trend_regime.MIN_BARS_PARTIAL))
    for back in range(span - 1, -1, -1):
        sub = closes[: n - back] if back else closes
        raw = trend_regime.classify(sub).state
        committed, history = trend_regime.commit_state(raw, history, committed)
    days = 1
    return result, (committed or result.state), days

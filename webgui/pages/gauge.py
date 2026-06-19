"""Shared Highcharts solid-gauge builder — the speedometer used by the Sentiment,
Market Trend, and Trade-detail panels.

A semicircular **solid-gauge**: an arc filled to ``value`` (0-100) whose color is
value-mapped (red→amber→blue→green, flipping at the 40/55/75 thresholds the legacy
SVG speedometer used), over a faint track, with the integer value + ``label`` text
in the center. Requires the ``solid-gauge`` module — render via
``ui.highchart(gauge_figure(...), extras=["solid-gauge"])``. Pure + unit-tested.
"""
_RED = "#ef5350"
_AMBER = "#ffd54f"
_BLUE = "#42a5f5"
_GREEN = "#66bb6a"

# Value-mapped fill-color stops, duplicated at the zone thresholds (40/55/75) so
# the color flips crisply at the same points the legacy speedometer's colored
# zones did (rather than blending) — keeping the read consistent.
GAUGE_STOPS = [
    [0.00, _RED], [0.3999, _RED],
    [0.40, _AMBER], [0.5499, _AMBER],
    [0.55, _BLUE], [0.7499, _BLUE],
    [0.75, _GREEN], [1.00, _GREEN],
]
_GAUGE_INNER = "72%"   # arc thickness (shared by the pane track + the series)


def _safe_float(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _esc(text):
    """Minimal HTML escape so a label can't break the dataLabel format string."""
    return (str(text or "").replace("&", "&amp;")
            .replace("<", "&lt;").replace(">", "&gt;"))


def gauge_figure(value, label, height=120):
    """Highcharts semicircular solid-gauge options (see module docstring)."""
    v = _clamp(_safe_float(value), 0.0, 100.0)
    # Display int(v) (truncates, matching the legacy SVG) baked into the format;
    # the series data keeps the true float so the arc fills precisely.
    fmt = (f'<div style="text-align:center;line-height:1.05">'
           f'<span style="font-size:20px;font-weight:bold;color:#fff">{int(v)}</span>'
           f'<br><span style="font-size:11px;color:#bdbdbd">{_esc(label)}</span></div>')
    labels = {"useHTML": True, "borderWidth": 0, "y": -18, "format": fmt}
    return {
        "chart": {"type": "solidgauge", "backgroundColor": "transparent",
                  "height": height, "margin": [0, 0, 0, 0], "spacing": [0, 0, 0, 0]},
        "title": {"text": None},
        "credits": {"enabled": False},
        "accessibility": {"enabled": False},
        "tooltip": {"enabled": False},
        # size is a % of min(plotWidth, plotHeight); the elements are wider than
        # tall, so keep it ≤ width/height (~1.4) or the semicircle's ends clip on
        # the left/right. 128% leaves a small margin at the widest panel.
        "pane": {"startAngle": -90, "endAngle": 90,
                 "center": ["50%", "78%"], "size": "128%",
                 "background": [{"outerRadius": "100%", "innerRadius": _GAUGE_INNER,
                                 "backgroundColor": "#2f2f2f", "borderWidth": 0,
                                 "shape": "arc"}]},
        "yAxis": {"min": 0, "max": 100, "lineWidth": 0, "tickWidth": 0,
                  "minorTickWidth": 0, "tickPositions": [],
                  "labels": {"enabled": False}, "stops": GAUGE_STOPS},
        "plotOptions": {"solidgauge": {"innerRadius": _GAUGE_INNER, "rounded": False,
                                       "dataLabels": labels}},
        "series": [{"type": "solidgauge", "data": [v], "name": "value",
                    "dataLabels": labels}],
    }

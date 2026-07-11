"""Shared Highcharts solid-gauge builder — the speedometer used by the Sentiment,
Market Trend, and Trade-detail panels.

A semicircular **angular gauge** with a painted "speedometer face": the arc is a
smooth low→mid→high ramp (many graduated plotBands, red→yellow→green by default)
and a needle points to ``value`` (0-100), with the integer value + ``label`` text
in the center. The face ramp + needle colors come from ``config/theme.toml``
``[gauge]`` (via the shared ``THEME`` — edit + restart the webgui to restyle;
defaults preserved). The ``gauge`` series type lives in the ``highcharts-more``
module, which the nicegui-highcharts component auto-loads (``loadMore``) on
mount, so the element needs NO ``extras`` — just ``ui.highchart(gauge_figure(...))``.
Pure + unit-tested.
"""
from pages.options.theme import THEME, hex_rgb

_G = THEME["gauge"]
_RED = hex_rgb(_G["low"], (239, 83, 80))       # left of the arc  (default #ef5350)
_YELLOW = hex_rgb(_G["mid"], (255, 213, 79))   # middle           (default #ffd54f)
_GREEN = hex_rgb(_G["high"], (102, 187, 106))  # right            (default #66bb6a)
_NEEDLE = _G["needle"]                          # needle + pivot   (default #f5f5f5)
_FACE_BANDS = 60          # graduated plotBands across the arc → smooth gradient


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


def _lerp(c1, c2, t):
    return tuple(round(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))


def _ramp_color(t):
    """Red→yellow→green at position ``t`` in [0, 1] as ``#rrggbb``."""
    t = max(0.0, min(1.0, t))
    rgb = _lerp(_RED, _YELLOW, t * 2) if t < 0.5 else _lerp(_YELLOW, _GREEN, (t - 0.5) * 2)
    return "#%02x%02x%02x" % rgb


def rainbow_bands(n=_FACE_BANDS, thickness=13):
    """``n`` thin plotBands spanning 0-100, colored along the red→green ramp, so
    the gauge face reads as one continuous rainbow sweep."""
    return [{"from": i / n * 100.0, "to": (i + 1) / n * 100.0,
             "color": _ramp_color(i / (n - 1)), "thickness": thickness}
            for i in range(n)]


def gauge_figure(value, label, height=120):
    """Highcharts angular-gauge options — painted rainbow face + needle (see
    module docstring)."""
    v = _clamp(_safe_float(value), 0.0, 100.0)
    # Display int(v) (truncates, matching the legacy SVG) baked into the format;
    # the series data keeps the true float so the needle points precisely.
    fmt = (f'<div style="text-align:center;line-height:1.05">'
           f'<span style="font-size:20px;font-weight:bold;color:#fff">{int(v)}</span>'
           f'<br><span style="font-size:11px;color:#bdbdbd">{_esc(label)}</span></div>')
    labels = {"useHTML": True, "borderWidth": 0, "y": -18, "format": fmt}
    return {
        "chart": {"type": "gauge", "backgroundColor": "transparent",
                  "height": height, "margin": [0, 0, 0, 0], "spacing": [0, 0, 0, 0]},
        "title": {"text": None},
        "credits": {"enabled": False},
        "accessibility": {"enabled": False},
        "tooltip": {"enabled": False},
        # size is a % of min(plotWidth, plotHeight); the elements are wider than
        # tall, so keep it ≤ width/height (~1.4) or the semicircle's ends clip on
        # the left/right. 128% leaves a small margin at the widest panel.
        "pane": {"startAngle": -90, "endAngle": 90,
                 "center": ["50%", "78%"], "size": "128%", "background": []},
        "yAxis": {"min": 0, "max": 100, "lineWidth": 0, "tickWidth": 0,
                  "minorTickWidth": 0, "tickPositions": [],
                  "labels": {"enabled": False}, "plotBands": rainbow_bands()},
        "plotOptions": {"gauge": {
            "dial": {"backgroundColor": _NEEDLE, "baseWidth": 3, "topWidth": 1,
                     "radius": "80%", "rearLength": "0%",
                     "borderColor": "#222222", "borderWidth": 0.5},
            "pivot": {"backgroundColor": _NEEDLE, "radius": 4,
                      "borderColor": "#222222", "borderWidth": 0.5},
            "dataLabels": labels,
        }},
        "series": [{"type": "gauge", "data": [v], "name": "value", "dataLabels": labels}],
    }

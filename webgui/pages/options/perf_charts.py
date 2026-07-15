"""Shared PURE builders for paper-book performance analytics charts.

Used by BOTH the driver monitor (``pages/driver.py`` — Claude's selected book) and the
Paper Portfolio page (``pages/options/portfolio.py`` — the scanner-baseline book) so the
equity curve renders identically and the two books can be read side by side. No I/O — each
takes the ``perf_analytics``-shaped payload and returns a Highcharts option dict / string.
"""


def signed_dollar(v):
    return f"{'+' if v >= 0 else '-'}${abs(v):,.0f}" if isinstance(v, (int, float)) else "$0"


def equity_curve_figure(curve):
    """Highcharts figure: cumulative-realized EQUITY line + daily-realized P&L columns.

    ``curve`` = ``perf_analytics.equity_curve`` output (``[{date, equity, realized}]``).
    An empty curve yields a valid empty chart (no series data). Dark-navy themed; built
    once and updated in place (``el.options = fig; el.update()``)."""
    curve = [c for c in (curve or []) if isinstance(c, dict)]
    cats = [c.get("date", "") for c in curve]
    equity = [c.get("equity") for c in curve]
    daily = [c.get("realized") for c in curve]
    return {
        "chart": {"height": 260, "backgroundColor": "transparent", "zoomType": "x"},
        "title": {"text": None},
        "credits": {"enabled": False},
        "accessibility": {"enabled": False},
        "legend": {"enabled": True, "itemStyle": {"color": "#cdd8ee"}},
        "xAxis": {"categories": cats, "labels": {"style": {"color": "#8794b4"}},
                  "lineColor": "#213152", "tickColor": "#213152"},
        "yAxis": [
            {"title": {"text": "Equity ($)", "style": {"color": "#8794b4"}},
             "labels": {"style": {"color": "#8794b4"}}, "gridLineColor": "#1b2740"},
            {"title": {"text": "Daily P&L ($)", "style": {"color": "#8794b4"}},
             "labels": {"style": {"color": "#8794b4"}}, "opposite": True,
             "gridLineWidth": 0},
        ],
        "tooltip": {"shared": True, "valueDecimals": 2, "valuePrefix": "$"},
        "series": [
            {"type": "column", "name": "Daily P&L", "yAxis": 1, "data": daily,
             "color": "#3b82f6", "negativeColor": "#f87171", "borderWidth": 0},
            {"type": "line", "name": "Equity", "yAxis": 0, "data": equity,
             "color": "#34d399", "lineWidth": 2, "marker": {"enabled": False}},
        ],
    }


def excursion_text(ex):
    """Compact MAE/MFE line, or '' when no excursions have been recorded yet."""
    ex = ex or {}
    if not ex.get("n"):
        return ""
    cap = ex.get("mfe_capture")
    cap_s = f" · MFE capture {cap:.2f}×" if isinstance(cap, (int, float)) else ""
    return (f"Avg peak {signed_dollar(ex.get('avg_mfe'))} / avg drawdown "
            f"{signed_dollar(ex.get('avg_mae'))} over {ex['n']} closed{cap_s}")

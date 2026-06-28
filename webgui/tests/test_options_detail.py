"""Tests for the shared Trade detail panel pure helpers."""
from pages.options import detail, theme


def test_pop_color_thresholds():
    assert detail.pop_color(75) == detail.GREEN
    assert detail.pop_color(60) == detail.AMBER
    assert detail.pop_color(40) == detail.RED


def test_pop_color_returns_state_class_tokens():
    assert detail.pop_color(75) == theme.TXT_POS
    assert detail.pop_color(60) == theme.TXT_WARN
    assert detail.pop_color(40) == theme.TXT_NEG
    assert detail.pop_color("n/a") == theme.TXT_NEUTRAL


def test_factor_rows_returns_11_for_non_ic():
    fs = {"rr": 80, "pop": 62, "theta": 60, "iv": 94, "iv_hv": 46, "vega": 75,
          "em": 19, "liq": 0, "trend": 75, "gex": 100, "dex": 83}
    rows = detail.factor_rows(fs, "PCS")
    labels = [r[0] for r in rows]
    assert "R:R" in labels and "DEX" in labels
    assert len(rows) == 11


def test_factor_rows_ic_variant():
    rows = detail.factor_rows({"pcs_leg": 70, "ccs_leg": 65, "delta_bonus": -2}, "IC")
    labels = [r[0] for r in rows]
    assert "Put leg" in labels and "Call leg" in labels


def test_factor_rows_missing_values_default_zero():
    rows = detail.factor_rows({}, "PCS")
    assert all(isinstance(v, (int, float)) for _, v in rows)


def test_render_returns_handle_with_update():
    # render() needs a NiceGUI context; just assert the API surface exists.
    assert callable(detail.render)


def test_detail_header_uses_highcharts_gauge_not_svg_speedometer():
    import inspect
    src = inspect.getsource(detail)
    # The composite-score header is the shared Highcharts solid-gauge now.
    assert "gauge_figure" in src
    assert "speedometer_svg" not in src

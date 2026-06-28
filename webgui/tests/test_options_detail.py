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


def test_tile_color_fns_only_return_state_token_classes():
    # _set_color does remove=STATE_TEXT_CLASSES then add=color_fn(s). That only fully
    # clears the prior tile color if EVERY color-fn output is a member of the 4-token
    # set. Pin that invariant: across a representative input space, every tile color-fn
    # must return a class in theme.STATE_TEXT_CLASSES.
    allowed = set(theme.STATE_TEXT_CLASSES.split())
    # Representative signals: vary the pop_pct that drives pop_color across its branches
    # plus the non-numeric/missing fallback; the other color-fns are constant.
    signals = [
        {"pop_pct": 80}, {"pop_pct": 60}, {"pop_pct": 40},
        {"pop_pct": None}, {"pop_pct": "n/a"}, {},
    ]
    for _key, _label, _value_fn, color_fn in detail._TILES:
        for s in signals:
            cls = color_fn(s)
            assert cls in allowed, f"tile {_key!r} returned {cls!r} not in STATE_TEXT_CLASSES"


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

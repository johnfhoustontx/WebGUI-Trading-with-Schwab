"""Tests for the Gamma page pure figure/transform builders."""
from pages.options import gamma

GEX = {"spot": 450.0, "gex": {
    448.0: {"call": 100.0, "put": -40.0, "net": 60.0},
    450.0: {"call": 200.0, "put": -250.0, "net": -50.0},
    452.0: {"call": 30.0, "put": -10.0, "net": 20.0},
}, "strike_count": 3}


def test_bars_from_gex_filters_band_and_sorts():
    b = gamma.bars_from_gex(GEX, 450.0, pct=0.01)
    assert b["strikes"] == [448.0, 450.0, 452.0]
    assert b["nets"] == [60.0, -50.0, 20.0]
    assert b["colors"][1] != b["colors"][0]


def test_bars_from_gex_excludes_out_of_band():
    b = gamma.bars_from_gex(GEX, 450.0, pct=0.001)
    assert b["strikes"] == [450.0]


def test_bar_figure_is_plotly_dict():
    fig = gamma.bar_figure(GEX, 450.0, view="GEX", walls=[450.0], flip=449.5, pct=0.02)
    assert "data" in fig and "layout" in fig


def test_heatmap_matrix_from_history():
    rows = [("09:30", 450, None, None, None, 0, {448.0: 5, 450.0: -3}),
            ("09:35", 450, None, None, None, 0, {448.0: 7, 450.0: -1})]
    m = gamma.heatmap_matrix(rows)
    assert m["x"] == ["09:30", "09:35"]
    assert 448.0 in m["y"] and 450.0 in m["y"]
    assert len(m["z"]) == len(m["y"]) and len(m["z"][0]) == 2


def test_heatmap_matrix_empty():
    assert gamma.heatmap_matrix([])["z"] == []


def test_heatmap_matrix_formats_epoch_ts():
    rows = [(1718370600, 450, None, None, None, 0, {448.0: 5})]
    x = gamma.heatmap_matrix(rows)["x"][0]
    assert isinstance(x, str) and ":" in x


def test_term_heatmap_axes_and_zero_filter():
    tg = {"underlying_price": 450.0, "expirations": ["2026-06-18", "2026-06-19"],
          "cells": {"2026-06-18": {450.0: {"net_gex_usd": 5}},
                    "2026-06-19": {450.0: {"net_gex_usd": -3}, 451.0: {"net_gex_usd": 0}}}}
    fig = gamma.term_heatmap(tg)
    assert fig["data"][0]["x"] == ["2026-06-18", "2026-06-19"]
    assert 450.0 in fig["data"][0]["y"]
    assert 451.0 not in fig["data"][0]["y"]   # all-zero strike filtered out


def test_term_heatmap_empty():
    assert gamma.term_heatmap({})["data"][0]["z"] == []


def test_wrap_explain_fragment_and_document():
    body = "<h2>GAMMA EXPOSURE (GEX)</h2><p>hi</p>"
    frag = gamma.wrap_explain("$SPX", body, full=False)
    assert "gx-explain" in frag and body in frag and "$SPX" in frag
    assert not frag.lstrip().startswith("<!DOCTYPE")
    doc = gamma.wrap_explain("$SPX", body, full=True)
    assert doc.lstrip().startswith("<!DOCTYPE") and body in doc


def test_summary_text_mentions_spot_and_flip():
    txt = gamma.summary_text({"spot": 450.0, "flip": 449.5, "net_total": 1234.0,
                              "strike_count": 3}, "GEX")
    assert "450" in txt and "449.5" in txt

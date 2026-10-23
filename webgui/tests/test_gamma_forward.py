"""The heat map's projected afternoon on the page: the service's band, a mark
every few minutes, turned into one history-shaped row a minute
(``gamma_heat.forward_series``), and the payload reader (``gamma.history_forward``).

Design: docs/plans/2026-10-23-heatmap-full-session-forward-design.md
"""
import pytest

from pages.options import gamma
from pages.options import gamma_heat as gh

T0 = 1_761_228_000                     # a whole minute (epoch seconds)


def _band(**over):
    band = {"ts": [T0 + 20, T0 + 300, T0 + 600], "times": ["x"] * 3, "spot": 100.0,
            "call": {"100.0": [10.0, 20.0, 40.0], "105.0": [1.0, 1.0, 1.0]},
            "put": {"100.0": [-4.0, -4.0, -10.0], "105.0": [0.0, 0.0, 0.0]},
            "cone": {"mid": [100.0] * 3, "up": [100.0, 101.0, 102.0],
                     "down": [100.0, 99.0, 98.0]}}
    band.update(over)
    return band


def test_rows_start_the_minute_after_the_last_collected_and_end_at_the_last_mark():
    rows = gh.forward_series(_band(), T0 + 5)["rows"]
    assert [r[0] for r in rows] == [T0 + 60 * i for i in range(1, 11)]
    assert rows[0][1] == 100.0 and rows[0][2:6] == (None, None, None, None)


def test_minutes_between_marks_are_interpolated_side_by_side():
    rows = gh.forward_series(_band(), T0)["rows"]
    at = {r[0]: r[6] for r in rows}
    # T0+180 is 160/280 of the way from the first mark (T0+20) to the second.
    w = 160 / 280
    assert at[T0 + 180][100.0]["call"] == pytest.approx(10.0 + 10.0 * w)
    assert at[T0 + 180][100.0]["put"] == pytest.approx(-4.0)
    assert at[T0 + 180][100.0]["net"] == pytest.approx(6.0 + 10.0 * w)
    assert at[T0 + 300][100.0] == {"call": 20.0, "put": -4.0, "net": 16.0}
    assert at[T0 + 600][100.0] == {"call": 40.0, "put": -10.0, "net": 30.0}
    assert at[T0 + 420][100.0]["call"] == pytest.approx(28.0)     # 120/300 of 20 -> 40


def test_strike_keys_come_back_as_floats():
    rows = gh.forward_series(_band(), T0)["rows"]
    assert set(rows[0][6]) == {100.0, 105.0}


def test_a_gap_at_a_mark_is_a_gap_not_a_zero():
    band = _band(call={"100.0": [10.0, None, 40.0]}, put={"100.0": [0.0, 0.0, 0.0]})
    at = {r[0]: r[6] for r in gh.forward_series(band, T0)["rows"]}
    assert 100.0 not in at[T0 + 120]
    assert 100.0 not in at[T0 + 420]


def test_the_cone_follows_the_minutes():
    out = gh.forward_series(_band(), T0)
    assert len(out["up"]) == len(out["down"]) == len(out["rows"])
    assert out["up"][-1] == 102.0 and out["down"][-1] == 98.0
    assert gh.forward_series(_band(cone=None), T0)["up"] == [None] * 10


def test_without_a_collected_row_the_band_starts_at_its_first_whole_minute():
    rows = gh.forward_series(_band(), None)["rows"]
    assert rows[0][0] == T0 + 60


def test_a_collected_row_past_the_band_leaves_nothing():
    assert gh.forward_series(_band(), T0 + 900)["rows"] == []


@pytest.mark.parametrize("junk", [None, "x", {}, {"ts": []}, {"ts": ["a"], "call": {}, "put": {}},
                                  {"ts": [T0], "call": "x", "put": {}}])
def test_junk_is_an_empty_band(junk):
    assert gh.forward_series(junk, T0) == {"rows": [], "up": [], "down": []}


# ── the whole session in heatmap_figure ──────────────────────────────────────

def _session():
    """Ten collected minutes (T0 … T0+540) at strikes 98/100/102, strike 100's
    net equal to its column, and a band from T0+600 to T0+900 that carries strike
    100 from 20 to 50 (so T0+780 reads 38) and adds a strike 104 the collected
    rows never had."""
    rows = []
    for i in range(10):
        grid = {98.0: {"call": 1.0, "put": -3.0, "net": -2.0},
                100.0: {"call": float(i) + 1.0, "put": -1.0, "net": float(i)},
                102.0: {"call": 2.0, "put": -1.0, "net": 1.0}}
        rows.append((T0 + 60 * i, 100.0, None, None, None, 0, grid))
    band = {"ts": [T0 + 600, T0 + 900], "spot": 100.0,
            "call": {"98.0": [1.0, 1.0], "100.0": [20.0, 50.0], "102.0": [2.0, 2.0],
                     "104.0": [3.0, 3.0]},
            "put": {"98.0": [-3.0, -3.0], "100.0": [0.0, 0.0], "102.0": [-1.0, -1.0],
                    "104.0": [0.0, 0.0]},
            "cone": {"mid": [100.0, 100.0], "up": [100.0, 103.0], "down": [100.0, 97.0]}}
    return rows, band


def _heat(fig):
    return next(s for s in fig["series"] if s["type"] == "heatmap")


def _cells(fig):
    return {(x, y): v for x, y, v in _heat(fig)["data"]}


@pytest.mark.parametrize("view", ["GEX", "Charm", "DEX", "Vanna"])
@pytest.mark.parametrize("mode", list(gh.VALUES))
@pytest.mark.parametrize("show", list(gh.SHOWS))
@pytest.mark.parametrize("frame,scale", [("strike", "adaptive"), ("strike", "share"),
                                         ("spot", "adaptive"), ("strike", "locked")])
def test_every_view_and_control_draws_the_band(view, mode, show, frame, scale):
    """No control drops the projected afternoon any more: it runs through the
    same transforms as the collected minutes."""
    rows, band = _session()
    fig = gamma.heatmap_figure(rows, view, yrange=[96.0, 106.0], forward=band,
                               mode=mode, show=show, frame=frame, scale=scale,
                               half=3, change_window_min=5, lock={mode: 9.0})
    assert len(fig["xAxis"]["categories"]) == 16
    assert any(x >= 10 for x, _y, _v in _heat(fig)["data"])


def test_a_projected_change_is_measured_back_by_real_minutes():
    """Change over 5 minutes at T0+780 (column 13) is less the column at T0+480
    (column 8): 38 - 8."""
    rows, band = _session()
    fig = gamma.heatmap_figure(rows, "GEX", yrange=[96.0, 106.0], forward=band,
                               show="window", change_window_min=5)
    assert _cells(fig)[(13, 100.0)] == pytest.approx(30.0)


def test_a_strike_only_the_band_holds_keeps_the_ladder_whole():
    rows, band = _session()
    cells = _cells(gamma.heatmap_figure(rows, "GEX", yrange=[96.0, 106.0], forward=band))
    assert {y for _x, y in cells} == {98.0, 100.0, 102.0, 104.0}
    assert not any((x, 104.0) in cells for x in range(10))       # a gap, not a zero
    assert all((x, 104.0) in cells for x in range(10, 16))


def _contours(fig):
    return [s for s in fig["series"] if str(s.get("name", "")).startswith("Contours")]


def test_contours_run_past_now_and_are_dashed_there():
    rows, band = _session()
    fig = gamma.heatmap_figure(rows, "GEX", yrange=[96.0, 106.0], forward=band,
                               contours={"steps": 3, "min_columns": 0, "max_points": 6000})
    lines = _contours(fig)
    assert len(lines) == 2
    assert any(p[0] > 9.5 and p[1] is not None for s in lines for p in s["data"])
    for s in lines:
        assert s["zoneAxis"] == "x"
        assert s["zones"] == [{"value": 9.5}, {"dashStyle": "Dash"}]


def test_without_a_band_the_dash_and_the_divider_are_cleared_not_left_out():
    """An in-place update MERGES options: a key left out keeps the last band's
    dashes and 'now' line on a chart that has none (after the close)."""
    rows, _band = _session()
    fig = gamma.heatmap_figure(rows, "GEX", yrange=[96.0, 106.0], forward=None,
                               contours={"steps": 3, "min_columns": 0, "max_points": 6000})
    assert fig["xAxis"]["plotLines"] == []
    for s in _contours(fig):
        assert s["zoneAxis"] == "x" and s["zones"] == []


def test_the_series_count_is_the_same_with_and_without_a_band():
    rows, band = _session()
    with_band = gamma.heatmap_figure(rows, "GEX", yrange=[96.0, 106.0], forward=band)
    without = gamma.heatmap_figure(rows, "Vanna", yrange=[96.0, 106.0])
    assert [s["type"] for s in with_band["series"]] == [s["type"] for s in without["series"]]


def test_the_categories_are_the_heatmaps_columns():
    rows, band = _session()
    fig = gamma.heatmap_figure(rows, "GEX", yrange=[96.0, 106.0], forward=band)
    assert gamma.heatmap_categories(rows, band) == fig["xAxis"]["categories"]
    assert len({x for x, _y, _v in _heat(fig)["data"]}) == len(fig["xAxis"]["categories"])


def test_the_adaptive_scale_is_set_from_the_whole_session_and_locked_holds():
    """The operator's choice (2026-10-23): the projected cells count toward the
    Adaptive scale. Locked keeps the service's figure."""
    rows, band = _session()
    alone, both, held = {}, {}, {}
    gamma.heatmap_figure(rows, "GEX", yrange=[96.0, 106.0], legend=alone)
    gamma.heatmap_figure(rows, "GEX", yrange=[96.0, 106.0], forward=band, legend=both)
    gamma.heatmap_figure(rows, "GEX", yrange=[96.0, 106.0], forward=band, legend=held,
                         scale="locked", lock={"net": 7.0})
    assert both["zmax"] > alone["zmax"]
    assert held["zmax"] == 7.0


def test_the_spot_line_and_level_tracks_stop_at_now():
    rows, band = _session()
    levels = {"flip": [99.0] * 10, "call_wall": [102.0] * 10, "put_wall": [98.0] * 10}
    fig = gamma.heatmap_figure(rows, "GEX", yrange=[96.0, 106.0], forward=band,
                               levels=levels, show_tracks=True, spot_style="candle",
                               spot_interval=1)
    named = {s["name"]: s["data"] for s in fig["series"] if s.get("name")}
    assert max(p["x"] for p in named["Spot candles"]) == 9
    assert max(p[0] for p in named["Flip track"]) == 9
    assert named["EM up"][-1] == [15, 103.0]


def test_history_forward_is_the_payloads_band_for_the_same_symbol_only():
    band = _band()
    payload = {"symbol": "SPY", "view": "GEX", "rows": [], "forward": band}
    assert gamma.history_forward(payload, "SPY") is band
    assert gamma.history_forward(payload, "spy") is band
    assert gamma.history_forward(payload, None) is band
    assert gamma.history_forward(payload, "QQQ") is None
    assert gamma.history_forward({"symbol": "SPY", "forward": None}, "SPY") is None
    assert gamma.history_forward(None, "SPY") is None

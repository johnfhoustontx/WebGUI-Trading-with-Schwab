"""The ridge plot's arithmetic: which readings become ridges, and how each is
drawn over its own baseline.

Design: docs/plans/2026-10-10-gamma-ridge-plot-design.md
"""
import ast
import datetime as dt
import pathlib

import pytest

from pages.options import gamma_ridge as gr


def _ts(hh, mm):
    return int(dt.datetime(2026, 10, 9, hh, mm).timestamp())


def _minutes(start, count):
    """``count`` one-minute readings from ``start`` (hh, mm)."""
    first = _ts(*start)
    return [first + 60 * i for i in range(count)]


# ── how many ridges a session holds ──────────────────────────────────────────

def test_a_session_holds_one_ridge_a_bucket_plus_the_close_and_now():
    assert gr.slots(30) == 15          # 13 half hours, the 15:00 bucket, and now
    assert gr.slots(15) == 28
    assert gr.slots(390) == 3
    assert gr.slots(0) == gr.slots(1)  # a bad spacing never divides by zero


# ── which readings become ridges ─────────────────────────────────────────────

def test_the_first_reading_of_each_half_hour_and_the_latest_are_drawn():
    times = _minutes((8, 30), 78)                      # 08:30 … 09:47
    assert [times[i] for i in gr.pick(times, 30)] == [
        _ts(8, 30), _ts(9, 0), _ts(9, 30), _ts(9, 47)]


def test_a_missing_mark_takes_the_next_reading_in_its_half_hour():
    times = [_ts(8, 31), _ts(8, 32), _ts(9, 2), _ts(9, 3)]
    assert gr.pick(times, 30) == [0, 2, 3]


def test_the_latest_reading_is_not_drawn_twice_when_it_is_a_mark():
    times = _minutes((8, 30), 31)                      # 08:30 … 09:00
    assert gr.pick(times, 30) == [0, 30]


def test_past_the_most_the_earliest_ridges_go():
    times = _minutes((8, 30), 200)
    every = gr.pick(times, 30)
    assert gr.pick(times, 30, most=3) == every[-3:]
    assert gr.pick(times, 30, most=99) == every


def test_nothing_to_pick_from():
    assert gr.pick([], 30) == []
    assert gr.pick([None, "09:30", True, float("nan")], 30) == []


# ── a ridge ──────────────────────────────────────────────────────────────────

STRIKES = [95.0, 100.0, 105.0]
Z = [[1.0, 4.0], [4.0, 16.0], [-1.0, -4.0]]            # z[strike][reading]
TIMES = [_ts(9, 0), _ts(9, 30)]
SPOTS = [100.0, 101.0]


def _ridges(**kw):
    kw = {"lo": 90.0, "hi": 110.0, "scale": "linear", "overlap": 2.0, **kw}
    return gr.ridges(STRIKES, Z, TIMES, SPOTS, [0, 1], **kw)


def test_the_earliest_ridge_is_on_top_and_the_latest_in_front():
    early, late = _ridges()
    assert (early["ts"], early["baseline"]) == (TIMES[0], 1.0)
    assert (late["ts"], late["baseline"]) == (TIMES[1], 0.0)


def test_every_ridge_is_drawn_on_one_scale():
    """Of two ridges the taller (16) spans ``overlap`` baselines. The earlier,
    smaller profile is drawn smaller: the growth is the read."""
    early, late = _ridges()
    assert late["points"] == [(95.0, 0.0, 0.5, 4.0), (100.0, 0.0, 2.0, 16.0),
                              (105.0, 0.0, 0.5, -4.0)]
    assert early["points"] == [(95.0, 1.0, 1.125, 1.0), (100.0, 1.0, 1.5, 4.0),
                               (105.0, 1.0, 1.125, -1.0)]


def test_the_typical_ridge_sets_the_scale_not_the_tallest():
    """Peaks of 4, 5 and 100: the middle one spans the overlap, and the outlier
    is drawn in proportion, far past it. Scaled to the 100, the other two would
    be a twentieth of a row."""
    z = [[4.0, 5.0, 100.0]]
    times = [TIMES[0], TIMES[0] + 1800, TIMES[0] + 3600]
    a, b, c = gr.ridges([100.0], z, times, [None] * 3, [0, 1, 2], lo=0.0, hi=200.0,
                        scale="linear", overlap=2.0)
    heights = [r["points"][0][2] - r["points"][0][1] for r in (a, b, c)]
    assert heights == pytest.approx([1.6, 2.0, 40.0])


def test_a_ridge_with_nothing_in_it_does_not_set_the_scale():
    z = [[0.0, 0.0, 8.0]]
    times = [TIMES[0], TIMES[0] + 1800, TIMES[0] + 3600]
    last = gr.ridges([100.0], z, times, [None] * 3, [0, 1, 2], lo=0.0, hi=200.0,
                     scale="linear", overlap=2.0)[-1]
    assert last["points"][0][2] - last["points"][0][1] == pytest.approx(2.0)


def test_a_negative_value_is_a_hill_too_and_keeps_its_figure():
    late = _ridges()[1]
    strike, low, high, value = late["points"][2]
    assert high > low and value == -4.0


def test_the_root_scale_draws_the_square_root_of_the_size():
    early, late = _ridges(scale="root")
    # Sizes 4 and 2 at strike 100, the taller (4) spanning two baselines.
    assert late["points"][1] == (100.0, 0.0, 2.0, 16.0)
    assert early["points"][1] == (100.0, 1.0, 2.0, 4.0)
    assert early["points"][0] == (95.0, 1.0, 1.5, 1.0)


def test_a_ridge_changes_colour_where_its_value_crosses_zero():
    """+4 at 100 and -1 at 105: zero is four fifths of the way across."""
    early, late = _ridges()
    assert early["zones"] == [(104.0, 1), (None, -1)]
    assert late["zones"] == [(104.0, 1), (None, -1)]


def test_a_zero_takes_the_sign_beside_it():
    r = gr.ridges([90.0, 95.0, 100.0, 105.0], [[0.0], [5.0], [0.0], [-5.0]],
                  [TIMES[0]], [None], [0], lo=0.0, hi=200.0)[0]
    assert r["zones"] == [(100.0, 1), (None, -1)]
    one_sign = gr.ridges([90.0, 95.0], [[-1.0], [-2.0]], [TIMES[0]], [None], [0],
                         lo=0.0, hi=200.0)[0]
    assert one_sign["zones"] == [(None, -1)]
    flat = gr.ridges([90.0, 95.0], [[0.0], [0.0]], [TIMES[0]], [None], [0],
                     lo=0.0, hi=200.0)[0]
    assert flat["zones"] == [(None, 1)]
    assert all(high == low for _k, low, high, _v in flat["points"])


def test_only_strikes_in_the_window_are_drawn():
    early, late = _ridges(lo=98.0, hi=110.0)
    assert [p[0] for p in late["points"]] == [100.0, 105.0]


def test_a_strike_with_no_reading_is_left_out_of_that_ridge():
    z = [[1.0, None], [4.0, 16.0], [None, float("nan")]]
    early, late = gr.ridges(STRIKES, z, TIMES, SPOTS, [0, 1], lo=90.0, hi=110.0)
    assert [p[0] for p in early["points"]] == [95.0, 100.0]
    assert [p[0] for p in late["points"]] == [100.0]


def test_the_price_marker_is_each_readings_own_price_inside_the_window():
    early, late = _ridges()
    assert (early["spot"], late["spot"]) == (100.0, 101.0)
    assert _ridges(lo=100.5, hi=110.0)[0]["spot"] is None      # off the window
    no_spot = gr.ridges(STRIKES, Z, TIMES, [None, "x"], [0, 1], lo=90.0, hi=110.0)
    assert [r["spot"] for r in no_spot] == [None, None]


def test_no_columns_is_no_ridges():
    assert gr.ridges(STRIKES, Z, TIMES, SPOTS, [], lo=90.0, hi=110.0) == []


def test_ridges_do_not_write_to_the_grid():
    z = [list(row) for row in Z]
    gr.ridges(STRIKES, z, TIMES, SPOTS, [0, 1], lo=90.0, hi=110.0)
    assert z == Z


# ── the module ───────────────────────────────────────────────────────────────

def test_the_module_is_pure():
    """No NiceGUI, no bus, nothing from ``gamma`` (which imports it)."""
    tree = ast.parse(pathlib.Path(gr.__file__).read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            names.add(node.module or "")
    assert names <= {"math"}, names

"""``tools/measure_prem_remark.py`` -- how much of a change in stored premium is
re-marking rather than trading.

The arithmetic is tested on synthetic rows; no database is opened here.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from tools import measure_prem_remark as tool  # noqa: E402


def _rows(series):
    """``series`` is {strike: [(call, put), ...]} with one entry per minute; None
    leaves the strike out of that minute's grid."""
    n = max(len(v) for v in series.values())
    rows = []
    for i in range(n):
        grid = {}
        for strike, cells in series.items():
            if i < len(cells) and cells[i] is not None:
                call, put = cells[i]
                grid[strike] = {"call": call, "put": put, "net": call - put}
        rows.append((1000 + 60 * i, 100.0, None, None, None, 0, grid))
    return rows


def test_premium_that_only_ever_rises_has_no_remarking():
    m = tool.measure(_rows({100.0: [(10.0, 5.0), (12.0, 5.0), (15.0, 9.0)]}), window_min=1)
    assert m["risen"] == 9.0 and m["fallen"] == 0.0
    assert m["fallen_pct_of_risen"] == 0.0 and m["cells_fell_pct"] == 0.0
    assert m["cells"] == 4                       # two sides, two steps


def test_a_falling_cumulative_is_counted_as_remarking():
    """A true traded total can never fall, so every fall is the mark moving."""
    m = tool.measure(_rows({100.0: [(10.0, 5.0), (8.0, 5.0), (14.0, 5.0)]}), window_min=1)
    assert m["risen"] == 6.0 and m["fallen"] == 2.0
    assert m["fallen_pct_of_risen"] == 33.3
    assert m["cells_fell_pct"] == 25.0           # one of four side-steps fell


def test_the_window_figures_are_taken_over_the_window_not_minute_to_minute():
    """Up 4, down 1, up 4: minute to minute one step fell, but over two minutes
    every comparison rose, which is what a two-minute Change view would draw."""
    m = tool.measure(_rows({100.0: [(10.0, 0.0), (14.0, 0.0), (13.0, 0.0), (17.0, 0.0)]}),
                     window_min=2)
    assert m["fallen"] == 1.0                    # minute to minute
    assert m["window_fallen"] == 0.0 and m["window_risen"] == 6.0
    assert m["window_cells_fell_pct"] == 0.0


def test_a_strike_missing_from_a_minute_is_skipped_not_read_as_zero():
    """The collector omits a strike with no usable price; a gap is not a fall to
    zero followed by a jump back."""
    m = tool.measure(_rows({100.0: [(10.0, 5.0), None, (12.0, 5.0)]}), window_min=1)
    assert m["fallen"] == 0.0 and m["risen"] == 0.0
    assert m["cells"] == 0


def test_the_net_figure_is_what_the_heatmap_would_draw():
    """Net premium (calls less puts) can fall honestly, when puts trade. The tool
    reports each SIDE, where a fall can only be the mark."""
    m = tool.measure(_rows({100.0: [(10.0, 5.0), (10.0, 9.0)]}), window_min=1)
    assert m["fallen"] == 0.0 and m["risen"] == 4.0


def test_no_rows_measures_as_nothing():
    assert tool.measure([], window_min=30) is None
    assert tool.measure(_rows({100.0: [(1.0, 1.0)]}), window_min=30) is None


def test_the_report_states_the_verdict_against_the_threshold():
    clean = tool.measure(_rows({100.0: [(10.0, 5.0), (12.0, 5.0), (15.0, 9.0)]}),
                         window_min=1)
    dirty = tool.measure(_rows({100.0: [(10.0, 5.0), (8.0, 5.0), (14.0, 5.0)]}),
                         window_min=1)
    assert "under" in tool.report("SPX", "2026-09-21", clean, 1)[-1]
    assert "OVER" in tool.report("SPX", "2026-09-21", dirty, 1)[-1]
    assert tool.report("SPX", "2026-09-21", None, 30) == [
        "SPX 2026-09-21: fewer than two premium rows in the display window"]


def test_it_never_writes():
    src = pathlib.Path(tool.__file__).read_text(encoding="utf-8")
    for call in ("INSERT", "UPDATE ", "DELETE", "insert_snapshot", ".commit("):
        assert call not in src

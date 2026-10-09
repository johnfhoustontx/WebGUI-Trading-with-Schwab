"""``tools/measure_gamma_crop.py`` -- what the wider gamma-history crop costs.

The arithmetic is tested on synthetic rows; no database is opened here.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from tools import measure_gamma_crop as tool  # noqa: E402

LADDER = [float(k) for k in range(0, 201)]


def _rows(spots):
    """One row per spot; every strike on the ladder carries a full cell."""
    grid = {k: {"call": 1.0, "put": -1.0, "net": 0.5} for k in LADDER}
    return [(1000 + 60 * i, s, None, None, None, 0, grid) for i, s in enumerate(spots)]


# The tests use a display window of 5 strikes and an edge window of 2, the same
# shape as the shipped 20 and 10: the edge window (plus the one strike that
# brackets the frame's outermost row) fits inside the display window.

def test_a_flat_session_costs_nothing():
    """Price never left the window around the last spot: both rules keep the same."""
    m = tool.measure(_rows([100.0, 100.0, 100.0]), n_side=5, edge_side=2)
    assert m["old_strikes"] == m["new_strikes"] == 11
    assert m["growth_pct"] == 0.0
    assert m["rows"] == 3


def test_a_trending_session_adds_a_window_below_the_low():
    """From 100 up to 150: the old rule keeps 100..155, the new one 97..155
    (two strikes below the low, and the bracketing third)."""
    m = tool.measure(_rows([100.0, 125.0, 150.0]), n_side=5, edge_side=2)
    assert m["old_strikes"] == 56 and m["new_strikes"] == 59
    assert m["old_bytes"] < m["new_bytes"]
    assert 5.0 < m["growth_pct"] < 6.0           # 3 more strikes on 56
    assert m["path_strikes"] == 51


def test_a_round_trip_adds_a_window_above_the_high():
    m = tool.measure(_rows([100.0, 160.0, 100.0]), n_side=5, edge_side=2)
    assert m["old_strikes"] == 66 and m["new_strikes"] == 69


def test_an_edge_window_of_zero_measures_the_rule_as_it_was():
    m = tool.measure(_rows([100.0, 125.0, 150.0]), n_side=5, edge_side=0)
    assert m["old_strikes"] == m["new_strikes"] == 56 and m["growth_pct"] == 0.0


def test_the_default_edge_window_is_the_full_display_window():
    """What the design first proposed, kept as the tool's default so its cost
    can always be compared."""
    full = tool.measure(_rows([100.0, 125.0, 150.0]), n_side=5)
    assert full["new_strikes"] == 63             # 94..156


def test_no_rows_measures_as_nothing():
    assert tool.measure([], n_side=5) is None
    assert tool.measure([(1, None, 0, 0, 0, 0, {})], n_side=5) is None


def test_the_old_rule_is_the_one_the_service_had():
    """The window around the LAST spot plus every strike the path crossed."""
    assert tool.old_keep(LADDER, 150.0, [100.0, 150.0], 5) == {
        float(k) for k in range(100, 156)}


def test_a_symbol_typed_without_its_dollar_sign_is_found():
    loaded = []

    def load(symbol):
        loaded.append(symbol)
        return _rows([100.0]) if symbol == "$SPX" else []

    assert tool.load_symbol(load, "SPX")[0] == "$SPX"
    assert loaded == ["SPX", "$SPX"]
    assert tool.load_symbol(load, "NOPE") == ("NOPE", [])


def test_the_report_states_both_sizes_and_the_growth():
    m = tool.measure(_rows([100.0, 125.0, 150.0]), n_side=5, edge_side=2)
    text = "\n".join(tool.report("SPX", "2026-10-07", m))
    assert "56 strikes" in text and "59 strikes" in text and "%" in text
    assert tool.report("SPX", "2026-10-07", None) == [
        "SPX 2026-10-07: no rows in the display window"]


def test_it_never_writes():
    src = pathlib.Path(tool.__file__).read_text(encoding="utf-8")
    for call in ("INSERT", "UPDATE ", "DELETE", "insert_snapshot", ".commit("):
        assert call not in src

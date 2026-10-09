"""The Dealer Positioning display window, and what is computed inside it.

Design: docs/plans/2026-10-09-gamma-heatmap-value-scale-frame-design.md
"""
import inspect

from services.options_svc import compute, gamma_window as gw


# ── the window ───────────────────────────────────────────────────────────────

def test_window_is_n_strikes_each_side_and_the_strike_at_spot():
    strikes = [float(k) for k in range(90, 111)]
    assert gw.window_around(strikes, 100.0, 2) == {98.0, 99.0, 100.0, 101.0, 102.0}
    assert gw.window_around(strikes, 100.5, 2) == {99.0, 100.0, 101.0, 102.0}


def test_a_short_ladder_gives_what_exists():
    assert gw.window_around([99.0, 100.0], 100.0, 5) == {99.0, 100.0}
    assert gw.window_around([], 100.0, 5) == set()
    assert gw.window_around(None, 100.0, 5) == set()


def test_no_usable_spot_keeps_every_numeric_strike():
    assert gw.window_around([1.0, 2.0, "x", None], None, 2) == {1.0, 2.0}
    assert gw.window_around([1.0, 2.0], float("nan"), 1) == {1.0, 2.0}


def test_compute_uses_the_one_window():
    assert compute._window_around is gw.window_around


def test_the_module_imports_nothing_from_compute():
    """compute.py has a line ceiling; a sibling that imported it back would make
    the move a cycle instead of a split."""
    src = inspect.getsource(gw)
    assert "import compute" not in src and "from services.options_svc" not in src


# ── the scale lock ───────────────────────────────────────────────────────────

def _rows(minutes, scale=1.0, spot=100.0):
    """One row a minute from t=1000; five strikes; the cell at 100 is the largest."""
    out = []
    for m in range(minutes):
        grid = {k: {"call": 10.0 * scale * w, "put": -4.0 * scale * w,
                    "net": 6.0 * scale * w}
                for k, w in ((98.0, 1), (99.0, 2), (100.0, 5), (101.0, 2), (102.0, 1))}
        grid[500.0] = {"call": 9e9, "put": -9e9, "net": 9e9}     # far outside the window
        out.append((1000 + 60 * m, spot, None, None, None, 0, grid))
    return out


LOCK = dict(n_side=2, minutes=30, quantile=1.0, headroom=1.5)


def test_no_lock_until_the_window_has_passed():
    assert gw.scale_lock(_rows(30), **LOCK) is None       # the 30th minute not yet over
    assert gw.scale_lock(_rows(31), **LOCK) is not None


def test_the_lock_is_the_quantile_times_the_headroom_per_value():
    assert gw.scale_lock(_rows(40), **LOCK) == {
        "minutes": 30, "net": 45.0, "call": 75.0, "put": 30.0, "size": 105.0}


def test_a_lower_quantile_gives_a_lower_lock():
    lower = gw.scale_lock(_rows(40), **{**LOCK, "quantile": 0.5})
    assert lower["net"] < 45.0


def test_the_lock_ignores_strikes_outside_the_display_window():
    assert gw.scale_lock(_rows(40), **LOCK)["net"] < 1e6


def test_the_lock_is_taken_around_each_rows_own_spot():
    """A session that opened 400 points away is locked from where it was THEN."""
    grid = {k: {"call": 2.0, "put": -1.0, "net": 1.0}
            for k in (498.0, 499.0, 500.0, 501.0, 502.0)}
    grid[100.0] = {"net": 9e9}          # where a later spot might sit; not in THIS window
    far = [(1000 + 60 * m, 500.0, None, None, None, 0, grid) for m in range(40)]
    assert gw.scale_lock(far, **LOCK)["net"] == 1.5


def test_the_lock_does_not_move_after_it_is_set():
    """Later rows, however large, are outside the minutes it was computed from."""
    early = gw.scale_lock(_rows(40), **LOCK)
    late = gw.scale_lock(_rows(31) + _rows(300, scale=50.0)[31:], **LOCK)
    assert early == late


def test_string_strike_keys_are_read():
    """A grid stored in the legacy JSON format round-trips its keys as text."""
    rows = [(1000 + 60 * m, 100.0, None, None, None, 0,
             {"99.0": {"call": 4.0, "put": -2.0, "net": 2.0}, "junk": {"net": 9e9}})
            for m in range(40)]
    assert gw.scale_lock(rows, **LOCK)["net"] == 3.0


def test_the_lock_is_none_for_rows_it_cannot_read():
    for rows in (None, [], [("x", 1.0, 0, 0, 0, 0, {})], [(1, 2, 3)],
                 [(1000 + 60 * m, None, 0, 0, 0, 0, {}) for m in range(40)],
                 [(1000 + 60 * m, 100.0, 0, 0, 0, 0, None) for m in range(40)]):
        assert gw.scale_lock(rows, **LOCK) is None


def test_bare_number_cells_lock_net_only():
    rows = [(1000 + 60 * m, 100.0, 0, 0, 0, 0, {99.0: 4.0, 100.0: -8.0})
            for m in range(40)]
    lock = gw.scale_lock(rows, **LOCK)
    assert lock["net"] == 12.0
    assert lock["call"] is None and lock["put"] is None and lock["size"] is None


def test_the_lock_does_not_write_to_the_rows():
    """They are the history memo's own rows."""
    rows = _rows(40)
    before = repr(rows)
    gw.scale_lock(rows, **LOCK)
    assert repr(rows) == before

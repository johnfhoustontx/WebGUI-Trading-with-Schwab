"""Tests for BasisSmoother — the futures-cash basis noise filter.

The basis is carry plus dividends and drifts over a session. What moves it a
few ticks every couple of seconds is measurement noise, and because every
displayed level is cash_level + basis, that noise lands on every wall.
"""
from tools.nq_hud import BasisSmoother


def test_median_ignores_a_single_outlier():
    """One crossed or stale quote must move the basis by nothing at all."""
    s = BasisSmoother(window=5, jump=100.0)
    for v in (100.0, 100.0, 100.0):
        s.smooth("nq", v)
    # An outlier well inside the jump threshold is absorbed, not adopted.
    assert s.smooth("nq", 104.0) == 100.0


def test_smooths_alternating_noise_to_the_centre():
    s = BasisSmoother(window=5, jump=100.0)
    out = None
    for v in (102.8, 103.6, 102.4, 103.1, 102.9):
        out = s.smooth("nq", v)
    assert 102.4 <= out <= 103.1


def test_jump_resets_history_and_adopts_immediately():
    """A real move — a roll, or the tape reconnecting — must not fade in."""
    s = BasisSmoother(window=10, jump=5.0)
    for _ in range(10):
        s.smooth("nq", 100.0)
    assert s.smooth("nq", 300.0) == 300.0


def test_none_passes_through_without_polluting_history():
    """A tape gap must not shorten the window or read as a basis of zero."""
    s = BasisSmoother(window=3, jump=100.0)
    s.smooth("nq", 100.0)
    assert s.smooth("nq", None) is None
    assert s.smooth("nq", 100.0) == 100.0


def test_histories_are_per_instrument():
    s = BasisSmoother(window=3, jump=100.0)
    s.smooth("nq", 100.0)
    s.smooth("nq", 100.0)
    assert s.smooth("es", 20.0) == 20.0
    assert s.smooth("nq", 100.0) == 100.0


def test_window_is_bounded():
    """Old readings must age out, or the filter stops tracking real drift."""
    s = BasisSmoother(window=3, jump=1000.0)
    for v in (0.0, 0.0, 0.0):
        s.smooth("nq", v)
    for v in (50.0, 50.0, 50.0):
        out = s.smooth("nq", v)
    assert out == 50.0

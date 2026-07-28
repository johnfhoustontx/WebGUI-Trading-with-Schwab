"""Momentum regime gate — one case per branch, plus each constant's boundary."""
import pytest

from scoring import momentum_regime as mr


def _decline_then_violent_rebound():
    """Below the 200 DMA, 21d return positive, and the most volatile stretch
    of the series is the rebound — the momentum-crash setup."""
    decline = [200.0 * 0.9965 ** i for i in range(200)]
    closes = list(decline)
    for i in range(25):
        closes.append(closes[-1] * (1.03 if i % 2 == 0 else 0.985))
    return closes


def _steady_uptrend(n=260, daily=0.0008):
    return [100.0 * (1.0 + daily) ** i for i in range(n)]


# --- dispersion -------------------------------------------------------------

def test_dispersion_is_cross_sectional_stdev():
    got = mr.dispersion({"A": 0.10, "B": -0.10, "C": 0.0})

    assert got == pytest.approx(0.0816496, rel=1e-4)


def test_dispersion_of_a_single_symbol_is_none():
    # A stdev over one point is 0.0, which would read as "no dispersion".
    assert mr.dispersion({"A": 0.05}) is None


def test_dispersion_ignores_missing_returns():
    assert mr.dispersion({"A": 0.10, "B": None, "C": -0.10}) == pytest.approx(0.10)


def test_dispersion_of_nothing_is_none():
    assert mr.dispersion({}) is None


# --- dispersion_percentile --------------------------------------------------

def test_dispersion_percentile_ranks_against_history():
    history = [float(i) for i in range(100)]

    # Exactly half of the history (0..49) sits at or below 49.5.
    assert mr.dispersion_percentile(49.5, history) == pytest.approx(0.50)
    assert mr.dispersion_percentile(-1.0, history) == pytest.approx(0.0)
    assert mr.dispersion_percentile(999.0, history) == pytest.approx(1.0)


def test_dispersion_percentile_needs_sixty_observations():
    assert mr.dispersion_percentile(0.5, [0.1] * 59) is None
    assert mr.dispersion_percentile(0.5, [0.1] * 60) is not None


def test_dispersion_percentile_of_missing_current_is_none():
    assert mr.dispersion_percentile(None, [0.1] * 60) is None


# --- classify: the three branches ------------------------------------------

def test_favorable_when_above_200dma_in_contango_with_dispersion():
    verdict = mr.classify(_steady_uptrend(), vix_term=0.90, dispersion_pct=0.62)

    assert verdict.state == "favorable"
    assert verdict.lookback == "63/126"
    assert verdict.crash_risk is False
    assert verdict.reasons


def test_neutral_when_dispersion_is_too_low():
    verdict = mr.classify(_steady_uptrend(), vix_term=0.90, dispersion_pct=0.20)

    assert verdict.state == "neutral"
    assert verdict.lookback == "21/63"


def test_suppressed_on_a_volatile_rebound_below_the_200dma():
    closes = _decline_then_violent_rebound()
    sma200 = sum(closes[-200:]) / 200.0
    assert closes[-1] < sma200                      # fixture precondition
    assert closes[-1] / closes[-22] - 1.0 > 0       # fixture precondition

    verdict = mr.classify(closes, vix_term=0.90, dispersion_pct=0.62)

    assert verdict.state == "suppressed"
    assert verdict.crash_risk is True


def test_suppressed_is_checked_before_favorable():
    # Contango + high dispersion must not talk the gate out of a crash warning.
    verdict = mr.classify(_decline_then_violent_rebound(),
                          vix_term=0.5, dispersion_pct=0.99)

    assert verdict.state == "suppressed"


# --- classify: boundaries on each constant ---------------------------------

def test_dispersion_exactly_at_the_floor_is_not_favorable():
    verdict = mr.classify(_steady_uptrend(), vix_term=0.90,
                          dispersion_pct=mr.DISPERSION_FLOOR)

    assert verdict.state == "neutral"


def test_vix_term_exactly_at_contango_boundary_is_not_favorable():
    verdict = mr.classify(_steady_uptrend(), vix_term=mr.CONTANGO_MAX,
                          dispersion_pct=0.62)

    assert verdict.state == "neutral"


def test_close_exactly_on_the_200dma_is_not_favorable():
    verdict = mr.classify([100.0] * 260, vix_term=0.90, dispersion_pct=0.62)

    assert verdict.state == "neutral"


def test_vol_percentile_exactly_at_the_crash_threshold_is_not_suppressed():
    closes = _decline_then_violent_rebound()

    at = mr.classify(closes, vix_term=0.90, dispersion_pct=0.62,
                     vol_pct=mr.CRASH_VOL_PCT)
    above = mr.classify(closes, vix_term=0.90, dispersion_pct=0.62,
                        vol_pct=mr.CRASH_VOL_PCT + 0.01)

    assert at.state != "suppressed"
    assert above.state == "suppressed"


# --- classify: degradation --------------------------------------------------

def test_short_spy_history_degrades_to_neutral_and_says_why():
    verdict = mr.classify([100.0] * 10, vix_term=0.90, dispersion_pct=0.62)

    assert verdict.state == "neutral"
    assert any("history" in r.lower() for r in verdict.reasons)


def test_missing_spy_history_never_raises():
    verdict = mr.classify(None, vix_term=None, dispersion_pct=None)

    assert verdict.state == "neutral"
    assert verdict.crash_risk is False


def test_missing_dispersion_is_not_favorable_and_says_why():
    verdict = mr.classify(_steady_uptrend(), vix_term=0.90, dispersion_pct=None)

    assert verdict.state == "neutral"
    assert any("dispersion" in r.lower() for r in verdict.reasons)


def test_verdict_is_frozen():
    verdict = mr.classify(_steady_uptrend(), vix_term=0.90, dispersion_pct=0.62)

    with pytest.raises(Exception):
        verdict.state = "suppressed"


def test_realized_vol_percentile_needs_history():
    assert mr.realized_vol_percentile([100.0] * 10) is None


def test_realized_vol_percentile_is_high_after_a_volatility_spike():
    got = mr.realized_vol_percentile(_decline_then_violent_rebound())

    assert got is not None and got > 0.9

"""The Trade detail panel's calibration line over a row the REAL producers build.

``webgui/pages/options/ev.calibrated_facts`` looks up
``shared.calibration.bucket_key(trade_type, composite_score)``, and those buckets
are built from MARKET SCANNER signals scored by the scanner composite.
``strategy_scoring.score_signal`` OVERWRITES ``composite_score`` with its own
Fit+Quality score while the row keeps ``trade_type`` SWING, so without a guard a
Strategy Finder row would print another model's history.

This lives in the service suite, not beside ``webgui/tests/test_ev.py``, because
building the row needs options-scanner on ``sys.path`` - which the webgui suite
deliberately does not have, so that a Tier-1 import violation fails there.
"""
import datetime as dt
import pathlib

import pytest

_REPO = pathlib.Path(__file__).resolve().parents[3]
_WEBGUI = _REPO / "webgui"
_SCANNER_TESTS = _REPO / "options-scanner" / "tests"


@pytest.fixture
def ev_and_table(monkeypatch):
    monkeypatch.syspath_prepend(str(_WEBGUI))
    from pages.options import ev, strategy_table
    return ev, strategy_table


@pytest.fixture
def finder_row(monkeypatch, ev_and_table):
    """A real screen_spreads PCS -> adapt_credit_spread -> score_all ->
    detail_signal: the exact dict the panel receives for a Finder row."""
    import scanner_engine
    import strategy_scanner as ssn
    import strategy_scoring as ssc

    _ev, strategy_table = ev_and_table
    monkeypatch.syspath_prepend(str(_SCANNER_TESTS))
    from test_scanner_engine import _chain_at

    # The liquidity gate reads the clock; pin it open so the row does not depend
    # on the hour the suite runs.
    monkeypatch.setattr(scanner_engine, "_is_options_market_open", lambda: True)
    spot = 100.0
    exp = (dt.date.today() + dt.timedelta(days=12)).isoformat()
    chain = _chain_at(spot, exp, 12)
    spreads = scanner_engine.screen_spreads(chain, "ORCL", 5, 20, -0.35, -0.10, 0.10, 0.35,
                                            0.0, "SWING", max_risk_dollars=5000)
    pcs = next(s for s in spreads if s["type"] == "PCS")
    norm = ssn.adapt_credit_spread(dict(pcs))
    view = {"direction": "bullish", "conviction": 0.6, "vol_regime": "mid"}
    scored = ssc.score_all([norm], view, 0.25, 4.0, daily_move=2.0)
    assert len(scored) == 1
    return strategy_table.detail_signal(scored[0])


def test_a_finder_row_built_by_the_real_producers_reads_no_calibration_bucket(
        ev_and_table, finder_row):
    from shared.calibration import bucket_key

    ev, _table = ev_and_table
    # The premise the guard keys on, stated against the producers' output.
    assert finder_row.get("fit_score") is not None
    assert finder_row.get("trade_type") == "SWING"
    key = bucket_key(finder_row["trade_type"], finder_row["composite_score"])
    assert key is not None
    cal = {"buckets": {key: {"speaks": True, "ev_r": 0.8, "n": 30, "days": 12}}}

    assert ev.calibrated_facts(finder_row, cal) is None
    # Control: the bucket IS the row's own, so the guard - not a key mismatch -
    # is what withholds it.
    no_fit = {k: v for k, v in finder_row.items() if k != "fit_score"}
    assert ev.calibrated_facts(no_fit, cal) is not None

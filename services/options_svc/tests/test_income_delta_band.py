"""A4: the short-delta band the caller supplies reaches the SINGLE-LEG builder.

``swing_scan`` took ``put_d_min``/``put_d_max`` and handed them to
``screen_spreads`` only, so in one call the Income Window's documented 0.15-0.25
band governed its credit spreads while its cash-secured put was built at a fixed
0.28 target. Measured on the live board 2026-09-11, the day's only ``SHORT_PUT``
(XOM 160, 35 DTE) carried a short delta of **-0.334** - a third past the top of
the band the window documents.

⚠ This also makes the Strategy Finder's own delta inputs bind on its single-leg
shorts, which they never did. That is the user's page controls finally taking
effect rather than a new policy, and it is called out in the CHANGELOG.
"""
# Importing the service package first runs its module-top sys.path glue
# (OPTIONS_SCANNER -> sys.path), so the engine modules resolve below. They are
# imported LAZILY inside swing_scan, so the patch target is the MODULE, never a
# `compute.ssn` attribute that does not exist.
from services.options_svc import compute  # noqa: F401

import strategy_scanner  # noqa: E402
import strategy_scoring  # noqa: E402


def _band_of(monkeypatch):
    """Run income_scan with everything stubbed and capture what
    ``build_directional`` was handed."""
    seen = {}

    def _spy(chain, symbol, spot, atm_iv, dte_min, dte_max,
             put_band=None, call_band=None):
        seen.update(put_band=put_band, call_band=call_band,
                    dte=(dte_min, dte_max))
        return []

    monkeypatch.setattr(strategy_scanner, "build_directional", _spy)
    return seen


def _stub_pipeline(monkeypatch):
    """Everything ``swing_scan`` fetches before it reaches the builders. Stubbed
    at the engine + proxy seams so the test needs no chain, no Schwab call and no
    market hours."""
    monkeypatch.setattr(compute.se, "fetch_option_chain",
                        lambda *a, **k: {"underlyingPrice": 100.0})
    monkeypatch.setattr(compute._proxy.schwab_client, "get_quote",
                        lambda s: {"last": 100.0})
    monkeypatch.setattr(compute.se, "fetch_price_history", lambda *a, **k: None)
    monkeypatch.setattr(compute, "run_iv_analysis", lambda *a, **k: {})
    monkeypatch.setattr(strategy_scoring, "infer_market_view", lambda *a, **k: {})
    monkeypatch.setattr(strategy_scoring, "score_all", lambda *a, **k: [])


def test_income_scan_hands_its_documented_band_to_the_single_leg_builder(monkeypatch):
    seen = _band_of(monkeypatch)
    monkeypatch.setattr(compute, "_income_earnings", lambda s: ("none", None))
    monkeypatch.setattr(compute.se, "screen_spreads", lambda *a, **k: iter(()))
    _stub_pipeline(monkeypatch)

    compute.income_scan("XOM")

    assert seen["put_band"] == compute.INCOME_PUT_DELTA
    assert seen["call_band"] == compute.INCOME_CALL_DELTA


def test_the_band_reaching_the_builder_is_the_SAME_one_the_spreads_get(monkeypatch):
    """The defect was a divergence inside ONE call, so the test is that the two
    halves of the pipeline now read one band."""
    seen = _band_of(monkeypatch)
    spread_args = {}

    def _spy_spreads(chain, symbol, dte_min, dte_max, pdmin, pdmax, cdmin, cdmax,
                     *a, **k):
        spread_args.update(put=(pdmin, pdmax), call=(cdmin, cdmax))
        return iter(())

    monkeypatch.setattr(compute, "_income_earnings", lambda s: ("none", None))
    monkeypatch.setattr(compute.se, "screen_spreads", _spy_spreads)
    _stub_pipeline(monkeypatch)

    compute.income_scan("XOM")

    assert seen["put_band"] == spread_args["put"]
    assert seen["call_band"] == spread_args["call"]

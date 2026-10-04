"""A volatility-index day change that could not be read is absent, not zero.

Audit AC-55. ``compute_intraday_trend`` started ``vix_change_pct`` at 0.0 and
put 0.0 back on any failure, so a missing reading reached ``score_vix_context``
as a real "unchanged" and scored 35.0 at confidence 0.8, where the scorer's own
rule for a missing change is confidence 0. And "the prior close" was
``iloc[-2]``, which off-hours is two sessions back.
"""
import datetime as dt
import pathlib
import sys

import pandas as pd
import pytest

from services.sentiment_svc import compute

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3] / "sentiment-dashboard"))
from scoring import intraday_trend  # noqa: E402

TODAY = "2026-10-06"


def _frame(rows):
    return pd.DataFrame({"datetime": pd.to_datetime([d for d, _ in rows]),
                         "close": [c for _, c in rows]})


class _Schwab:
    def __init__(self, frame=None, boom=False):
        self.frame, self.boom = frame, boom

    def get_daily_history(self, symbol, *a, **k):
        if self.boom:
            raise RuntimeError("proxy down")
        return self.frame


@pytest.fixture(autouse=True)
def _today(monkeypatch):
    monkeypatch.setattr(compute, "_local_date_iso", lambda: TODAY)
    monkeypatch.setattr(compute, "_safe_daily",
                        lambda schwab, symbol, months: schwab.get_daily_history(symbol))


def test_the_change_is_against_the_prior_SESSION_during_the_day():
    """The last row is today's unfinished bar; the prior close is the row before."""
    frame = _frame([("2026-10-02", 18.0), ("2026-10-05", 20.0), (TODAY, 21.0)])
    assert compute._vix_day_change_pct(_Schwab(frame), 22.0) == pytest.approx(10.0)


def test_the_change_is_against_the_prior_session_off_hours_too():
    """Off-hours the frame already ends on the prior session. ``iloc[-2]`` then
    reached two sessions back and reported +22.2% where the truth is +10%."""
    frame = _frame([("2026-10-02", 18.0), ("2026-10-05", 20.0)])
    assert compute._vix_day_change_pct(_Schwab(frame), 22.0) == pytest.approx(10.0)


@pytest.mark.parametrize("schwab", [_Schwab(None), _Schwab(boom=True),
                                    _Schwab(_frame([(TODAY, 21.0)]))])
def test_no_prior_close_is_no_change(schwab):
    assert compute._vix_day_change_pct(schwab, 22.0) is None


@pytest.mark.parametrize("vix", [0.0, None, float("nan"), -5.0])
def test_no_usable_level_is_no_change(vix):
    frame = _frame([("2026-10-05", 20.0), (TODAY, 21.0)])
    assert compute._vix_day_change_pct(_Schwab(frame), vix) is None


def test_an_absent_change_carries_no_confidence():
    """The scorer's own rule, which the caller used to defeat by passing 0.0."""
    assert intraday_trend.score_vix_context(25.0, None, None, None).confidence == 0.0
    assert intraday_trend.score_vix_context(25.0, 0.0, None, None).confidence > 0.0


def test_the_trend_never_puts_a_zero_in_for_a_change_it_could_not_read():
    """Source guard on the call site: the 0.0 default is what made the guard in
    the scorer unreachable."""
    import ast
    import inspect
    fn = ast.parse(inspect.getsource(compute.compute_intraday_trend)).body[0]
    zeroed = [n.lineno for n in ast.walk(fn) if isinstance(n, ast.Assign)
              and any(getattr(t, "id", "") == "vix_change_pct" for t in n.targets)
              and isinstance(n.value, ast.Constant) and n.value.value == 0.0]
    assert zeroed == [], f"vix_change_pct is set to 0.0 at {zeroed}"
    assert "_vix_day_change_pct(" in inspect.getsource(compute.compute_intraday_trend)

"""The degrade counter: a swallowed exception must leave a trace.

The repo's most-documented bug class is `try/except Exception -> return a
plausible default`, which turns a real bug into a confident-looking number with
nothing in the log to say it happened (the five NaN incidents, the
`_neutral_trend()` guard over 294 lines of trend computation). Guards like that
are load-bearing - they keep a refresh path alive - so the fix is not to remove
them but to make them AUDIBLE: log with a traceback, and count so `/health` can
show "sentiment: 340 degrades this session".
"""
import logging

import pytest

from services import _degrade


@pytest.fixture(autouse=True)
def _clean():
    _degrade.reset()
    yield
    _degrade.reset()


def _boom(area, **kw):
    try:
        raise ValueError("boom")
    except Exception:
        _degrade.degraded(area, **kw)


def test_counts_per_area():
    _boom("sentiment.trend")
    _boom("sentiment.trend")
    _boom("options.rescue")
    assert _degrade.counts() == {"sentiment.trend": 2, "options.rescue": 1}
    assert _degrade.total() == 3


def test_logs_at_warning_with_the_area_and_a_traceback(caplog):
    with caplog.at_level(logging.WARNING):
        _boom("sentiment.trend")
    rec = next(r for r in caplog.records if "sentiment.trend" in r.getMessage())
    assert rec.levelno == logging.WARNING
    assert rec.exc_info is not None, "a degrade without a traceback is not diagnosable"
    assert "ValueError" in caplog.text and "boom" in caplog.text


def test_reset_clears():
    _boom("a")
    _degrade.reset()
    assert _degrade.counts() == {} and _degrade.total() == 0


def test_never_raises_even_on_junk_input():
    """A telemetry helper that can raise turns a degraded path into a crash."""
    for junk in (None, 123, object(), ""):
        _degrade.degraded(junk)          # outside any except block, too
    assert _degrade.total() == 4


def test_detail_is_appended_to_the_message(caplog):
    with caplog.at_level(logging.WARNING):
        _boom("options.matrix", detail="symbol=$SPX")
    assert "symbol=$SPX" in caplog.text


def test_counts_is_a_copy_not_the_live_dict():
    _boom("a")
    snap = _degrade.counts()
    snap["a"] = 999
    assert _degrade.counts()["a"] == 1

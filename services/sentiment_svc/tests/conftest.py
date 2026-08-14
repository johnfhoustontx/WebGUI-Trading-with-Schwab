"""Shared fixtures for the sentiment service tests.

The intraday recorder (`handlers._record_intraday`) lazily opens the REAL
`repo_paths.SENTIMENT_INTRADAY_DB` SQLite file via the module-global
`handlers._intraday_conn`. Under pytest the Redis bus is already isolated
(fakeredis), but the SQLite file was NOT — any test that called
`handlers.refresh(...)` during RTH weekday hours inserted its FIXTURE values
(sentiment 7.80, trend 50/88/70) into the live DB, and the running service's
next refresh republished them: the "volatile spikes" bug on the /sentiment
intraday graphs (2026-07-07). This autouse fixture gives every test a fresh
in-memory intraday DB and restores the global afterwards, so no test can ever
touch the real file (connect() ALSO guards path=None under pytest —
defense-in-depth; this fixture additionally makes each test's DB fresh).
"""
import pytest

from services.sentiment_svc import compute, handlers, intraday_history_db


@pytest.fixture(autouse=True)
def _isolate_intraday_db():
    old = handlers._intraday_conn
    handlers._intraday_conn = intraday_history_db.connect(":memory:")
    yield
    handlers._intraday_conn = old


@pytest.fixture(autouse=True)
def _reset_compute_ttl_caches():
    """Empty compute's module-global TTL caches around every test.

    Same class of leak as the intraday DB above, one layer up: any test that
    stubs ``_fetch_closes`` (or ``_safe_daily``) leaves its FIXTURE values in
    these globals, and a later test whose self-fetching call is NOT monkeypatched
    silently consumes them — a probe ``compute_30d_trend()`` appended after the
    delegate test scored its sector sub-score 73.33 off stale stub data with ZERO
    fan-outs. The suite only stayed green because ``pytest-randomly`` isn't
    installed and the ordering happened to be kind.

    ``_SECTOR_PCTS_CACHE`` is the one that widened the blast radius (before it
    existed, ``_fetch_sector_month_pcts`` refetched every call, so only a fully
    populated ``_TREND_30D_CACHE`` could go stale). Clearing BEFORE as well as
    after means a test can't inherit a leak from whatever ran ahead of it."""
    def _clear():
        compute.reset_sector_pcts_cache()
        compute.reset_trend_7d_cache()
        compute.reset_trend_30d_cache()

    _clear()
    yield
    _clear()

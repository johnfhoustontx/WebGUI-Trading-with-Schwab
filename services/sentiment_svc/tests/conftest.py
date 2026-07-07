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

from services.sentiment_svc import handlers, intraday_history_db


@pytest.fixture(autouse=True)
def _isolate_intraday_db():
    old = handlers._intraday_conn
    handlers._intraday_conn = intraday_history_db.connect(":memory:")
    yield
    handlers._intraday_conn = old

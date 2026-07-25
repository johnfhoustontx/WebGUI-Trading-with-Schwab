# services/options_svc/tests/conftest.py
import pytest

from services.options_svc import compute


@pytest.fixture(autouse=True)
def _no_live_claude(monkeypatch):
    """Neutralize real Claude-client resolution across the whole options_svc suite.

    ``compute._make_analyze_client()`` resolves a real ``anthropic.Anthropic``
    client from the environment / gitignored ``shared/anthropic_key.txt`` — on a
    machine with a real key configured (this dev box has one), any test that
    exercises the ``client = client or _make_analyze_client()`` fallback without
    injecting its own fake client would fire a REAL, BILLED API call. This
    already happened once (the ``_research_news`` no-key test silently made a
    live web-search call). Forcing the resolver to ``None`` makes every test's
    real-client path degrade to the documented no-key behavior — no network,
    regardless of which call site (``gamma_analyze``, ``_research_news``, and
    whatever Task 4 adds next) reaches it. Tests that inject their own fake
    client (the overwhelmingly common case in this file) are unaffected — the
    ``client or _make_analyze_client()`` fallback short-circuits before this
    patched function is ever called. Mirrors ``services/market_svc/tests/conftest.py``.
    """
    monkeypatch.setattr(compute, "_make_analyze_client", lambda: None)

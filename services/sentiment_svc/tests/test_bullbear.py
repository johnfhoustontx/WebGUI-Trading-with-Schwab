"""Tier-2 merge for the Bull / Bear Map: the nightly momentum cascade plus one
batched live ``/quotes`` call -> ``cache:sentiment:bullbear``.

Every quote fixture below uses the FLATTENED shape
``SchwabProxyClient.get_quotes`` returns (``schwab-proxy/proxy_client.py:284``):
``{symbol: {"last", "change", "change_pct", "high", "low", "volume"}}``. There is
no ``{"quote": {...}}`` envelope — a fixture inventing one would leave every
row's ``day_pct`` None while this suite stayed green.
"""
import copy
import datetime as _dt

import pytest

from services import _proxy
from services.sentiment_svc import compute


def test_bullbear_symbols_covers_all_three_levels_deduped():
    """An industry ETF is often a scored stock too; ask the quote call once."""
    levels = {"sector": [{"symbol": "XLV"}], "industry": [{"symbol": "XBI"}],
              "stock": [{"symbol": "AMGN"}, {"symbol": "XBI"}]}
    assert compute.bullbear_symbols(levels) == ["XLV", "XBI", "AMGN"]


def test_bullbear_symbols_skips_rows_with_no_usable_symbol():
    levels = {"sector": [{"symbol": ""}, {"symbol": None}, {}, None,
                         {"symbol": "XLV"}]}
    assert compute.bullbear_symbols(levels) == ["XLV"]


def test_merge_live_attaches_the_day_move_at_every_level():
    """0.0 must survive as 0.0: a genuinely flat day is a reading, not a gap."""
    levels = {"sector": [{"symbol": "XLV"}], "industry": [{"symbol": "XBI"}],
              "stock": [{"symbol": "AMGN"}]}
    quotes = {"XLV": {"change_pct": 1.25}, "XBI": {"change_pct": -0.5},
              "AMGN": {"change_pct": 0.0}}
    merged = compute.merge_live(levels, quotes)
    assert [merged[k][0]["day_pct"] for k in compute.BULLBEAR_LEVELS] == \
        [1.25, -0.5, 0.0]


def test_merge_live_leaves_day_pct_none_rather_than_zero_without_a_quote():
    """A dash, not "unchanged" — a different claim. Live, this is a symbol the
    proxy omitted from its reply; the key-present-but-empty case guards a
    different client, since ``_extract_change_pct`` always returns a float for a
    symbol ``get_quotes`` does return."""
    levels = {"sector": [{"symbol": "XLV"}]}
    assert compute.merge_live(levels, {})["sector"][0]["day_pct"] is None
    assert compute.merge_live(levels, {"XLV": {}})["sector"][0]["day_pct"] is None


def test_merge_live_reads_the_shape_get_quotes_actually_returns():
    """Captured off the running proxy on 2026-08-20. Named for the bug it
    guards: an earlier draft read ``["quote"]["netPercentChange"]``, a shape
    this producer never emits, which would have killed the whole live column."""
    real = {"XLV": {"last": 174.7, "change": -0.98, "change_pct": -0.55783242,
                    "high": 175.19, "low": 173.63, "volume": 4546574}}
    merged = compute.merge_live({"sector": [{"symbol": "XLV"}]}, real)
    assert merged["sector"][0]["day_pct"] == -0.55783242


def test_merge_live_leaves_the_cached_momentum_payload_untouched():
    """The momentum tree is shared with /sentiment/momentum. Deep-compared, so a
    write into a nested ``raw``/``components`` dict is caught as well as a
    top-level one."""
    levels = {"sector": [{"symbol": "XLV", "raw": {"trend": 0.4}}],
              "industry": [{"symbol": "XBI", "raw": {}}]}
    before = copy.deepcopy(levels)
    compute.merge_live(levels, {"XLV": {"change_pct": 1.0}})
    assert levels == before


class _RecordingClient:
    """Stand-in for ``services._proxy.schwab_client``; records every ask."""

    def __init__(self, result=None):
        self.asked = []
        self.result = result or {}

    def get_quotes(self, symbols):
        self.asked.append(list(symbols))
        return self.result


def test_bullbear_quotes_forwards_the_symbol_list_to_the_shared_proxy_client(
        monkeypatch):
    """The one test pinning the wiring to the real client — everything below
    stubs ``_bullbear_quotes`` out."""
    client = _RecordingClient({"XLV": {"change_pct": 1.0}})
    monkeypatch.setattr(_proxy, "schwab_client", client)
    assert compute._bullbear_quotes(["XLV", "AMGN"]) == {"XLV": {"change_pct": 1.0}}
    assert client.asked == [["XLV", "AMGN"]]


def test_bullbear_quotes_does_not_ask_the_proxy_for_an_empty_symbol_list(
        monkeypatch):
    """A cold cascade has no symbols, and ``/quotes?symbols=`` is a wasted call."""
    client = _RecordingClient()
    monkeypatch.setattr(_proxy, "schwab_client", client)
    assert compute._bullbear_quotes([]) == {}
    assert client.asked == []


def test_bullbear_view_carries_the_nightly_tree_and_the_live_moves(monkeypatch):
    monkeypatch.setattr(compute, "_bullbear_quotes",
                        lambda s: {"XLV": {"change_pct": 2.0}})
    nightly = {"session_date": "2026-08-19",
               "computed_at": "2026-08-19T16:20:00-05:00",
               "regime": {"state": "risk_on"},
               "levels": {"sector": [{"symbol": "XLV"}]}}
    view = compute.bullbear_view(nightly)
    assert view["session_date"] == "2026-08-19"
    assert view["computed_at"] == nightly["computed_at"]
    assert view["regime"] == {"state": "risk_on"}
    assert view["levels"]["sector"][0]["day_pct"] == 2.0


def test_bullbear_view_stamps_quoted_at_now_and_offset_aware(monkeypatch):
    """Two clocks on purpose: ``computed_at`` dates last night's SCORES,
    ``quoted_at`` the day-moves taken just now. Stamped exactly the way
    ``compute_momentum`` stamps ``computed_at`` — the page renders the pair side
    by side, so one formatter has to read both."""
    monkeypatch.setattr(compute, "_bullbear_quotes", lambda s: {})
    view = compute.bullbear_view({"computed_at": "2026-08-19T16:20:00-05:00"})
    stamped = _dt.datetime.fromisoformat(view["quoted_at"])
    assert stamped.tzinfo is not None
    assert abs((_dt.datetime.now().astimezone() - stamped).total_seconds()) < 60


def test_bullbear_view_asks_for_quotes_once_for_every_distinct_symbol(monkeypatch):
    """Measured 374 symbols returning in a SINGLE call; a per-row fetch would be
    374 proxy round-trips every 30 s."""
    calls = []
    monkeypatch.setattr(compute, "_bullbear_quotes",
                        lambda s: calls.append(list(s)) or {})
    compute.bullbear_view({"levels": {
        "sector": [{"symbol": "XLV"}],
        "stock": [{"symbol": "XLV"}, {"symbol": "AMGN"}]}})
    assert calls == [["XLV", "AMGN"]]


def test_bullbear_view_degrades_to_the_nightly_tree_when_the_quote_call_fails(
        monkeypatch):
    """A dead proxy costs the day-move column and nothing else. Raising instead
    would publish no view at all and lose a perfectly good nightly tree."""
    def _boom(symbols):
        raise RuntimeError("proxy down")

    monkeypatch.setattr(compute, "_bullbear_quotes", _boom)
    view = compute.bullbear_view({"session_date": "2026-08-19",
                                  "levels": {"sector": [{"symbol": "XLV"}]}})
    assert view["quoted_at"] is None           # the tell: moves absent, not flat
    assert view["session_date"] == "2026-08-19"
    assert view["levels"]["sector"][0]["day_pct"] is None


def test_bullbear_view_on_a_cold_momentum_cache_yields_an_empty_tree(monkeypatch):
    """The map's 30 s poll starts before the first nightly cascade on a fresh
    install. All three levels must be present and empty — the page indexes them
    by name."""
    monkeypatch.setattr(compute, "_bullbear_quotes", lambda s: {})
    view = compute.bullbear_view(None)
    assert view["session_date"] is None
    assert view["levels"] == {"sector": [], "industry": [], "stock": []}


def test_bullbear_view_does_not_swallow_a_malformed_momentum_tree(monkeypatch):
    """The degrade wraps the QUOTE CALL only. A shape drift in the cascade's own
    payload is a real bug, and hiding it behind an all-None day-move column is
    the exact failure mode this feature already nearly shipped once.

    The property is "does not swallow", so the type is deliberately unpinned — a
    guard refactor that changes AttributeError to ValueError is not a regression
    of anything this test is about."""
    monkeypatch.setattr(compute, "_bullbear_quotes", lambda s: {})
    with pytest.raises(Exception):
        compute.bullbear_view({"levels": {"sector": ["XLV"]}})

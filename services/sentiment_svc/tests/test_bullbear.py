"""Tier-2 merge for the Bull / Bear Map: the nightly momentum cascade plus one
batched live ``/quotes`` call -> ``cache:sentiment:bullbear``.

Every quote fixture below uses the FLATTENED shape
``SchwabProxyClient.get_quotes`` returns (``schwab-proxy/proxy_client.py:284``):
``{symbol: {"last", "change", "change_pct", "high", "low", "volume"}}``. There is
no ``{"quote": {...}}`` envelope — a fixture inventing one would leave every
row's ``day_pct`` None while this suite stayed green.
"""
import copy

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

"""One symbol universe, read by all three tiers."""
import pytest

from shared import symbols


@pytest.fixture(autouse=True)
def _fresh():
    symbols.reset_cache()
    yield
    symbols.reset_cache()


EXPECTED_COLLECTION = [
    "$SPX", "$VIX", "SPY", "QQQ", "$NDX",
    "IWM", "DIA",
    "XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY",
    "NVDA", "MSFT", "GOOGL", "AMZN", "META", "AAPL", "TSLA", "AVGO", "PLTR", "AMD",
]
EXPECTED_BIG10 = ("NVDA", "MSFT", "GOOGL", "AMZN", "META",
                  "AAPL", "TSLA", "AVGO", "PLTR", "AMD")


def test_collection_matches_the_pre_extraction_list_exactly():
    """Order matters as much as membership - this list is fetched in order every
    minute and its size is a Schwab API-budget decision."""
    assert symbols.collection_base() == EXPECTED_COLLECTION


def test_big10_is_a_tuple_in_basket_order():
    assert symbols.big10() == EXPECTED_BIG10


def test_netprem_groups_keep_shape_and_display_order():
    groups = symbols.netprem_groups()
    assert [g["key"] for g in groups] == ["indices", "sectors", "megacaps"]
    assert groups[0]["label"] == "Indices & Broad"
    for g in groups:
        assert isinstance(g["symbols"], tuple), "consumers index and compare tuples"


def test_megacap_display_order_differs_from_the_basket_order():
    """Deliberate, and easy to 'tidy' away: the group is display order, the
    basket is composition order. Same members, different sequence."""
    mega = next(g for g in symbols.netprem_groups() if g["key"] == "megacaps")
    assert set(mega["symbols"]) == set(symbols.big10())
    assert mega["symbols"] != symbols.big10()


def test_collection_dedups_while_preserving_order(monkeypatch):
    """A symbol listed in two sections would otherwise be fetched twice a minute."""
    monkeypatch.setattr(symbols, "load", lambda: {"collection": {
        "base": ["SPY", "QQQ"], "broad": ["SPY", "IWM"],
        "sectors": ["QQQ", "XLK"], "megacaps": ["AMD"]}})
    assert symbols.collection_base() == ["SPY", "QQQ", "IWM", "XLK", "AMD"]


def test_a_junk_section_falls_back_to_defaults(monkeypatch):
    monkeypatch.setattr(symbols, "load",
                        lambda: {"collection": {"base": "not-a-list"}})
    assert symbols.collection_base()[0] == "$SPX"


def test_a_non_string_entry_rejects_the_whole_list(monkeypatch):
    """Half-applying a malformed list would silently drop symbols from collection."""
    monkeypatch.setattr(symbols, "load",
                        lambda: {"baskets": {"big10": ["NVDA", 7, None]}})
    assert symbols.big10() == EXPECTED_BIG10


def test_a_malformed_group_is_dropped_not_fatal(monkeypatch):
    monkeypatch.setattr(symbols, "load", lambda: {"netprem_groups": [
        {"key": "ok", "label": "OK", "symbols": ["SPY"]},
        {"key": "broken"},
    ]})
    assert [g["key"] for g in symbols.netprem_groups()] == ["ok"]


# --- every tier resolves the same thing -------------------------------------

def test_tier2_net_premium_reads_the_config():
    from services.options_svc import net_premium

    assert net_premium.GROUPS == symbols.netprem_groups()
    assert net_premium.BASKETS["BIG10"] == symbols.big10()


def test_tier2_market_svc_basket_matches():
    """market_svc/symbols.py carried its own BIG10 copy under a comment saying it
    must match net_premium's 'by design'. Both now read one file."""
    from services.market_svc import symbols as msym

    tile = next(e for e in msym.SYMBOL_MAP if e["display"] == "BIG10")
    assert tile["basket"] == symbols.big10()


def test_all_three_copies_agree(monkeypatch):
    """The discriminating test. The four literals were equal before this change
    too - tests pinned them. Move the config and require every tier to follow."""
    import importlib

    from services.options_svc import net_premium

    monkeypatch.setattr(symbols, "netprem_groups",
                        lambda: ({"key": "z", "label": "Z", "symbols": ("SPY",)},))
    try:
        importlib.reload(net_premium)
        assert [g["key"] for g in net_premium.GROUPS] == ["z"], \
            "options_svc/net_premium.py is not reading config/symbols.toml"
    finally:
        monkeypatch.undo()
        importlib.reload(net_premium)

"""Pure group model + series builder for the Dealer Positioning Net Prem view."""
from services.market_svc import symbols as market_symbols
from services.options_svc import net_premium as np_mod


def test_three_groups_in_display_order():
    keys = [g["key"] for g in np_mod.GROUPS]
    assert keys == ["indices", "sectors", "megacaps"]


def test_group_membership_matches_the_spec():
    by_key = {g["key"]: list(g["symbols"]) for g in np_mod.GROUPS}
    assert by_key["indices"] == ["$SPX", "$NDX", "BIG10", "SPY", "QQQ", "IWM", "DIA"]
    assert by_key["sectors"] == ["XLB", "XLC", "XLE", "XLF", "XLI", "XLK",
                                 "XLP", "XLRE", "XLU", "XLV", "XLY"]
    assert by_key["megacaps"] == ["NVDA", "AVGO", "AAPL", "META", "MSFT",
                                  "TSLA", "PLTR", "AMZN", "GOOGL", "AMD"]


def test_big10_basket_matches_market_dashboard_membership():
    # Must stay identical to market_svc/symbols.py's BIG10 basket, or "BIG10"
    # would mean two different things on two pages. Read from market_svc rather
    # than hardcoding the ten, so this fails on drift from EITHER side — a
    # hardcoded copy would stay green while the two pages silently diverged.
    # The cross-service import is TEST-ONLY (symbols.py is pure data, no
    # imports); net_premium.py itself stays free of it.
    mkt = next(e["basket"] for e in market_symbols.SYMBOL_MAP
               if e["kind"] == "basket" and e["display"] == "BIG10")
    assert set(np_mod.BASKETS["BIG10"]) == set(mkt)


def test_big10_basket_covers_the_megacap_group():
    # The two lists are the same ten tickers in different orders (display vs
    # market_svc-mirroring). Nothing structural ties them, so pin it: adding an
    # 11th mega-cap to the group without the basket would make BIG10 quietly
    # under-sum while the new line rendered right beside it.
    megacaps = next(g["symbols"] for g in np_mod.GROUPS if g["key"] == "megacaps")
    assert set(megacaps) == set(np_mod.BASKETS["BIG10"])


def test_display_symbols_are_every_group_member_deduped_in_order():
    out = np_mod.display_symbols()
    assert out[0] == "$SPX"
    assert "BIG10" in out
    assert len(out) == len(set(out)) == 7 + 11 + 10


def test_source_symbols_drop_baskets_and_add_their_members():
    out = np_mod.source_symbols()
    assert "BIG10" not in out          # not a real ticker — nothing to read
    # Every real ticker survives and BIG10's members are all already mega-cap
    # group entries, so the source set is exactly the display set less BIG10.
    assert set(out) == set(np_mod.display_symbols()) - {"BIG10"}
    assert len(out) == len(set(out))   # deduped

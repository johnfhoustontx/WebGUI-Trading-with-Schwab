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


def test_every_source_symbol_is_in_the_static_collection_base():
    """Drift guard: a group symbol that isn't collected has no premium history,
    so its line would be permanently empty.

    Asserts against the STATIC ``SYMBOLS`` base, deliberately NOT against
    ``collection_symbols()`` (= base ∪ ``Top 20.xlsx``). That union is the weaker
    claim and it fails open on exactly the machine this guard protects: the
    workbook is GITIGNORED, and it already happens to carry IWM, DIA and all ten
    mega-caps. So a union-based assertion stays GREEN on the dev box while a
    fresh clone silently loses those twelve lines — the regression would surface
    only for whoever clones next. ``SYMBOLS ⊆ collection_symbols()`` always, so
    this is strictly stronger, and it pins the actual design intent: every symbol
    a named UI group renders must be collected independently of that workbook."""
    import sys

    from repo_paths import OPTIONS_SCANNER
    if str(OPTIONS_SCANNER) not in sys.path:
        sys.path.insert(0, str(OPTIONS_SCANNER))
    import gex_collector

    missing = [s for s in np_mod.source_symbols()
               if s not in set(gex_collector.SYMBOLS)]
    assert not missing, f"not in gex_collector.SYMBOLS: {missing}"


def _row(ts, call, put):
    """A gex_history flow row: (ts, spot, call_vol, put_vol, call_prem, put_prem)."""
    return (ts, 100.0, 0, 0, call, put)


def test_build_series_projects_ts_call_put():
    out = np_mod.build_series({"SPY": [_row(1, 10.0, 4.0), _row(2, 20.0, 5.0)]})
    assert out["SPY"] == [[1, 10.0, 4.0], [2, 20.0, 5.0]]


def test_build_series_skips_rows_with_no_premium_at_all():
    out = np_mod.build_series({"SPY": [_row(1, None, None), _row(2, 20.0, 5.0)]})
    assert out["SPY"] == [[2, 20.0, 5.0]]


def test_build_series_treats_one_missing_side_as_zero():
    out = np_mod.build_series({"SPY": [_row(1, 10.0, None)]})
    assert out["SPY"] == [[1, 10.0, 0.0]]


def test_build_series_sums_basket_members_by_timestamp():
    out = np_mod.build_series({
        "NVDA": [_row(1, 10.0, 1.0), _row(2, 30.0, 2.0)],
        "MSFT": [_row(1, 5.0, 3.0), _row(2, 7.0, 4.0)],
    })
    assert out["BIG10"] == [[1, 15.0, 4.0], [2, 37.0, 6.0]]


def test_basket_tolerates_partially_reported_timestamps():
    # A member missing a snapshot contributes nothing at that ts rather than
    # dropping the whole column.
    out = np_mod.build_series({
        "NVDA": [_row(1, 10.0, 1.0), _row(2, 30.0, 2.0)],
        "MSFT": [_row(1, 5.0, 3.0)],
    })
    assert out["BIG10"] == [[1, 15.0, 4.0], [2, 30.0, 2.0]]


def test_basket_absent_when_no_member_has_data():
    out = np_mod.build_series({"NVDA": []})
    assert "BIG10" not in out


def test_symbols_with_no_rows_are_omitted():
    out = np_mod.build_series({"XLK": [], "SPY": [_row(1, 1.0, 1.0)]})
    assert "XLK" not in out and "SPY" in out


def test_build_series_never_raises_on_junk():
    out = np_mod.build_series({"SPY": [("x",), None, _row(1, 1.0, 1.0)]})
    assert out["SPY"] == [[1, 1.0, 1.0]]

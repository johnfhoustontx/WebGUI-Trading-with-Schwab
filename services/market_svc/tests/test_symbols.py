from datetime import date

import pytest

from services.market_svc import symbols as S
from shared import futures

# Pinned dates either side of the September 2026 roll (expiry Friday 2026-09-18,
# roll eight days earlier on Thursday the 10th). Every test that names a futures
# contract passes one of these, so the suite does not change its answer on the
# day a contract rolls.
_BEFORE_ROLL = date(2026, 9, 9)
_AFTER_EXPIRY = date(2026, 10, 4)       # the evening the tiles were seen blank


def _map(today=_BEFORE_ROLL):
    return S.symbol_map(today)


_EXPECTED_DISPLAYS = {
    "VIX", "VIX1D", "VIX3M", "SKEW",
    "Put/Call", "Net Prem", "MGTN",
    "$ADVN", "$DECN", "$ADVN-$DECN", "$TICK",
    "$DXY", "FXY", "FXE", "FXB",
    "SPX", "NDX",
    "/ES[U26]", "/NQ[U26]",
    "SPY", "DIA", "QQQ", "IWM", "RSP", "QQEW",
    "BIG10", "NVDA", "MSFT", "GOOGL", "AMZN", "META", "AAPL", "TSLA",
    "AVGO", "PLTR", "AMD",
    "SMH", "XSD", "IGV", "QTUM", "XBI", "XRT", "XME", "USO", "IYT",
    "XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY",
    "TLT", "HYG", "LQD",
    "GDLC", "VCX",
    "GLD", "SLV", "COPX",
    "MCHI", "EWJ", "EWY", "INDA", "EWT", "EWZ", "EWA", "EWU", "EWG", "EWQ", "EWW", "EWC",
}


def test_every_csv_symbol_is_mapped():
    # 75 tiles: base 55 + the Top 10 frame (a BIG10 composite + 10 constituents:
    # the Mag-7 + AVGO/PLTR/AMD) + the Net Prem external tile + the $MGTN index tile
    # (CBOE Magnificent Ten, added 2026-07-21) + USO (added 2026-08-12) + IYT
    # (added 2026-09-11) + the Metals frame GLD/SLV/COPX (added 2026-09-21)
    # + EWG/EWQ country ETFs (added 2026-09-27) + the FXY/FXE/FXB currency tiles
    # (added 2026-09-27) − the MTUM/SPMO Factor frame (dropped 2026-09-27).
    # (base 55 = $PCALL+$PCSP→ONE put/call tile; HYG-LQD dropped; $ADD/$ADSPD dropped;
    # XLB added; +10 country ETFs.)
    assert len(_map()) == 76
    # A future mistyped ticker/display must fail: the full display set is pinned.
    assert len(_EXPECTED_DISPLAYS) == 76
    assert {t["display"] for t in _map()} == _EXPECTED_DISPLAYS
    # Every entry has a non-empty display; every quote tile has a real quote_symbol.
    for t in _map():
        assert t["display"], t
        if t["kind"] == "quote":
            assert t["quote_symbol"], t


def test_categories_cover_the_expected_set_in_frame_order():
    assert S.CATEGORY_ORDER == [
        "Volatility", "Options Sentiment", "Market Internals / Breadth", "Currency",
        "Cash Index", "Equity Index Futures", "Broad-Market ETF", "Top 10",
        "Sector SPDR", "Thematic / Industry ETF",
        "Fixed Income / Credit ETF", "Crypto / Alternatives", "Metals", "Countries",
    ]
    # every mapped tile's category is in the order list
    assert {t["category"] for t in _map()} <= set(S.CATEGORY_ORDER)


def test_translations_and_polarities():
    by_disp = {t["display"]: t for t in _map()}
    assert by_disp["VIX"]["quote_symbol"] == "$VIX"
    assert by_disp["VIX"]["polarity"] == "inverted"
    assert by_disp["SPX"]["quote_symbol"] == "$SPX"
    assert by_disp["/ES[U26]"]["quote_symbol"] == "/ESU26"
    assert by_disp["$DXY"]["quote_symbol"] == "UUP"      # equivalent
    # CBOE Magnificent Ten index (ToS IMGTN:CGI -> Schwab API $MGTN)
    assert by_disp["MGTN"]["quote_symbol"] == "$MGTN"
    assert by_disp["MGTN"]["category"] == "Options Sentiment"
    assert by_disp["$DXY"]["polarity"] == "inverted"
    # yen = carry-funding safe haven (up = risk-off); euro/pound mirror the dollar
    assert by_disp["FXY"]["polarity"] == "inverted"
    assert by_disp["FXE"]["polarity"] == "normal"
    assert by_disp["FXB"]["polarity"] == "normal"
    assert {by_disp[s]["category"] for s in ("FXY", "FXE", "FXB")} == {"Currency"}
    assert by_disp["TLT"]["polarity"] == "inverted"
    assert by_disp["XLP"]["polarity"] == "normal"        # defensive sector stays literal


def test_kinds():
    kinds = {t["display"]: t["kind"] for t in _map()}
    assert kinds["$ADVN-$DECN"] == "spread"
    # the two Options-Sentiment tiles are external (Put/Call fed from sentiment,
    # Net Prem fed from cache:options:matrix)
    ext = [t for t in _map() if t["kind"] == "external"]
    assert {t["display"] for t in ext} == {"Put/Call", "Net Prem"}
    assert all(t["category"] == "Options Sentiment" for t in ext)


def test_big10_basket():
    by_disp = {t["display"]: t for t in _map()}
    mag = by_disp["BIG10"]
    assert mag["kind"] == "basket" and mag["category"] == "Top 10"
    assert mag["basket"] == ("NVDA", "MSFT", "GOOGL", "AMZN", "META", "AAPL", "TSLA",
                             "AVGO", "PLTR", "AMD")
    # each constituent is ALSO its own quote tile in the same frame
    for sym in mag["basket"]:
        assert by_disp[sym]["kind"] == "quote"
        assert by_disp[sym]["category"] == "Top 10"


def test_quote_symbols_are_the_real_ones_only():
    qs = S.quote_symbols(_map())
    # includes spread legs + basket members, excludes computed/external composites
    assert "$ADVN" in qs and "$DECN" in qs and "HYG" in qs and "LQD" in qs
    assert "NVDA" in qs and "TSLA" in qs          # basket members are fetched
    assert "$ADVN-$DECN" not in qs and "HYG-LQD" not in qs and "BIG10" not in qs
    assert "$PCALL" not in qs


def test_quote_symbols_deduped():
    # $ADVN/$DECN each appear as both a quote tile AND a leg of the $ADVN-$DECN
    # spread — the dedup in quote_symbols() must collapse them.
    qs = S.quote_symbols(_map())
    assert len(qs) == len(set(qs))


# ── the futures tiles follow the front month ────────────────────────────────

def _futures_tiles(today):
    return [t for t in S.symbol_map(today) if t["category"] == "Equity Index Futures"]


def test_before_the_roll_the_tiles_are_the_september_contract():
    es, nq = _futures_tiles(_BEFORE_ROLL)
    assert (es["display"], es["quote_symbol"]) == ("/ES[U26]", "/ESU26")
    assert (nq["display"], nq["quote_symbol"]) == ("/NQ[U26]", "/NQU26")
    assert es["description"] == "E-mini S&P 500 future, Sep 2026"
    assert nq["description"] == "E-mini Nasdaq 100 future, Sep 2026"


def test_on_the_roll_day_display_quote_and_description_all_move_together():
    es, nq = _futures_tiles(date(2026, 9, 10))
    assert (es["display"], es["quote_symbol"]) == ("/ES[Z26]", "/ESZ26")
    assert (nq["display"], nq["quote_symbol"]) == ("/NQ[Z26]", "/NQZ26")
    assert es["description"] == "E-mini S&P 500 future, Dec 2026"
    assert nq["description"] == "E-mini Nasdaq 100 future, Dec 2026"
    # csv_symbol is the display fallback, so it must not lag on the old contract
    assert es["csv_symbol"] == es["display"]


def test_after_expiry_nothing_names_the_dead_contract():
    """The bug: on 2026-10-04 the service was still asking Schwab for /ESU26,
    which had expired sixteen days earlier and quotes nothing."""
    entries = S.symbol_map(_AFTER_EXPIRY)
    qs = S.quote_symbols(entries)
    assert "/ESZ26" in qs and "/NQZ26" in qs
    assert not [q for q in qs if q.endswith("U26")]
    assert not [t for t in entries if "U26" in t["display"] + t["description"]]


def test_december_hands_to_march_of_the_next_year():
    es, nq = _futures_tiles(date(2026, 12, 10))
    assert (es["display"], es["quote_symbol"]) == ("/ES[H27]", "/ESH27")
    assert nq["description"] == "E-mini Nasdaq 100 future, Mar 2027"


def test_the_futures_frame_keeps_its_two_tiles_in_es_then_nq_order():
    """Cash Index reads SPX then NDX and the futures frame pairs with it."""
    for today in (_BEFORE_ROLL, _AFTER_EXPIRY, date(2027, 1, 4)):
        assert [t["display"][:3] for t in _futures_tiles(today)] == ["/ES", "/NQ"]


def test_only_the_two_futures_tiles_differ_across_a_roll():
    before = {t["display"]: t for t in S.symbol_map(_BEFORE_ROLL)}
    after = {t["display"]: t for t in S.symbol_map(_AFTER_EXPIRY)}
    assert set(before) ^ set(after) == {"/ES[U26]", "/NQ[U26]", "/ES[Z26]", "/NQ[Z26]"}
    for name in set(before) & set(after):
        assert before[name] == after[name]


def test_resolving_one_date_does_not_leak_into_the_next():
    """symbol_map builds fresh futures entries per call. Mutating the static
    table in place would leave the December contract behind for a caller that
    then asked about September."""
    S.symbol_map(_AFTER_EXPIRY)
    assert _futures_tiles(_BEFORE_ROLL)[0]["quote_symbol"] == "/ESU26"


def test_with_no_date_the_map_is_for_today(monkeypatch):
    monkeypatch.setattr(futures, "today_ct", lambda: _AFTER_EXPIRY)
    assert "/ES[Z26]" in {t["display"] for t in S.symbol_map()}
    assert "/ESZ26" in S.quote_symbols()
    monkeypatch.setattr(futures, "today_ct", lambda: _BEFORE_ROLL)
    assert "/ES[U26]" in {t["display"] for t in S.symbol_map()}


def test_the_roll_offset_comes_from_config(monkeypatch):
    from shared import symbols as shared_symbols
    monkeypatch.setattr(shared_symbols, "futures_roll_days", lambda: 0)
    assert _futures_tiles(date(2026, 9, 12))[0]["quote_symbol"] == "/ESU26"
    monkeypatch.setattr(shared_symbols, "futures_roll_days", lambda: 8)
    assert _futures_tiles(date(2026, 9, 12))[0]["quote_symbol"] == "/ESZ26"


def test_there_is_no_import_time_symbol_map():
    """A module-level SYMBOL_MAP is resolved once, when the service starts. The
    market service runs for weeks, so it would go on quoting an expired contract
    until something restarted it - the September literal with extra steps."""
    assert not hasattr(S, "SYMBOL_MAP")


@pytest.mark.parametrize("today", [_BEFORE_ROLL, _AFTER_EXPIRY])
def test_every_quote_tile_has_a_quote_symbol_on_either_side_of_a_roll(today):
    for t in S.symbol_map(today):
        assert t["display"], t
        if t["kind"] == "quote":
            assert t["quote_symbol"], t

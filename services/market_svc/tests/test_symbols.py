from services.market_svc import symbols as S


_EXPECTED_DISPLAYS = {
    "VIX", "VIX1D", "VIX3M", "SKEW",
    "Put/Call", "Net Prem", "MGTN",
    "$ADVN", "$DECN", "$ADVN-$DECN", "$TICK",
    "$DXY",
    "SPX", "NDX",
    "/ES[U26]", "/NQ[U26]",
    "SPY", "DIA", "QQQ", "IWM", "RSP", "QQEW",
    "BIG10", "NVDA", "MSFT", "GOOGL", "AMZN", "META", "AAPL", "TSLA",
    "AVGO", "PLTR", "AMD",
    "MTUM", "SPMO",
    "SMH", "XSD", "IGV", "QTUM", "XBI", "XRT", "XME",
    "XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY",
    "TLT", "HYG", "LQD",
    "GDLC", "VCX",
    "MCHI", "EWJ", "EWY", "INDA", "EWT", "EWZ", "EWA", "EWU", "EWW", "EWC",
}


def test_every_csv_symbol_is_mapped():
    # 68 tiles: base 55 + the Top 10 frame (a BIG10 composite + 10 constituents:
    # the Mag-7 + AVGO/PLTR/AMD) + the Net Prem external tile + the $MGTN index tile
    # (CBOE Magnificent Ten, added 2026-07-21).
    # (base 55 = $PCALL+$PCSP→ONE put/call tile; HYG-LQD dropped; $ADD/$ADSPD dropped;
    # XLB added; +10 country ETFs.)
    assert len(S.SYMBOL_MAP) == 68
    # A future mistyped ticker/display must fail: the full display set is pinned.
    assert len(_EXPECTED_DISPLAYS) == 68
    assert {t["display"] for t in S.SYMBOL_MAP} == _EXPECTED_DISPLAYS
    # Every entry has a non-empty display; every quote tile has a real quote_symbol.
    for t in S.SYMBOL_MAP:
        assert t["display"], t
        if t["kind"] == "quote":
            assert t["quote_symbol"], t


def test_categories_cover_the_expected_set_in_frame_order():
    assert S.CATEGORY_ORDER == [
        "Volatility", "Options Sentiment", "Market Internals / Breadth", "Currency",
        "Cash Index", "Equity Index Futures", "Broad-Market ETF", "Top 10",
        "Sector SPDR", "Thematic / Industry ETF", "Factor / Momentum ETF",
        "Fixed Income / Credit ETF", "Crypto / Alternatives", "Countries",
    ]
    # every mapped tile's category is in the order list
    assert {t["category"] for t in S.SYMBOL_MAP} <= set(S.CATEGORY_ORDER)


def test_translations_and_polarities():
    by_disp = {t["display"]: t for t in S.SYMBOL_MAP}
    assert by_disp["VIX"]["quote_symbol"] == "$VIX"
    assert by_disp["VIX"]["polarity"] == "inverted"
    assert by_disp["SPX"]["quote_symbol"] == "$SPX"
    assert by_disp["/ES[U26]"]["quote_symbol"] == "/ESU26"
    assert by_disp["$DXY"]["quote_symbol"] == "UUP"      # equivalent
    # CBOE Magnificent Ten index (ToS IMGTN:CGI -> Schwab API $MGTN)
    assert by_disp["MGTN"]["quote_symbol"] == "$MGTN"
    assert by_disp["MGTN"]["category"] == "Options Sentiment"
    assert by_disp["$DXY"]["polarity"] == "inverted"
    assert by_disp["TLT"]["polarity"] == "inverted"
    assert by_disp["XLP"]["polarity"] == "normal"        # defensive sector stays literal


def test_kinds():
    kinds = {t["display"]: t["kind"] for t in S.SYMBOL_MAP}
    assert kinds["$ADVN-$DECN"] == "spread"
    # the two Options-Sentiment tiles are external (Put/Call fed from sentiment,
    # Net Prem fed from cache:options:matrix)
    ext = [t for t in S.SYMBOL_MAP if t["kind"] == "external"]
    assert {t["display"] for t in ext} == {"Put/Call", "Net Prem"}
    assert all(t["category"] == "Options Sentiment" for t in ext)


def test_big10_basket():
    by_disp = {t["display"]: t for t in S.SYMBOL_MAP}
    mag = by_disp["BIG10"]
    assert mag["kind"] == "basket" and mag["category"] == "Top 10"
    assert mag["basket"] == ("NVDA", "MSFT", "GOOGL", "AMZN", "META", "AAPL", "TSLA",
                             "AVGO", "PLTR", "AMD")
    # each constituent is ALSO its own quote tile in the same frame
    for sym in mag["basket"]:
        assert by_disp[sym]["kind"] == "quote"
        assert by_disp[sym]["category"] == "Top 10"


def test_quote_symbols_are_the_real_ones_only():
    qs = S.quote_symbols()
    # includes spread legs + basket members, excludes computed/external composites
    assert "$ADVN" in qs and "$DECN" in qs and "HYG" in qs and "LQD" in qs
    assert "NVDA" in qs and "TSLA" in qs          # basket members are fetched
    assert "$ADVN-$DECN" not in qs and "HYG-LQD" not in qs and "BIG10" not in qs
    assert "$PCALL" not in qs


def test_quote_symbols_deduped():
    # $ADVN/$DECN each appear as both a quote tile AND a leg of the $ADVN-$DECN
    # spread — the dedup in quote_symbols() must collapse them.
    qs = S.quote_symbols()
    assert len(qs) == len(set(qs))

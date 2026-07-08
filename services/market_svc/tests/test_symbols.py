from services.market_svc import symbols as S


_EXPECTED_DISPLAYS = {
    "VIX", "VIX1D", "VIX3M", "SKEW",
    "Put/Call",
    "$ADVN", "$DECN", "$ADVN-$DECN", "$ADD", "$ADSPD", "$TICK",
    "$DXY",
    "SPX", "NDX",
    "/ES[U26]", "/NQ[U26]",
    "SPY", "DIA", "QQQ", "IWM", "RSP", "QQEW",
    "MTUM", "SPMO",
    "SMH", "XSD", "IGV", "QTUM", "XBI", "XRT", "XME",
    "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY",
    "TLT", "HYG", "LQD",
    "GDLC", "VCX",
}


def test_every_csv_symbol_is_mapped():
    # 48 CSV rows collapse to 46 tiles: $PCALL+$PCSP → ONE external put/call tile,
    # and the redundant HYG-LQD spread was dropped (HYG/LQD show individually).
    assert len(S.SYMBOL_MAP) == 46
    # A future mistyped ticker/display must fail: the full display set is pinned.
    assert len(_EXPECTED_DISPLAYS) == 46
    assert {t["display"] for t in S.SYMBOL_MAP} == _EXPECTED_DISPLAYS
    # Every entry has a non-empty display; every quote tile has a real quote_symbol.
    for t in S.SYMBOL_MAP:
        assert t["display"], t
        if t["kind"] == "quote":
            assert t["quote_symbol"], t


def test_categories_cover_the_expected_set_in_frame_order():
    assert S.CATEGORY_ORDER == [
        "Volatility", "Options Sentiment", "Market Internals / Breadth", "Currency",
        "Cash Index", "Equity Index Futures", "Broad-Market ETF",
        "Sector SPDR", "Thematic / Industry ETF", "Factor / Momentum ETF",
        "Fixed Income / Credit ETF", "Crypto / Alternatives",
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
    assert by_disp["$DXY"]["polarity"] == "inverted"
    assert by_disp["TLT"]["polarity"] == "inverted"
    assert by_disp["XLP"]["polarity"] == "normal"        # defensive sector stays literal


def test_kinds():
    kinds = {t["display"]: t["kind"] for t in S.SYMBOL_MAP}
    assert kinds["$ADVN-$DECN"] == "spread"
    # the collapsed put/call tile is external (fed from sentiment)
    ext = [t for t in S.SYMBOL_MAP if t["kind"] == "external"]
    assert len(ext) == 1 and ext[0]["category"] == "Options Sentiment"


def test_quote_symbols_are_the_real_ones_only():
    qs = S.quote_symbols()
    # includes spread legs, excludes computed/external
    assert "$ADVN" in qs and "$DECN" in qs and "HYG" in qs and "LQD" in qs
    assert "$ADVN-$DECN" not in qs and "HYG-LQD" not in qs
    assert "$PCALL" not in qs


def test_quote_symbols_deduped():
    # $ADVN/$DECN each appear as both a quote tile AND a leg of the $ADVN-$DECN
    # spread — the dedup in quote_symbols() must collapse them.
    qs = S.quote_symbols()
    assert len(qs) == len(set(qs))

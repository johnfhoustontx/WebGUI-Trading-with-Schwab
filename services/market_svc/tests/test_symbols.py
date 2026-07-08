from services.market_svc import symbols as S


def test_every_csv_symbol_is_mapped():
    # 48 CSV rows, but $PCALL+$PCSP collapse to ONE external put/call tile,
    # so the dashboard has 47 tiles.
    assert len(S.SYMBOL_MAP) == 47


def test_categories_cover_the_expected_set_in_frame_order():
    assert S.CATEGORY_ORDER == [
        "Volatility", "Options Sentiment", "Market Internals / Breadth", "Currency",
        "Cash Index", "Equity Index Futures", "Broad-Market ETF", "Custom Basket / Spread",
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
    assert kinds["HYG-LQD"] == "spread"
    # the collapsed put/call tile is external (fed from sentiment)
    ext = [t for t in S.SYMBOL_MAP if t["kind"] == "external"]
    assert len(ext) == 1 and ext[0]["category"] == "Options Sentiment"


def test_quote_symbols_are_the_real_ones_only():
    qs = S.quote_symbols()
    # includes spread legs, excludes computed/external
    assert "$ADVN" in qs and "$DECN" in qs and "HYG" in qs and "LQD" in qs
    assert "$ADVN-$DECN" not in qs and "HYG-LQD" not in qs
    assert "$PCALL" not in qs

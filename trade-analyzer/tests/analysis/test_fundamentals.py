from src.analysis.fundamentals import parse_schwab_fundamentals, parse_finviz_fundamentals, Fundamentals


class TestFundamentalsDataclass:
    def test_is_sufficient_true_with_three_core(self):
        f = Fundamentals(pe_ratio=18, rev_growth_ttm=0.10, eps_growth_ttm=0.12, roe=0.15)
        assert f.is_sufficient() is True

    def test_is_sufficient_false_with_two_core(self):
        f = Fundamentals(pe_ratio=18, rev_growth_ttm=0.10)
        assert f.is_sufficient() is False

    def test_is_sufficient_false_when_empty(self):
        assert Fundamentals().is_sufficient() is False

    def test_short_interest_does_not_count_toward_sufficiency(self):
        """``is_sufficient`` gates the Investor verdict on the four CORE
        valuation/growth fields. Short interest feeds the SHORT-side squeeze
        gate, not the fundamental read, so a symbol carrying only short
        interest must still take the insufficient-data path."""
        f = Fundamentals(short_int_to_float=30.0, short_int_day_to_cover=9.0)
        assert f.is_sufficient() is False


class TestParseSchwabFundamentals:
    def test_well_formed_payload(self):
        payload = {
            "fundamental": {
                "peRatio": 22.5, "pegRatio": 1.4,
                "epsTTM": 5.10, "revGrowthTTM": 0.18, "epsGrowthTTM": 0.22,
                "returnOnEquity": 0.21,
                "operatingMargin": 0.31, "operatingMarginYoy": 0.28,
                "freeCashFlow": 1.2e9,
                "epsSurprises": [0.06, 0.04, -0.01, 0.08],
                "guidanceDirection": "RAISED",
                "nextEarningsDate": "2026-05-15",
            }
        }
        f = parse_schwab_fundamentals(payload, as_of="2026-04-27")
        assert f.pe_ratio == 22.5
        assert f.peg_ratio == 1.4
        assert f.rev_growth_ttm == 0.18
        assert f.eps_growth_ttm == 0.22
        assert f.roe == 0.21
        assert f.margin_expanding is True
        assert f.fcf == 1.2e9
        assert f.last_eps_surprise == 0.08
        assert f.guidance == "RAISED"
        assert f.days_to_earnings == 18

    def test_short_interest_parses_as_percent_of_float(self):
        """Schwab serves these in the SAME payload every analyze already
        fetches — they were simply never read. Both stay in Schwab's units
        (percent of float, and days), matching the deep-dive engine that
        already consumes them, so squeeze thresholds read naturally."""
        payload = {"fundamental": {"shortIntToFloat": 12.5,
                                   "shortIntDayToCover": 4.2}}
        f = parse_schwab_fundamentals(payload, as_of="2026-04-27")
        assert f.short_int_to_float == 12.5
        assert f.short_int_day_to_cover == 4.2

    def test_short_interest_absent_stays_none(self):
        f = parse_schwab_fundamentals({"fundamental": {}}, as_of="2026-04-27")
        assert f.short_int_to_float is None
        assert f.short_int_day_to_cover is None

    def test_schwabs_zero_short_interest_is_a_SENTINEL_not_a_reading(self):
        """Schwab ships both fields but populates NEITHER — measured live
        2026-08-22: `shortIntToFloat` and `shortIntDayToCover` came back 0.0 for
        AAPL, TSLA, **GME** and **CVNA** alike, while `peRatio`/`returnOnEquity`/
        `marketCapFloat` in the same payload were correct. A listed, optionable
        US equity with literally zero short interest does not exist, so 0.0 here
        means "not served", exactly like Schwab's `volatility = -999`.

        Letting it through as a real reading would silently disable the
        short-side squeeze gate for EVERY symbol, forever, with nothing on
        screen to say why — the repo's documented "a 0.00%% cell is not proof of
        a flat tape" trap. The real source has to be finviz."""
        payload = {"fundamental": {"shortIntToFloat": 0.0,
                                   "shortIntDayToCover": 0.0}}
        f = parse_schwab_fundamentals(payload, as_of="2026-04-27")
        assert f.short_int_to_float is None
        assert f.short_int_day_to_cover is None

    def test_margin_contracting(self):
        payload = {"fundamental": {"operatingMargin": 0.20, "operatingMarginYoy": 0.28}}
        f = parse_schwab_fundamentals(payload, as_of="2026-04-27")
        assert f.margin_expanding is False

    def test_margin_unknown_when_one_missing(self):
        payload = {"fundamental": {"operatingMargin": 0.20}}
        f = parse_schwab_fundamentals(payload, as_of="2026-04-27")
        assert f.margin_expanding is None

    def test_missing_fields_become_none(self):
        f = parse_schwab_fundamentals({"fundamental": {}}, as_of="2026-04-27")
        assert f.pe_ratio is None
        assert f.peg_ratio is None
        assert f.rev_growth_ttm is None
        assert f.last_eps_surprise is None
        assert f.guidance is None
        assert f.days_to_earnings is None
        assert f.is_sufficient() is False

    def test_none_payload(self):
        f = parse_schwab_fundamentals(None, as_of="2026-04-27")
        assert f.pe_ratio is None
        assert f.is_sufficient() is False

    def test_empty_payload(self):
        f = parse_schwab_fundamentals({}, as_of="2026-04-27")
        assert f.is_sufficient() is False

    def test_malformed_earnings_date(self):
        payload = {"fundamental": {"nextEarningsDate": "not-a-date"}}
        f = parse_schwab_fundamentals(payload, as_of="2026-04-27")
        assert f.days_to_earnings is None

    def test_empty_surprises_list(self):
        payload = {"fundamental": {"epsSurprises": []}}
        f = parse_schwab_fundamentals(payload, as_of="2026-04-27")
        assert f.last_eps_surprise is None

    def test_sufficient_when_core_present(self):
        payload = {"fundamental": {
            "peRatio": 18, "revGrowthTTM": 0.10, "epsGrowthTTM": 0.12, "returnOnEquity": 0.15
        }}
        f = parse_schwab_fundamentals(payload, as_of="2026-04-27")
        assert f.is_sufficient() is True


class TestParseSchwabRealInstrumentPayload:
    """The REAL Schwab /instruments?projection=fundamental shape (captured live).

    Schwab uses ``revChangeTTM``/``epsChangePercentTTM`` in PERCENT units,
    ``returnOnEquity`` in PERCENT, ``operatingMarginTTM``/``MRQ`` instead of
    YoY, and omits earnings date / EPS surprises / guidance / FCF. The parser is
    a superset: it reads these real fields (percent->fraction) while still
    honoring the legacy speculative names tested above.
    """

    def _aapl(self):
        return {"fundamental": {
            "symbol": "AAPL", "peRatio": 35.93383, "pegRatio": 1.71939,
            "returnOnEquity": 141.4705, "revChangeTTM": 12.7573,
            "epsChangePercentTTM": 22.6973,
            "operatingMarginTTM": 27.1518, "operatingMarginMRQ": 26.6027,
            "epsTTM": 8.24905,
        }}

    def test_real_payload_maps_and_converts_units(self):
        f = parse_schwab_fundamentals(self._aapl(), as_of="2026-06-16")
        assert f.pe_ratio == 35.93383
        assert f.peg_ratio == 1.71939
        assert abs(f.rev_growth_ttm - 0.127573) < 1e-9   # percent -> fraction
        assert abs(f.eps_growth_ttm - 0.226973) < 1e-9
        assert abs(f.roe - 1.414705) < 1e-9              # 141.47% -> 1.4147
        # MRQ (26.60) < TTM (27.15) -> not expanding
        assert f.margin_expanding is False

    def test_real_payload_is_sufficient(self):
        assert parse_schwab_fundamentals(self._aapl(), as_of="2026-06-16").is_sufficient() is True

    def test_real_payload_missing_optionals_are_none(self):
        f = parse_schwab_fundamentals(self._aapl(), as_of="2026-06-16")
        assert f.fcf is None
        assert f.eps_surprises is None and f.last_eps_surprise is None
        assert f.guidance is None
        assert f.days_to_earnings is None  # not in the instruments payload

    def test_margin_expanding_when_mrq_above_ttm(self):
        payload = {"fundamental": {"operatingMarginTTM": 20.0, "operatingMarginMRQ": 24.0}}
        assert parse_schwab_fundamentals(payload, as_of="2026-06-16").margin_expanding is True

    def test_low_roe_percent_still_divided(self):
        # A 12% ROE comes back as 12.0 (percent) -> 0.12 (fraction).
        payload = {"fundamental": {"returnOnEquity": 12.0}}
        f = parse_schwab_fundamentals(payload, as_of="2026-06-16")
        assert abs(f.roe - 0.12) < 1e-9

    def test_legacy_fraction_roe_preserved(self):
        # Legacy speculative payloads passed ROE as a fraction (<=2); keep as-is.
        payload = {"fundamental": {"returnOnEquity": 0.21}}
        assert parse_schwab_fundamentals(payload, as_of="2026-06-16").roe == 0.21


class TestParseFinvizFundamentals:
    def test_well_formed_dict(self):
        fund = {
            "P/E": "22.50",
            "PEG": "1.40",
            "Sales Y/Y TTM": "18.50%",
            "EPS Y/Y TTM": "22.00%",
            "ROE": "21.00%",
            "Oper. Margin": "31.00%",
            "EPS Surprise": "8.00%",
            "Earnings": "May 28 AMC",
        }
        f = parse_finviz_fundamentals(fund, as_of="2026-04-27")
        assert f.pe_ratio == 22.5
        assert f.peg_ratio == 1.4
        assert abs(f.rev_growth_ttm - 0.185) < 1e-9
        assert abs(f.eps_growth_ttm - 0.22) < 1e-9
        assert abs(f.roe - 0.21) < 1e-9
        assert f.last_eps_surprise == 0.08
        assert f.eps_surprises == [0.08]
        assert f.days_to_earnings == 31
        assert f.is_sufficient() is True

    def test_dash_values_become_none(self):
        f = parse_finviz_fundamentals({"P/E": "-", "PEG": "-", "ROE": "-"}, as_of="2026-04-27")
        assert f.pe_ratio is None
        assert f.peg_ratio is None
        assert f.roe is None

    def test_missing_keys_become_none(self):
        f = parse_finviz_fundamentals({}, as_of="2026-04-27")
        assert f.pe_ratio is None
        assert f.is_sufficient() is False

    def test_none_input(self):
        f = parse_finviz_fundamentals(None, as_of="2026-04-27")
        assert f.is_sufficient() is False

    def test_earnings_date_in_past_rolls_to_next_year(self):
        f = parse_finviz_fundamentals({"Earnings": "Jan 15 AMC"}, as_of="2026-04-27")
        assert f.days_to_earnings is not None
        assert 250 <= f.days_to_earnings <= 270

    def test_sales_fallback_to_3_5y(self):
        # Sales Y/Y TTM missing - should pick first number from Sales past 3/5Y
        fund = {"P/E": "20", "PEG": "1.5", "Sales past 3/5Y": "8.50% 12.00%",
                "EPS Y/Y TTM": "10.00%", "ROE": "18.00%"}
        f = parse_finviz_fundamentals(fund, as_of="2026-04-27")
        assert abs(f.rev_growth_ttm - 0.085) < 1e-9

    def test_eps_fallback_to_this_year(self):
        # EPS Y/Y TTM missing - falls back to EPS this Y
        fund = {"EPS this Y": "15.00%"}
        f = parse_finviz_fundamentals(fund, as_of="2026-04-27")
        assert abs(f.eps_growth_ttm - 0.15) < 1e-9

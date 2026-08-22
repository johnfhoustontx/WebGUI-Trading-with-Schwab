"""Tests for the FINRA short-interest source (Phase 1, task 1.2 — the half
that is settled).

Schwab serves both short-interest fields as a 0.0 sentinel for every symbol
(see test_fundamentals), so the data has to come from somewhere else. FINRA
publishes the regulatory filing itself: free, official, and its terms permit
"non-commercial personal or professional use" plus derivative data. It gives
shares-short and a PRE-COMPUTED days-to-cover, but no float — the denominator
comes from Schwab's ``marketCapFloat``, which despite its name is float in
SHARES.

Every network call is stubbed here; the live shape was verified separately.
"""
import pytest

from services.trade_svc import short_interest as si


# ── the CSV FINRA actually returns ──────────────────────────────────────────
# Verified live 2026-08-22: content-type is text/plain and the body is CSV
# with every field quoted, NOT the JSON its docs imply. Empty fields are bare.
_CSV = (
    '"accountingYearMonthNumber","symbolCode","issueName","issuerServicesGroupExchangeCode",'
    '"marketClassCode","currentShortPositionQuantity","previousShortPositionQuantity",'
    '"stockSplitFlag","averageDailyVolumeQuantity","daysToCoverQuantity","revisionFlag",'
    '"changePercent","changePreviousNumber","settlementDate"\n'
    '"20260415","GME","GameStop Corp.","A","NYSE","61907606","55993560",,'
    '"6607000","9.37",,"10.56","5914046","2026-04-15"\n'
    '"20260415","AAPL","Apple Inc.","Q","NNM","134422787","130000000",,'
    '"39652000","3.39",,"3.40","4422787","2026-04-15"\n'
)


class TestParseCycle:
    def test_parses_the_quoted_csv_finra_serves(self):
        rows = si.parse_cycle(_CSV)
        assert len(rows) == 2
        gme = next(r for r in rows if r["symbol"] == "GME")
        assert gme["short_qty"] == 61907606
        assert gme["days_to_cover"] == pytest.approx(9.37)
        assert gme["settlement_date"] == "2026-04-15"

    def test_a_blank_body_yields_no_rows_rather_than_raising(self):
        assert si.parse_cycle("") == []
        assert si.parse_cycle("\n") == []

    def test_an_unparseable_row_is_skipped_not_fatal(self):
        """One malformed row must not cost the other 22,000."""
        bad = _CSV + '"20260415","ZZZZ","Bad Co","A","NYSE","not-a-number","0",,"0","",,"","","2026-04-15"\n'
        rows = si.parse_cycle(bad)
        assert {r["symbol"] for r in rows} == {"GME", "AAPL"}


class TestPercentOfFloat:
    def test_normal_case(self):
        # 61,907,606 short against a 408,810,860 float
        assert si.percent_of_float(61907606, 408810860) == pytest.approx(15.14, abs=0.01)

    def test_over_100_percent_is_UNKNOWN_not_extreme(self):
        """FINRA is NOT split-adjusted, and its ``stockSplitFlag`` only appears
        in the cycle AFTER a split — so a reverse split between settlement and
        today leaves a pre-split numerator over a post-split float. Measured
        live: BYND computed to 783% after a 1-for-30 reverse split.

        A number that large is a unit mismatch, not a squeeze. Returning it
        would fire the gate HARDEST exactly when the data is meaningless —
        the same shape as the NaN-clamps-to-the-high-bound bug this codebase
        keeps rediscovering."""
        assert si.percent_of_float(130476904, 16666000) is None

    def test_missing_or_nonsense_float_yields_none(self):
        assert si.percent_of_float(1000, None) is None
        assert si.percent_of_float(1000, 0) is None
        assert si.percent_of_float(1000, -5) is None

    def test_missing_short_quantity_yields_none(self):
        assert si.percent_of_float(None, 408810860) is None


class TestSqueezeFlag:
    """The gate fires on EITHER leg, deliberately.

    Float is the contested term: for CHWY, Schwab's float, Yahoo's float and
    shares-outstanding give 89% / 51% / 12% — three defensible answers
    straddling any single threshold. Days-to-cover is computed by FINRA from
    its own numerator and an exchange volume figure, so it never touches float
    at all. Requiring both would let the float disagreement veto a real
    signal; either-or keeps the robust leg live."""

    def test_high_percent_alone_fires(self):
        flag, why = si.squeeze_flag(pct_of_float=21.0, days_to_cover=2.0)
        assert flag is True and "21.0% of float" in why

    def test_high_days_to_cover_alone_fires(self):
        flag, why = si.squeeze_flag(pct_of_float=4.0, days_to_cover=12.5)
        assert flag is True and "12.5 days to cover" in why

    def test_neither_leg_does_not_fire(self):
        assert si.squeeze_flag(pct_of_float=4.0, days_to_cover=2.0) == (False, "")

    def test_no_data_does_not_fire_and_says_so(self):
        """Absence must not be read as safety OR as risk — the caller needs to
        know the gate could not be evaluated."""
        flag, why = si.squeeze_flag(pct_of_float=None, days_to_cover=None)
        assert flag is False
        assert "no short-interest data" in why.lower()

    def test_one_leg_present_is_still_evaluated(self):
        assert si.squeeze_flag(pct_of_float=None, days_to_cover=11.0)[0] is True
        assert si.squeeze_flag(pct_of_float=30.0, days_to_cover=None)[0] is True


class TestStore:
    @pytest.fixture
    def conn(self, tmp_path):
        c = si.init_db(tmp_path / "si.db")
        yield c
        si.close_db(c)

    def test_store_and_lookup_round_trip(self, conn):
        si.store_cycle(conn, si.parse_cycle(_CSV))
        row = si.lookup(conn, "GME")
        assert row["short_qty"] == 61907606
        assert row["days_to_cover"] == pytest.approx(9.37)

    def test_lookup_returns_the_newest_settlement_for_a_symbol(self, conn):
        si.store_cycle(conn, si.parse_cycle(_CSV))
        si.store_cycle(conn, [{"symbol": "GME", "short_qty": 70000000,
                               "days_to_cover": 11.0, "avg_daily_volume": 6000000,
                               "settlement_date": "2026-04-30"}])
        assert si.lookup(conn, "GME")["short_qty"] == 70000000

    def test_an_unknown_symbol_is_none_not_an_exception(self, conn):
        si.store_cycle(conn, si.parse_cycle(_CSV))
        assert si.lookup(conn, "NOPE") is None

    def test_symbols_are_matched_case_insensitively(self, conn):
        si.store_cycle(conn, si.parse_cycle(_CSV))
        assert si.lookup(conn, "gme") is not None

    def test_latest_settlement_reports_what_the_store_holds(self, conn):
        assert si.latest_settlement(conn) is None
        si.store_cycle(conn, si.parse_cycle(_CSV))
        assert si.latest_settlement(conn) == "2026-04-15"

    def test_store_cycle_never_raises(self, conn):
        conn.close()
        assert si.store_cycle(conn, si.parse_cycle(_CSV)) is False


class TestEnrich:
    """The join the whole exercise exists for: FINRA numerator, Schwab float."""

    @pytest.fixture
    def conn(self, tmp_path):
        c = si.init_db(tmp_path / "si.db")
        si.store_cycle(c, si.parse_cycle(_CSV))
        yield c
        si.close_db(c)

    def test_returns_percent_and_days_to_cover(self, conn):
        got = si.for_symbol(conn, "GME", float_shares=408810860)
        assert got["pct_of_float"] == pytest.approx(15.14, abs=0.01)
        assert got["days_to_cover"] == pytest.approx(9.37)
        assert got["settlement_date"] == "2026-04-15"
        assert got["squeeze"] is True

    def test_days_to_cover_survives_a_missing_float(self, conn):
        """The robust leg must not be lost because the contested one is.

        GME's 9.37 days sits just UNDER the 10-day threshold, so the gate
        correctly does not fire here — the point is that the reading is still
        reported rather than nulled along with the float."""
        got = si.for_symbol(conn, "GME", float_shares=None)
        assert got["pct_of_float"] is None
        assert got["days_to_cover"] == pytest.approx(9.37)
        assert got["squeeze"] is False

    def test_a_high_days_to_cover_fires_with_no_float_at_all(self, conn):
        """The case the either-or rule exists for: no usable float, but FINRA's
        own days-to-cover is unambiguous."""
        si.store_cycle(conn, [{"symbol": "CROWDED", "short_qty": 5_000_000,
                               "days_to_cover": 14.2, "avg_daily_volume": 352_000,
                               "settlement_date": "2026-04-15"}])
        got = si.for_symbol(conn, "CROWDED", float_shares=None)
        assert got["pct_of_float"] is None
        assert got["squeeze"] is True
        assert "14.2 days to cover" in got["squeeze_reason"]

    def test_a_symbol_finra_does_not_carry_returns_none(self, conn):
        """Renames are the live failure mode — Block's SQ became XYZ, and
        FINRA keys on the CURRENT symbol, so a stale ticker silently misses."""
        assert si.for_symbol(conn, "SQ", float_shares=1000000) is None

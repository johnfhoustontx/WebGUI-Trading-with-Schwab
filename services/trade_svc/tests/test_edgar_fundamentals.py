"""Tests for free cash flow from SEC EDGAR.

`Fundamentals.fcf` has always been None — Schwab's payload carries no cash
flow — so the Investor gate that caps a stock at HOLD on negative free cash
flow paired with a missed quarter has never once fired. EDGAR files the two
components, so free cash flow is computable from the primary record.

**EDGAR cannot supply earnings surprises** and this module does not pretend to:
a surprise is reported minus ESTIMATE, and EDGAR holds no estimates. Probed
live 2026-08-23 across all 629 us-gaap concepts Micron files — the only
"estimate" hits are accounting disclosures. Surprises stay with Alpha Vantage.

Network is stubbed throughout.
"""
import datetime as dt
import json

import pytest

from services.trade_svc import edgar_fundamentals as ef


def _fact(start, end, val, fy, form="10-K", fp="FY", filed="2026-10-01"):
    return {"start": start, "end": end, "val": val, "fy": fy, "fp": fp,
            "form": form, "filed": filed}


_FACTS = json.dumps({"facts": {"us-gaap": {
    "NetCashProvidedByUsedInOperatingActivities": {"units": {"USD": [
        _fact("2023-09-01", "2024-08-29", 8_507_000_000, 2024),
        _fact("2024-08-30", "2025-08-28", 17_520_000_000, 2025),
        # a QUARTER — must not be mistaken for a year
        _fact("2025-05-30", "2025-08-28", 5_000_000_000, 2025,
              form="10-Q", fp="Q4"),
    ]}},
    "PaymentsToAcquirePropertyPlantAndEquipment": {"units": {"USD": [
        _fact("2023-09-01", "2024-08-29", 8_386_000_000, 2024),
        _fact("2024-08-30", "2025-08-28", 15_856_000_000, 2025),
    ]}},
}}})


class TestParse:
    def test_it_computes_free_cash_flow_per_fiscal_year(self):
        rows = ef.parse_annual_fcf(_FACTS)
        by_fy = {r["fiscal_year"]: r for r in rows}
        assert by_fy[2025]["fcf"] == pytest.approx(1_664_000_000)
        assert by_fy[2024]["fcf"] == pytest.approx(121_000_000)

    def test_rows_come_back_chronological(self):
        rows = ef.parse_annual_fcf(_FACTS)
        assert [r["fiscal_year"] for r in rows] == [2024, 2025]

    def test_a_QUARTERLY_row_is_never_treated_as_a_year(self):
        """The single most damaging mistake available here: a 3-month cash-flow
        figure read as annual understates operating cash flow four-fold and can
        flip the sign of free cash flow, firing a gate that should not fire."""
        rows = ef.parse_annual_fcf(_FACTS)
        assert all(r["ocf"] != 5_000_000_000 for r in rows)

    def test_a_year_missing_its_capex_is_DROPPED_not_treated_as_zero(self):
        """FCF = OCF - capex. Absent capex silently becomes FCF = OCF, which
        for a capital-intensive filer turns a large negative into a large
        positive — the exact inversion the gate reads."""
        payload = json.dumps({"facts": {"us-gaap": {
            "NetCashProvidedByUsedInOperatingActivities": {"units": {"USD": [
                _fact("2024-08-30", "2025-08-28", 17_520_000_000, 2025)]}},
            "PaymentsToAcquirePropertyPlantAndEquipment": {"units": {"USD": []}},
        }}})
        assert ef.parse_annual_fcf(payload) == []

    def test_it_falls_back_to_the_other_capex_spellings(self):
        """Utilities and REITs file `PaymentsToAcquireProductiveAssets`."""
        payload = json.dumps({"facts": {"us-gaap": {
            "NetCashProvidedByUsedInOperatingActivities": {"units": {"USD": [
                _fact("2024-01-01", "2024-12-31", 1_000, 2024)]}},
            "PaymentsToAcquireProductiveAssets": {"units": {"USD": [
                _fact("2024-01-01", "2024-12-31", 400, 2024)]}},
        }}})
        assert ef.parse_annual_fcf(payload)[0]["fcf"] == 600

    def test_junk_yields_nothing_rather_than_raising(self):
        for body in ("", "<html>", "{}", '{"facts": {}}', "null"):
            assert ef.parse_annual_fcf(body) == []


class TestTickerLookup:
    _MAP = json.dumps({
        "0": {"cik_str": 723125, "ticker": "MU", "title": "MICRON TECHNOLOGY"},
        "1": {"cik_str": 1067983, "ticker": "BRK-B", "title": "BERKSHIRE"},
    })

    def test_it_maps_a_ticker_to_a_zero_padded_cik(self):
        assert ef.parse_ticker_map(self._MAP)["MU"] == "0000723125"

    def test_class_shares_use_the_SEC_dash_spelling(self):
        """EDGAR writes BRK-B where most feeds write BRK.B or BRK/B."""
        m = ef.parse_ticker_map(self._MAP)
        assert ef.cik_for("BRK.B", m) == "0001067983"
        assert ef.cik_for("BRK/B", m) == "0001067983"
        assert ef.cik_for("brk-b", m) == "0001067983"

    def test_an_unknown_ticker_is_None(self):
        assert ef.cik_for("ZZZZ", ef.parse_ticker_map(self._MAP)) is None


class TestStoreAndRead:
    @pytest.fixture()
    def conn(self, tmp_path):
        c = ef.init_db(tmp_path / "edgar.db")
        ef.store(c, "MU", ef.parse_annual_fcf(_FACTS))
        return c

    def test_latest_fcf_is_the_most_recent_fiscal_year(self, conn):
        got = ef.latest_fcf(conn, "MU")
        assert got["fcf"] == pytest.approx(1_664_000_000)
        assert got["fiscal_year"] == 2025

    def test_an_unknown_symbol_is_None(self, conn):
        assert ef.latest_fcf(conn, "ZZZZ") is None

    def test_storing_twice_does_not_duplicate_years(self, conn):
        ef.store(conn, "MU", ef.parse_annual_fcf(_FACTS))
        cur = conn.cursor()
        n = cur.execute("SELECT COUNT(*) FROM fcf_annual WHERE symbol='MU'").fetchone()[0]
        assert n == 2

    def test_a_read_works_without_a_row_factory(self, tmp_path):
        """The trap that bit the earnings calendar the same day."""
        import sqlite3
        p = tmp_path / "edgar.db"
        c = ef.init_db(p)
        ef.store(c, "MU", ef.parse_annual_fcf(_FACTS))
        c.close()
        assert ef.latest_fcf(sqlite3.connect(str(p)), "MU")["fiscal_year"] == 2025

    def test_a_symbol_with_no_filings_is_remembered(self, conn):
        ef.store(conn, "ZZZZ", [])
        assert ef.is_due(conn, "ZZZZ") is False
        assert ef.latest_fcf(conn, "ZZZZ") is None


class TestFreshness:
    @pytest.fixture()
    def conn(self, tmp_path):
        return ef.init_db(tmp_path / "edgar.db")

    def test_a_symbol_never_fetched_is_due(self, conn):
        assert ef.is_due(conn, "MU") is True

    def test_annual_data_is_not_re_asked_daily(self, conn):
        ef.store(conn, "MU", ef.parse_annual_fcf(_FACTS))
        assert ef.is_due(conn, "MU") is False

    def test_it_becomes_due_after_the_refresh_window(self, conn):
        ef.store(conn, "MU", ef.parse_annual_fcf(_FACTS))
        later = dt.datetime.now(dt.timezone.utc) + dt.timedelta(
            days=ef.REFRESH_AFTER_DAYS + 1)
        assert ef.is_due(conn, "MU", now=later) is True


class TestRefresh:
    @pytest.fixture()
    def conn(self, tmp_path):
        return ef.init_db(tmp_path / "edgar.db")

    def test_it_stores_what_it_fetched(self, conn, monkeypatch):
        monkeypatch.setattr(ef, "_fetch_ticker_map", lambda: TestTickerLookup._MAP)
        monkeypatch.setattr(ef, "_fetch_facts", lambda cik: _FACTS)
        assert ef.refresh(conn, "MU") is True
        assert ef.latest_fcf(conn, "MU")["fiscal_year"] == 2025

    def test_an_unlisted_ticker_does_not_reach_the_filings_endpoint(
            self, conn, monkeypatch):
        """No CIK means no request to make. Asking anyway is a wasted call and
        a 404 in the log every time the symbol is analysed."""
        calls = []
        monkeypatch.setattr(ef, "_fetch_ticker_map", lambda: TestTickerLookup._MAP)
        monkeypatch.setattr(ef, "_fetch_facts",
                            lambda cik: calls.append(cik) or _FACTS)
        assert ef.refresh(conn, "ZZZZ") is False
        assert calls == []

    def test_refresh_never_raises(self, conn, monkeypatch):
        def boom(*_a):
            raise RuntimeError("network")
        monkeypatch.setattr(ef, "_fetch_ticker_map", boom)
        assert ef.refresh(conn, "MU") is False

    def test_a_failed_fetch_is_not_cached_as_no_filings(self, conn, monkeypatch):
        """Same rule the earnings history learned the hard way: a transport
        failure is not evidence that the company files nothing."""
        monkeypatch.setattr(ef, "_fetch_ticker_map", lambda: TestTickerLookup._MAP)
        def boom(_cik):
            raise RuntimeError("503")
        monkeypatch.setattr(ef, "_fetch_facts", boom)
        assert ef.refresh(conn, "MU") is False
        assert ef.is_due(conn, "MU") is True


class TestAThrottleIsNotEvidenceAboutAFiler:
    """SEC throttles bursts. Measured live 2026-08-23: four back-to-back
    `refresh` calls each re-fetched the 218KB ticker map — because a failed
    fetch caches nothing — and SEC answered 403 to all of them.

    The dangerous half is what an empty map implies. `cik_for` returns None
    for every symbol, and a None CIK is REMEMBERED as "not an SEC filer" for
    90 days. One throttled minute would have marked the entire universe as
    unfilable until November."""

    @pytest.fixture()
    def conn(self, tmp_path):
        return ef.init_db(tmp_path / "edgar.db")

    def test_an_empty_ticker_map_does_not_brand_a_symbol_unfilable(
            self, conn, monkeypatch):
        monkeypatch.setattr(ef, "_fetch_ticker_map", lambda: "")
        assert ef.refresh(conn, "MU") is False
        assert ef.is_due(conn, "MU") is True          # retried, not written off

    def test_a_map_fetch_failure_does_not_brand_a_symbol_unfilable(
            self, conn, monkeypatch):
        def boom():
            raise RuntimeError("403")
        monkeypatch.setattr(ef, "_fetch_ticker_map", boom)
        assert ef.refresh(conn, "MU") is False
        assert ef.is_due(conn, "MU") is True

    def test_a_symbol_genuinely_absent_from_a_GOOD_map_is_remembered(
            self, conn, monkeypatch):
        """The distinction: a populated map that lacks the ticker is a real
        answer — an ETF, an index, a foreign line."""
        monkeypatch.setattr(ef, "_fetch_ticker_map",
                            lambda: TestTickerLookup._MAP)
        assert ef.refresh(conn, "SPY") is False
        assert ef.is_due(conn, "SPY") is False

    def test_the_map_is_fetched_ONCE_for_many_symbols(self, conn, monkeypatch):
        """Re-fetching 218KB per symbol is what tripped the throttle."""
        n = []
        monkeypatch.setattr(ef, "_fetch_ticker_map",
                            lambda: n.append(1) or TestTickerLookup._MAP)
        monkeypatch.setattr(ef, "_fetch_facts", lambda cik: _FACTS)
        for sym in ("MU", "MU", "MU"):
            ef.refresh(conn, sym)
        assert len(n) == 1


class TestTheRequestHeaders:
    def test_it_asks_for_gzip_ONLY(self):
        """Measured live 2026-08-23: SEC's CDN answers 403 to
        `Accept-Encoding: gzip, deflate` and 200 to `gzip` — same URL, same
        User-Agent, one header apart. Nothing documents it, and the symptom is
        a Forbidden that reads like a User-Agent policy or a rate limit. A
        source check, because only a live request can prove the behaviour and
        only this can stop someone "tidying" the header back."""
        import inspect
        src = inspect.getsource(ef._get)
        assert '"gzip, deflate"' not in src
        assert '"Accept-Encoding": "gzip"' in src

    def test_it_declares_a_user_agent(self):
        """SEC's fair-access policy requires one; requests without are refused."""
        assert ef._user_agent()

    def test_the_default_user_agent_carries_no_personal_data(self):
        ua = ef._DEFAULT_UA.lower()
        assert "@" not in ua


class TestPeriodIdentityComesFromTheDatesNotTheFilingYear:
    """Two bugs found the moment this met real filings, both silent.

    1. `fy` is the fiscal year of the FILING, not of the period. A 10-K
       carries comparatives, so NVDA's `fy=2010` covers period-ends 2008,
       2009, 2010 AND 2011. Keying on it collapses four years into one and
       picks an arbitrary winner.
    2. A filer can CHANGE concepts over time. NVDA's
       `PaymentsToAcquirePropertyPlantAndEquipment` stops at fy 2011; taking
       the first present concept and stopping pinned it to 2011 data. The
       observable symptom was NVDA reporting free cash flow for FY2011.
    """

    _COMPARATIVES = json.dumps({"facts": {"us-gaap": {
        "NetCashProvidedByUsedInOperatingActivities": {"units": {"USD": [
            # ONE filing, three comparative years, all tagged fy=2026
            _fact("2023-01-30", "2024-01-28", 100, 2026),
            _fact("2024-01-29", "2025-01-26", 200, 2026),
            _fact("2025-01-27", "2026-01-25", 300, 2026),
        ]}},
        "PaymentsToAcquirePropertyPlantAndEquipment": {"units": {"USD": [
            _fact("2023-01-30", "2024-01-28", 10, 2026),
        ]}},
        # the filer SWITCHED concepts for the later years
        "PaymentsToAcquireProductiveAssets": {"units": {"USD": [
            _fact("2024-01-29", "2025-01-26", 20, 2026),
            _fact("2025-01-27", "2026-01-25", 30, 2026),
        ]}},
    }}})

    def test_comparatives_in_one_filing_are_separate_years(self):
        rows = ef.parse_annual_fcf(self._COMPARATIVES)
        assert len(rows) == 3, rows

    def test_the_year_label_comes_from_the_period_end(self):
        rows = ef.parse_annual_fcf(self._COMPARATIVES)
        assert [r["fiscal_year"] for r in rows] == [2024, 2025, 2026]

    def test_capex_concepts_are_MERGED_not_first_wins(self):
        rows = {r["fiscal_year"]: r for r in ef.parse_annual_fcf(self._COMPARATIVES)}
        assert rows[2024]["fcf"] == 90       # 100 - 10, old concept
        assert rows[2026]["fcf"] == 270      # 300 - 30, new concept

    def test_the_latest_year_is_the_latest_PERIOD(self):
        rows = ef.parse_annual_fcf(self._COMPARATIVES)
        assert rows[-1]["period_end"] == "2026-01-25"

"""The Strategy Finder reads the earnings calendar too (gap assessment A5).

``swing_scan`` has taken an ``earnings_date`` and gated per signal since the
0-DTE-bucket fix, and ``income_scan`` supplies one — but the Strategy Finder's
handler passed nothing, so ``if earnings_date and ...`` was always False and the
gate was a no-op on that whole surface while reading exactly like protection.
That is the same shape as the defect A1 fixed on the Market Scanner, one surface
over: three scan paths, and the gate was live on one.

⚠ ``not_listed`` must NOT drop the symbol. With no Alpha Vantage key it is the
answer for every name, so failing closed would empty the page. The row is
STAMPED instead, which is the repo's "never print a number you did not read"
rule applied to a gate: a row that skipped the check must not look like a row
that passed it.
"""
import datetime as dt

import pytest

from services.options_svc import compute, handlers
from shared.bus import Bus


def _sig(**kw):
    base = {"id": "SPY_PCS_x", "symbol": "SPY", "type": "PCS",
            "expiration": (dt.date.today() + dt.timedelta(days=20)).isoformat(),
            "dte": 20, "composite_score": 70.0, "grade": "Good"}
    base.update(kw)
    return base


@pytest.fixture
def swing_seam(monkeypatch):
    """Capture what the handler hands ``compute.swing_scan``."""
    seen = {}

    def _spy(**kwargs):
        seen.update(kwargs)
        return {"signals": [_sig()], "view": {}, "filtered_out": 0}

    monkeypatch.setattr(compute, "swing_scan", _spy)
    return seen


def test_the_handler_passes_the_symbols_report_date(swing_seam, monkeypatch):
    report = (dt.date.today() + dt.timedelta(days=10)).isoformat()
    monkeypatch.setattr(compute, "scan_earnings",
                        lambda s: ("upcoming", report))

    handlers.swing_scan(Bus(fake=True), {"symbol": "SPY"})

    assert swing_seam["earnings_date"] == report


def test_a_symbol_with_no_date_passes_None_rather_than_skipping_the_scan(
        swing_seam, monkeypatch):
    """Failing closed here would empty the page for every symbol whenever vendor
    coverage thins - the documented reason ``not_listed`` does not block."""
    monkeypatch.setattr(compute, "scan_earnings", lambda s: ("not_listed", None))

    handlers.swing_scan(Bus(fake=True), {"symbol": "SPY"})

    assert swing_seam["earnings_date"] is None
    assert "symbol" in swing_seam            # the scan still ran


def test_every_row_is_stamped_with_the_coverage_it_got(monkeypatch):
    """So a row that skipped the check cannot be mistaken for one that passed."""
    monkeypatch.setattr(compute, "scan_earnings", lambda s: ("not_listed", None))
    monkeypatch.setattr(compute, "swing_scan",
                        lambda **kw: {"signals": [_sig(), _sig(id="b")],
                                      "view": {}, "filtered_out": 0})
    bus = Bus(fake=True)

    handlers.swing_scan(bus, {"symbol": "SPY"})

    rows = bus.cache_get(handlers.CACHE_SWING).payload["signals"]
    assert rows and all(r["earnings_status"] == "not_listed" for r in rows)


def test_an_upcoming_report_stamps_upcoming(monkeypatch):
    report = (dt.date.today() + dt.timedelta(days=10)).isoformat()
    monkeypatch.setattr(compute, "scan_earnings", lambda s: ("upcoming", report))
    monkeypatch.setattr(compute, "swing_scan",
                        lambda **kw: {"signals": [_sig()], "view": {},
                                      "filtered_out": 0})
    bus = Bus(fake=True)

    handlers.swing_scan(bus, {"symbol": "SPY"})

    assert bus.cache_get(handlers.CACHE_SWING).payload["signals"][0][
        "earnings_status"] == "upcoming"


def test_a_lookup_failure_does_not_cost_the_scan(swing_seam, monkeypatch):
    """A gate that raises costs the user the page. ``_income_earnings`` already
    never raises; this pins that the handler does not reintroduce the risk."""
    def _boom(_s):
        raise RuntimeError("earnings store is locked")

    monkeypatch.setattr(compute, "scan_earnings", _boom)

    handlers.swing_scan(Bus(fake=True), {"symbol": "SPY"})

    assert swing_seam["earnings_date"] is None


def test_scan_earnings_is_the_same_lookup_the_income_window_uses(monkeypatch):
    """One store, one reader. A second lookup would be a second answer."""
    monkeypatch.setattr(compute, "_income_earnings",
                        lambda s, db_path=None: ("upcoming", "2026-10-20"))
    assert compute.scan_earnings("SPY") == ("upcoming", "2026-10-20")

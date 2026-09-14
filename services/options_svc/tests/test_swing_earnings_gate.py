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


# ── Flag mode: the Strategy Finder keeps a candidate that spans a report and
# tags it (operator decision 2026-09-14). The drop stays the default. ─────────
BANDS = (-0.2, -0.1, 0.1, 0.2, 0.1)


def _report_in(days):
    return (dt.date.today() + dt.timedelta(days=days)).isoformat()


def test_flag_mode_keeps_a_spanning_candidate_and_stamps_it(scan_env):
    report = _report_in(20)
    out = compute.swing_scan("SPY", 0, None, *BANDS, earnings_date=report,
                             earnings_mode="flag", every_expiry=True, payoff=False)
    late = [s for s in out["signals"] if compute._latest_expiration(s) >= report]
    early = [s for s in out["signals"] if compute._latest_expiration(s) < report]
    assert late and early
    assert all(s["spans_earnings"] is True and s["earnings_date"] == report for s in late)
    assert all("spans_earnings" not in s and "earnings_date" not in s for s in early)


def test_flag_mode_keeps_every_row_the_drop_would_have_removed(scan_env):
    """Same rows as a scan with no report at all: the flag removes nothing."""
    report = _report_in(20)
    flagged = compute.swing_scan("SPY", 0, None, *BANDS, earnings_date=report,
                                 earnings_mode="flag", every_expiry=True, payoff=False)
    clean = compute.swing_scan("SPY", 0, None, *BANDS, every_expiry=True, payoff=False)

    def key(s):
        return (s["type"], compute._latest_expiration(s),
                tuple(leg.get("strike") for leg in s.get("legs") or []))

    assert clean["signals"]
    assert sorted(map(key, flagged["signals"])) == sorted(map(key, clean["signals"]))


def test_flag_mode_hands_screen_spreads_no_date(scan_env, monkeypatch):
    seen = {}
    real = compute.se.screen_spreads

    def _spy(*a, **k):
        seen["earnings_date"] = k.get("earnings_date", "absent")
        return real(*a, **k)

    monkeypatch.setattr(compute.se, "screen_spreads", _spy)
    compute.swing_scan("SPY", 0, None, *BANDS, earnings_date=_report_in(20),
                       earnings_mode="flag", payoff=False)
    assert seen["earnings_date"] is None


def test_a_calendar_is_judged_on_its_back_month_in_flag_mode(scan_env):
    """Front (10 or 14 DTE) before the report, back (38) after it: flagged in flag
    mode and dropped in drop mode - its ``expiration`` alone would read clean."""
    report = _report_in(20)
    e = scan_env.exp_by_dte
    flagged = compute.swing_scan("SPY", 0, None, *BANDS, families=("CALENDAR",),
                                 earnings_date=report, earnings_mode="flag",
                                 every_expiry=True, payoff=False)
    straddling = [s for s in flagged["signals"]
                  if s["expiration"] < report <= compute._latest_expiration(s)]
    assert straddling
    assert {s["expiration"] for s in straddling} <= {e[10], e[14]}
    assert all(s["spans_earnings"] is True and s["earnings_date"] == report
               for s in straddling)
    dropped = compute.swing_scan("SPY", 0, None, *BANDS, families=("CALENDAR",),
                                 earnings_date=report, every_expiry=True, payoff=False)
    assert not [s for s in dropped["signals"]
                if s["expiration"] < report <= compute._latest_expiration(s)]


def test_drop_mode_is_the_default_and_unchanged(scan_env):
    report = _report_in(20)
    out = compute.swing_scan("SPY", 0, None, *BANDS, earnings_date=report,
                             every_expiry=True, payoff=False)
    explicit = compute.swing_scan("SPY", 0, None, *BANDS, earnings_date=report,
                                  every_expiry=True, payoff=False, earnings_mode="drop")
    assert out["signals"]
    assert all(compute._latest_expiration(s) < report for s in out["signals"])
    assert all("spans_earnings" not in s for s in out["signals"])

    def strip(obj):
        """Two scans stamp different build times, nested ones included."""
        if isinstance(obj, dict):
            return {k: strip(v) for k, v in obj.items() if k != "timestamp"}
        if isinstance(obj, list):
            return [strip(v) for v in obj]
        return obj

    assert strip(out) == strip(explicit)


@pytest.mark.parametrize("mode", ["Flag", "tag", "", None])
def test_an_unknown_earnings_mode_is_refused_before_any_fetch(mode, monkeypatch):
    """A typo must not quietly become the drop or the flag."""
    fetched = []
    monkeypatch.setattr(compute, "fetch_scan_chain",
                        lambda *a, **k: fetched.append(1) or (None, 0))
    with pytest.raises(ValueError):
        compute.swing_scan("SPY", 0, None, *BANDS, earnings_mode=mode)
    assert fetched == []


def _one_row_scan(monkeypatch, *, trade_type, dte, report, mode):
    """swing_scan over ONE spread row expiring ``dte`` days from today."""
    row = {"symbol": "ORCL", "type": "PCS", "short_strike": 145.0,
           "long_strike": 143.0, "short_mark": 1.2, "long_mark": 0.6,
           "credit": 0.44, "max_loss": 1.56, "expiration": _report_in(dte),
           "dte": dte, "underlying_price": 163.0}
    monkeypatch.setattr(compute, "fetch_scan_chain",
                        lambda s, d: ({"underlyingPrice": 163.0, "putExpDateMap": {},
                                       "callExpDateMap": {}}, 0))
    monkeypatch.setattr(compute._proxy.schwab_client, "get_quote",
                        lambda *a, **kw: {"last": 163.0})
    monkeypatch.setattr(compute.se, "fetch_price_history", lambda *a, **kw: {"h": 1})
    monkeypatch.setattr(compute.se, "calc_technicals",
                        lambda *a, **kw: {"trend": "NEUTRAL", "rsi14": 50,
                                          "price": 163.0, "sma20": 163.0})
    monkeypatch.setattr(compute, "run_iv_analysis",
                        lambda *a, **kw: {"iv_rank": 50.0, "expected_moves":
                                          {"daily": {"move_dollars": 5.0}}})
    monkeypatch.setattr(compute.se, "screen_spreads", lambda *a, **kw: [dict(row)])
    monkeypatch.setattr(compute.se, "build_iron_condors", lambda *a, **kw: [])
    monkeypatch.setattr(compute, "_passes_swing_cut", lambda *a, **kw: True)
    return compute.swing_scan("ORCL", 0, None, *BANDS, trade_type=trade_type,
                              earnings_date=report, earnings_mode=mode,
                              payoff=False)["signals"]


@pytest.mark.parametrize("trade_type,dte,report_days", [
    ("SWING", 9, 4), ("INCOME", 38, 20), ("0-DTE", 3, 1), ("DIRECTIONAL", 3, 1),
    ("0-DTE", 0, 0),      # expires today, report today: cannot be held through it
    ("0-DTE", 0, -3),     # a report three days ago is inside the engine's window
    ("SWING", 9, 30),     # report after the expiry: no conflict either way
])
def test_the_flag_tags_exactly_the_rows_the_drop_removes(
        trade_type, dte, report_days, monkeypatch):
    """The flag honours the same-day exemption the drop does. Why that hides no
    real span is explained at the flag branch in ``compute.swing_scan``."""
    report = _report_in(report_days)
    dropped = _one_row_scan(monkeypatch, trade_type=trade_type, dte=dte,
                            report=report, mode="drop") == []
    flag = _one_row_scan(monkeypatch, trade_type=trade_type, dte=dte,
                         report=report, mode="flag")
    assert len(flag) == 1                                  # the flag never removes
    assert flag[0].get("spans_earnings", False) is dropped
    assert ("earnings_date" in flag[0]) is dropped
    assert dropped is (compute.se.earnings_gate_applies(trade_type, dte)
                       and compute.se.check_earnings_conflict(report, _report_in(dte)))


def test_the_flag_table_holds_both_a_tag_and_a_same_day_exemption(monkeypatch):
    """Guards the table above: a harness that tags nothing (or everything) would
    pass every row of it for the wrong reason."""
    tagged = _one_row_scan(monkeypatch, trade_type="SWING", dte=9,
                           report=_report_in(4), mode="flag")
    plain = _one_row_scan(monkeypatch, trade_type="0-DTE", dte=0,
                          report=_report_in(0), mode="flag")
    assert tagged and tagged[0]["spans_earnings"] is True
    assert plain and "spans_earnings" not in plain[0]
    # ... and the exempt row DOES conflict by the engine's window, so it is the
    # exemption, not a missing conflict, that leaves it untagged.
    assert compute.se.check_earnings_conflict(_report_in(0), _report_in(0))


def test_the_finder_handler_asks_for_every_expiry_flags_and_limits(
        swing_seam, monkeypatch):
    monkeypatch.setattr(compute, "scan_earnings", lambda s: ("upcoming", "2026-12-01"))
    handlers.swing_scan(Bus(fake=True), {"symbol": "SPY"})
    assert swing_seam["every_expiry"] is True
    assert swing_seam["earnings_mode"] == "flag"
    assert swing_seam["per_type_limit"] == compute.FINDER_PER_TYPE_LIMIT == 25


def test_the_handler_publishes_not_shown(monkeypatch):
    monkeypatch.setattr(compute, "scan_earnings", lambda s: ("not_listed", None))
    bus = Bus(fake=True)
    monkeypatch.setattr(compute, "swing_scan",
                        lambda **kw: {"signals": [], "view": {}, "not_shown": 1040})
    handlers.swing_scan(bus, {"symbol": "SPY"})
    assert bus.cache_get(handlers.CACHE_SWING).payload["not_shown"] == 1040
    monkeypatch.setattr(compute, "swing_scan", lambda **kw: {"signals": [], "view": {}})
    handlers.swing_scan(bus, {"symbol": "SPY"})
    assert bus.cache_get(handlers.CACHE_SWING).payload["not_shown"] == 0


def test_the_income_window_keeps_nearest_expiry_the_drop_and_no_limit(monkeypatch):
    seen = {}
    monkeypatch.setattr(compute, "_income_earnings", lambda s: ("upcoming", "2026-12-01"))
    monkeypatch.setattr(compute, "swing_scan",
                        lambda *a, **k: seen.update(k) or {"signals": [], "view": {}})
    compute.income_scan("SPY")
    assert seen["earnings_date"] == "2026-12-01"          # the seam was reached
    assert seen.get("every_expiry", False) is False
    assert seen.get("earnings_mode", "drop") == "drop"
    assert seen.get("per_type_limit") is None

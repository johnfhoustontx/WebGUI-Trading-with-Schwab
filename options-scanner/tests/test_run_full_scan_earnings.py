"""``run_full_scan`` threads a real earnings date into ``screen_spreads``.

It never did. The gate at that call site reads ``if earnings_date and ...``, and
no caller in the live scan path supplied one -- so every SWING signal the
scanner has emitted was ungated, and the gate looked like protection while
being a no-op. Both buckets are checked here: the 0-DTE bucket needs the date
too, since it spans DTE 0..4 and an overnight hold in it can straddle a report
(see earnings_gate_applies).
"""
import scanner_engine as se


class _Resp:
    def __init__(self, payload):
        self._p, self.status_code = payload, 200
    def json(self):
        return self._p


def _chain(price):
    return {"underlyingPrice": price, "putExpDateMap": {}, "callExpDateMap": {}}


def _client(price=163.0):
    class _Stub:
        class Options:
            class ContractType:
                ALL = "ALL"
        def get_quotes(self, symbols):
            return _Resp({s: {"quote": {"lastPrice":
                              18.0 if s.startswith("$VIX") else price}}
                          for s in symbols})
        def get_option_chain(self, symbol, **kw):
            return _Resp(_chain(price))
        def get_price_history_every_day(self, symbol):
            return _Resp({"candles": [
                {"open": 160 + i * .1, "high": 161 + i * .1, "low": 159 + i * .1,
                 "close": 160 + i * .1, "datetime": i} for i in range(60)]})
    return _Stub()


def _run(monkeypatch, dates):
    """Run a scan with screen_spreads recorded; return its per-window kwargs."""
    seen = []

    def _screen(chain, symbol, dte_min, dte_max, pd_min, pd_max, cd_min, cd_max,
                min_cr, kind, **kw):
        seen.append({"symbol": symbol, "kind": kind,
                     "earnings_date": kw.get("earnings_date")})
        return []

    monkeypatch.setattr(se, "screen_spreads", _screen)
    monkeypatch.setattr(se, "scan_earnings_dates", lambda syms, **kw: dates)
    monkeypatch.setattr(se, "MIN_IV_RANK", {"0-DTE": 0, "SWING": 0})
    import regime_filter
    monkeypatch.setattr(regime_filter, "evaluate_regime", lambda **kw: {
        "active": False, "allow_ccs": True, "allow_pcs": True, "per_symbol": {},
        "bias": "neutral", "trend_state": None, "trend_confidence": None,
        "aggregate_confidence": None, "divergence_flag": None,
        "composite_score": 5.0, "reason": "test"})
    se.run_full_scan(client=_client(), symbols=["ORCL"])
    return seen


def test_the_scan_resolves_dates_for_the_symbols_it_is_scanning(monkeypatch):
    asked = {}
    monkeypatch.setattr(se, "screen_spreads", lambda *a, **kw: [])
    monkeypatch.setattr(se, "scan_earnings_dates",
                        lambda syms, **kw: (asked.__setitem__("syms", list(syms)),
                                            {})[1])
    monkeypatch.setattr(se, "MIN_IV_RANK", {"0-DTE": 0, "SWING": 0})
    import regime_filter
    monkeypatch.setattr(regime_filter, "evaluate_regime", lambda **kw: {
        "active": False, "allow_ccs": True, "allow_pcs": True, "per_symbol": {},
        "bias": "neutral", "trend_state": None, "trend_confidence": None,
        "aggregate_confidence": None, "divergence_flag": None,
        "composite_score": 5.0, "reason": "test"})
    se.run_full_scan(client=_client(), symbols=["ORCL"])
    assert "ORCL" in asked["syms"]


def test_the_swing_window_receives_the_date(monkeypatch):
    seen = _run(monkeypatch, {"ORCL": "2026-09-10"})
    swing = [c for c in seen if c["kind"] == "SWING"]
    assert swing and all(c["earnings_date"] == "2026-09-10" for c in swing)


def test_the_zero_dte_bucket_receives_the_date_too(monkeypatch):
    """It spans DTE 0..4, so it needs the date even though DTE 0 is exempt --
    the exemption is decided per candidate, not by withholding the input."""
    seen = _run(monkeypatch, {"ORCL": "2026-09-10"})
    zero = [c for c in seen if c["kind"] == "0-DTE"]
    assert zero and all(c["earnings_date"] == "2026-09-10" for c in zero)


def test_a_symbol_with_no_scheduled_report_passes_none(monkeypatch):
    seen = _run(monkeypatch, {"ORCL": None})
    assert seen and all(c["earnings_date"] is None for c in seen)


def test_an_empty_calendar_does_not_break_the_scan(monkeypatch):
    seen = _run(monkeypatch, {})
    assert seen and all(c["earnings_date"] is None for c in seen)

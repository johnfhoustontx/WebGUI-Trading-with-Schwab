"""The Opening Scalp's net-gamma regime study: the gap rule, the cells and the
usable sessions (design 2026-10-25-scalp-gamma-regime-study, section 3.1)."""
from services.options_svc import scalp_regime_study as rs
from shared import scalp_config


def snap(symbol="$SPX", net_gex=-1e9, state="ok", age=1.0, untrusted=False):
    return {"matrix": {symbol: {"spot": 100.0, "flip": 99.0, "call_wall": 105.0,
                                "put_wall": 95.0, "net_gex": net_gex,
                                "walls_untrusted": untrusted}},
            "sources": {"matrix": {"state": state, "age_min": age}}}


def out(day, slot, symbol="$SPX", role="long_with", result="win", scale=1.0):
    return {"day": day, "slot": slot, "symbol": symbol, "iv_scale": scale,
            "role": role, "block": scalp_config.intraday_block(slot), "result": result}


def test_regime_of_signs():
    assert rs.regime_of(snap(net_gex=-5e8), "$SPX") == rs.NEGATIVE
    assert rs.regime_of(snap(net_gex=5e8), "$SPX") == rs.POSITIVE


def test_regime_of_gaps():
    assert rs.regime_of(snap(net_gex=0.0), "$SPX") is None
    assert rs.regime_of(snap(net_gex=None), "$SPX") is None
    assert rs.regime_of(snap(net_gex=float("nan")), "$SPX") is None
    assert rs.regime_of(snap(untrusted=True), "$SPX") is None
    assert rs.regime_of(snap(state="stale"), "$SPX") is None
    assert rs.regime_of(snap(state="absent"), "$SPX") is None
    assert rs.regime_of(snap(age=6.0), "$SPX") is None          # > max_age_min 5
    assert rs.regime_of(None, "$SPX") is None
    assert rs.regime_of(snap(symbol="SPY"), "$SPX") is None     # no row for it


def test_regime_of_reads_a_backfilled_snapshot():
    assert rs.regime_of(snap(state="backfill", age=None), "$SPX") == rs.NEGATIVE


def test_regime_of_matches_symbol_without_dollar():
    assert rs.regime_of(snap(symbol="SPX"), "$SPX") == rs.NEGATIVE


def test_regimes_maps_each_reading_by_its_symbol():
    readings = {("2026-10-21", "12:36", "$SPX"): {"snapshot": snap(net_gex=-5e8)},
                ("2026-10-21", "13:06", "$SPX"): {"snapshot": snap(net_gex=5e8)},
                ("2026-10-21", "12:36", "SPY"): {"snapshot": snap(net_gex=5e8)},
                ("2026-10-21", "13:36", "$SPX"): {}}
    assert rs.regimes(readings) == {("2026-10-21", "12:36", "$SPX"): rs.NEGATIVE,
                                    ("2026-10-21", "13:06", "$SPX"): rs.POSITIVE,
                                    ("2026-10-21", "12:36", "SPY"): None,
                                    ("2026-10-21", "13:36", "$SPX"): None}


def test_cells_count_labelled_long_rows_of_the_block():
    rows = [out("2026-10-21", "12:36", result="win"),
            out("2026-10-21", "12:36", role="long_against", result="timeout"),
            out("2026-10-21", "12:36", role="credit_with", result="win"),
            out("2026-10-21", "12:36", result="skip"),
            out("2026-10-21", "12:36", result="win", scale=1.25),
            out("2026-10-21", "10:06", result="win")]
    got = rs.cells(rows, 1.0, "afternoon", rs.LONG_ROLES)
    assert got == {("2026-10-21", "$SPX", "12:36"): [1, 2, 1]}


def test_usable_sessions_start_on_the_declared_day():
    regime = {("2026-10-20", "12:36", "$SPX"): rs.NEGATIVE,
              ("2026-10-21", "12:36", "$SPX"): rs.NEGATIVE}
    rows = [out("2026-10-20", "12:36"), out("2026-10-21", "12:36")]
    assert rs.usable_sessions(regime, rows) == ["2026-10-21"]


def test_usable_sessions_skip_early_close_and_gaps():
    regime = {("2026-11-27", "12:36", "$SPX"): rs.NEGATIVE,
              ("2026-10-22", "12:36", "$SPX"): None}
    rows = [out("2026-11-27", "12:36"), out("2026-10-22", "12:36")]
    assert rs.usable_sessions(regime, rows) == []


def test_usable_sessions_ignore_the_0836_record():
    regime = {("2026-10-21", "08:36", "$SPX"): rs.NEGATIVE}
    assert rs.usable_sessions(regime, [out("2026-10-21", "08:36")]) == []


def test_usable_session_needs_regime_and_outcome_on_the_same_call():
    regime = {("2026-10-21", "12:36", "$SPX"): rs.NEGATIVE}
    apart = [out("2026-10-21", "13:06", "$SPX")]
    assert rs.usable_sessions(regime, apart) == []
    other_symbol = [out("2026-10-21", "12:36", "SPY")]
    assert rs.usable_sessions(regime, other_symbol) == []
    together = apart + [out("2026-10-21", "12:36", "$SPX")]
    assert rs.usable_sessions(regime, together) == ["2026-10-21"]


def test_day_without_afternoon_outcomes_is_not_usable():
    regime = {("2026-10-21", "12:36", "$SPX"): rs.NEGATIVE}
    assert rs.usable_sessions(regime, [out("2026-10-21", "10:06")]) == []

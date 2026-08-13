"""Pure-helper tests for the big_delta additions to flow_delta_instrumentation.py.

The script's main() needs a live proxy + Redis (like its siblings, it has never
had a test file), so only the extracted pure functions are tested here: the
"live config" selection (must match flow_alerts.detect_big_delta exactly, or the
report and the live detector could quietly drift) and the reconciliation against
what actually fired.

``tools/`` has no ``__init__.py``; the sys.path insert mirrors
tools/tests/test_stop_all.py / test_check_stack_down.py.
"""
import pathlib
import sys
from datetime import date as _date

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from services.options_svc import flow_alerts  # noqa: E402
from tools import flow_delta_instrumentation as instr  # noqa: E402

_CFG = {"big_delta": {"enabled": True, "push": False, "rel_threshold": 0.20,
        "min_contract_notional": 10_000_000, "delta_lo": 0.05, "delta_hi": 0.85,
        "delta_max": 1.0, "top_n": 10}}   # top_n high so the cap can't trim the set


def _chain(spot, contracts):
    # contracts: list of (side, strike, expiry, dte, delta, vol) -- same shape the
    # options_svc flow_alerts tests use, and what contracts_from()/detect_big_delta
    # both expect.
    m = {"call": {}, "put": {}}
    for side, strike, expiry, dte, delta, vol in contracts:
        m[side].setdefault(f"{expiry}:{dte}", {}).setdefault(f"{strike}", []).append(
            {"delta": delta, "totalVolume": vol, "mark": 1.0})
    return {"underlyingPrice": spot, "callExpDateMap": m["call"], "putExpDateMap": m["put"]}


# --- live-config selection must match the live detector exactly ────────────
def test_live_config_selection_applies_big_delta_cfg():
    """The script's modelled selection (contracts_from + passes_big_delta) must
    pick the EXACT same contracts as the live flow_alerts.detect_big_delta, for
    the same chain + [big_delta] config -- proving exploration and the live
    detector never drift."""
    ch = _chain(100.0, [
        ("call", 100, "2026-08-14", 3, 0.5, 300_000),   # dominant -> fires
        ("call", 101, "2026-08-14", 3, 0.5, 50_000),    # sub-share -> doesn't fire
        ("put", 90, "2026-08-14", 3, 0.4, 250_000),     # its own big share -> fires
    ])
    live = {a["strike"] for a in flow_alerts.detect_big_delta("SPY", ch, _CFG)}
    rows, dropped = instr.contracts_from("SPY", ch)
    gross = instr.gross_by_symbol(rows, _CFG)
    modelled = {r["strike"] for r in rows if instr.passes_big_delta(r, gross, _CFG)}
    assert live == modelled
    assert live == {100.0, 90.0}   # non-vacuous -- proves the equality means something


def test_live_config_gross_excludes_out_of_band_contracts():
    """The live-config gross DENOMINATOR must exclude out-of-band contracts exactly
    like detect_big_delta — a large near-zero-delta contract must NOT inflate gross
    and suppress a real in-band alert. The all-in-band fixture above can't catch
    this; it was the drift the code review found (gross summed over ALL rows)."""
    ch = _chain(100.0, [
        ("call", 100, "2026-08-14", 3, 0.5, 15_000),      # in band, $75M -> the symbol's only real flow
        ("call", 120, "2026-08-14", 3, 0.02, 5_000_000),  # OUT of band, $1B -> must NOT count toward gross
    ])
    live = {a["strike"] for a in flow_alerts.detect_big_delta("SPY", ch, _CFG)}
    rows, _ = instr.contracts_from("SPY", ch)
    gross = instr.gross_by_symbol(rows, _CFG)
    modelled = {r["strike"] for r in rows if instr.passes_big_delta(r, gross, _CFG)}
    # With the inflated all-rows gross ($1.075B) the $75M contract wouldn't clear
    # 20% ($215M) and modelled would be empty; band-filtered gross ($75M) fires it.
    assert live == modelled == {100.0}


def test_gross_by_symbol_sums_delta_notional_per_symbol():
    rows = [
        {"symbol": "SPY", "delta_notional": 100.0},
        {"symbol": "SPY", "delta_notional": 50.0},
        {"symbol": "QQQ", "delta_notional": 10.0},
    ]
    assert instr.gross_by_symbol(rows) == {"SPY": 150.0, "QQQ": 10.0}
    assert instr.gross_by_symbol([]) == {}


def test_passes_big_delta_relative_and_absolute_floor():
    gross = {"SPY": 1000.0}
    r = {"symbol": "SPY", "delta": 0.5, "delta_notional": 250.0}   # 25% of gross
    small_floor_cfg = {"big_delta": {**_CFG["big_delta"], "min_contract_notional": 1}}
    assert instr.passes_big_delta(r, gross, small_floor_cfg) is True
    # Same relative share, but the absolute floor isn't cleared.
    floor_cfg = {"big_delta": {**_CFG["big_delta"], "min_contract_notional": 10_000}}
    assert instr.passes_big_delta(r, gross, floor_cfg) is False
    # Below the delta band -> excluded regardless of size.
    r_lo = {"symbol": "SPY", "delta": 0.01, "delta_notional": 900.0}
    assert instr.passes_big_delta(r_lo, gross, small_floor_cfg) is False
    # No gross recorded for the symbol -> can't be a share of nothing.
    assert instr.passes_big_delta(r, {}, small_floor_cfg) is False


def test_alert_big_delta_id_matches_the_live_handler_format():
    r = {"symbol": "SPY", "side": "call", "strike": 100.0, "expiry": "2026-08-14"}
    assert instr.alert_big_delta_id(r) == "SPY|big_delta|call|100|2026-08-14"


# --- reconciliation vs what actually fired ──────────────────────────────────
def test_big_delta_reconciliation_filters_live_alerts_by_type():
    live = [{"type": "uoa", "id": "x"}, {"type": "big_delta", "id": "y"}]
    assert instr.live_big_delta_ids(live) == {"y"}


def test_reconcile_big_delta_ok_and_not_comparable_paths():
    rows = [
        {"symbol": "SPY", "side": "call", "strike": 100.0, "expiry": "2026-08-14",
         "delta": 0.5, "delta_notional": 1_500_000_000.0},
        {"symbol": "SPY", "side": "call", "strike": 101.0, "expiry": "2026-08-14",
         "delta": 0.5, "delta_notional": 250_000_000.0},
    ]
    today = _date(2026, 8, 11)
    # No payload at all -> not comparable.
    rec = instr.reconcile_big_delta(rows, _CFG, None, today, note="no redis")
    assert rec["ok"] is False and "no redis" in rec["note"]
    # Payload dated differently -> not comparable.
    stale = {"date": "2026-08-10", "alerts": []}
    rec2 = instr.reconcile_big_delta(rows, _CFG, stale, today)
    assert rec2["ok"] is False
    # Matching date: one live big_delta alert that matches the modelled id exactly,
    # plus an unrelated uoa alert that must be filtered out.
    payload = {"date": str(today), "alerts": [
        {"type": "big_delta", "id": "SPY|big_delta|call|100|2026-08-14"},
        {"type": "uoa", "id": "SPY|uoa|call|450|2026-08-14"},
    ]}
    rec3 = instr.reconcile_big_delta(rows, _CFG, payload, today)
    assert rec3["ok"] is True
    assert rec3["modelled"] == 1 and rec3["actual"] == 1 and rec3["matched"] == 1
    assert rec3["only_modelled"] == [] and rec3["only_actual"] == []


# --- CLI overrides ───────────────────────────────────────────────────────────
def test_build_report_wires_the_live_config_and_reconciliation_sections():
    """Wiring smoke test over the already-TDD'd pure pieces: build_report must not
    crash with the new sections, must include both headers when a big_delta_rec
    is supplied, and --rel/--abs overrides must actually reach the candidate
    tables (not silently keep the module defaults)."""
    rows = [
        {"symbol": "SPY", "side": "call", "strike": 100.0, "expiry": "2026-08-14",
         "dte": 3, "spot": 100.0, "volume": 300_000, "oi": 0, "delta": 0.5,
         "mark": 1.0, "premium": 30_000_000.0, "delta_notional": 1_500_000_000.0,
         "signed_delta_notional": 1_500_000_000.0},
    ]
    today = _date(2026, 8, 11)
    payload = {"date": str(today), "alerts": [
        {"type": "big_delta", "id": "SPY|big_delta|call|100|2026-08-14"}]}
    big_delta_rec = instr.reconcile_big_delta(rows, _CFG, payload, today)
    report = instr.build_report(rows, _CFG, ["SPY"], [], sentinel=0, rec=None,
                                big_delta_rec=big_delta_rec)
    assert "## Live config" in report
    assert "## big_delta reconciliation" in report

    report2 = instr.build_report(rows, _CFG, ["SPY"], [],
                                 rel_thresholds=[0.99], abs_thresholds=[1e15])
    assert "99%" in report2   # the override reached the candidate REL table


def test_parse_args_rel_abs_overrides_and_defaults():
    ns = instr.parse_args([])
    assert ns.force is False and ns.alerts_db is None
    assert ns.rel is None and ns.abs_ is None

    ns2 = instr.parse_args(["--force", "--alerts-db", "1",
                            "--rel", "0.10,0.15", "--abs", "5e7,1e8"])
    assert ns2.force is True and ns2.alerts_db == 1
    assert ns2.rel == [0.10, 0.15]
    assert ns2.abs_ == [5e7, 1e8]

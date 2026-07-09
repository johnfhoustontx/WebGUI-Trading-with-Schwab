"""Tests for the Driver page autonomous-monitor pure builders.

The autonomous ``/driver`` page is a MONITOR + OVERRIDE: a target-progress proxy, a
control-state label, the open driver-positions table rows, and the per-checkpoint
decision log. These are the unit-tested pure transforms; the ``render`` wiring is
smoke-covered by the shell suite (and ``test_render_is_callable`` in
``test_driver.py``).
"""
from pages import driver


# ── target_progress ──────────────────────────────────────────────────────────
def test_target_progress_fraction():
    assert driver.target_progress(250, 500) == 0.5
    assert driver.target_progress(0, 500) == 0.0
    assert driver.target_progress(125, 500) == 0.25


def test_target_progress_clamps_to_unit_interval():
    assert driver.target_progress(600, 500) == 1.0      # banked past target → clamp high
    assert driver.target_progress(-100, 500) == 0.0     # in the red → clamp low


def test_target_progress_handles_none_and_zero_target():
    assert driver.target_progress(None, 500) == 0.0     # no P&L yet
    assert driver.target_progress(250, 0) == 0.0        # no/zero target → avoid /0
    assert driver.target_progress(250, None) == 0.0


# ── control_state_label ──────────────────────────────────────────────────────
def test_control_label_disabled():
    label = driver.control_state_label({"enabled": False, "halted": False})
    assert label != ""
    assert "disabled" in label.lower() and "off" in label.lower()


def test_control_label_active():
    label = driver.control_state_label({"enabled": True, "halted": False})
    assert "active" in label.lower() and "running" in label.lower()


def test_control_label_halted_includes_reason():
    label = driver.control_state_label(
        {"enabled": True, "halted": True, "reason": "Target reached"})
    assert "halted" in label.lower()
    assert "Target reached" in label


def test_control_label_halted_without_reason_is_safe():
    label = driver.control_state_label({"enabled": True, "halted": True})
    assert "halted" in label.lower()


def test_control_label_none_is_disabled():
    # An absent/empty control payload reads as the safe default (disabled/off).
    assert "disabled" in driver.control_state_label({}).lower()
    assert "disabled" in driver.control_state_label(None).lower()


# ── control_state_color ──────────────────────────────────────────────────────
def test_control_state_color_distinguishes_states():
    off = driver.control_state_color({"enabled": False, "halted": False})
    on = driver.control_state_color({"enabled": True, "halted": False})
    halted = driver.control_state_color({"enabled": True, "halted": True})
    assert off and on and halted
    assert on != off and halted != on   # three visually distinct states


def test_control_bg_class_maps_states():
    assert driver.control_bg_class({"enabled": False, "halted": False}) == "bg-[#888888]"
    assert driver.control_bg_class({"enabled": True, "halted": False}) == "bg-[#1D9E75]"
    assert driver.control_bg_class({"enabled": True, "halted": True}) == "bg-[#BA7517]"


# ── decision_log_rows ────────────────────────────────────────────────────────
def test_decision_log_rows_surfaces_fields():
    rows = driver.decision_log_rows([{
        "ts": "2026-06-24T10:00:00", "thesis": "bull", "stand_down": False,
        "executed": [{"id": "m0", "symbol": "QQQ", "qty": 2, "rationale": "high pop"}],
        "rejected": [{"id": "m9", "reason": "off-menu"}],
        "halted": False, "halt_reason": None,
    }])
    assert rows and rows[0]["thesis"] == "bull"
    assert rows[0]["ts"] == "2026-06-24T10:00:00"
    assert rows[0]["stand_down"] is False
    assert rows[0]["executed"][0]["symbol"] == "QQQ"
    assert rows[0]["rejected"][0]["reason"] == "off-menu"
    assert rows[0]["halted"] is False


def test_decision_log_rows_handles_none_and_empty():
    assert driver.decision_log_rows(None) == []
    assert driver.decision_log_rows([]) == []


def test_decision_log_rows_tolerates_sparse_dicts():
    rows = driver.decision_log_rows([{"thesis": "stand down"}])
    r = rows[0]
    assert r["thesis"] == "stand down"
    assert r["ts"] == ""
    assert r["stand_down"] is False
    assert r["executed"] == [] and r["rejected"] == []
    assert r["halted"] is False and r["halt_reason"] is None


def test_decision_log_rows_filters_nested_non_dicts():
    """Contract-legal-but-malformed nested executed/rejected (a str, or a list with
    non-dict items) are filtered to dicts so the card loops can't AttributeError and
    blank the monitor — this page is the audit-log resilience boundary."""
    rows = driver.decision_log_rows([{
        "ts": "t", "thesis": "x",
        "executed": ["not-a-dict", 42, {"symbol": "QQQ", "qty": 1}],
        "rejected": "garbage",   # a str is iterable — must NOT splay into chars
    }])
    assert rows[0]["executed"] == [{"symbol": "QQQ", "qty": 1}]
    assert rows[0]["rejected"] == []
    # The downstream one-line summary survives the now-clean row.
    assert "QQQ" in driver.decision_summary(rows[0])


def test_decision_log_rows_halt_row():
    rows = driver.decision_log_rows([{
        "ts": "t", "thesis": "", "stand_down": True, "executed": [], "rejected": [],
        "halted": True, "halt_reason": "VIX 26.0 > 25 — no new entries.",
    }])
    assert rows[0]["halted"] is True
    assert "VIX" in rows[0]["halt_reason"]


# ── decision_summary (one-line per log row) ──────────────────────────────────
def test_decision_summary_executed_and_rejected():
    txt = driver.decision_summary({
        "stand_down": False, "halted": False,
        "executed": [{"symbol": "QQQ", "qty": 2}, {"symbol": "SPX", "qty": 1}],
        "rejected": [{"id": "m9", "reason": "off-menu"}],
    })
    assert "QQQ" in txt and "2" in txt
    assert "reject" in txt.lower()


def test_decision_summary_stand_down():
    assert "stood down" in driver.decision_summary(
        {"stand_down": True, "executed": [], "rejected": []}).lower()


def test_decision_summary_halted():
    txt = driver.decision_summary(
        {"halted": True, "halt_reason": "Target reached", "executed": [], "rejected": []})
    assert "halt" in txt.lower() and "Target reached" in txt


# ── position_rows ────────────────────────────────────────────────────────────
def test_position_rows_formats_pnl():
    rows = driver.position_rows([
        {"position_id": 7, "symbol": "QQQ", "strategy": "PCS",
         "quantity": 2, "unrealized_pnl": 45.0, "status": "OPEN"},
    ])
    assert rows[0]["symbol"] == "QQQ"
    assert rows[0]["strategy"] == "PCS"
    assert rows[0]["quantity"] == 2
    assert rows[0]["pnl"] == "+$45.00"
    assert rows[0]["status"] == "OPEN"


def test_position_rows_handles_none_and_missing():
    assert driver.position_rows(None) == []
    rows = driver.position_rows([{"symbol": "SPX"}])
    assert rows[0]["symbol"] == "SPX"
    assert rows[0]["pnl"] == "—"        # missing unrealized_pnl → dash


# ── paper_summary (live paper-account P&L, the truthful source) ──────────────
def test_paper_summary_extracts_live_pnl():
    """paper_summary pulls the live P&L from the cache:options:driver_paper_account
    snapshot (the driver's isolated book, where it trades via driver_paper_create),
    so the monitor shows real P&L whether or not the autonomous loop is running."""
    pv = {"has_account": True, "snapshot": {
        "session_pnl": 5.0, "realized_pnl": 12.5, "open_unrealized": -3.0,
        "equity": 25014.5, "open_count": 2}, "positions": []}
    s = driver.paper_summary(pv)
    assert s["has_account"] is True
    assert s["session_pnl"] == 5.0 and s["realized_pnl"] == 12.5
    assert s["open_unrealized"] == -3.0 and s["equity"] == 25014.5
    assert s["open_count"] == 2


def test_paper_summary_no_account_is_safe():
    assert driver.paper_summary(None)["has_account"] is False
    assert driver.paper_summary({})["has_account"] is False
    s = driver.paper_summary({"has_account": True, "snapshot": None})
    assert s["has_account"] is False and s["session_pnl"] is None


# ── resolve_switch_state (optimistic toggle — no flip during command latency) ─
def test_resolve_switch_state_no_pending_shows_actual():
    assert driver.resolve_switch_state(None, True) == (True, None)
    assert driver.resolve_switch_state(None, False) == (False, None)


def test_resolve_switch_state_confirmed_clears_pending():
    # Pending intent matches the actual control state → confirmed, pending cleared.
    assert driver.resolve_switch_state(True, True) == (True, None)
    assert driver.resolve_switch_state(False, False) == (False, None)


def test_resolve_switch_state_pending_holds_intent():
    # Clicked ON but control hasn't caught up yet → keep showing ON (don't flip),
    # keep waiting. This is the anti-flicker guarantee during the ~1s latency.
    assert driver.resolve_switch_state(True, False) == (True, True)
    # Clicked OFF, control still enabled → keep showing OFF.
    assert driver.resolve_switch_state(False, True) == (False, False)


# ── to_central (UTC → Central time for display) ──────────────────────────────
def test_to_central_converts_utc_to_central():
    # 2026-06-25 19:30:20 UTC → 14:30:20 Central (CDT, UTC-5 in June).
    assert driver.to_central("2026-06-25T19:30:20.799378+00:00") == "2026-06-25 14:30:20 CT"


def test_to_central_naive_assumed_utc():
    assert driver.to_central("2026-06-25T19:30:00") == "2026-06-25 14:30:00 CT"


def test_to_central_empty_and_garbage_safe():
    assert driver.to_central("") == ""
    assert driver.to_central(None) == ""
    assert driver.to_central("not-a-date") == "not-a-date"


def test_target_text_signed():
    assert driver.target_text(250.0, 500.0) == "+$250.00 / $500.00"
    assert driver.target_text(None, 500.0) == "—  / $500.00" or \
        "500" in driver.target_text(None, 500.0)


# ── performance scorecard pure builders (cache:options:driver_paper_perf) ─────
# The scorecard reads the driver-account perf view published every 5-min manage
# tick. Builders turn the payload (see the module docstring shape) into render-
# ready (label, value) chip pairs, formatted breakdown rows, and a best/worst line.
def _perf(**o):
    base = {"total_trades": 4, "open": 1, "closed": 3, "wins": 2, "losses": 1,
            "win_rate": round(2 / 3, 4), "realized_pnl": 100.0, "open_unrealized": 15.0,
            "total_pnl": 115.0, "session_pnl": 115.0, "avg_win": 80.0, "avg_loss": -60.0,
            "profit_factor": round(160.0 / 60.0, 2),
            "best": {"symbol": "MU", "strategy": "PCS", "realized_pnl": 120.0},
            "worst": {"symbol": "MU", "strategy": "PCS", "realized_pnl": -60.0},
            "by_symbol": [{"symbol": "MU", "trades": 2, "pnl": 70.0, "win_rate": 0.5},
                          {"symbol": "SPY", "trades": 1, "pnl": 40.0, "win_rate": 1.0}],
            "by_strategy": [{"strategy": "PCS", "trades": 2, "pnl": 70.0, "win_rate": 0.5}]}
    base.update(o)
    return base


def test_scorecard_headline_chips():
    chips = driver.scorecard_headline_chips(_perf())
    d = {lbl: val for lbl, val in chips}
    assert d["Trades"] == "4"
    assert d["Open"] == "1" and d["Closed"] == "3"
    assert d["Win rate"] == "66.7%"            # 2/3 of closed
    assert d["Realized"] == "+$100.00"
    assert d["Open P&L"] == "+$15.00"
    assert d["Total P&L"] == "+$115.00"


def test_scorecard_headline_chips_empty_safe():
    # An unpublished view → {} → a placeholder card, never a raise.
    chips = driver.scorecard_headline_chips({})
    d = {lbl: val for lbl, val in chips}
    assert d["Trades"] == "0"
    assert d["Win rate"] == "0.0%"
    assert d["Realized"] == "$0.00"            # 0.0 renders unsigned
    assert driver.scorecard_headline_chips(None)   # None tolerated


def test_scorecard_quality_chips():
    chips = driver.scorecard_quality_chips(_perf())
    d = {lbl: val for lbl, val in chips}
    assert d["Avg win"] == "+$80.00"
    assert d["Avg loss"] == "-$60.00"
    assert d["Profit factor"] == "2.67"


def test_scorecard_quality_chips_profit_factor_none_is_dash():
    # profit_factor None (no losses yet) renders as the em-dash, never crashes.
    chips = driver.scorecard_quality_chips(_perf(profit_factor=None))
    d = {lbl: val for lbl, val in chips}
    assert d["Profit factor"] == "—"


def test_scorecard_quality_chips_empty_safe():
    d = {lbl: val for lbl, val in driver.scorecard_quality_chips({})}
    assert d["Profit factor"] == "—"     # missing → undefined → dash
    assert d["Avg win"] == "$0.00"


def test_scorecard_symbol_rows():
    rows = driver.scorecard_symbol_rows(_perf())
    by = {r["symbol"]: r for r in rows}
    assert by["MU"]["trades"] == 2
    assert by["MU"]["pnl"] == "+$70.00"
    assert by["MU"]["win_rate"] == "50.0%"
    assert by["SPY"]["pnl"] == "+$40.00"


def test_scorecard_strategy_rows():
    rows = driver.scorecard_strategy_rows(_perf())
    assert rows[0]["strategy"] == "PCS"
    assert rows[0]["pnl"] == "+$70.00"
    assert rows[0]["win_rate"] == "50.0%"


def test_scorecard_breakdown_rows_empty_and_none_safe():
    assert driver.scorecard_symbol_rows({}) == []
    assert driver.scorecard_symbol_rows(None) == []
    assert driver.scorecard_strategy_rows({}) == []
    # A losing bucket keeps its signed pnl.
    rows = driver.scorecard_symbol_rows(
        {"by_symbol": [{"symbol": "X", "trades": 1, "pnl": -30.0, "win_rate": 0.0}]})
    assert rows[0]["pnl"] == "-$30.00"


def test_best_worst_text():
    txt = driver.best_worst_text(_perf())
    assert "MU" in txt and "+$120.00" in txt        # best
    assert "-$60.00" in txt                          # worst


def test_best_worst_text_empty_safe():
    assert driver.best_worst_text({}) == ""          # nothing closed yet
    assert driver.best_worst_text(None) == ""


# ── render smoke (monitor section builds without raising) ─────────────────────
import bus_client  # noqa: E402


def test_render_monitor_graceful_empty_cache():
    """render() paints the monitor without crashing when driver_svc is cold
    (no cache:driver:autonomous / control) — the Tier-3 graceful-empty path."""
    from nicegui import ui

    bus_client.reset()  # fresh empty fakeredis cache
    assert bus_client.read("driver:autonomous") is None
    with ui.card():
        driver.render()  # must not raise


def test_render_monitor_from_seeded_autonomous_state():
    """render() builds from a seeded cache:driver:autonomous + control
    (the Unit-8 live-verification shape: enabled + day P&L + positions + log)."""
    from nicegui import ui

    bus_client.reset()
    bus_client.bus().cache_set("cache:driver:control",
                               {"enabled": True, "halted": False, "reason": None})
    bus_client.bus().cache_set("cache:driver:autonomous", {
        "date": "2026-06-24", "enabled": True, "halted": False, "halt_reason": None,
        "day_pnl": 220.0, "target": 500.0,
        "positions": [{"position_id": 1, "symbol": "QQQ", "strategy": "PCS",
                       "quantity": 2, "unrealized_pnl": 30.0, "status": "OPEN"}],
        "decisions": [{
            "ts": "2026-06-24T10:00:00", "thesis": "bullish drift", "stand_down": False,
            "executed": [{"id": "m0", "symbol": "QQQ", "qty": 2, "rationale": "high pop"}],
            "rejected": [{"id": "m9", "reason": "off-menu"}],
            "halted": False, "halt_reason": None}],
        "last_cycle_ts": "2026-06-24T10:00:01",
    })
    with ui.card():
        driver.render()  # must not raise with populated views


def test_render_monitor_shows_live_paper_pnl_when_autonomy_off():
    """The monitor's Day P&L + positions come from the LIVE DRIVER paper account
    (cache:options:driver_paper_account) even when autonomy is OFF — the reported bug.

    With autonomy never enabled there is no cache:driver:autonomous, so the old
    code showed "—" forever; the P&L must instead come from the isolated driver
    paper account the autonomous loop actually trades into (and update as it reprices)."""
    from nicegui import ui

    bus_client.reset()  # no driver:control / driver:autonomous → autonomy never enabled
    bus_client.bus().cache_set("cache:options:driver_paper_account", {
        "has_account": True,
        "snapshot": {"session_pnl": 5.0, "realized_pnl": -333.0, "open_unrealized": 5.0,
                     "equity": 24672.0, "open_count": 2, "halted": False},
        "positions": [
            {"position_id": 1, "symbol": "INTC", "strategy": "PCS", "quantity": 1,
             "unrealized_pnl": -11.0, "status": "OPEN"},
            {"position_id": 2, "symbol": "SPY", "strategy": "CCS", "quantity": 1,
             "unrealized_pnl": 16.0, "status": "OPEN"}],
    })
    pv = bus_client.read("options:driver_paper_account")
    s = driver.paper_summary(pv)
    assert s["has_account"] and s["session_pnl"] == 5.0 and s["open_count"] == 2
    assert driver.target_text(s["session_pnl"], 500.0) == "+$5.00 / $500.00"
    assert [r["symbol"] for r in driver.position_rows(pv.get("positions"))] == ["INTC", "SPY"]
    with ui.card():
        driver.render()  # must not raise; the monitor paints from the driver account


def test_render_monitor_shows_scorecard_from_driver_perf():
    """The performance scorecard renders from cache:options:driver_paper_perf
    (published every 5-min driver manage tick) — headline + quality + breakdowns."""
    from nicegui import ui

    bus_client.reset()
    bus_client.bus().cache_set("cache:options:driver_paper_perf", {
        "total_trades": 4, "open": 1, "closed": 3, "wins": 2, "losses": 1,
        "win_rate": round(2 / 3, 4), "realized_pnl": 100.0, "open_unrealized": 15.0,
        "total_pnl": 115.0, "session_pnl": 115.0, "avg_win": 80.0, "avg_loss": -60.0,
        "profit_factor": 2.67,
        "best": {"symbol": "MU", "strategy": "PCS", "realized_pnl": 120.0},
        "worst": {"symbol": "MU", "strategy": "PCS", "realized_pnl": -60.0},
        "by_symbol": [{"symbol": "MU", "trades": 2, "pnl": 70.0, "win_rate": 0.5}],
        "by_strategy": [{"strategy": "PCS", "trades": 2, "pnl": 70.0, "win_rate": 0.5}],
    })
    perf = bus_client.read("options:driver_paper_perf")
    chips = {lbl: val for lbl, val in driver.scorecard_headline_chips(perf)}
    assert chips["Win rate"] == "66.7%" and chips["Total P&L"] == "+$115.00"
    with ui.card():
        driver.render()  # must not raise; the scorecard paints from the perf view


def test_monitor_reads_driver_paper_account_not_manual():
    """3-tier re-point: the monitor's live-P&L source is the DRIVER paper account
    (cache:options:driver_paper_account), NOT the user's manual paper_account — so
    the day-P&L bar / summary / positions reflect the driver's own isolated book."""
    import inspect

    src = inspect.getsource(driver.render)
    assert 'read("options:driver_paper_account")' in src
    assert 'read_version("options:driver_paper_account")' in src
    # The monitor path must no longer read the manual account.
    assert 'options:paper_account"' not in src


def test_page_imports_no_engine_or_services():
    """3-tier rule: the Tier-3 page must not import engine / services / proxy code.

    Guards the import *statements* (prose in the module docstring naturally names
    ``services/driver_svc`` — that's documentation, not a dependency).
    """
    import inspect

    src = inspect.getsource(driver)
    for forbidden in ("import services", "from services import",
                      "import proxy", "from proxy import"):
        assert forbidden not in src, f"driver.py must not contain `{forbidden}`"
    # No engine/proxy objects leaked into the page module namespace.
    for attr in ("proxy", "compute", "handlers"):
        assert not hasattr(driver, attr), f"driver.py exposes engine attr {attr!r}"

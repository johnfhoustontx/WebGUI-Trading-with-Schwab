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


# ── control_badge_class ──────────────────────────────────────────────────────
# Re-aimed 2026-09-20: ``control_state_color`` and ``control_bg_class`` were a
# pair (a hex, then that hex interpolated into ``bg-[…]``) and are now one
# function returning the theme's own badge token, which carries the fill, the
# foreground and the radius together. Both tests keep the invariant they had.
def test_control_badge_distinguishes_states():
    off = driver.control_badge_class({"enabled": False, "halted": False})
    on = driver.control_badge_class({"enabled": True, "halted": False})
    halted = driver.control_badge_class({"enabled": True, "halted": True})
    assert off and on and halted
    assert on != off and halted != on   # three visually distinct states


def test_control_badge_class_maps_states():
    from pages.options import theme as _theme
    assert driver.control_badge_class(
        {"enabled": False, "halted": False}) == _theme.BADGE_MUTED
    assert driver.control_badge_class(
        {"enabled": True, "halted": False}) == _theme.BADGE_POS
    assert driver.control_badge_class(
        {"enabled": True, "halted": True}) == _theme.BADGE_WARN


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


def _render_tree():
    """``render``'s own body as an AST.

    ⚠ These three tests used to read ``inspect.getsource(driver.render)`` as TEXT
    and assert a substring was absent, which pins a SPELLING rather than a fact:
    ``'options:paper_account"' not in src`` only worked because of the trailing
    quote (the driver's own view ends in the same nine characters), and
    ``"read_version(" not in src`` would miss ``read_version (x)``. Worse, an
    absence-of-text assertion over a page that no longer holds the thing cannot
    fail at all. Reading the tree lets each one assert what the page DOES — which
    views it names, which bus call it makes, what it imports — so re-introducing
    the mistake in any spelling turns it red.
    """
    import ast
    import inspect

    return ast.parse(inspect.getsource(driver.render).lstrip())


def _names_used(tree):
    """Every bare and dotted name USED in ``tree``.

    ⚠ Not just callees: ``run.io_bound(bus_client.read_versions, views)`` hands
    the reader over as a value, so a callee-only walk would not see it.
    """
    import ast

    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            out.add(node.attr)
        elif isinstance(node, ast.Name):
            out.add(node.id)
    return out


def _assigned_list(tree, name):
    """The string members of ``name = [...]`` inside ``tree``."""
    import ast

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, (ast.List, ast.Tuple)):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if name in targets:
                return {e.value for e in node.value.elts
                        if isinstance(e, ast.Constant) and isinstance(e.value, str)}
    return set()


def _string_args(tree, callee):
    """The flat string arguments of every ``callee(...)`` call, list args included
    (``read_versions([...])`` passes its views inside one list)."""
    import ast

    out = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
        if name != callee:
            continue
        for arg in node.args:
            items = arg.elts if isinstance(arg, (ast.List, ast.Tuple)) else [arg]
            for item in items:
                if isinstance(item, ast.Constant) and isinstance(item.value, str):
                    out.add(item.value)
    return out


def test_monitor_reads_driver_paper_account_not_manual():
    """3-tier re-point: the monitor's live-P&L source is the DRIVER paper account
    (cache:options:driver_paper_account), NOT the user's manual paper_account — so
    the day-P&L bar / summary / positions reflect the driver's own isolated book."""
    tree = _render_tree()
    read = _string_args(tree, "read")
    polled = _assigned_list(tree, "_POLL_VIEWS")
    assert "options:driver_paper_account" in read          # the payload read
    assert "options:driver_paper_account" in polled        # version-gated on it
    # The monitor path must not read the manual account under ANY spelling — the
    # view name is compared as a whole string, not searched for in the source.
    assert "options:paper_account" not in (read | polled)


def test_poll_pipelines_versions_and_reads_off_loop():
    """The 2s driver poll must batch its version probes into ONE pipelined
    read_versions call (was 5 sequential read_version round-trips) and read the
    changed payloads OFF the event loop."""
    import ast

    tree = _render_tree()
    used = _names_used(tree)
    assert any(isinstance(n, ast.AsyncFunctionDef) and n.name == "_poll"
               for n in ast.walk(tree))
    assert "read_versions" in used and "io_bound" in used
    assert len(_assigned_list(tree, "_POLL_VIEWS")) > 1, \
        "the batched probe has to carry more than one view to be batching anything"
    # ⚠ Singular ``read_version`` is the five-round-trip regression. It stays
    # absent even though the header's Updated stamp needs a version: the kit
    # calls ``bus_client.read_meta`` inside ui_kit.py, not on this page.
    assert "read_version" not in used


def test_page_imports_no_engine_or_services():
    """3-tier rule: the Tier-3 page must not import engine / services / proxy code.

    Reads the import STATEMENTS out of the tree — prose in the module docstring
    naturally names ``services/driver_svc``, and that is documentation, not a
    dependency. ⚠ The root package is what is compared, so the submodule
    spellings the old substring test missed (``import services.options_svc``,
    ``from proxy.client import x``) are caught too.
    """
    import ast
    import inspect

    roots = set()
    for node in ast.walk(ast.parse(inspect.getsource(driver))):
        if isinstance(node, ast.Import):
            roots |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    for forbidden in ("services", "proxy"):
        assert forbidden not in roots, f"driver.py must not import `{forbidden}`"
    # No engine/proxy objects leaked into the page module namespace.
    for attr in ("proxy", "compute", "handlers"):
        assert not hasattr(driver, attr), f"driver.py exposes engine attr {attr!r}"


# ── Claude Trades on the page kit (Phase 5, Task 8) ──────────────────────────
# The page named itself "Claude Driver" while the rail said Claude Trades; its
# four action buttons lived inside the container every repaint clears; its only
# wait was a scrim that the FIRST paint deleted; and its status label was
# written twice and never reset, so "Stopping…" stayed on screen for the rest of
# the session.
import inspect as _inspect
import pathlib as _pathlib

from nicegui import ui as _ui

from pages import ui_kit as kit
from pages.options import theme as _t

_ACTIONS = ("Stop", "Resume today", "Refresh", "Run now")


def _driver_src():
    return (_pathlib.Path(__file__).resolve().parents[1] / "pages"
            / "driver.py").read_text(encoding="utf-8")


def _driver_render():
    """Render Claude Trades and return ONLY the elements IT built.

    ``ui.context.client.elements`` is the auto-index client the whole module
    shares, so a plain ``elements.values()`` also hands back widgets another
    test's render left behind — and a scrim assertion then passes off someone
    else's page (the Phase 3 Task 1 measurement). Diffing the ids around the
    render is what scopes it, and it is the whole reason this test can see the
    bug below."""
    before = set(_ui.context.client.elements)
    with _ui.card():
        driver.render()
    return [e for i, e in _ui.context.client.elements.items() if i not in before]


def _driver_ancestors(el):
    out = []
    slot = getattr(el, "parent_slot", None)
    while slot is not None:
        out.append(slot.parent)
        slot = getattr(slot.parent, "parent_slot", None)
    return out


def _driver_buttons(els):
    return {e.text: e for e in els if isinstance(e, _ui.button)}


def _driver_actions(els):
    """The buttons in the HEADER's actions row, keyed by label.

    Scoped rather than keyed off every button on the page: each confirm dialog
    carries a button with the SAME caption as the action that opens it ("Stop",
    "Resume today"), so a flat text->element map silently keeps the dialog's,
    and an assertion about "the page's Stop button" then reads the confirm's."""
    actions = _driver_buttons(els)["Run now"].parent_slot.parent
    return {e.text: e for e in els
            if isinstance(e, _ui.button) and e.parent_slot.parent is actions}


def test_the_scrim_survives_the_repaint_that_used_to_delete_it():
    """THE BUG, and it had been live since the monitor was written.

    ``monitor_busy = _busy.build_busy(monitor, "Running…")`` mounted the scrim
    INSIDE ``monitor`` (driver.py:614) and ``_render_monitor`` opens with
    ``monitor.clear()`` (driver.py:698), first run at 993 — so the build-time
    paint deleted it, and every ``monitor_busy.show()`` at 905 and 926 since has
    reached a deleted element. Measured on the pre-change page: ZERO spinners
    survived a render, against portfolio.py's one on the same probe.
    ``kit.region`` keeps the spinner on ``outer`` and clears only ``content``."""
    els = _driver_render()
    assert any(isinstance(e, _ui.spinner) for e in els), \
        "the monitor's scrim was deleted by the build-time repaint"


class TestTheDriverFrameIsTheKits:
    def test_the_title_is_the_navs_word_and_the_blurb_is_gone(self):
        src = _inspect.getsource(driver.render)
        assert "kit.page()" in src
        assert 'kit.header("Claude Trades", view=STAMP_VIEW, stale=False)' in src
        assert "Claude Driver" not in src
        assert "Autonomous PAPER options trader" not in src

    def test_the_stamp_reads_the_book_not_the_cycle(self):
        """⚠ NOT ``driver:autonomous``: that view is published only WHILE a
        cycle runs, so its stamp would freeze between cycles. The paper account
        is the widest-reach view this page reads."""
        assert driver.STAMP_VIEW == "options:driver_paper_account"

    def test_the_stamp_never_goes_amber_outside_the_session(self):
        """``stale=False`` is a decision: the publisher is ``manage_due``, gated
        on a trading day AND 08:00–15:15 CT, with no ``STALE_OVERRIDES`` entry
        and no ``RTH_ONLY_VIEWS`` membership — so ``stale=True`` would paint the
        stamp amber every evening and all weekend."""
        import alerts
        assert driver.STAMP_VIEW not in alerts.RTH_ONLY_VIEWS
        assert driver.STAMP_VIEW not in alerts.STALE_OVERRIDES
        assert "stale=False" in _inspect.getsource(driver.render)

    def test_the_page_column_is_re_entered_so_the_source_greps_still_bite(self):
        """``with kit.page():`` around the whole of ``render`` would re-indent
        the poll and its reads; ``test_poll_pipelines_versions_and_reads_off_loop``
        and ``test_monitor_reads_driver_paper_account_not_manual`` read them at
        render's own indent. The gamma precedent (95adc5d)."""
        src = _inspect.getsource(driver.render)
        assert "page_col = kit.page()" in src
        assert "with page_col:" in src


class TestTheDriverActionsSurviveTheRepaint:
    def test_the_four_actions_sit_in_the_header_not_in_the_cleared_monitor(self):
        """``kit.set_busy`` creates a button's backstop timer with
        ``with btn.parent_slot:``, and ``_render_monitor`` clears ``monitor`` on
        every version move — so a held button inside it loses itself AND its
        timer to the repaint its own command causes. They are page actions, so
        the header's one actions row is where they belong (the Rank Board
        precedent)."""
        els = _driver_render()
        actions_by_name = _driver_actions(els)
        for name in _ACTIONS:
            assert name in actions_by_name, \
                f"{name} is not in the header's actions row"
        actions = _driver_buttons(els)["Run now"].parent_slot.parent
        title = next(e for e in els
                     if isinstance(e, _ui.label) and e.text == "Claude Trades")
        assert actions.parent_slot.parent in _driver_ancestors(title)
        # ...and the container they used to live in really is still cleared.
        assert "monitor.clear()" in _inspect.getsource(driver.render)

    def test_run_now_is_the_one_primary_and_stop_is_the_one_danger(self):
        btns = _driver_actions(_driver_render())
        assert _t.BTN_PRIMARY in " ".join(btns["Run now"].classes)
        assert _t.BTN_DANGER in " ".join(btns["Stop"].classes)
        for secondary in ("Refresh", "Resume today"):
            assert _t.BTN in " ".join(btns[secondary].classes)

    def test_each_action_holds_its_own_spinner(self):
        assert "kit.set_busy(" in _driver_src()
        btns = _driver_actions(_driver_render())
        for name in _ACTIONS:
            kit.set_busy(btns[name])
            assert not btns[name].enabled and "loading" in btns[name].props

    def test_the_orphan_status_label_is_gone(self):
        """It was written at 906 ("Stopping…") and 927 ("Repricing…") and never
        reset — those were its only two writes, so the word stayed on screen for
        the rest of the session."""
        src = _driver_src()
        assert "status.text" not in src
        assert '"Repricing…"' not in src

    def test_resume_today_is_hidden_until_the_driver_halts_ITSELF(self):
        """It used to be built only under ``is_risk_halt``; out of the cleared
        container it is built once and its VISIBILITY carries the condition.
        Losing that would offer a halt override on a page with no halt."""
        import bus_client
        bus_client.reset()
        assert _driver_actions(_driver_render())["Resume today"].visible is False
        bus_client.bus().cache_set("cache:driver:control", {
            "enabled": True, "halted": True, "reason": "daily loss cap"})
        assert _driver_actions(_driver_render())["Resume today"].visible is True
        bus_client.reset()


class TestTheDriverDialogsAreTheKits:
    def test_both_confirms_are_the_kits_and_both_are_danger(self):
        """Resume today re-arms an autonomous trader that halted ITSELF, so it
        is as destructive as the stop it undoes. Both cards also carried NO
        classes — default Quasar cards, the unthemed-dialog case."""
        src = _driver_src()
        assert src.count("kit.confirm(") == 2
        assert "ui.dialog(" not in src
        els = _driver_render()
        cards = [e for e in els if isinstance(e, _ui.card)
                 and kit.CONFIRM_CARD in " ".join(e.classes)]
        assert len(cards) == 2

    def test_the_stop_body_says_WHICH_halt_enable_clears(self):
        """It read "Enable re-arms it (clears the halt)" — true of this manual
        stop and false of a risk halt, which is the entire reason the Resume
        today button exists one line below (``is_risk_halt``)."""
        body = driver.STOP_BODY
        assert "(clears the halt)" not in body
        assert "Resume today" in body, "it must name the way back from a risk halt"
        assert "itself" in body.lower(), "and say whose halt that is"


class TestTheDriverTablesAreTheKits:
    def test_the_five_tables_are_the_kits_and_keep_their_pnl_slot(self):
        src = _driver_src()
        assert "ui.table(" not in src
        assert src.count("kit.table(") == 5
        assert src.count('add_slot("body-cell-pnl", _PNL_CELL_SLOT)') == 5

    def test_the_numbers_are_right_and_the_two_text_columns_move_LEFT(self):
        """⚠ ``strategy`` and ``status`` carry no ``align`` today, so Quasar
        renders them RIGHT — nobody writes ``align`` for a text column expecting
        that. Under the kit they move left, which is how every other text column
        in the app draws. A deliberate fix, not a regression."""
        cases = [(driver._CLOSED_COLS, driver._CLOSED_NUMERIC),
                 (driver._POSITION_COLS, driver._POSITION_NUMERIC),
                 (driver._SCORE_SYMBOL_COLS, driver._SCORE_NUMERIC),
                 (driver._SCORE_STRATEGY_COLS, driver._SCORE_NUMERIC),
                 (driver._POSTMORTEM_COLS, driver._POSTMORTEM_NUMERIC)]
        for cols, numeric in cases:
            for c in kit.table_columns(cols, numeric=numeric):
                want = "right" if c["name"] in numeric else "left"
                assert c["align"] == want, c["name"]
                assert c["sortable"] is True, c["name"]
        for cols, numeric in cases[:2]:
            assert "strategy" not in numeric
        assert "status" not in driver._POSITION_NUMERIC

    def test_the_numeric_sets_are_the_measured_ones(self):
        assert driver._CLOSED_NUMERIC == ("qty", "pnl")
        assert driver._POSITION_NUMERIC == ("quantity", "pnl")
        assert driver._SCORE_NUMERIC == ("trades", "pnl", "win_rate")
        assert driver._POSTMORTEM_NUMERIC == ("trades", "win_rate", "pnl", "avg")

    def test_the_pnl_body_slot_and_its_header_now_agree(self):
        """``_PNL_CELL_SLOT`` hardcodes ``text-right`` on the ``q-td``. With
        ``pnl`` named numeric on all five tables the header is right too; naming
        it nowhere would have left the two disagreeing visibly, with no test
        able to see it."""
        assert 'class="text-right"' in driver._PNL_CELL_SLOT
        for cols, numeric in ((driver._CLOSED_COLS, driver._CLOSED_NUMERIC),
                              (driver._POSITION_COLS, driver._POSITION_NUMERIC),
                              (driver._SCORE_SYMBOL_COLS, driver._SCORE_NUMERIC),
                              (driver._POSTMORTEM_COLS, driver._POSTMORTEM_NUMERIC)):
            assert "pnl" in numeric

    def test_the_css_keeps_only_the_scroll_height(self):
        """Its sticky-thead half duplicated — and fought, with a hardcoded hex —
        ``shell.TABLE_CSS``, which is app-wide and already does sticky thead,
        row dividers and the 11px/600 head."""
        css = driver.DRIVER_CSS
        assert "max-height" in css
        assert "sticky" not in css and "#141a30" not in css


class TestTheDriverColoursAreTheAppsTokens:
    def test_no_quasar_colour_word_and_no_opacity_muting_is_left(self):
        """28 ``opacity-*`` mutings and five Quasar colour words, replaced by
        the theme's own text tokens — so a theme edit reaches this page."""
        src = _driver_src()
        for word in ("text-amber-9", "text-red-9", "text-green-9", "text-red-8",
                     "bg-[#E24B4A]"):
            assert word not in src, word
        assert "opacity-" not in src

    def test_the_control_badge_is_the_theme_s_state_fills(self):
        """⚠ ``#888888`` is a pure neutral and the other two are a STATE reading
        on a filled pill. ``control_state_color`` goes with them — it existed
        only to build this class."""
        assert driver.control_badge_class({"enabled": False}) == _t.BADGE_MUTED
        assert driver.control_badge_class(
            {"enabled": True, "halted": False}) == _t.BADGE_POS
        assert driver.control_badge_class(
            {"enabled": True, "halted": True}) == _t.BADGE_WARN

    def test_the_three_control_states_stay_visually_distinct(self):
        """Re-aimed from ``test_control_state_color_distinguishes_states``,
        which pinned the same invariant on the hex the tokens replace."""
        seen = {driver.control_badge_class(c) for c in (
            {"enabled": False, "halted": False},
            {"enabled": True, "halted": False},
            {"enabled": True, "halted": True})}
        assert len(seen) == 3

    def test_every_card_wears_the_app_card_token(self):
        """Five classless ``ui.card()``s — stock Quasar cards on a navy page."""
        import re
        calls = re.findall(r"ui\.card\(\)(?:\.classes\(([^)]*)\))?", _driver_src())
        assert len(calls) >= 5, "the page still builds its panels as cards"
        for cls in calls:
            assert cls and "_t.CARD" in cls, cls


def test_the_equity_chart_is_built_once_and_never_inside_the_region():
    """The ESM import-map gotcha: a ui.highchart added dynamically on a page
    with no chart at first render fails to resolve ``nicegui-highcharts``. It
    must exist at page build AND sit outside anything a repaint clears."""
    els = _driver_render()
    charts = [e for e in els if type(e).__name__ == "Highchart"]
    assert len(charts) == 1
    spinner = next(e for e in els if isinstance(e, _ui.spinner))
    region_outer = spinner.parent_slot.parent
    assert region_outer not in _driver_ancestors(charts[0])


def test_the_page_tells_the_operator_the_REAL_manage_cadence():
    """``options_svc/scheduler.py`` has been ``_MANAGE_INTERVAL_MIN = 1`` since
    2026-07-16; the page said 5-min in four places on screen and four more in
    its own comments."""
    src = _driver_src()
    assert "5-min" not in src and "~5 min" not in src
    assert "1-min manage cycle" in src


def test_position_rows_no_longer_carry_a_colour_nothing_reads():
    """Re-aimed from ``test_position_rows_carry_pnl_color``. ``_pnl_color`` was
    written into every position row and read by no renderer — ``_PNL_CELL_SLOT``
    binds ``_pnl_class``. Half-live is the thing to remove, not to keep."""
    rows = driver.position_rows([{"position_id": "p1", "unrealized_pnl": -12.0}])
    assert "_pnl_color" not in rows[0]
    assert rows[0]["_pnl_class"] == driver.pnl_class(-12.0)


def test_the_kill_switch_is_sentence_case_like_every_other_button():
    """The standard is sentence case for every label; ``page_help`` names the
    control, so it moves in the same commit or the help stops matching."""
    src = _driver_src()
    assert '"STOP"' not in src
    help_src = (_pathlib.Path(__file__).resolve().parents[1]
                / "page_help.py").read_text(encoding="utf-8")
    driver_help = help_src[help_src.index('"/driver":'):]
    driver_help = driver_help[:driver_help.index('"/market":')]
    assert "**Stop**" in driver_help and "**STOP**" not in driver_help

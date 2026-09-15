"""The Market Scanner's Checks column and "Only clear" filter - PURE parts.

The checklist's Paper book line reads the DISPLAY row's ``_allow_paper`` gate,
which raw scan signals never carry, so ``stamp_checks`` must hand the gate
across and must run after ``stamp_stale`` has settled it.
"""
import inspect

from pages.options import checks, checks_feed, scanner

LIMITS = {"max_positions_per_symbol": 3, "max_risk_per_symbol": 750.0,
          "max_positions_per_expiry": 5, "max_positions_per_sector": 5,
          "max_risk_per_sector": 1500.0, "max_deployed_risk_pct": 0.2,
          "max_risk_per_trade": 250.0}
CAPS = {"limits": LIMITS, "equity": 25000.0, "open": [], "sectors": {"ORCL": "IT"},
        "unmapped_prefix": "?"}
BOARD_ROW = {"symbol": "ORCL", "spot": 110.0, "put_wall": 102.0, "call_wall": 120.0,
             "gex_regime": "above", "trend_dir": 0.4, "trend_state": "up"}
REGIME = {"direction": 1}


def _pcs_signal(**over):
    """A stamped scanner PCS signal as the options service publishes it."""
    sig = {"id": "ORCL_PCS_2026-10-17_100_97.5", "symbol": "ORCL", "type": "PCS",
           "trade_type": "SWING", "expiration": "2026-10-17", "dte": 12,
           "short_strike": 100.0, "long_strike": 97.5, "width": 2.5,
           "credit": 0.60, "max_loss": 1.90, "rr_pct": 31.6, "pop_pct": 72.0,
           "iv_rank": 55.0, "iv_rank_known": True, "vol_floor": 30,
           "friction_pct": 8.0, "em_to_expiry": 6.0,
           "earnings_status": "none_scheduled", "earnings_date": None,
           "ledger_risk_per_contract": 190.0,
           "ledger_risk_basis": {"per_contract": 190.0},
           "underlying_price": 110.0, "composite_score": 70, "grade": "Good",
           "live": True, "stale_since": None}
    sig.update(over)
    return sig


def _ctx(**over):
    ctx = {"matrix": {"ORCL": BOARD_ROW}, "regime": REGIME, "calibration": None,
           "caps": CAPS}
    ctx.update(over)
    return ctx


# ── columns ──────────────────────────────────────────────────────────────────
def test_signal_columns_put_checks_just_before_the_dropped_column():
    fields = [c["field"] for c in scanner.signal_columns()]
    assert fields[fields.index("stale_since") - 1] == "checks"
    labels = {c["field"]: c["label"] for c in scanner.signal_columns()}
    assert labels["checks"] == "Checks"


def test_directional_columns_put_checks_just_before_the_dropped_column():
    fields = [c["field"] for c in scanner.directional_columns()]
    assert fields[fields.index("stale_since") - 1] == "checks"


# ── stamp_checks ─────────────────────────────────────────────────────────────
def test_stamp_checks_joins_by_id_and_hands_over_the_rows_paper_gate():
    received = []

    def build(row, ctx):
        received.append((row["id"], row["_allow_paper"], ctx))
        if row["id"] == "a":
            return [{"key": "cost", "tone": "pos", "text": "fine"}]
        return [{"key": "cost", "tone": "warn", "text": "wide"}]

    sigs = [{"id": "a", "symbol": "SPY"}, {"id": "b", "symbol": "QQQ"}]
    # Display rows in the OPPOSITE order: the builders re-sort, so a zip would lie.
    rows = [{"id": "b", "_allow_paper": False}, {"id": "a", "_allow_paper": True}]
    ctx = object()
    scanner.stamp_checks(rows, sigs, ctx, build=build)

    assert sorted(received, key=lambda t: t[0]) == [("a", True, ctx), ("b", False, ctx)]
    by_id = {r["id"]: r for r in rows}
    assert by_id["a"]["_checks_state"] == "pos"
    assert by_id["a"]["checks"].startswith("Clear")
    assert by_id["a"]["_checks_class"] == checks.TONE_CLASS["pos"]
    assert by_id["b"]["_checks_state"] == "warn"
    assert by_id["b"]["checks"] == "1 caution"
    assert by_id["b"]["_checks_class"] == checks.TONE_CLASS["warn"]


def test_stamp_checks_never_mutates_the_shared_signal():
    sig = {"id": "a", "symbol": "SPY"}
    scanner.stamp_checks([{"id": "a", "_allow_paper": True}], [sig], None,
                         build=lambda row, ctx: [])
    assert sig == {"id": "a", "symbol": "SPY"}


def test_a_row_with_no_matching_signal_is_unchecked():
    calls = []
    rows = [{"id": "orphan", "_allow_paper": True}]
    scanner.stamp_checks(rows, [{"id": "other"}], None,
                         build=lambda row, ctx: calls.append(row) or [])
    assert calls == []
    assert rows[0]["checks"] == "unchecked"
    assert rows[0]["_checks_state"] == "muted"
    assert rows[0]["_checks_class"] == checks.TONE_CLASS["muted"]


def test_only_clear_keeps_only_the_clear_rows():
    rows = [{"id": s, "_checks_state": s} for s in ("pos", "warn", "neg", "muted")]
    rows.append({"id": "none"})
    assert [r["id"] for r in scanner.only_clear(rows)] == ["pos"]


# ── end to end over the real builders ────────────────────────────────────────
def test_real_rows_are_stamped_with_the_paper_book_line_evaluated():
    sig = _pcs_signal()
    rows = scanner.signal_rows([sig])
    scanner.stamp_stale(rows, [sig])
    scanner.stamp_checks(rows, [sig], _ctx())

    merged = {**sig, "_allow_paper": rows[0]["_allow_paper"]}
    items = checks_feed.checks_for(merged, _ctx())
    book = [c for c in items if c["key"] == "book"]
    assert book and book[0]["tone"] == "pos"
    # Calibration is cold, so the track record is missing: partly checked, and
    # the count includes the Paper book line.
    assert rows[0]["checks"] == checks.summary(items)["text"]
    assert rows[0]["checks"] == "Partly checked · 8 of 9 checked"
    assert rows[0]["_checks_state"] == "muted"
    # Without the gate the book line would silently drop out of the count.
    bare = checks_feed.checks_for(sig, _ctx())
    assert not [c for c in bare if c["key"] == "book"]


def test_real_rows_read_clear_with_every_view_published():
    sig = _pcs_signal()
    rows = scanner.stamp_stale(scanner.signal_rows([sig]), [sig])
    calibration = {"by_bucket": {}}
    scanner.stamp_checks(rows, [sig], _ctx(calibration=calibration))
    items = checks_feed.checks_for({**sig, "_allow_paper": True},
                                   _ctx(calibration=calibration))
    assert rows[0]["checks"] == checks.summary(items)["text"]
    assert rows[0]["_checks_state"] == checks.summary(items)["state"] == "pos"
    assert rows[0]["checks"] == "Clear · 8 of 9 checked"
    # ...and that row is the one the "Only clear" filter keeps.
    assert scanner.only_clear(rows) == rows


def test_a_dropped_row_loses_its_paper_book_line():
    sig = _pcs_signal(live=False, stale_since="2026-07-16T11:00:00")
    rows = scanner.stamp_stale(scanner.signal_rows([sig]), [sig])
    seen = []
    scanner.stamp_checks(rows, [sig], _ctx(),
                         build=lambda row, ctx: seen.append(row["_allow_paper"]) or [])
    assert seen == [False]


def test_build_populate_stamps_stale_then_checks_off_the_loop():
    sig = _pcs_signal()
    env = {"date": scanner.today_ct(), "signals_0dte": [], "signals_swing": [sig],
           "signals_directional": []}
    built = scanner._build_populate(env, {}, _ctx())
    row = built["rows"]["signals_swing"][0]
    assert row["_allow_paper"] is True
    assert row["checks"] == "Partly checked · 8 of 9 checked"
    # No context at all (the instant empty paint) never raises.
    cold = scanner._build_populate(env, {}, None)
    assert cold["rows"]["signals_swing"][0]["_checks_state"] in ("muted", "warn", "pos")


# ── render wiring (source level) ─────────────────────────────────────────────
def test_render_wires_the_switch_the_refresh_timer_the_probe_and_the_slots():
    src = inspect.getsource(scanner.render)
    assert 'ui.switch("Only clear"' in src
    assert "_ONLY_CLEAR_TIP" in src
    assert "only_clear(painted[key])" in src
    assert "ui.timer(checks_feed.TABLE_REFRESH_SEC, _force_repaint)" in src
    assert "checks_feed.REFRESH_VIEWS" in src
    assert "bus_client.read_versions(_probe_views)" in src
    for table in ("_t", "table_dir"):
        assert f"{table}.add_slot('body-cell-checks', _CHECKS_SLOT)" in src
    # The loop over (table_0dte, table_swing) is where _t comes from.
    assert "for _t in (table_0dte, table_swing):" in src


def test_the_only_clear_tooltip_is_plain_words():
    assert scanner._ONLY_CLEAR_TIP == (
        "Hide rows with a caution, a block, or a check that couldn't run")


def test_the_checks_slot_binds_classes_not_styles():
    assert ":class=" in scanner._CHECKS_SLOT
    assert "style" not in scanner._CHECKS_SLOT
    assert "props.row._checks_class" in scanner._CHECKS_SLOT


def test_read_and_build_passes_the_checklist_context(monkeypatch):
    seen = {}
    monkeypatch.setattr(scanner, "_read_all", lambda: ({}, {}))
    monkeypatch.setattr(checks_feed, "read_context", lambda: "CTX")

    def fake_build(day_env, live, ctx=None):
        seen["ctx"] = ctx
        return {}

    monkeypatch.setattr(scanner, "_build_populate", fake_build)
    scanner._read_and_build()
    assert seen["ctx"] == "CTX"

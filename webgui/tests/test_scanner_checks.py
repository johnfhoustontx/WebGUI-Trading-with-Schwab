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
    rows = [{"id": s, "_checks_state": s, "_checks_clear": s == "pos"}
            for s in ("pos", "warn", "neg", "muted")]
    rows.append({"id": "none"})
    assert [r["id"] for r in scanner.only_clear(rows)] == ["pos"]


def test_only_clear_fails_closed_on_a_row_without_the_clear_stamp():
    assert scanner.only_clear([{"id": "p", "_checks_state": "pos"}]) == []


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
        "Hide rows with a block, a caution, a feed that hasn't loaded, "
        "or a paper book fit that couldn't be checked")


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


# ── Only clear hides a paper book fit that could not be checked ─────────────
_CAL = {"by_bucket": {}}      # a published calibration with no bucket for the row


def _stamped(sig, ctx=None, rows_fn=None):
    rows = (rows_fn or scanner.signal_rows)([sig])
    scanner.stamp_stale(rows, [sig])
    return scanner.stamp_checks(rows, [sig], ctx or _ctx(calibration=_CAL))


def test_only_clear_keeps_a_row_with_full_stamps():
    rows = _stamped(_pcs_signal())
    assert rows[0]["_checks_state"] == "pos" and rows[0]["_checks_clear"] is True
    assert scanner.only_clear(rows) == rows


def test_only_clear_hides_a_clear_row_whose_book_fit_could_not_be_checked():
    rows = _stamped(_pcs_signal(ledger_risk_basis=None, ledger_risk_per_contract=None))
    # The chip's own verdict is unchanged - a grey book line alone still reads Clear.
    assert rows[0]["_checks_state"] == "pos"
    assert rows[0]["_checks_clear"] is False
    assert scanner.only_clear(rows) == []


def test_only_clear_hides_a_row_with_no_stamps_at_all():
    unstamped = ("ledger_risk_basis", "ledger_risk_per_contract", "friction_pct",
                 "em_to_expiry", "vol_floor", "iv_rank_known", "earnings_status",
                 "earnings_date")
    bare = {k: v for k, v in _pcs_signal().items() if k not in unstamped}
    rows = _stamped(bare)
    assert rows[0]["_checks_clear"] is False
    assert scanner.only_clear(rows) == []


def test_only_clear_keeps_a_clear_naked_short_that_has_no_book_line():
    naked = {"id": "ORCL_SHORT_PUT_2026-10-17_100", "symbol": "ORCL", "type": "SHORT_PUT",
             "family": "DIRECTIONAL", "strategy_label": "Short Put", "bias": "bullish",
             "legs": [{"side": "short", "kind": "put", "strike": 100.0,
                       "expiration": "2026-10-17"}],
             "expiration": "2026-10-17", "dte": 12, "composite_score": 70, "grade": "Good",
             "max_profit": 1.2, "unbounded_loss": True, "net_vega": -0.3,
             "iv_rank": 55.0, "iv_rank_known": True, "vol_floor": 30, "friction_pct": 8.0,
             "em_to_expiry": 6.0, "earnings_status": "none_scheduled", "earnings_date": None,
             "underlying_price": 110.0, "live": True}
    rows = _stamped(naked, rows_fn=scanner.directional_rows)
    items = checks_feed.checks_for({**naked, "_allow_paper": False}, _ctx(calibration=_CAL))
    assert "book" not in {c["key"] for c in items}
    assert rows[0]["_allow_paper"] is False
    assert rows[0]["_checks_state"] == "pos"
    assert scanner.only_clear(rows) == rows


def test_stamp_checks_stamps_the_short_chip():
    rows = _stamped(_pcs_signal())
    assert rows[0]["_checks_short"] == "Clear · 8 of 9"
    assert rows[0]["checks"] == "Clear · 8 of 9 checked"


# ── tab counts follow the filter ─────────────────────────────────────────────
def test_tab_label_while_filtered_reads_shown_of_total():
    assert scanner.filtered_tab_label("Swing", 40, 3, have=True, filtering=True) == "Swing (3 of 40)"


def test_tab_label_unfiltered_is_the_plain_count():
    assert scanner.filtered_tab_label("Swing", 40, 3, have=True, filtering=False) == "Swing (40)"


def test_tab_label_has_no_count_before_todays_scan_filtered_or_not():
    assert scanner.filtered_tab_label("Swing", 0, 0, have=False, filtering=True) == "Swing"
    assert scanner.filtered_tab_label("Swing", 0, 0, have=False, filtering=False) == "Swing"


# ── an empty filtered table explains itself ──────────────────────────────────
def _rows_in(*states):
    return [{"id": f"r{i}", "_checks_state": s} for i, s in enumerate(states)]


def test_empty_label_when_every_hidden_row_is_partly_checked():
    full = _rows_in(*(["muted"] * 40))
    assert scanner.only_clear_empty_label(full, [], filtering=True) == (
        "Every row is only partly checked — a feed the checks read hasn't loaded. "
        "Turn off Only clear to see all 40.")


def test_empty_label_when_rows_were_hidden_for_other_reasons():
    full = _rows_in(*(["muted"] * 38 + ["warn", "neg"]))
    assert scanner.only_clear_empty_label(full, [], filtering=True) == (
        "No row is fully clear — 40 hidden by Only clear.")


def test_empty_label_is_the_normal_one_otherwise():
    full = _rows_in("pos", "muted")
    assert scanner.only_clear_empty_label(full, [], filtering=False) is None
    assert scanner.only_clear_empty_label([], [], filtering=True) is None
    assert scanner.only_clear_empty_label(full, full[:1], filtering=True) is None


# ── a context change re-stamps; only a scan change re-reads the day union ────
def test_repaint_action_decides_rebuild_restamp_or_skip():
    day, live = scanner._DAY_VIEW, scanner._LIVE_VIEW
    caps, regime = checks_feed.CAPS_VIEW, checks_feed.REGIME_VIEW
    assert scanner.repaint_action({day}) == "rebuild"
    assert scanner.repaint_action({live, caps}) == "rebuild"
    assert scanner.repaint_action({caps}) == "restamp"
    assert scanner.repaint_action({regime}) == "restamp"
    assert scanner.repaint_action(set()) == "skip"


def test_a_calibration_change_restamps_the_rows():
    """The nightly calibration rebuild must reach the Track record line the same
    evening, not wait for the Opportunity Board to move the next morning."""
    assert checks_feed.CALIBRATION_VIEW in checks_feed.REFRESH_VIEWS
    assert scanner.repaint_action({checks_feed.CALIBRATION_VIEW}) == "restamp"
    assert scanner.repaint_action({scanner._DAY_VIEW, checks_feed.CALIBRATION_VIEW}) == "rebuild"


def test_repaint_action_on_the_timer_restamps_only_when_the_board_moved():
    assert scanner.repaint_action(set(), timer=True, matrix_moved=True) == "restamp"
    assert scanner.repaint_action(set(), timer=True, matrix_moved=False) == "skip"


def test_restamp_stamps_copies_and_leaves_the_painted_rows_alone():
    sig = _pcs_signal()
    rows = _stamped(sig, _ctx(calibration=None))
    rows[0]["_new"] = True
    before = dict(rows[0])
    assert before["checks"] == "Partly checked · 8 of 9 checked"
    out = scanner.restamp({"signals_swing": rows}, {"signals_swing": [sig]},
                          _ctx(calibration=_CAL))
    assert rows[0] == before
    fresh = out["signals_swing"][0]
    assert fresh is not rows[0]
    assert fresh["checks"] == "Clear · 8 of 9 checked" and fresh["_checks_clear"] is True
    assert fresh["_new"] is True and fresh["_allow_paper"] is True


def test_read_and_restamp_reads_the_live_context(monkeypatch):
    sig = _pcs_signal()
    rows = _stamped(sig, _ctx(calibration=None))
    monkeypatch.setattr(checks_feed, "read_context", lambda: _ctx(calibration=_CAL))
    out = scanner._read_and_restamp({"signals_swing": rows}, {"signals_swing": [sig]})
    assert out["signals_swing"][0]["_checks_state"] == "pos"


def test_render_restamps_without_rereading_the_day_union():
    src = inspect.getsource(scanner.render)
    assert src.count("run.io_bound(_read_and_build)") == 2
    assert "run.io_bound(_read_and_restamp" in src
    assert "repaint_action(" in src
    assert "bus_client.read_version(checks_feed.MATRIX_VIEW)" in src
    assert '"no-data-label"' in src
    assert "only_clear_empty_label(" in src
    assert "filtered_tab_label(" in src


def test_the_checks_slot_shows_the_short_chip_with_the_full_text_in_a_tooltip():
    assert "props.row._checks_short" in scanner._CHECKS_SLOT
    assert "<q-tooltip" in scanner._CHECKS_SLOT
    assert "{{ props.value }}" in scanner._CHECKS_SLOT


# ── the Trade detail panel's checklist ───────────────────────────────────────
def test_checklist_candidate_for_takes_the_painted_rows_paper_gate():
    live = _pcs_signal()
    stale = _pcs_signal(id="old", live=False, stale_since="2026-07-16T11:00:00")
    painted = {"signals_swing": _stamped(live) + _stamped(stale)}
    by_id = {live["id"]: live, stale["id"]: stale}
    cand = scanner.checklist_candidate_for(live["id"], by_id, painted)
    assert cand["_allow_paper"] is True and cand["id"] == live["id"]
    assert "book" in {c["key"] for c in checks_feed.checks_for(cand, _ctx())}
    gone = scanner.checklist_candidate_for("old", by_id, painted)
    assert gone["_allow_paper"] is False
    assert "book" not in {c["key"] for c in checks_feed.checks_for(gone, _ctx())}
    assert scanner.checklist_candidate_for("nope", by_id, painted) is None
    # A signal with no painted row yet has no gate to hand across.
    assert scanner.checklist_candidate_for(live["id"], by_id, {})["_allow_paper"] is None


def test_build_populate_carries_the_context_it_stamped_against():
    env = {"date": scanner.today_ct(), "signals_0dte": [], "signals_swing": [_pcs_signal()],
           "signals_directional": []}
    ctx = _ctx()
    assert scanner._build_populate(env, {}, ctx)["ctx"] is ctx


def test_read_and_restamp_can_hand_back_the_context_it_read(monkeypatch):
    sig = _pcs_signal()
    rows = _stamped(sig, _ctx(calibration=None))
    ctx = _ctx(calibration=_CAL)
    monkeypatch.setattr(checks_feed, "read_context", lambda: ctx)
    holder = {}
    scanner._read_and_restamp({"signals_swing": rows}, {"signals_swing": [sig]}, holder)
    assert holder["ctx"] is ctx


def test_render_hands_the_detail_panel_a_candidate_and_refreshes_it():
    src = inspect.getsource(scanner.render)
    assert src.count("candidate=") >= 2          # the signal and directional clicks
    assert "checklist_candidate_for(" in src
    assert "detail_panel.refresh_checks(" in src
    assert "checks_feed.read_context()" not in src

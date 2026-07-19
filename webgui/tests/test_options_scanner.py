"""Tests for the scanner quality-score coloring (pages/options/scanner.py).

``score_zone_color`` maps a composite score (0-100) to a hex zone color,
matching the speedometer zones in pages/options/svg.py. ``signal_rows`` stamps
each row with ``_score_color`` so the table body-cell slot can render a colored
chip.
"""
import bus_client
import pytest

from pages.options import scanner


@pytest.fixture
def fresh_bus():
    """Fresh fakeredis-backed Bus per test so cache state does not leak (the
    ``bus_client.reset()`` idiom from test_bus_client.py)."""
    bus_client.reset()
    yield
    bus_client.reset()


def test_score_zone_color_none():
    assert scanner.score_zone_color(None) == "#666666"


def test_score_zone_color_red():
    assert scanner.score_zone_color(30) == scanner.RED


def test_score_zone_color_amber():
    assert scanner.score_zone_color(50) == scanner.AMBER


def test_score_zone_color_blue():
    assert scanner.score_zone_color(60) == scanner.BLUE


def test_score_zone_color_green():
    assert scanner.score_zone_color(90) == scanner.GREEN


def test_score_zone_color_boundaries():
    # Zone edges are exclusive lower bounds: <40 RED, <55 AMBER, <75 BLUE, else GREEN.
    assert scanner.score_zone_color(39) == scanner.RED
    assert scanner.score_zone_color(40) == scanner.AMBER
    assert scanner.score_zone_color(54) == scanner.AMBER
    assert scanner.score_zone_color(55) == scanner.BLUE
    assert scanner.score_zone_color(74) == scanner.BLUE
    assert scanner.score_zone_color(75) == scanner.GREEN


def test_signal_columns_merges_short_long_into_strikes():
    """Short + Long collapse into one compact 'Strikes' column so the right-hand
    columns (Score/Grade/actions) fit without horizontal scrolling."""
    fields = [c["field"] for c in scanner.signal_columns()]
    assert "strikes" in fields
    assert "short_strike" not in fields and "long_strike" not in fields


def test_signal_rows_builds_strikes_for_spread():
    rows = scanner.signal_rows([
        {"symbol": "SPY", "type": "PCS", "short_strike": 450, "long_strike": 445}])
    assert rows[0]["strikes"] == "450/445"


def test_signal_rows_builds_strikes_for_iron_condor():
    rows = scanner.signal_rows([
        {"symbol": "SPY", "type": "IC", "short_strike": 450, "long_strike": 445,
         "call_short": 460, "call_long": 465}])
    assert "450/445" in rows[0]["strikes"] and "460/465" in rows[0]["strikes"]


def test_signal_rows_shortens_expiration_to_mmdd():
    rows = scanner.signal_rows([{"symbol": "SPY", "expiration": "2026-06-26"}])
    assert rows[0]["expiration"] == "06/26"


def test_signal_rows_strikes_strip_whole_number_decimals():
    """Whole-number strikes render without a trailing '.0' (narrower column)."""
    rows = scanner.signal_rows([
        {"symbol": "MU", "type": "PCS", "short_strike": 1085.0, "long_strike": 1070.0}])
    assert rows[0]["strikes"] == "1085/1070"


def test_score_zone_class_maps_zones():
    assert scanner.score_zone_class(80) == "bg-[#66bb6a]"
    assert scanner.score_zone_class(60) == "bg-[#42a5f5]"
    assert scanner.score_zone_class(50) == "bg-[#ffa726]"
    assert scanner.score_zone_class(30) == "bg-[#ef5350]"
    assert scanner.score_zone_class(None) == "bg-[#666666]"


def test_signal_rows_stamp_score_class():
    rows = scanner.signal_rows([
        {"symbol": "HI", "composite_score": 90},
        {"symbol": "LO", "composite_score": 30},
        {"symbol": "NA"},
    ])
    by_sym = {r["symbol"]: r for r in rows}
    assert by_sym["HI"]["_score_class"] == "bg-[#66bb6a]"
    assert by_sym["LO"]["_score_class"] == "bg-[#ef5350]"
    assert by_sym["NA"]["_score_class"] == "bg-[#666666]"


def _sig(symbol, **kw):
    base = {"symbol": symbol, "type": "PCS", "short_strike": 100,
            "long_strike": 95, "expiration": "2026-06-19"}
    base.update(kw)
    return base


# ── signal identity (_sig_key) ───────────────────────────────────────────────
def test_sig_key_uses_the_engine_id():
    assert scanner._sig_key({"id": "SPY_PCS_2026-07-17_450_445"}) == \
        "SPY_PCS_2026-07-17_450_445"


def test_sig_key_distinguishes_display_rows_at_different_strikes():
    """REGRESSION: ``_sig_key`` is fed DISPLAY rows by the page, and ``signal_rows``
    merges short+long into ONE ``strikes`` cell — so a strike-rebuilt key collapsed
    every row on a symbol/type/expiry onto 'SPY|PCS|None|None|07/17' and a genuinely
    new signal at different strikes went unmarked. Keying off the engine's unique
    ``id`` keeps them distinct."""
    rows = scanner.signal_rows([
        {"id": "SPY_PCS_2026-07-17_450_445", "symbol": "SPY", "type": "PCS",
         "short_strike": 450, "long_strike": 445, "expiration": "2026-07-17"},
        {"id": "SPY_PCS_2026-07-17_460_455", "symbol": "SPY", "type": "PCS",
         "short_strike": 460, "long_strike": 455, "expiration": "2026-07-17"},
    ])
    assert len({scanner._sig_key(r) for r in rows}) == 2


def test_sig_key_falls_back_to_the_composite_key_without_an_id():
    """alerts.py feeds RAW signals; an id-less signal must still key on its legs
    rather than collapsing every id-less signal onto one key."""
    a = scanner._sig_key(_sig("SPY", short_strike=100, long_strike=95))
    b = scanner._sig_key(_sig("SPY", short_strike=110, long_strike=105))
    assert a != b


# ── NEW markers: unseen-since-you-last-VIEWED-the-page (date-scoped, id-keyed) ─
DAY, NEXT_DAY = "2026-07-16", "2026-07-17"


def test_unseen_ids_marks_everything_on_a_cold_state():
    scanner._reset_seen_state()
    assert scanner.unseen_ids({"a", "b"}, DAY) == {"a", "b"}


def test_unseen_ids_does_not_mark_them_seen():
    """``unseen_ids`` is a pure QUERY — calling it twice must not clear the marks
    (only ``acknowledge_ids`` does)."""
    scanner._reset_seen_state()
    scanner.unseen_ids({"a"}, DAY)
    assert scanner.unseen_ids({"a"}, DAY) == {"a"}


def test_acknowledge_ids_clears_them():
    scanner._reset_seen_state()
    scanner.acknowledge_ids({"a", "b"}, DAY)
    assert scanner.unseen_ids({"a", "b"}, DAY) == set()


def test_only_ids_unseen_since_the_last_view_are_new():
    scanner._reset_seen_state()
    scanner.acknowledge_ids({"a", "b"}, DAY)
    assert scanner.unseen_ids({"a", "b", "c"}, DAY) == {"c"}


def test_marks_accumulate_across_scans_until_the_page_is_viewed():
    """The point of the rework: the OLD marker cleared on the next scan whether or
    not anyone looked. Repeated scans while away must ACCUMULATE unseen ids."""
    scanner._reset_seen_state()
    scanner.acknowledge_ids({"a"}, DAY)          # viewed the page
    scanner.unseen_ids({"a", "b"}, DAY)          # scan lands while away
    assert scanner.unseen_ids({"a", "b", "c"}, DAY) == {"b", "c"}   # b still new


def test_seen_state_resets_on_a_new_day():
    scanner._reset_seen_state()
    scanner.acknowledge_ids({"a"}, DAY)
    assert scanner.unseen_ids({"a"}, NEXT_DAY) == {"a"}


def test_acknowledge_on_a_new_day_drops_yesterdays_ids():
    scanner._reset_seen_state()
    scanner.acknowledge_ids({"a"}, DAY)
    scanner.acknowledge_ids({"b"}, NEXT_DAY)
    assert scanner.unseen_ids({"a"}, NEXT_DAY) == {"a"}


def test_new_ids_for_paint_snapshots_before_acknowledging():
    """ORDERING IS LOAD-BEARING — the paint must snapshot ``unseen`` BEFORE it
    acknowledges. Acknowledge first and nothing is ever New."""
    scanner._reset_seen_state()
    assert scanner.new_ids_for_paint({"a"}, DAY, acknowledge=True) == {"a"}
    assert scanner.new_ids_for_paint({"a"}, DAY, acknowledge=True) == set()


def test_new_ids_for_paint_without_acknowledge_keeps_the_marks():
    """A background version-poll repaint is NOT a view — it must not clear marks."""
    scanner._reset_seen_state()
    assert scanner.new_ids_for_paint({"a"}, DAY, acknowledge=False) == {"a"}
    assert scanner.new_ids_for_paint({"a"}, DAY, acknowledge=False) == {"a"}


def test_stamp_new_sets_flag_from_keys():
    rows = [_sig("AAA"), _sig("BBB")]
    new_keys = {scanner._sig_key(_sig("BBB"))}
    scanner.stamp_new(rows, new_keys)
    by_sym = {r["symbol"]: r for r in rows}
    assert by_sym["AAA"]["_new"] is False
    assert by_sym["BBB"]["_new"] is True


def test_stamp_new_matches_rows_by_id():
    rows = scanner.signal_rows([
        {"id": "keep", "symbol": "AAA"}, {"id": "fresh", "symbol": "BBB"}])
    scanner.stamp_new(rows, {"fresh"})
    by_sym = {r["symbol"]: r for r in rows}
    assert by_sym["BBB"]["_new"] is True and by_sym["AAA"]["_new"] is False


# ── day union: the envelope's date is a GATE, not decoration ─────────────────
def _day_env(date, **lists):
    env = {"date": date}
    env.update(lists)
    return env


def test_day_is_today_true_for_todays_envelope():
    assert scanner.day_is_today(_day_env(DAY), today=DAY) is True


def test_day_is_today_false_for_yesterdays_envelope():
    assert scanner.day_is_today(_day_env("2026-07-15"), today=DAY) is False


def test_day_is_today_false_for_an_empty_or_dateless_envelope():
    assert scanner.day_is_today({}, today=DAY) is False
    assert scanner.day_is_today({"signals_0dte": [{"id": "a"}]}, today=DAY) is False


def test_day_signals_returns_todays_list():
    env = _day_env(DAY, signals_0dte=[{"id": "a", "live": True}])
    assert scanner.day_signals(env, "signals_0dte", today=DAY) == [{"id": "a", "live": True}]


def test_yesterdays_envelope_renders_no_rows_even_though_its_signals_say_live():
    """The merge is best-effort and leaves the key UNTOUCHED on failure, so the
    failure mode is STALE, not absent: on a failed first scan of a new day the key
    still holds yesterday's envelope INCLUDING live=True entries. Rendering it blind
    presents day-old signals as live and tradeable."""
    stale_env = _day_env("2026-07-15", signals_0dte=[
        {"id": "a", "symbol": "SPY", "live": True, "stale_since": None}])
    sigs = scanner.day_signals(stale_env, "signals_0dte", today=DAY)
    assert sigs == []
    assert scanner.signal_rows(sigs) == []


def test_day_signals_tolerates_a_missing_or_malformed_list():
    env = _day_env(DAY, signals_0dte="not-a-list")
    assert scanner.day_signals(env, "signals_0dte", today=DAY) == []
    assert scanner.day_signals(env, "signals_directional", today=DAY) == []


def test_today_ct_is_an_iso_date_in_central_time():
    import datetime as dt
    from zoneinfo import ZoneInfo
    assert scanner.today_ct() == dt.datetime.now(ZoneInfo("America/Chicago")).date().isoformat()


# ── day-cap truncation notice (no silent caps) ───────────────────────────────
def test_day_note_surfaces_truncation():
    env = _day_env(DAY, truncated={"signals_0dte": 12, "signals_swing": 3})
    note = scanner.day_note(env, today=DAY)
    assert "15" in note and "dropped" in note.lower()


def test_day_note_is_empty_when_nothing_was_truncated():
    assert scanner.day_note(_day_env(DAY, signals_0dte=[]), today=DAY) == ""


def test_day_note_singular_for_one_dropped_signal():
    note = scanner.day_note(_day_env(DAY, truncated={"signals_swing": 1}), today=DAY)
    assert "1 earlier signal " in note


def test_day_note_flags_a_stale_dated_envelope():
    note = scanner.day_note(_day_env("2026-07-15", signals_0dte=[{"id": "a"}]), today=DAY)
    assert "previous session" in note


def test_day_note_empty_when_the_service_is_cold():
    # status_line already says "Waiting for options service…" — don't say it twice.
    assert scanner.day_note({}, today=DAY) == ""


def test_truncated_note_absent_safe():
    assert scanner.truncated_note({}) == ""
    assert scanner.truncated_note({"truncated": {}}) == ""
    assert scanner.truncated_note(None) == ""


# ── dropped-out (stale) signals stay visible, dimmed + frozen ────────────────
def test_stamp_stale_dims_a_dropped_signal():
    sigs = [{"id": "a", "symbol": "SPY", "live": False,
             "stale_since": "2026-07-16T13:32:00"}]
    rows = scanner.stamp_stale(scanner.signal_rows(sigs), sigs)
    assert rows[0]["_stale"] is True
    assert rows[0]["_row_class"] == scanner.STALE_ROW_CLASS
    assert rows[0]["stale_since"] == "1:32 PM"


def test_stamp_stale_leaves_a_live_signal_undimmed():
    sigs = [{"id": "a", "symbol": "SPY", "live": True, "stale_since": None}]
    rows = scanner.stamp_stale(scanner.signal_rows(sigs), sigs)
    assert rows[0]["_stale"] is False
    assert rows[0]["_row_class"] == ""
    assert rows[0]["stale_since"] == ""


def test_stamp_stale_treats_an_absent_live_key_as_live():
    """A payload from the OLD live-only cache:options:scan carries no ``live`` key
    at all — it must NOT read as stale. Hence ``live is False``, not ``not live``."""
    sigs = [{"id": "a", "symbol": "SPY"}]
    rows = scanner.stamp_stale(scanner.signal_rows(sigs), sigs)
    assert rows[0]["_stale"] is False
    assert rows[0]["_row_class"] == ""


def test_stamp_stale_joins_by_id_not_by_position():
    """``signal_rows`` re-sorts by score, so the row order does not match the signal
    order — the stale join must key on id."""
    sigs = [{"id": "lo", "symbol": "LO", "composite_score": 10, "live": False,
             "stale_since": "2026-07-16T13:32:00"},
            {"id": "hi", "symbol": "HI", "composite_score": 90, "live": True}]
    rows = scanner.stamp_stale(scanner.signal_rows(sigs), sigs)
    by_sym = {r["symbol"]: r for r in rows}
    assert rows[0]["symbol"] == "HI"          # re-sorted: order differs from sigs
    assert by_sym["LO"]["_stale"] is True
    assert by_sym["HI"]["_stale"] is False


def test_signal_columns_carry_a_dropped_column():
    cols = {c["field"]: c["label"] for c in scanner.signal_columns()}
    assert cols.get("stale_since") == "Dropped"


def test_row_class_prop_binds_the_stamped_field():
    """The dimming is a ``.props()`` string no row test can reach, so a rename of
    ``_row_class`` would silently kill it while every row test stayed green."""
    assert "_row_class" in scanner._ROW_CLASS_PROP


# ── a dropped signal is frozen at an hours-old price: never paper-tradeable ──
def test_stamp_stale_blocks_paper_on_a_dropped_row():
    """``paper_create`` records ``signal['credit']`` VERBATIM with no re-pricing,
    so booking a dropped row writes a fictional entry (a 9:30 credit stamped with a
    2pm entry_time) into the manual book — which IS the scanner-baseline-vs-decider
    benchmark. Before the day union this path did not exist; persistence created it."""
    sigs = [{"id": "a", "symbol": "SPY", "type": "PCS", "live": False,
             "stale_since": "2026-07-16T11:00:00"}]
    rows = scanner.stamp_stale(scanner.signal_rows(sigs), sigs)
    assert rows[0]["_allow_paper"] is False


def test_stamp_stale_leaves_paper_available_on_a_live_row():
    """A gate that blocks everything is as broken as one that blocks nothing."""
    sigs = [{"id": "a", "symbol": "SPY", "type": "PCS", "live": True}]
    rows = scanner.stamp_stale(scanner.signal_rows(sigs), sigs)
    assert rows[0]["_allow_paper"] is True


def test_stamp_stale_leaves_paper_available_when_live_is_absent():
    sigs = [{"id": "a", "symbol": "SPY", "type": "PCS"}]
    rows = scanner.stamp_stale(scanner.signal_rows(sigs), sigs)
    assert rows[0]["_allow_paper"] is True


def test_stamp_stale_only_ever_narrows_the_paper_gate():
    """A LIVE naked short is already un-tradeable (undefined risk) — the stale pass
    must not hand it a Paper button back."""
    naked = {"id": "n", "symbol": "SPY", "type": "SHORT_CALL", "live": True,
             "unbounded_loss": True, "max_profit": 1.2, "legs": [],
             "strategy_label": "Short Call", "bias": "bearish"}
    rows = scanner.stamp_stale(scanner.directional_rows([naked]), [naked])
    assert rows[0]["_allow_paper"] is False


def test_stamp_stale_blocks_paper_on_a_stale_directional_long():
    sigs = [dict(_DIR_SIG, live=False, stale_since="2026-07-16T11:00:00")]
    rows = scanner.stamp_stale(scanner.directional_rows(sigs), sigs)
    assert rows[0]["_allow_paper"] is False


# ── bounded DOM: the day union reaches ~1,746 rows per list ──────────────────
def test_table_pagination_is_bounded():
    """Quasar reads ``rowsPerPage: 0`` as INFINITE. Unpaginated, the day's three
    tables would render ~5,238 rows x 13-16 columns (~75k cells, many carrying a
    q-badge/q-tooltip) and rebuild them wholesale on every scan — worst at 3pm,
    exactly when a trader is looking."""
    assert scanner._TABLE_PAGINATION["rowsPerPage"] > 0


# ── the ~4.5 MB day payload never blocks the event loop ─────────────────────
def test_read_all_returns_both_views(fresh_bus):
    bus = bus_client.bus()
    bus.cache_set("cache:options:scan_day", {"date": DAY, "signals_0dte": []})
    bus.cache_set("cache:options:scan", {"timestamp": "x", "signals_0dte": []})
    day_env, live = scanner._read_all()
    assert day_env["date"] == DAY and live["timestamp"] == "x"


def test_read_all_is_graceful_when_the_service_is_cold(fresh_bus):
    assert scanner._read_all() == ({}, {})


def test_render_reads_the_day_payload_off_the_event_loop():
    """The day union serializes to ~4.5 MB by day's end (~880 B x ~5,238 entries),
    and building its ~5,238 display rows is likewise heavy. Both read+build sites —
    the first paint and the on-change repaint — must go through ``run.io_bound``
    (via _read_and_build, which reads AND builds the rows off the loop); the 2 s
    poll itself stays on-loop (it reads only ``:ver`` ints)."""
    import inspect
    src = inspect.getsource(scanner.render)
    assert src.count("run.io_bound(_read_and_build)") == 2
    # No un-wrapped payload read may remain in render (read_versions is the cheap
    # :ver probe and is deliberately allowed to stay on the loop).
    assert "bus_client.read(" not in src


def test_read_and_build_builds_rows_off_loop(fresh_bus):
    """_read_and_build reads BOTH payloads and constructs the display rows in one
    off-thread call, so only the UI assignment is left for the event loop."""
    bus = bus_client.bus()
    bus.cache_set("cache:options:scan_day",
                  {"date": scanner.today_ct(), "signals_0dte": [], "signals_swing": [],
                   "signals_directional": []})
    bus.cache_set("cache:options:scan", {"timestamp": "x", "signals_0dte": []})
    built = scanner._read_and_build()
    assert set(built["rows"]) == {"signals_0dte", "signals_swing", "signals_directional"}
    assert built["live"]["timestamp"] == "x"
    assert isinstance(built["by_id"], dict)


# ── Directional tab (single-leg long/short calls+puts, Fit+Quality scored) ───
_DIR_SIG = {"id": "SPY_LONG_CALL_2026-07-17_450", "symbol": "SPY",
            "type": "LONG_CALL", "family": "DIRECTIONAL",
            "strategy_label": "Long Call", "bias": "bullish",
            "legs": [{"side": "long", "kind": "call", "strike": 450,
                      "expiration": "2026-07-17"}],
            "expiration": "2026-07-17", "dte": 1, "composite_score": 71,
            "grade": "Good", "max_profit": None, "unbounded_profit": True}


def test_directional_columns_lead_with_symbol():
    """The Scanner's directional scan spans the WHOLE watchlist (unlike the
    single-symbol Swing page that strategy_columns was built for), so the symbol
    is load-bearing here."""
    fields = [c["field"] for c in scanner.directional_columns()]
    assert fields[0] == "symbol"
    assert "stale_since" in fields
    assert fields[-1] == "actions"


def test_directional_columns_reuse_the_shared_strategy_columns():
    from pages.options import strategy_table
    shared = {c["field"] for c in strategy_table.strategy_columns()}
    fields = {c["field"] for c in scanner.directional_columns()}
    assert shared <= fields


def test_directional_columns_have_no_premium_composite_columns():
    """A Fit+Quality score must never sit beside premium credit-spread economics —
    that is the whole reason directional gets its own tab."""
    fields = {c["field"] for c in scanner.directional_columns()}
    assert "credit" not in fields and "rr_pct" not in fields


def test_directional_rows_carry_the_symbol():
    rows = scanner.directional_rows([_DIR_SIG])
    assert rows[0]["symbol"] == "SPY"
    assert rows[0]["strategy_label"] == "Long Call"


def test_directional_rows_badge_a_naked_short_as_undefined_risk():
    naked = dict(_DIR_SIG, id="SPY_SHORT_CALL_2026-07-17_450", type="SHORT_CALL",
                 strategy_label="Short Call", unbounded_profit=False,
                 unbounded_loss=True, max_profit=1.2, max_loss=9999)
    rows = scanner.directional_rows([naked])
    assert rows[0]["_undefined_risk"] is True
    assert rows[0]["max_loss"] == "∞"


def test_directional_rows_never_allow_paper_trading_a_naked_short():
    naked = dict(_DIR_SIG, id="x", type="SHORT_PUT", unbounded_loss=True,
                 max_profit=1.2)
    assert scanner.directional_rows([naked])[0]["_allow_paper"] is False


def test_directional_rows_allow_paper_trading_a_long_option():
    assert scanner.directional_rows([_DIR_SIG])[0]["_allow_paper"] is True


def test_directional_rows_keep_both_windows_of_the_same_type():
    """A 0-DTE and a swing LONG_CALL are two distinct trades (different ids +
    expirations, disambiguated by the DTE column) — never deduped."""
    swing = dict(_DIR_SIG, id="SPY_LONG_CALL_2026-07-24_450",
                 expiration="2026-07-24", dte=8, composite_score=60)
    rows = scanner.directional_rows([_DIR_SIG, swing])
    assert len(rows) == 2
    assert {r["dte"] for r in rows} == {1, 8}


def test_directional_rows_take_stale_and_new_marks():
    sigs = [dict(_DIR_SIG, live=False, stale_since="2026-07-16T13:32:00")]
    rows = scanner.stamp_stale(scanner.directional_rows(sigs), sigs)
    scanner.stamp_new(rows, {_DIR_SIG["id"]})
    assert rows[0]["_stale"] is True and rows[0]["_new"] is True
    assert rows[0]["stale_since"] == "1:32 PM"


# ── tab header counts ────────────────────────────────────────────────────────
def test_tab_label_appends_count():
    assert scanner.tab_label("0-DTE", 3) == "0-DTE (3)"
    assert scanner.tab_label("Swing", 0) == "Swing (0)"


def test_tab_label_no_count_when_none():
    assert scanner.tab_label("0-DTE", None) == "0-DTE"


# ── bottom status line ───────────────────────────────────────────────────────
def test_status_line_waiting_when_empty():
    assert scanner.status_line({}) == "Waiting for options service…"


def test_status_line_has_time_count_and_cadence():
    out = scanner.status_line({
        "signals_0dte": [{}], "signals_swing": [{}, {}],
        "timestamp": "2026-06-15T13:32:00-05:00"})
    assert "Last scan 1:32" in out
    assert "3 live signals" in out
    assert "auto-scans every 15 min" in out


def test_status_line_counts_directional_signals():
    out = scanner.status_line({"signals_0dte": [{}], "signals_swing": [{}],
                              "signals_directional": [{}, {}]})
    assert "4 live signals" in out


def test_status_line_says_LIVE_so_it_cannot_read_as_the_day_count():
    """The tab headers carry the DAY's counts (hundreds by 3pm) while this line
    sums the LAST SCAN (dozens). A bare '37 signals' under tabs reading '(412)'
    reads as a bug — the word 'live' is what makes the ~50x gap legible."""
    out = scanner.status_line({"signals_0dte": [{}], "timestamp": None})
    assert "1 live signal" in out
    assert "1 signals" not in out


def test_status_line_includes_errors():
    out = scanner.status_line({"signals_0dte": [], "signals_swing": [],
                               "errors": ["x"], "timestamp": None})
    assert "1 errors" in out

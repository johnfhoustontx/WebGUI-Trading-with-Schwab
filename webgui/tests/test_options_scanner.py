"""Tests for the scanner quality-score coloring (pages/options/scanner.py).

``score_zone_color`` maps a composite score (0-100) to a hex zone color,
matching the speedometer zones in pages/options/svg.py. ``signal_rows`` stamps
each row with ``_score_color`` so the table body-cell slot can render a colored
chip.
"""
from pages.options import scanner


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


def test_signal_rows_stamp_score_color():
    rows = scanner.signal_rows([
        {"symbol": "HI", "composite_score": 90},
        {"symbol": "LO", "composite_score": 30},
        {"symbol": "NA"},
    ])
    by_sym = {r["symbol"]: r for r in rows}
    assert by_sym["HI"]["_score_color"] == scanner.GREEN
    assert by_sym["LO"]["_score_color"] == scanner.RED
    assert by_sym["NA"]["_score_color"] == "#666666"


def _sig(symbol, **kw):
    base = {"symbol": symbol, "type": "PCS", "short_strike": 100,
            "long_strike": 95, "expiration": "2026-06-19"}
    base.update(kw)
    return base


# ── persistent NEW markers (tied to the scan VERSION, survive navigation) ────
def test_compute_new_keys_first_scan_marks_nothing():
    scanner._reset_new_state()
    assert scanner.compute_new_keys(1, {"a", "b"}) == set()


def test_compute_new_keys_flags_only_new_on_next_scan():
    scanner._reset_new_state()
    scanner.compute_new_keys(1, {"a", "b"})
    assert scanner.compute_new_keys(2, {"a", "b", "c"}) == {"c"}


def test_compute_new_keys_persists_across_same_version():
    """Re-rendering at the same scan version returns the SAME new set — the
    markers persist across navigation, not re-diffed away to empty."""
    scanner._reset_new_state()
    scanner.compute_new_keys(1, {"a"})
    scanner.compute_new_keys(2, {"a", "b"})              # b is new
    assert scanner.compute_new_keys(2, {"a", "b"}) == {"b"}   # nav away + back


def test_compute_new_keys_recomputes_each_new_scan():
    scanner._reset_new_state()
    scanner.compute_new_keys(1, {"a"})
    scanner.compute_new_keys(2, {"a", "b"})              # b new this scan
    assert scanner.compute_new_keys(3, {"a", "b", "c"}) == {"c"}   # b no longer new


def test_compute_new_keys_none_version_marks_nothing():
    scanner._reset_new_state()
    assert scanner.compute_new_keys(None, {"a"}) == set()


def test_stamp_new_sets_flag_from_keys():
    rows = [_sig("AAA"), _sig("BBB")]
    new_keys = {scanner._sig_key(_sig("BBB"))}
    scanner.stamp_new(rows, new_keys)
    by_sym = {r["symbol"]: r for r in rows}
    assert by_sym["AAA"]["_new"] is False
    assert by_sym["BBB"]["_new"] is True


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
    assert "3 signals" in out
    assert "auto-scans every 15 min" in out


def test_status_line_includes_errors():
    out = scanner.status_line({"signals_0dte": [], "signals_swing": [],
                               "errors": ["x"], "timestamp": None})
    assert "1 errors" in out

"""shared.futures_chain: the /ES and /NQ 0-DTE chain feed's shared vocabulary.

Design: docs/plans/2026-10-31-futures-0dte-chain-feed-design.md (section 3 for the
contract names and descriptions, 5.8 for the fill, 5.11 for the session).
"""
import ast
import json
import pathlib
import subprocess
import sys
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from shared import futures_chain
from shared import futures_chain_config as cfg
from shared import service_limits
from shared.futures_chain import (
    STATUS_KEY,
    STREAM,
    ContractTerms,
    build_plan,
    candidate_series,
    chain_key,
    date_text,
    display_symbol,
    feed_symbols,
    fill_rows,
    frozen_key,
    in_session,
    instrument_for,
    is_feed_symbol,
    parse_description,
    session_bounds,
    session_expiry,
)
from shared.symbols import clean_symbol

REPO = pathlib.Path(__file__).resolve().parents[2]
CT = ZoneInfo("America/Chicago")


def ct(*args):
    """An ``America/Chicago`` aware datetime: ``ct(2026, 11, 2, 15, 0)``."""
    return datetime(*args, tzinfo=CT)


def imports_of(module):
    """The module names a source file imports. ``from shared import x`` counts as
    ``shared.x``, so the pin names the sibling it really takes."""
    tree = ast.parse(pathlib.Path(module.__file__).read_text(encoding="utf-8"))
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            if node.module == "shared":
                mods |= {f"shared.{a.name}" for a in node.names}
            else:
                mods.add(node.module)
    return mods


# ── the names ───────────────────────────────────────────────────────────────

def test_the_keys_and_the_stream():
    assert STREAM == "cmd:futures_chain"
    assert chain_key("FOP.ES") == "cache:options:futures_chain:FOP.ES"
    assert frozen_key("FOP.ES") == "cache:options:futures_chain:FOP.ES:frozen"
    assert STATUS_KEY == "cache:options:futures_chain_status"


def test_the_stream_is_not_the_option_commands():
    """cmd:options guards paper books; each command here carries a document."""
    assert STREAM != "cmd:options"


def test_is_feed_symbol_is_only_the_feed():
    assert is_feed_symbol("FOP.ES") and not is_feed_symbol("ES") and not is_feed_symbol("$SPX")
    assert is_feed_symbol("fop.nq") and is_feed_symbol(" FOP.NQ ")
    assert not is_feed_symbol(None) and not is_feed_symbol("") and not is_feed_symbol(5)


def test_feed_symbols_pass_the_ticker_rule_unchanged():
    assert feed_symbols() == ["FOP.ES", "FOP.NQ"]
    assert all(clean_symbol(s) == s for s in feed_symbols())


def test_display_symbol():
    assert display_symbol("FOP.ES") == "/ES" and display_symbol("FOP.NQ") == "/NQ"
    assert display_symbol("SPY") == "SPY" and display_symbol("$SPX") == "$SPX"
    assert display_symbol("ES") == "ES"


def test_instrument_for():
    assert instrument_for("FOP.ES") == "ES" and instrument_for("FOP.NQ") == "NQ"
    assert instrument_for("ES") is None and instrument_for("SPY") is None
    assert instrument_for(None) is None


def test_a_symbol_the_ticker_rule_refuses_is_not_a_feed_symbol(monkeypatch):
    """The symbol becomes part of a Redis key name, so a configured one the
    app's one ticker rule would refuse never joins the feed."""
    monkeypatch.setattr(cfg, "instruments", lambda: {
        "ES": {"symbol": "FOP.ES", "label": "/ES"},
        "XX": {"symbol": "fop.x y", "label": "/XX"}})
    assert feed_symbols() == ["FOP.ES"]
    assert not is_feed_symbol("fop.x y")
    assert instrument_for("FOP.ES") == "ES"


# ── a contract's own description ────────────────────────────────────────────

def test_parse_description_reads_the_contracts_own_terms():
    t = parse_description("/ESZ26:XCME 1/50 2 NOV 26 (Monday) (Wk1) /E1AX26:XCME 7600 CALL")
    assert t == ContractTerms("/ESZ26:XCME", 50, date(2026, 11, 2), False, "E1AX26")


def test_parse_description_marks_morning_settlement():
    t = parse_description("/ESZ26:XCME 1/50 18 DEC 26 (AM Settled) /ESZ26:XCME 7600 CALL")
    assert t.am_settled is True and t.expiry == date(2026, 12, 18)


def test_parse_description_reads_month_end_and_nq():
    assert parse_description("/ESZ26:XCME 1/50 30 NOV 26 (EOM) /EWX26:XCME 7600 CALL").series == "EWX26"
    assert parse_description("/NQZ26:XCME 1/20 2 NOV 26 (Monday) (Wk1) /Q1AX26:XCME 28500 CALL").multiplier == 20


def test_parse_description_reads_a_put_and_a_decimal_strike():
    t = parse_description("/NQZ26:XCME 1/20 4 NOV 26 (Wednesday) (Wk1) /Q1CX26:XCME 28500.5 PUT")
    assert t == ContractTerms("/NQZ26:XCME", 20, date(2026, 11, 4), False, "Q1CX26")


def test_parse_description_tolerates_case_and_spacing():
    t = parse_description("  /ESZ26:XCME 1/50 2 nov 26 (monday) /E1AX26:XCME 7600 call  ")
    assert t is not None and t.expiry == date(2026, 11, 2)


@pytest.mark.parametrize("junk", ["N/A", "", None, 7600, b"x", [],
                                  "/ESZ26:XCME 1/50 99 NOV 26 /E1AX26:XCME 7600 CALL"])
def test_parse_description_refuses_what_it_cannot_read(junk):
    assert parse_description(junk) is None


@pytest.mark.parametrize("text", [
    "/ESZ26:XCME 1/50 2 NOV /E1AX26:XCME 7600 CALL",                 # no year
    "/ESZ26:XCME 1/50 2 XXX 26 /E1AX26:XCME 7600 CALL",              # no such month
    "/ESZ26:XCME 2 NOV 26 /E1AX26:XCME 7600 CALL",                   # no multiplier
    "/ESZ26:XCME 1/50 30 FEB 26 /E1AX26:XCME 7600 CALL",             # no such day
    "/ESZ26:XCME 1/50 2 NOV 26 /E1AX26:XCME CALL",                   # no strike
])
def test_parse_description_refuses_a_description_missing_a_term(text):
    assert parse_description(text) is None


# ── which series might hold a date ──────────────────────────────────────────

def test_candidates_for_a_monday_include_the_month_end_name():
    names = candidate_series("ES", date(2026, 11, 30))
    assert "EWX26" in names and "E5AX26" in names


def test_candidates_for_a_friday():
    names = candidate_series("NQ", date(2026, 11, 20))
    assert "QN3X26" in names and "QNEX26" in names and "NQX26" in names


def test_candidates_for_each_weekday_name_five_weeks_of_that_weekdays_root():
    assert [f"E{w}AX26" for w in range(1, 6)] == [
        n for n in candidate_series("ES", date(2026, 11, 2)) if n.startswith("E") and n[2] == "A"]
    assert {f"Q{w}DX26" for w in range(1, 6)} <= set(candidate_series("NQ", date(2026, 11, 5)))
    assert {f"E{w}CX26" for w in range(1, 6)} <= set(candidate_series("ES", date(2026, 11, 4)))
    assert {f"E{w}BX26" for w in range(1, 6)} <= set(candidate_series("ES", date(2026, 11, 3)))


def test_a_weekday_does_not_ask_for_another_weekdays_root():
    names = candidate_series("ES", date(2026, 11, 3))          # Tuesday
    assert not any(n.startswith("E1A") or n.startswith("EW1") for n in names)


def test_a_friday_names_five_weeks_and_nothing_of_the_weekday_roots():
    names = candidate_series("ES", date(2026, 11, 6))
    assert {f"EW{w}X26" for w in range(1, 6)} <= set(names)
    assert not any(n.startswith("E1") for n in names)


def test_the_quarterly_names_follow_the_expiry_month_and_the_next_quarter():
    names = candidate_series("ES", date(2026, 11, 20))
    assert "ESX26" in names and "ESZ26" in names
    # a December expiry's next quarter is in the next year
    dec = candidate_series("NQ", date(2026, 12, 18))
    assert "NQZ26" in dec and "NQH27" in dec


def test_the_month_code_and_year_are_the_expiry_dates():
    names = candidate_series("ES", date(2027, 1, 4))
    assert "E1AF27" in names and "EWF27" in names
    assert not any(n.endswith("X26") for n in names)


def test_candidates_have_no_duplicates():
    for d in (date(2026, 11, 2), date(2026, 12, 18), date(2026, 11, 30)):
        names = candidate_series("ES", d)
        assert len(names) == len(set(names))


def test_an_unknown_instrument_has_no_candidates():
    assert candidate_series("CL", date(2026, 11, 2)) == []
    assert candidate_series(None, date(2026, 11, 2)) == []


def test_the_roots_are_read_from_config(monkeypatch):
    real = cfg.instruments()
    real["ES"]["eom_root"] = "ZZ"
    monkeypatch.setattr(cfg, "instruments", lambda: real)
    assert "ZZX26" in candidate_series("ES", date(2026, 11, 30))


# ── the futures session ─────────────────────────────────────────────────────

def test_a_sunday_evening_capture_belongs_to_monday():
    assert session_expiry(ct(2026, 11, 1, 18, 0)) == date(2026, 11, 2)


def test_after_the_close_the_next_session_is_named():
    assert session_expiry(ct(2026, 11, 2, 15, 30)) == date(2026, 11, 3)


def test_friday_after_the_close_rolls_past_the_weekend():
    assert session_expiry(ct(2026, 11, 6, 15, 30)) == date(2026, 11, 9)


def test_through_the_day_the_session_is_the_days_own():
    assert session_expiry(ct(2026, 11, 3, 2, 0)) == date(2026, 11, 3)
    assert session_expiry(ct(2026, 11, 3, 14, 59)) == date(2026, 11, 3)


def test_a_saturday_names_monday():
    assert session_expiry(ct(2026, 11, 7, 12, 0)) == date(2026, 11, 9)


def test_a_naive_datetime_means_central_time():
    assert session_expiry(datetime(2026, 11, 1, 18, 0)) == date(2026, 11, 2)
    assert in_session(datetime(2026, 11, 2, 14, 59)) is True


def test_another_zone_is_converted_to_central():
    ny = ZoneInfo("America/New_York")
    # 19:00 in New York is 18:00 in Chicago
    assert session_expiry(datetime(2026, 11, 1, 19, 0, tzinfo=ny)) == date(2026, 11, 2)
    assert in_session(datetime(2026, 11, 2, 15, 30, tzinfo=ny)) is True    # 14:30 CT
    # 16:00 New York is 15:00 Chicago, the daily gap
    assert in_session(datetime(2026, 11, 2, 16, 0, tzinfo=ny)) is False


@pytest.mark.parametrize("when,expected", [
    ((2026, 11, 2, 14, 59), True),   # Monday, before expiry
    ((2026, 11, 2, 15, 0), False),   # the daily gap
    ((2026, 11, 2, 17, 0), True),    # opens for Tuesday
    ((2026, 11, 6, 18, 0), False),   # Friday evening
    ((2026, 11, 7, 12, 0), False),   # Saturday
    ((2026, 11, 8, 16, 59), False),  # Sunday, before the open
    ((2026, 11, 8, 17, 0), True),
])
def test_in_session(when, expected):
    assert in_session(ct(*when)) is expected


def test_session_bounds_span_two_calendar_dates():
    lo, hi = session_bounds(date(2026, 11, 3))
    assert lo == ct(2026, 11, 2, 17, 0).timestamp() and hi == ct(2026, 11, 3, 15, 0).timestamp()


def test_a_mondays_session_opens_on_the_sunday():
    lo, hi = session_bounds(date(2026, 11, 2))
    assert lo == ct(2026, 11, 1, 17, 0).timestamp() and hi == ct(2026, 11, 2, 15, 0).timestamp()


def test_the_session_bounds_follow_the_clock_across_a_clock_change():
    # US clocks went back on 2026-11-01: Sunday 17:00 CST is 23:00 UTC.
    lo, _hi = session_bounds(date(2026, 11, 2))
    assert datetime.fromtimestamp(lo, ZoneInfo("UTC")).hour == 23


def test_the_session_times_are_read_from_config(monkeypatch):
    monkeypatch.setattr(cfg, "session", lambda: {"open_ct": "18:00", "close_ct": "14:30"})
    lo, hi = session_bounds(date(2026, 11, 3))
    assert lo == ct(2026, 11, 2, 18, 0).timestamp() and hi == ct(2026, 11, 3, 14, 30).timestamp()
    assert in_session(ct(2026, 11, 3, 14, 45)) is False


# ── the fill ────────────────────────────────────────────────────────────────

def test_fill_repeats_a_row_up_to_one_interval_and_leaves_a_longer_outage_a_gap():
    rows = [(0, "a"), (300, "b"), (1500, "c")]
    out = fill_rows(rows, interval_sec=300, step_sec=60)
    assert [r[0] for r in out[:5]] == [0, 60, 120, 180, 240]
    assert [r[1] for r in out[:5]] == ["a"] * 5
    assert 600 not in [r[0] for r in out]      # the outage after "b" stays empty
    assert out[-1] == (1500, "c")              # the newest row is not drawn into the future


def test_fill_keeps_the_rest_of_each_row_and_does_not_mutate_input():
    rows = [(0, 1, 2, 3), (300, 4, 5, 6)]
    assert fill_rows(rows, 300, 60)[1] == (60, 1, 2, 3) and len(rows) == 2


def test_fill_does_not_repeat_a_row_past_the_next_one():
    out = fill_rows([(0, "a"), (100, "b")], interval_sec=300, step_sec=60)
    assert [r[0] for r in out] == [0, 60, 100]


def test_fill_of_a_list_row_stays_a_list():
    out = fill_rows([[0, "a"], [300, "b"]], 300, 60)
    assert out[1] == [60, "a"] and isinstance(out[1], list)


def test_fill_of_nothing_and_of_one_row():
    assert fill_rows([], 300, 60) == []
    assert fill_rows([(7, "x")], 300, 60) == [(7, "x")]


def test_fill_orders_rows_by_time():
    out = fill_rows([(300, "b"), (0, "a")], 300, 100)
    assert [r[0] for r in out] == [0, 100, 200, 300]


@pytest.mark.parametrize("step", [0, -60])
def test_a_step_that_cannot_advance_returns_the_rows_as_they_are(step):
    rows = [(0, "a"), (300, "b")]
    assert fill_rows(rows, 300, step) == rows


# ── text and the plan ───────────────────────────────────────────────────────

def test_date_text_is_the_form_thinkorswim_prints():
    assert date_text(date(2026, 11, 2)) == "2 NOV 26"
    assert date_text(date(2026, 12, 18)) == "18 DEC 26"
    assert date_text(date(2027, 1, 4)) == "4 JAN 27"


def test_plan_names_the_session_the_series_and_the_date_text():
    p = build_plan(ct(2026, 11, 1, 18, 0))
    assert p["expiry"] == "2026-11-02" and p["date_text"] == "2 NOV 26"
    assert p["interval_sec"] == 300 and p["in_session"] is True
    assert "E1AX26" in p["instruments"]["ES"]["candidates"]
    assert p["instruments"]["NQ"]["strike_step"] > 0
    assert p["valid_until"] == ct(2026, 11, 2, 15, 0).timestamp()


def test_plan_carries_what_the_capture_needs_per_instrument():
    p = build_plan(ct(2026, 11, 1, 18, 0))
    es = p["instruments"]["ES"]
    assert es["symbol"] == "FOP.ES" and es["multiplier"] == 50 and es["exchange"] == "XCME"
    assert es["strike_width_pct"] == 5 and es["parity_limit_pts"] == 15
    assert p["instruments"]["NQ"]["multiplier"] == 20
    assert p["min_fill_fraction"] == 0.8 and p["max_snapshot_kb"] == 512


def test_plan_carries_the_capture_machines_tunables(monkeypatch):
    """They reach the PC through the plan, so a change in Settings -> Configuration
    needs no edit of the script there. Read from config, not fixed here."""
    keys = ("min_read_fraction", "fill_settle_sec", "fill_wait_max_sec",
            "probe_round_steps", "probe_reach", "recentre_share", "ssh_timeout_sec",
            "no_plan_retry_sec", "plan_retry_max_sec", "hang_limit_sec")
    p = build_plan(ct(2026, 11, 1, 18, 0))
    for key in keys:
        assert p[key] == cfg.DEFAULTS["capture"][key]
    changed = {key: cfg.DEFAULTS["capture"][key] * 2 for key in keys}
    monkeypatch.setattr(cfg, "capture", lambda: {**cfg.DEFAULTS["capture"], **changed})
    p = build_plan(ct(2026, 11, 1, 18, 0))
    for key in keys:
        assert p[key] == changed[key]


def test_plan_carries_the_exit_after_a_hang(monkeypatch):
    p = build_plan(ct(2026, 11, 1, 18, 0))
    assert p["hang_exit_after_sec"] == 240
    monkeypatch.setattr(cfg, "capture",
                        lambda: {**cfg.DEFAULTS["capture"], "hang_exit_after_sec": 900})
    assert build_plan(ct(2026, 11, 1, 18, 0))["hang_exit_after_sec"] == 900


def test_the_stores_busy_wait_stays_on_the_server():
    assert "store_busy_wait_sec" not in build_plan(ct(2026, 11, 1, 18, 0))


def test_plan_is_plain_json():
    """It crosses a machine boundary: no dates, tuples or NamedTuples."""
    p = build_plan(ct(2026, 11, 1, 18, 0))
    assert json.loads(json.dumps(p)) == p


def test_plan_in_the_gap_names_the_next_session():
    p = build_plan(ct(2026, 11, 2, 16, 0))
    assert p["in_session"] is False and p["expiry"] == "2026-11-03"
    assert p["valid_until"] == ct(2026, 11, 3, 15, 0).timestamp()


def test_plan_follows_the_configured_interval(monkeypatch):
    monkeypatch.setattr(cfg, "capture", lambda: {**cfg.DEFAULTS["capture"], "interval_sec": 120})
    assert build_plan(ct(2026, 11, 1, 18, 0))["interval_sec"] == 120


# ── the pins ────────────────────────────────────────────────────────────────

def test_module_import_set_is_pinned():   # same AST shape as test_public_gamma.py
    assert imports_of(futures_chain) <= {"datetime", "re", "typing", "zoneinfo",
        "shared.futures_chain_config", "shared.symbols"}


PROBE = r"""
import sys
sys.path.insert(0, r"%s")
before = set(sys.modules)
import shared.futures_chain
new = set(sys.modules) - before
print("NEW:" + ",".join(sorted(m for m in new
                              if m.split(".")[0] not in sys.stdlib_module_names
                              and not m.startswith("_sysconfigdata"))))
""" % REPO


def test_importing_it_loads_only_the_config_loader_the_paths_and_the_symbol_rule():
    """Tier 1 imports this module, so what it pulls in is pinned in a FRESH
    interpreter, where a transitive import cannot hide behind an earlier test's."""
    r = subprocess.run([sys.executable, "-c", PROBE], cwd=REPO,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    new = set(filter(None, r.stdout.strip()[len("NEW:"):].split(",")))
    allowed = {"repo_paths", "shared", "shared.config_toml", "shared.futures_chain",
               "shared.futures_chain_config", "shared.symbols"}
    stray = {m for m in new if m not in allowed and m.split(".")[0] != "tzdata"}
    assert not stray, sorted(stray)
    assert {"shared.futures_chain", "shared.symbols"} <= new


def test_stream_cap_is_set_before_anything_writes_it():
    assert service_limits.stream_keep("cmd:futures_chain") == 20


def test_the_stream_cap_has_a_ceiling_of_its_own():
    assert service_limits.stream_keep_bounds("cmd:futures_chain") == (10, 200)
    assert service_limits.STREAM_KEEP_CEILINGS["cmd:futures_chain"] == 200


def test_the_stream_cap_is_in_the_shipped_services_file():
    import tomllib

    from repo_paths import SERVICES_TOML
    shipped = tomllib.loads(SERVICES_TOML.read_text(encoding="utf-8"))
    assert shipped["stream_keep"]["cmd:futures_chain"] == 20
    assert STREAM in shipped["stream_keep"]


"""The public Strategy Finder page: its pure helpers, and what it may send."""
import ast
import datetime as dt
import pathlib

from shared import public_scan as ps

from pages.options import finder_live as fl
from pages.options import finder_view as fv

UTC = dt.timezone.utc
T0 = dt.datetime(2026, 9, 21, 15, 0, tzinfo=UTC)          # 10:00 CT
SRC = pathlib.Path(fl.__file__).read_text(encoding="utf-8")


def _status(**last):
    return {"date": "2026-09-21", "scans_today": 3, "scans_left": 197,
            "daily_budget": 200, "busy": None,
            "window": {"start": "08:40", "end": "15:00", "tz": "CT"},
            "last": last}


# ── what the page says ──────────────────────────────────────────────────────

def test_the_intro_states_the_fixed_filters_in_words():
    text = fl.intro_text(ps.scan_pin(), {"start": "08:40", "end": "15:00"})
    assert "90 days" in text
    assert "10 and 20 delta" in text
    assert "10%" in text
    assert "08:40" in text and "15:00" in text


def test_the_intro_follows_the_config_not_a_literal():
    pin = {**ps.scan_pin(), "dte_max": 45, "min_cr_fraction": 0.15}
    text = fl.intro_text(pin, {"start": "09:00", "end": "14:00"})
    assert "45 days" in text and "15%" in text and "09:00" in text


def test_the_budget_line():
    assert fl.budget_text(_status()) == "197 public scans left today"
    assert fl.budget_text({**_status(), "scans_left": 1}) == "1 public scan left today"
    assert fl.budget_text({}) is None               # unread is not zero


def test_the_scanned_stamp_is_central_time():
    payload = {"scanned_at": "2026-09-21T10:42:07-05:00"}
    assert fl.scanned_at_text(payload) == "Scanned 10:42 CT"
    assert fl.scanned_at_text({}) is None


def test_the_answer_line_words_every_outcome():
    for code in ps.OUTCOMES:
        assert fl.answer_line(code, _status()).strip()


def test_the_answer_line_is_the_outcome_and_the_budget_and_nothing_twice():
    """The scan time lives in the summary strip beside its result; repeating it
    here printed "Showing ..." twice for a cached answer (seen in the harness)."""
    line = fl.answer_line("cached", _status())
    assert line == f"{ps.OUTCOME_TEXT['cached']} 197 public scans left today."
    assert line.count("Showing") == 1


# ── following one request ───────────────────────────────────────────────────

def test_a_request_is_done_when_its_own_symbol_records_an_outcome_after_it():
    st = _status(SPY={"outcome": "scanned", "at": "2026-09-21T10:00:05-05:00"})
    assert fl.request_state(st, "SPY", T0) == ("done", "scanned")


def test_an_outcome_from_before_the_request_is_not_its_answer():
    st = _status(SPY={"outcome": "scanned", "at": "2026-09-21T09:30:00-05:00"})
    assert fl.request_state(st, "SPY", T0) == ("queued", None)


def test_another_visitors_symbol_is_never_read_as_this_answer():
    st = _status(QQQ={"outcome": "scanned", "at": "2026-09-21T10:00:05-05:00"})
    assert fl.request_state(st, "SPY", T0) == ("queued", None)


def test_the_running_scan_is_named_only_when_it_is_this_symbol():
    st = {**_status(), "busy": {"symbol": "SPY", "since": "x"}}
    assert fl.request_state(st, "SPY", T0) == ("scanning", None)
    st = {**_status(), "busy": {"symbol": "QQQ", "since": "x"}}
    assert fl.request_state(st, "SPY", T0) == ("queued", None)


def test_no_status_yet_is_queued():
    assert fl.request_state(None, "SPY", T0) == ("queued", None)


def test_the_waiting_line_never_names_another_visitors_symbol():
    st = {**_status(), "busy": {"symbol": "QQQ", "since": "x"}}
    line = fl.waiting_text("SPY", fl.request_state(st, "SPY", T0)[0], 7)
    assert "QQQ" not in line and "SPY" in line and "7 s" in line


def test_the_page_never_reads_the_whole_last_map():
    """It is a list of every symbol anyone searched. The page reads its own
    symbol's entry and nothing else."""
    tree = ast.parse(SRC)
    for node in ast.walk(tree):
        if isinstance(node, ast.For):
            assert "last" not in ast.unparse(node.iter), ast.unparse(node.iter)
        if isinstance(node, ast.Call) and getattr(node.func, "attr", None) in (
                "items", "keys", "values"):
            assert "last" not in ast.unparse(node.func.value)


# ── the table ───────────────────────────────────────────────────────────────

def test_the_public_table_has_no_checks_and_no_actions():
    names = [c["name"] for c in fl.public_columns()]
    assert "checks" not in names and "actions" not in names
    private = [c["name"] for c in fv.finder_columns()]
    assert names == [n for n in private if n not in ("checks", "actions")]


# ── what the page may send ──────────────────────────────────────────────────

def test_its_only_write_is_the_public_scan_request():
    tree = ast.parse(SRC)
    calls = {ast.unparse(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    assert "bus_client.request_public_scan" in calls
    assert not {c for c in calls if c.endswith((".request", "enqueue_command",
                                                "cache_set", "publish"))}


def test_it_never_draws_an_owner_action():
    for owner_only in ("handoff", "send_to_paper", "send_signal_to_calculator",
                       "detail.render", "checks_feed", "add_strategy_row_actions"):
        assert owner_only not in SRC, owner_only


def test_every_scan_passes_the_visitor_limit_first():
    """The limit must be checked BEFORE the request, in the one function that
    makes it."""
    tree = ast.parse(SRC)
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
              and n.name == "_request")
    src = ast.unparse(fn)
    assert src.index("LIMITER.allow(") < src.index("request_public_scan(")

"""Tests for tools/check_env.py pure helpers.

Live network/process calls are not unit-tested; the parsing, freshness, and
formatting helpers are.
"""
import datetime as dt
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # tools/
import check_env  # noqa: E402


def test_classify_proxy_health_ok_with_token():
    state, detail = check_env.classify_proxy_health(
        {"status": "ok", "has_token": True, "token_expired": False})
    assert state == check_env.UP
    assert "token" in detail.lower()


def test_classify_proxy_health_no_token():
    state, _ = check_env.classify_proxy_health(
        {"status": "ok", "has_token": False})
    assert state == check_env.NO_TOKEN


def test_classify_proxy_health_expired_token():
    state, detail = check_env.classify_proxy_health(
        {"status": "ok", "has_token": True, "token_expired": True})
    assert state == check_env.NO_TOKEN
    assert "expired" in detail.lower()


def test_freshness_state_fresh(tmp_path):
    f = tmp_path / "bridge.json"
    f.write_text("{}")
    # mtime is now → fresh
    state = check_env.freshness_state(f, max_age_min=10, now_ts=f.stat().st_mtime)
    assert state == check_env.UP


def test_freshness_state_stale(tmp_path):
    f = tmp_path / "bridge.json"
    f.write_text("{}")
    older = f.stat().st_mtime
    state = check_env.freshness_state(f, max_age_min=10, now_ts=older + 11 * 60)
    assert state == check_env.STALE


def test_freshness_state_missing(tmp_path):
    state = check_env.freshness_state(tmp_path / "nope.json", max_age_min=10,
                                      now_ts=0)
    assert state == check_env.DOWN


def test_cmdline_matches_script():
    assert check_env.cmdline_matches(
        ["python", "dashboard.py"], "dashboard.py")
    assert check_env.cmdline_matches(
        ["C:\\py\\python.exe", "D:\\x\\options-scanner\\dashboard.py"],
        "dashboard.py")
    assert not check_env.cmdline_matches(
        ["python", "other.py"], "dashboard.py")
    assert not check_env.cmdline_matches([], "dashboard.py")


def test_format_table_contains_states():
    rows = [
        check_env.CheckRow("schwab-proxy", check_env.UP, "token ok"),
        check_env.CheckRow("trade-analyzer", check_env.DOWN, "not running"),
    ]
    out = check_env.format_table(rows)
    assert "schwab-proxy" in out
    assert "UP" in out
    assert "DOWN" in out


def test_exit_code_zero_when_required_up():
    rows = [
        check_env.CheckRow("schwab-proxy", check_env.UP, "", required=True),
        check_env.CheckRow("trade-analyzer", check_env.DOWN, "", required=False),
    ]
    assert check_env.compute_exit_code(rows) == 0


def test_exit_code_nonzero_when_required_down():
    rows = [
        check_env.CheckRow("schwab-proxy", check_env.DOWN, "", required=True),
    ]
    assert check_env.compute_exit_code(rows) != 0


def test_exit_code_stale_required_is_failure():
    rows = [
        check_env.CheckRow("sentiment-dashboard", check_env.STALE, "",
                           required=True),
    ]
    assert check_env.compute_exit_code(rows) != 0


def test_exit_code_idle_required_is_healthy():
    rows = [
        check_env.CheckRow("sentiment-dashboard", check_env.IDLE, "",
                           required=True),
    ]
    assert check_env.compute_exit_code(rows) == 0


# ── market awareness ────────────────────────────────────────────────

def test_should_be_publishing_weekend_is_false():
    # 2026-05-31 is a Sunday
    assert check_env.should_be_publishing(dt.datetime(2026, 5, 31, 10, 0)) is False


def test_should_be_publishing_weekday_market_hours_true():
    # 2026-06-01 is a Monday, 10:00 local (CT) is within 08:00-16:00
    assert check_env.should_be_publishing(dt.datetime(2026, 6, 1, 10, 0)) is True


def test_should_be_publishing_weekday_after_hours_false():
    assert check_env.should_be_publishing(dt.datetime(2026, 6, 1, 20, 0)) is False


def test_should_be_publishing_holiday_is_false():
    # 2026-07-03 is the observed Independence Day (July 4 falls on Saturday)
    assert check_env.should_be_publishing(dt.datetime(2026, 7, 3, 10, 0)) is False


def test_classify_sentiment_not_running():
    state, _ = check_env.classify_sentiment(
        alive=False, bridge_exists=True, is_stale=True, publishing=True)
    assert state == check_env.DOWN


def test_classify_sentiment_idle_off_hours():
    # alive, bridge old, but market closed → IDLE (not a fault)
    state, detail = check_env.classify_sentiment(
        alive=True, bridge_exists=True, is_stale=True, publishing=False)
    assert state == check_env.IDLE
    assert "idle" in detail.lower()


def test_classify_sentiment_stale_during_market_hours():
    state, _ = check_env.classify_sentiment(
        alive=True, bridge_exists=True, is_stale=True, publishing=True)
    assert state == check_env.STALE


def test_classify_sentiment_fresh_during_market_hours():
    state, _ = check_env.classify_sentiment(
        alive=True, bridge_exists=True, is_stale=False, publishing=True)
    assert state == check_env.UP

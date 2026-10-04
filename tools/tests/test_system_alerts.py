"""The two scripts systemd runs to tell a person something is wrong.

Audit AR-03 (2026-10-03): with no browser tab open, nobody was told that a
service had crash-looped until systemd gave up, that the nightly backup had
failed, or that the Schwab refresh token was about to lapse.

* ``tools/notify_failure.py <unit>`` - run by ``OnFailure=`` on every unit;
* ``tools/token_watch.py`` - run once a day by a timer.

Both are tested as pure decisions plus an injected sender; neither test sends
anything or reads a real proxy.
"""
import datetime as dt
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import notify_failure  # noqa: E402
import token_watch  # noqa: E402

NOW = dt.datetime(2026, 10, 5, 9, 0, tzinfo=dt.timezone.utc)


# ── the failure notifier ─────────────────────────────────────────────────────

def test_the_message_names_the_unit_and_how_to_read_its_log():
    title, lines = notify_failure.message(
        "trading-prod-backup.service", {"Result": "exit-code", "ExecMainStatus": "1"},
        host="vps2")
    assert title == "trading-prod-backup failed"
    text = "\n".join(lines)
    assert "exit-code" in text and "status 1" in text
    assert "journalctl --user -u trading-prod-backup.service" in text
    assert "vps2" in text


def test_a_service_that_gave_up_restarting_says_so():
    _, lines = notify_failure.message(
        "trading-prod-options_svc.service",
        {"Result": "start-limit-hit", "ExecMainStatus": "1"}, host="vps2")
    assert "stopped trying to restart" in "\n".join(lines)


def test_the_message_never_carries_log_text():
    """Log lines can hold a credential (a connection error prints its URL), so
    the alert points at the journal instead of quoting it."""
    _, lines = notify_failure.message("u.service", {"Result": "exit-code"}, host="h")
    assert all("Traceback" not in line for line in lines)
    assert len(lines) <= 6


def test_one_alert_per_unit_per_repeat_window(tmp_path):
    sent = []
    args = dict(state_dir=tmp_path, repeat_hours=6, show=lambda unit: {},
                send=lambda title, lines: sent.append(title) or ["telegram"], host="h")
    assert notify_failure.run("u.service", now=NOW, **args) == "sent"
    assert notify_failure.run("u.service", now=NOW + dt.timedelta(hours=1), **args) == "throttled"
    assert notify_failure.run("u.service", now=NOW + dt.timedelta(hours=7), **args) == "sent"
    assert len(sent) == 2


def test_a_different_unit_is_not_throttled_by_the_first(tmp_path):
    sent = []
    args = dict(state_dir=tmp_path, repeat_hours=6, show=lambda unit: {}, host="h",
                send=lambda title, lines: sent.append(title) or ["telegram"], now=NOW)
    notify_failure.run("a.service", **args)
    assert notify_failure.run("b.service", **args) == "sent"


def test_an_alert_that_reached_no_channel_is_not_counted_as_sent(tmp_path):
    """Notifications off, or no destination: the next failure must still try."""
    args = dict(state_dir=tmp_path, repeat_hours=6, show=lambda unit: {}, host="h",
                send=lambda title, lines: [], now=NOW)
    assert notify_failure.run("u.service", **args) == "not_sent"
    assert notify_failure.run("u.service", **args) == "not_sent"


def test_a_unit_name_cannot_escape_the_state_directory(tmp_path):
    sent = []
    notify_failure.run("../../etc/x.service", state_dir=tmp_path, repeat_hours=6,
                       show=lambda unit: {}, host="h", now=NOW,
                       send=lambda title, lines: sent.append(title) or ["telegram"])
    written = [p for p in tmp_path.rglob("*") if p.is_file()]
    assert len(written) == 1 and written[0].parent == tmp_path


def test_a_failing_sender_never_raises(tmp_path):
    def boom(title, lines):
        raise RuntimeError("down")

    assert notify_failure.run("u.service", state_dir=tmp_path, repeat_hours=6,
                              show=lambda unit: {}, host="h", now=NOW,
                              send=boom) == "not_sent"


def test_the_notifier_does_not_alert_about_itself():
    assert notify_failure.run("trading-prod-notify-failure@x.service",
                              send=lambda *a: pytest.fail("alerted about itself"),
                              show=lambda unit: {}, host="h", now=NOW) == "ignored"


# ── the token watch ──────────────────────────────────────────────────────────

def _health(**over):
    base = {"status": "ok", "has_token": True, "refresh_token_expired": False,
            "refresh_token_rejected": False, "refresh_token_hours_left": 120.0}
    base.update(over)
    return base


def test_a_token_with_days_left_is_quiet():
    assert token_watch.assess(_health(), warn_hours=48) is None


def test_a_token_inside_the_warning_window_is_reported_with_the_time_left():
    title, lines = token_watch.assess(_health(refresh_token_hours_left=30.4), warn_hours=48)
    assert "30 hours" in title
    assert "/auth" in "\n".join(lines)


def test_the_window_is_inclusive_and_configurable():
    assert token_watch.assess(_health(refresh_token_hours_left=48.0), warn_hours=48)
    assert token_watch.assess(_health(refresh_token_hours_left=48.1), warn_hours=48) is None
    assert token_watch.assess(_health(refresh_token_hours_left=60.0), warn_hours=72)


def test_an_expired_token_is_reported_as_expired_not_as_zero_hours():
    title, _ = token_watch.assess(
        _health(status="reauth_required", refresh_token_expired=True,
                refresh_token_hours_left=0.0), warn_hours=48)
    assert "expired" in title.lower()


def test_a_token_schwab_rejected_is_reported_whatever_the_clock_says():
    title, _ = token_watch.assess(
        _health(status="reauth_required", refresh_token_rejected=True,
                refresh_token_hours_left=100.0), warn_hours=48)
    assert "rejected" in title.lower()


def test_no_token_at_all_is_reported():
    title, _ = token_watch.assess({"status": "reauth_required", "has_token": False},
                                  warn_hours=48)
    assert "no schwab" in title.lower()


def test_a_proxy_that_does_not_answer_is_reported():
    title, _ = token_watch.assess(None, warn_hours=48)
    assert "proxy" in title.lower()


def test_an_unknown_time_left_is_not_read_as_plenty_of_time():
    """A proxy that predates the field, or a malformed value: say nothing can be
    read rather than staying quiet until the token lapses."""
    for bad in (None, "soon", float("nan")):
        got = token_watch.assess(_health(refresh_token_hours_left=bad), warn_hours=48)
        assert got is not None and "cannot be read" in got[0]


def test_run_sends_only_when_there_is_something_to_say():
    sent = []
    send = lambda title, lines: sent.append(title) or ["telegram"]   # noqa: E731
    assert token_watch.run(fetch=lambda: _health(), send=send, warn_hours=48) == "quiet"
    assert token_watch.run(fetch=lambda: _health(refresh_token_hours_left=10.0),
                           send=send, warn_hours=48) == "sent"
    assert len(sent) == 1


def test_run_never_raises_when_the_fetch_does():
    def boom():
        raise ConnectionError("refused")

    sent = []
    assert token_watch.run(fetch=boom, warn_hours=48,
                           send=lambda t, l: sent.append(t) or ["telegram"]) == "sent"
    assert "proxy" in sent[0].lower()

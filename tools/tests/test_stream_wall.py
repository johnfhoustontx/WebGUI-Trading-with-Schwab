"""The capture-and-encode script (``tools/stream_wall.sh``).

Tested at SOURCE level, the technique ``webgui/tests/test_proxy.py`` uses to
guard the Tier-1 import rule. The subject is a bash script that only ever runs
on the prod Linux host, driving Xvfb, Chrome and ffmpeg against a live YouTube
ingest -- there is nothing here a unit test could execute. What CAN be pinned is
every decision that would fail silently and only on camera: a holiday exiting
non-zero into a restart storm, a keyframe interval YouTube rejects, a stream key
committed to the repo, an orphaned Xvfb holding :99 against the next start.
"""
import pathlib
import re

SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "tools" / "stream_wall.sh"


def test_gates_on_the_market_calendar_not_a_holiday_literal():
    """ONE call does it: in_window("stream", now) already gates on
    is_trading_day internally, so it answers both "is the market open today"
    and "are we inside the broadcast window". A separate is_trading_day check
    would be a second source of the same truth."""
    text = SCRIPT.read_text()
    assert 'in_window("stream"' in text or "in_window('stream'" in text
    assert not re.search(r"\b(thanksgiving|christmas|juneteenth)\b", text, re.I)


def test_stands_down_with_exit_zero_not_a_failure():
    """A holiday is a NORMAL outcome. A non-zero exit would trip
    Restart=on-failure into a storm against StartLimitBurst, leaving the unit
    in failed state on every market holiday."""
    text = SCRIPT.read_text()
    assert "exit 0" in text


def test_never_embeds_the_stream_key():
    """The key lives in an operator-created 0600 EnvironmentFile and reaches
    the process as $RTMP_URL. A key in the repo is a leaked key."""
    text = SCRIPT.read_text()
    assert "RTMP_URL" in text
    assert not re.search(r"rtmp://[a-z0-9.]*/live2/\S", text)


def test_the_keyframe_interval_is_two_seconds_at_the_capture_framerate():
    """YouTube caps the interval at 4s and recommends 2s. -g is frames, so it
    must be framerate x 2 -- the two move together and a framerate change that
    leaves -g behind silently doubles the interval.

    Derived rather than hardcoded, which is the whole point: the literal version
    of this test asserted `-g 60` and would have had to be edited by the same
    person who forgot to edit `-g`.
    """
    text = SCRIPT.read_text()
    fps = int(re.search(r"-framerate (\d+)", text).group(1))
    assert f"-g {fps * 2}" in text
    assert f"-keyint_min {fps * 2}" in text
    # Kept from the literal version this replaces: without it x264 inserts its
    # own keyframes on scene cuts and the interval stops being strict.
    assert "-sc_threshold 0" in text


def test_capture_and_encode_do_not_drop_frames_under_load():
    """Without thread_queue_size, x11grab produced frames faster than the
    encoder consumed them -- measured 193% CPU with a 'Thread message queue
    blocking' warning, against 153% and clean with it."""
    assert "-thread_queue_size" in SCRIPT.read_text()


def test_sends_a_silent_audio_track():
    """YouTube ingest is unreliable with video-only. anullsrc costs nothing."""
    assert "anullsrc" in SCRIPT.read_text()


def test_cleans_up_its_children_on_exit():
    """Xvfb and Chrome outliving a stopped unit would hold the display and make
    the next start fail with a bind error nobody reads."""
    text = SCRIPT.read_text()
    assert "trap" in text and "set -euo pipefail" in text


def test_waits_for_the_webgui_to_ANSWER_not_merely_to_be_bound():
    """A dead accept loop stays bound and passes a TCP connect -- that is how a
    promote once left prod serving no UI at all. The repo's answer is
    tools/wait_http.py, which does a GET."""
    assert "wait_http.py" in SCRIPT.read_text()


def test_fails_when_the_browser_dies_instead_of_streaming_black():
    """ffmpeg grabs a framebuffer, not a browser. Without this the unit stays
    "running" while broadcasting a black screen."""
    text = SCRIPT.read_text()
    assert "kill -0" in text
    assert "WATCH_PID" in text


def test_the_watchdog_signals_the_encoder_not_the_shell():
    """`kill -TERM $$` is the obvious way to tear this down and it does not
    work: bash re-raises, so the process dies BY SIGTERM -- and systemd counts
    SIGHUP/SIGINT/SIGTERM/SIGPIPE as a clean exit, with Restart=on-failure
    explicitly excluding those four. The unit would go down and stay down.
    Killing the encoder instead makes `wait` return 143 as an exit CODE, which
    is a failure systemd will restart. Measured both ways before it was written.

    Comment lines are stripped first, deliberately: the script EXPLAINS this
    trap in prose, and a test that banned the words would force the script to
    stop naming the wrong answer it is steering away from.
    """
    code = "\n".join(ln for ln in SCRIPT.read_text().splitlines()
                     if not ln.lstrip().startswith("#"))
    assert "kill -TERM $$" not in code


def test_does_not_flood_the_journal_with_encoder_progress():
    """Two lines a second for nine hours a day. journalctl rate-limits per
    service and drops messages, so the spam suppresses the warnings you wanted."""
    text = SCRIPT.read_text()
    assert "-nostats" in text and "-loglevel" in text


def _watchdog_body():
    """The watchdog subshell, from `( while` to its closing `) &`.

    Extracted rather than searched for whole-file, because the regression that
    matters below is a line MOVING, not a line vanishing. A missing delimiter
    raises ValueError, which is a failure -- never a quiet pass.
    """
    text = SCRIPT.read_text()
    start = text.index("( while")
    return text[start:text.index("\n) &", start)]


def test_the_window_end_survives_a_mid_session_restart():
    """RuntimeMaxSec caps ONE invocation. A watchdog restart at 14:00 would
    otherwise get a fresh 7h20m and broadcast a frozen dashboard until 21:20.
    The end is an absolute instant computed at startup, not a duration."""
    text = SCRIPT.read_text()
    assert "END_EPOCH" in text
    assert "date +%s" in text


def test_the_end_check_runs_every_poll_not_only_at_startup():
    """The bug being fixed is precisely that a check which ran only at startup
    cannot stop a session that STARTED inside the window. Moving the comparison
    back out of the loop leaves END_EPOCH in the file and passes the test above,
    while restoring the whole defect -- so the assertion has to be about WHERE
    it lives, not whether it exists."""
    body = _watchdog_body()
    # Non-vacuity: prove we really extracted the loop and not an empty slice.
    assert "kill -0" in body, "watchdog block not found - has the loop moved?"
    assert "END_EPOCH" in body

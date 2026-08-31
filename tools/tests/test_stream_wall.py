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


def test_pins_a_two_second_keyframe_interval():
    """YouTube caps the keyframe interval at 4s and recommends 2s. At 30fps
    that is -g 60, and -sc_threshold 0 keeps it strict."""
    text = SCRIPT.read_text()
    assert "-g 60" in text and "-sc_threshold 0" in text


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

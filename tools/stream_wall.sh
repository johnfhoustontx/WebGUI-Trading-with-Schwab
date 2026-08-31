#!/usr/bin/env bash
# Capture /wall off a headless X display and encode it to YouTube.
#
# Xvfb gives Chrome a 1920x1080 framebuffer that no monitor is attached to;
# Chrome renders the rotating wall page into it; ffmpeg grabs that framebuffer
# and pushes it to the RTMP ingest. Nothing here decides WHAT is on screen --
# webgui/wall.py owns the rotation, the labels and the clock. This script only
# gets a browser in front of it and points an encoder at the result.
#
# It runs as the ExecStart of a systemd unit, which shapes three decisions:
# ffmpeg runs LAST and this shell does nothing but wait on it, so the encoder
# exiting is what ends this process and takes the unit down; the window gate
# exits ZERO, because a holiday is a normal outcome and not something to restart
# against; and the stream key arrives in the environment rather than living in
# the repo.
set -euo pipefail

# Resolve everything from this script's own location. A hardcoded /home/... path
# would work in prod and silently stream the wrong checkout from anywhere else.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PYTHON="$ROOT/.venv/bin/python"

DISPLAY_NUM=99
SCREEN=1920x1080

# Capture framerate, and the keyframe interval derived from it. YouTube caps the
# interval at 4s and recommends 2s, and -g counts FRAMES -- so it is FPS x 2 and
# the two must move together. Derived rather than typed: a framerate change that
# left a literal -g behind would silently double the interval, which is the
# failure that reads as slow player start-up and rebuffering rather than as a
# broken setting.
FPS=15
GOP=$((FPS * 2))

# ── Clean up our own children ────────────────────────────────────────────────
# Every child here is backgrounded, so without this they outlive a stopped unit
# and hold :99 -- the next start then dies with "Server is already active for
# display 99", a message that goes to the journal and nowhere a viewer looks.
# Each PID is unset until its process starts and `set -u` would abort the trap
# on the way past, so all four are defaulted; `|| true` keeps a stop that has
# already reaped one of them from failing the rest.
cleanup() {
  kill "${WATCH_PID:-}" 2>/dev/null || true
  kill "${FFMPEG_PID:-}" 2>/dev/null || true
  kill "${CHROME_PID:-}" 2>/dev/null || true
  kill "${XVFB_PID:-}" 2>/dev/null || true
  [ -n "${PROFILE_DIR:-}" ] && rm -rf "$PROFILE_DIR"
  return 0
}
trap cleanup EXIT

# ── Is this a broadcast day, and are we inside the window? ───────────────────
# ONE call answers both: in_window("stream", now) gates on is_trading_day
# internally, so weekends and every NYSE holiday fall out of it without this
# script carrying a second, driftable copy of the calendar.
#
# Standing down EXITS 0. The unit runs Restart=on-failure, so a non-zero exit on
# a market holiday would restart-storm into StartLimitBurst and leave the unit
# sitting in `failed` -- a state someone eventually has to clear by hand, caused
# by nothing worse than the exchange being shut. The timer starts us again
# tomorrow.
"$PYTHON" -c 'import datetime, sys
from shared.market_calendar import in_window, CT
sys.exit(0 if in_window("stream", datetime.datetime.now(CT)) else 42)' || {
  echo "outside the stream window (weekend, holiday, or off-hours) - standing down"
  exit 0; }

# ── The stream key ───────────────────────────────────────────────────────────
# It never lives in the repo. The operator writes it into a 0600 EnvironmentFile
# that the unit loads, and it reaches us as $RTMP_URL. Failing here NAMES that
# file: the alternative is ffmpeg happily encoding nine hours into nowhere.
: "${RTMP_URL:?RTMP_URL is not set - see /etc/neuralstrike-stream/env}"

# ── Wait for the page to actually exist ──────────────────────────────────────
# An HTTP GET, not a TCP connect: a dead accept loop stays bound and passes a
# connect test, which is how a promote once reported success and left prod
# serving no UI at all. tools/wait_http.py treats ANY HTTP status as alive.
# The third value is the stream window's END as an absolute Unix timestamp,
# fixed HERE at startup -- see the watchdog for why it has to be an instant
# rather than a duration. It rides along in this call because an interpreter
# start is by far the most expensive part of it, and this one already pays for
# itself twice over.
read -r NICEGUI_PORT WALL_PATH END_EPOCH <<<"$("$PYTHON" -c 'import datetime, sys
sys.path.insert(0, "webgui")
import repo_paths, wall
from shared.market_calendar import window_bounds, CT
_, end = window_bounds("stream")
now = datetime.datetime.now(CT)
end_dt = now.replace(hour=end.hour, minute=end.minute, second=0, microsecond=0)
print(repo_paths.NICEGUI_PORT, wall.PAGE_ROUTE, int(end_dt.timestamp()))')"
"$PYTHON" tools/wait_http.py --port "$NICEGUI_PORT" --timeout 120 --label "the web GUI"

WALL_URL="http://127.0.0.1:${NICEGUI_PORT}${WALL_PATH}"

# ── The framebuffer, and a browser in front of it ────────────────────────────
Xvfb ":$DISPLAY_NUM" -screen 0 "${SCREEN}x24" &
XVFB_PID=$!

# A FRESH profile every run. A reused --user-data-dir that was not shut down
# cleanly -- which is exactly what a `systemctl stop` produces -- makes Chrome
# open with a "Chrome didn't shut down correctly / Restore pages?" bubble. On
# this screen that bubble is on camera, on a public stream, until someone
# notices and clicks it. There is nothing in the profile worth keeping: the page
# holds no login and no state.
PROFILE_DIR="$(mktemp -d)"
# Three names because the same browser answers to all of them depending on how
# it was installed (.deb, distro package, snap), and `set -e` on an unfound
# command would abort with a shell message naming neither the browser nor why.
CHROME="$(command -v google-chrome || command -v chromium-browser \
          || command -v chromium || true)"
[ -n "$CHROME" ] || { echo "no chrome/chromium on PATH - cannot render the wall"; exit 1; }

DISPLAY=":$DISPLAY_NUM" "$CHROME" \
  --kiosk \
  --window-size=1920,1080 \
  --window-position=0,0 \
  --user-data-dir="$PROFILE_DIR" \
  --no-first-run \
  --no-default-browser-check \
  --disable-infobars \
  --disable-session-crashed-bubble \
  --autoplay-policy=no-user-gesture-required \
  "$WALL_URL" &
CHROME_PID=$!

# Let Chrome paint before the encoder starts looking. ffmpeg grabs whatever is
# in the framebuffer the instant it opens the display, and Chrome's startup —
# process launch, then three iframes each building a NiceGUI page and reading
# its caches — is several seconds of white. Without this pause the opening
# seconds of every broadcast are a blank screen, which is also the worst
# possible thing to be showing when a viewer arrives at the start.
sleep 15

# ── Encode ───────────────────────────────────────────────────────────────────
# LAST, and the only thing this shell then does is `wait` on it -- so the
# encoder exiting is what ends this process and takes the unit down for a
# restart. It is backgrounded ONLY so the watchdog below has a PID to aim at;
# `wait` under `set -e` makes that identical to running it in the foreground.
#
# NOT `exec ffmpeg`, which would be the obvious way to make ffmpeg the unit's
# main PID: exec replaces this shell, and the EXIT trap goes with it. Xvfb and
# Chrome would then be left holding :99 whenever ffmpeg exits on its own.
#
# -nostats -loglevel warning because stderr here IS the journal. ffmpeg's
# progress line is roughly two writes a second; over a nine-hour session, every
# day, that is not merely disk. journald rate-limits per service and starts
# DROPPING messages, so the real cost is that the one warning worth reading gets
# suppressed by the noise it was buried in. Warnings and errors still come
# through; only the progress spam stops.
#
# anullsrc supplies a silent stereo track. YouTube's ingest is unreliable with
# video-only input, and a dashboard has nothing to say -- a null source costs
# essentially no bitrate and removes the whole failure mode.
#
# MEASURED on the prod host during market hours against the real /wall page, on
# 4 cores. Chrome + Xvfb add a constant ~64% on top of each figure:
#
#     1080p30 veryfast      153%     <- what this used to be
#     1080p30 ultrafast     100%
#     1080p15 veryfast       72-87%  <- shipped
#     720p30  veryfast      106%
#     capture 15 -> out 30  140%
#
# 1080p at 15fps, because for THIS content the framerate is the cheapest thing
# to give up: the data behind it updates every 2 seconds and the panel rotates
# every 15. Resolution is the half that must not move -- the screen is dense
# small text read at a distance, which is precisely what resolution buys.
#
# 720p30 is DOMINATED, not a middle option: it costs MORE than 1080p30 at
# ultrafast and throws away resolution as well. Recorded so nobody optimises
# toward it later.
#
# -thread_queue_size 512 is worth ~20% ON ITS OWN. Without it these same
# settings measured 193% and ffmpeg logged "Thread message queue blocking" --
# x11grab producing frames faster than the encoder drained them, which means
# dropping them on the floor. With it: 153% and a clean log.
#
# The bitrate moving 4500k -> 3500k is MORE bits per frame, not fewer:
# 3500/15 = 233 against 4500/30 = 150. Text quality goes UP. That is
# counter-intuitive enough to write down, because otherwise someone reads the
# smaller number as a downgrade and "restores" it.
#
# The keyframe interval is $GOP, derived from $FPS at the top of this file --
# see there for why it is derived rather than typed. -sc_threshold 0 is the
# other half of holding it: without it x264 adds its own keyframes on the
# crossfades and the interval stops being strict.
#
# -y so ffmpeg never stops to ask about an existing output. Moot against an RTMP
# URL, costs nothing, and saves the next person a measurement run lost to a
# prompt when they point it at a file.
#
# Deliberately NO -tune zerolatency. Latency is irrelevant for an unattended
# dashboard broadcast -- nobody is interacting with what they see -- and that
# tune disables B-frames and lookahead, which are exactly what buy quality per
# bit on a screen that is mostly flat colour and small text.
ffmpeg -y -nostats -loglevel warning \
       -f x11grab -draw_mouse 0 -framerate "$FPS" -video_size "$SCREEN" \
       -thread_queue_size 512 -i ":$DISPLAY_NUM" \
       -f lavfi -i anullsrc=channel_layout=stereo:sample_rate=44100 \
       -c:v libx264 -preset veryfast -pix_fmt yuv420p \
       -b:v 3500k -maxrate 3500k -bufsize 7000k \
       -g "$GOP" -keyint_min "$GOP" -sc_threshold 0 \
       -c:a aac -b:a 128k -f flv "$RTMP_URL" &
FFMPEG_PID=$!

# ── Stop if the picture dies, or when the window closes ──────────────────────
# ffmpeg grabs a FRAMEBUFFER, not a browser. If Chrome or Xvfb exits -- a
# renderer crash, or the OOM killer on a 7.8 GB box with no swap after nine
# hours -- ffmpeg carries on encoding whatever is left in that framebuffer,
# which is black, and streams it perfectly happily. The unit stays `running`,
# because bash is still waiting on a live encoder, and nobody finds out until a
# viewer says so. Restart=on-failure is already the recovery mechanism; it just
# needs something to fail.
#
# The watchdog signals the ENCODER, and that is the load-bearing detail. The
# obvious `kill -TERM $$` does run the EXIT trap -- measured -- but bash
# re-raises, so this process dies BY SIGTERM, and systemd counts SIGHUP, SIGINT,
# SIGTERM and SIGPIPE as a CLEAN exit: Restart=on-failure explicitly excludes
# those four, so the unit would go quietly down and stay down. It would also
# orphan ffmpeg, since a bare `kill PID` never reaches a sibling. Killing the
# encoder instead makes `wait` below return 143 as an exit CODE, which is the
# unclean exit systemd will actually restart.
#
# Every check runs BEFORE the first sleep, so a child that is already gone is
# caught at once rather than up to 30s later.
#
# The same loop also enforces the END OF THE WINDOW, which nothing else can.
# RuntimeMaxSec in the unit caps ONE invocation, and the startup gate above runs
# ONCE -- so a restart at 14:00 (this very watchdog firing, or an OOM on a
# swapless box) would get a fresh 7h20m and broadcast a frozen after-hours
# dashboard until 21:20, publicly, with nothing on screen to say it is stale.
# Worse, that path is REACHED BY THE RECOVERY ITSELF: the better the watchdog
# works, the likelier it is. systemd has no way to express "and also stop at
# this wall-clock time regardless of when you started", so the end is carried as
# an absolute INSTANT computed at startup, which a restart simply recomputes to
# the same 15:20.
#
# `date +%s` rather than re-running in_window every 30s -- ~1,080 interpreter
# starts a session to re-answer a question already answered at startup. Both
# sides are epoch seconds, so the comparison holds whatever TZ the unit runs in.
#
# `-ge` stops AT 15:20:00. in_window's end is inclusive, so it is still true at
# exactly that instant -- a difference of zero width, and swamped four orders of
# magnitude by the 30s poll. Erring toward stopping is the right side for a
# public broadcast anyway.
( while :; do
    if [ "$(date +%s)" -ge "$END_EPOCH" ]; then
      echo "reached the end of the stream window - stopping" >&2
      break
    fi
    kill -0 "$XVFB_PID" 2>/dev/null || {
      echo "Xvfb exited - stopping the encoder so the unit restarts" >&2; break; }
    kill -0 "$CHROME_PID" 2>/dev/null || {
      echo "Chrome exited - stopping the encoder so the unit restarts" >&2; break; }
    sleep 30
  done
  kill -TERM "$FFMPEG_PID" 2>/dev/null || true
) &
WATCH_PID=$!

wait "$FFMPEG_PID"

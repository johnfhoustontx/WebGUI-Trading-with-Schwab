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
# ffmpeg runs LAST and in the FOREGROUND, so that the encoder exiting is what
# ends this process and takes the unit down; the window gate exits ZERO, because
# a holiday is a normal outcome and not something to restart against; and the
# stream key arrives in the environment rather than living in the repo.
set -euo pipefail

# Resolve everything from this script's own location. A hardcoded /home/... path
# would work in prod and silently stream the wrong checkout from anywhere else.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PYTHON="$ROOT/.venv/bin/python"

DISPLAY_NUM=99
SCREEN=1920x1080

# ── Clean up our own children ────────────────────────────────────────────────
# Xvfb and Chrome are backgrounded, so without this they outlive a stopped unit
# and hold :99 -- the next start then dies with "Server is already active for
# display 99", a message that goes to the journal and nowhere a viewer looks.
# The PIDs are unset until their process starts and `set -u` would abort the
# trap on the way past, so both are defaulted; `|| true` keeps a stop that has
# already reaped one of them from failing the other.
cleanup() {
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
read -r NICEGUI_PORT WALL_PATH <<<"$("$PYTHON" -c 'import sys
sys.path.insert(0, "webgui")
import repo_paths, wall
print(repo_paths.NICEGUI_PORT, wall.PAGE_ROUTE)')"
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
# FOREGROUND, and last: the shell does nothing but wait on it, so the encoder
# dying is what ends this process and takes the unit down for a restart.
# Backgrounding it would leave the unit "running" with nothing being streamed.
#
# NOT `exec ffmpeg`, which would be the obvious way to make ffmpeg the unit's
# main PID: exec replaces this shell, and the EXIT trap goes with it. Xvfb and
# Chrome would then be left holding :99 whenever ffmpeg exits on its own.
#
# anullsrc supplies a silent stereo track. YouTube's ingest is unreliable with
# video-only input, and a dashboard has nothing to say -- a null source costs
# essentially no bitrate and removes the whole failure mode.
#
# -g 60 at 30fps is a 2-second keyframe interval, which is what YouTube
# recommends and half its 4-second cap; -sc_threshold 0 stops x264 inserting
# extra keyframes on scene cuts, which the crossfade every 15s would otherwise
# look like.
#
# Deliberately NO -tune zerolatency. Latency is irrelevant for an unattended
# dashboard broadcast -- nobody is interacting with what they see -- and that
# tune disables B-frames and lookahead, which are exactly what buy quality per
# bit on a screen that is mostly flat colour and small text.
ffmpeg -f x11grab -draw_mouse 0 -framerate 30 -video_size "$SCREEN" -i ":$DISPLAY_NUM" \
       -f lavfi -i anullsrc=channel_layout=stereo:sample_rate=44100 \
       -c:v libx264 -preset veryfast -pix_fmt yuv420p \
       -b:v 4500k -maxrate 4500k -bufsize 9000k \
       -g 60 -keyint_min 60 -sc_threshold 0 \
       -c:a aac -b:a 128k -f flv "$RTMP_URL"

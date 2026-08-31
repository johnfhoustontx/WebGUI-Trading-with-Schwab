# Wall display rotation → YouTube live stream — design

**Date:** 2026-08-31
**Status:** approved, not yet implemented
**Scope:** a new `/wall` webgui route, a headless capture/encode pipeline on the
prod host, and a market-hours systemd timer that drives it.

## What this is

A public YouTube live stream that rotates three existing dashboards every 15
seconds:

| | |
|---|---|
| `/desk` | the aggregate desk screen |
| `/market` | the macro ticker board |
| `/sentiment/momentum` | the momentum argument |

## Why not OBS

OBS was the starting request and was rejected on measurement, not preference.

The prod host (`vps2`, 4 vCPU Ivy Bridge Xeon, 7.8 GB, **no swap**) has a **QXL
paravirtual graphics card** — no VAAPI, no NVENC, and no OpenGL driver. OBS
requires OpenGL 3.3, so on this box it falls back to **Mesa llvmpipe** and pays a
full software-composite pass on every frame *before* x264 runs. `ffmpeg`'s
`x11grab` reads the framebuffer directly and skips that pass entirely. On four
Ivy Bridge cores shared with a live trading stack, that difference is the whole
budget.

The second argument is that OBS's actual selling point — its UI — is lost on a
headless server. Its configuration would live in hand-edited JSON scene
collections on the box, i.e. nowhere, free to drift, outside git.

⚠ **The upgrade path is preserved and is not theoretical.** Because the rotation
lives in an HTML document rather than in OBS scenes, adopting OBS later (for a
webcam, mic commentary, or transitions) means pointing one OBS browser source at
`/wall`. Nothing built here is thrown away. Do not "simplify" the rotation into
the capture layer — that is what would strand it.

## Architecture

```
webgui :8500  ──GET /wall──▶  Chrome (kiosk, Xvfb :99)  ──x11grab──▶  ffmpeg  ──RTMP──▶  YouTube
   │                                   │
   └── /desk, /market, /sentiment/momentum
       three iframes, all live, all always laid out at full size
```

One browser, one encoder, one page. No browser automation, no CDP, no scene
switching, no navigation.

**It streams prod.** Dev is not a substitute: its schedulers are suppressed and
its stores are a disposable snapshot of prod's, so it would broadcast stale
numbers. The accepted cost is that the capture browser adds load to the same
webgui the desk is traded from.

## The `/wall` route

A new `webgui/wall.py` modelled directly on `webgui/desk_stream.py`: it owns
`PAGE_ROUTE = "/wall"`, exposes `document()`, and `main.py` registers it as a raw
`HTMLResponse`. It is **deliberately outside the Tailwind-first standard** — the
same documented out-of-scope case as `/desk/live`, the EOD report and the Gamma
Explain infographic — so it carries its own `<style>` block. Its palette and
branding still come out of `pages/options/theme`, never hand-picked.

It is **not** a shell page: no `_layout`, no drawer, no breadcrumb, and it is not
added to `NAV_SECTIONS`. It is a display target, not a destination a reader
navigates to.

### Rotation is `opacity` + `z-index`, never `display:none`

Three absolutely-positioned iframes at full 1920×1080, stacked. The active one
is brought forward. Three reasons, in order of how much the alternative hurts:

1. **An iframe hidden with `display:none` has zero layout size.** None of these
   three pages draws a Highcharts chart today — verified, `ui.highchart` count is
   zero in `pages/desk.py`, `pages/market.py` and `pages/momentum_view.py` — so
   nothing breaks now. But the documented no-ResizeObserver trap says a chart
   that mounts at 0×0 never recovers, so the day one of these pages gains a chart
   (or the day a chart-heavy page like `/options/gamma` is swapped in) the panel
   silently renders collapsed. Avoiding it costs nothing today.
2. **All three stay painted and connected**, so a rotation is instant.
   Navigating a single iframe would show a NiceGUI page rebuilding — white flash,
   websocket reconnect, one to two seconds of nothing — on every rotation.
3. **A 400 ms crossfade is cheaper to encode than a hard cut.** The encoder runs
   with scene-cut detection off (see below), so an instant full-frame change
   every 15 s is its worst case. A fade spreads that cost across frames.

⚠ A related trap this avoids by construction: Chromium throttles timers and
rendering in **backgrounded tabs**, which is why the documented "transitions
freeze and `getComputedStyle` lies" gotcha exists. Iframes inside one foreground
document are never backgrounded, so none of the three pages is ever throttled. A
three-tab implementation would have been.

### The app shell is stripped inside each panel

Each panel renders the **full NiceGUI shell** — the 68 px icon rail, the 60 px
header with breadcrumb, and on `/market` and `/sentiment/momentum` the tab strip.
That is ~170 px of vertical and 68 px of horizontal spent on navigation no viewer
can click, and it pushed real content below a fold nobody watching a stream can
scroll past. Measured on real 1920×1080 frames: the Macro Board was cut at SECTOR
SPDR, the Desk showed 5 of its opportunity and position rows.

`/wall` and all three panels are served from the **same origin**, so the wall
document reaches `iframe.contentDocument` and injects `PANEL_CSS` — no change to
`main.py`, `_layout`, or any of the three pages. After it: the Macro Board
reaches FACTOR / MOMENTUM, the Desk shows 6 and 6.

⚠ **Injection binds to each iframe's `load` event, not a one-shot at startup.**
The off-camera reload reassigns `src` every `RELOAD_MS`, which discards the
injected stylesheet — a startup-only version works perfectly and then breaks
fifteen minutes into a broadcast. A second eager call covers the race where a
panel finishes loading before the script runs. The whole thing is in a try/catch:
an unreachable document must render *with* its chrome, never throw and strand the
rotation.

### Overlay

Branding via `theme.BRAND_CSS` / `theme.BRAND_FONT_HEAD_HTML` and
`main.brand_lockup_html()` — the same wordmark the app header draws, not a second
copy, so a rename in `config/theme.toml` reaches the stream. Alongside it, the CT
clock and the active page's name, both driven by the same timer as the rotation
so they cannot disagree with what is on screen.

A **disclaimer slot is built but left empty** by operator decision. It is a
single constant in the document. ⚠ Worth revisiting: this stream shows a paper
book's positions and P&L publicly and permanently, and an unlabelled P&L reads as
a track record.

### Cost

Three live pages means three sets of the app's 2-second watchers instead of one.
On a host idling at ~7% that is expected to be noise — **but it is to be
measured, not asserted.** This repo has a standing rule that estimates of
localhost costs have twice been the bug.

## Capture and encode

```
Xvfb :99 -screen 0 1920x1080x24

google-chrome --kiosk --app=http://127.0.0.1:8500/wall \
              --window-size=1920,1080 --window-position=0,0 \
              --noerrdialogs --disable-session-crashed-bubble

ffmpeg -f x11grab -draw_mouse 0 -framerate 30 -video_size 1920x1080 -i :99 \
       -f lavfi -i anullsrc=channel_layout=stereo:sample_rate=44100 \
       -c:v libx264 -preset veryfast -pix_fmt yuv420p \
       -b:v 4500k -maxrate 4500k -bufsize 9000k \
       -g 60 -keyint_min 60 -sc_threshold 0 \
       -c:a aac -b:a 128k -f flv "$RTMP_URL"
```

### Measured, not estimated (prod host, market hours, 2026-08-31)

The design originally specified **1080p30 `veryfast`**. Measured against the real
`/wall` page on the 4-core host, that was the most expensive option tested:

| variant | ffmpeg CPU |
|---|---|
| 1080p30 veryfast (originally specified) | **153%** |
| 1080p30 ultrafast | 100% |
| **1080p15 veryfast — SHIPPED** | **72–87%** |
| 720p30 veryfast | 106% |
| capture 15 → output 30 | 140% |

Chrome adds a constant **~52% CPU / 1.84 GB RSS across 16 processes** and Xvfb
**~13% / 197 MB**, on any of these.

Three results worth keeping:

- **`-thread_queue_size 512` is worth ~20% on its own.** Without it the original
  settings measured **193%** and ffmpeg logged `Thread message queue blocking`,
  meaning x11grab was producing frames faster than the encoder consumed them —
  dropping them. With it, 153% and clean.
- **720p30 is dominated.** It costs *more* than 1080p30-ultrafast and loses
  resolution as well. Do not "optimise" toward it.
- **Capturing at 15 and outputting 30 does not pay off** (140%). Duplicated
  frames still take near-full motion estimation; the idea is intuitive and wrong.

**1080p15 was chosen** because for this content the framerate is the cheapest
thing to give up — the data updates every 2 s and the panel rotates every 15 —
while resolution is what matters, since it is dense small text read at a
distance. It is technically off YouTube's recommended 30 fps, which it accepts in
practice. Total system load lands ~35% against ~54% for the original settings, on
a box with **no swap** that also runs the trading stack.

⚠ The bitrate drops 4500k → 3500k but that is **more bits per frame**, not fewer
(3500/15 > 4500/30), so text quality improves. Someone will otherwise "restore"
the higher number.

Other choices worth recording:

- **The silent audio track is deliberate.** YouTube's ingest is unreliable with
  a video-only stream. `anullsrc` costs nothing.
- **`-g` pins a 2-second keyframe interval**, which YouTube requires (it caps the
  interval at 4 s). `-g` is in FRAMES, so it is framerate × 2 — 30 at 15 fps.
  A framerate change that leaves `-g` behind silently doubles the interval.
  `-sc_threshold 0` keeps it strict.
- **`-tune zerolatency` is deliberately absent.** Latency does not matter for a
  dashboard broadcast, and omitting it lets x264 use B-frames and lookahead for
  better quality per bit.
- **Google Chrome from Google's apt repo, not Ubuntu's `chromium`.** On 24.04
  that package is a snap transition, and snap confinement plus Xvfb is a known
  headache not worth debugging on the prod box.

## Scheduling

A new **`[windows.stream]`** in `config/sessions.toml`, **08:00–15:20 CT**. It is
a genuinely new operating window, so it belongs in that file — which is the
single source for window constants — rather than borrowing
`[windows.collection]`'s bounds, which the file explicitly warns against
conflating. The bounds match when the app's own collection runs, so the stream is
live exactly when the dashboards have moving data; a stream showing frozen
overnight numbers is worse than no stream.

A systemd timer fires at the boundaries on weekdays. The `ExecStart` wrapper then
asks **`shared.market_calendar.is_trading_day()`** and exits 0 if it is a
holiday. The timer cannot know about Thanksgiving; the calendar module derives it
algorithmically, and the repo rule is that no new holiday literal goes anywhere
else.

Units are emitted by `deploy/systemd/generate_units.py` like every other unit, so
ports, paths and the environment name come from `repo_paths` and cannot disagree
with the checkout.

⚠ `Restart=on-failure` with `StartLimitIntervalSec` / `StartLimitBurst` in
**`[Unit]`, not `[Service]`** — systemd moved them in v229 and silently ignores
them in the wrong section, so the storm cap would look configured and not exist.

## YouTube's auto-stop is REQUIRED, not a preference (learned 2026-08-31)

This stream stops every day at 15:20, which makes one YouTube setting
load-bearing in a way it is not for a one-off broadcast.

**With auto-stop disabled, YouTube keeps the broadcast in the `live` state after
ingest stops.** Observed: the encoder went down at 10:50 and the channel was
still showing as live nearly five hours later, on a frozen last frame. Nothing
was leaving the host — measured at 0 connections on `:1935`, no encoder process,
and 35 kbit/s total egress against ~2000 while streaming.

Two consequences, the second worse than the first:

- The channel advertises a live broadcast that is a still image.
- **The next morning's start reconnects into that same stale broadcast** rather
  than opening a new one, because the stream key is persistent. The archive
  becomes one endless video with a 17-hour dead gap in the middle of it, instead
  of one clean recording per trading day.

⚠ **The diagnostic is the frozen frame.** A frozen picture means YouTube is
holding a dead broadcast open; a *moving* picture with no encoder on this host
means something else is pushing to the same key, which is a different and more
urgent problem.

## The stream key

The YouTube stream key is a credential. It is created **by the operator** at
`/etc/neuralstrike-stream/env`, mode `0600`, and the unit reads it via
`EnvironmentFile=`. It does not enter the repository, a Claude conversation, or a
shell history.

## Verification

The failure modes here are visual and silent; the test suite cannot see any of
them. Before going live:

1. **Fonts.** A minimal Ubuntu image ships almost none. If Google Fonts is slow
   or blocked, Quasar's Material Icons render as literal ligature text — the word
   `trending_up` where an icon belongs, on a public stream. Screenshot `/wall`
   before streaming.
2. **CPU headroom during market hours**, not at idle. Measure the browser and the
   encoder separately, against the trading stack's real load.
3. **Chrome's sandbox.** Ubuntu 24.04's AppArmor restriction on unprivileged user
   namespaces interacts badly with it. Confirm Chrome starts sandboxed rather
   than reaching for `--no-sandbox` reflexively.
4. **A full rotation cycle**, watched on the live stream, not just locally.

Per the development rule, all of it lands in dev (`:9500`) and is verified
running there before `tools/promote.sh` touches prod. "Tests pass" is not
"verified in dev" for anything with a runtime surface, and this is nearly all
runtime surface.

## Out of scope

- Overlays beyond branding and the clock; webcam; commentary audio.
- A Status-page control. The timer is the interface.
- Any change to the three dashboards themselves.

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

Four choices worth recording:

- **The silent audio track is deliberate.** YouTube's ingest is unreliable with
  a video-only stream. `anullsrc` costs nothing.
- **`-g 60` pins a 2-second keyframe interval**, which YouTube requires (it caps
  the interval at 4 s). `-sc_threshold 0` keeps it strict.
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

# Desk — spoken alerts + neon row highlight (design)

**Date:** 2026-08-21
**Route:** `/desk` only (helpers built reusable; `/options/flow` and the paper
pages are deliberately out of scope)
**Status:** design approved, not yet implemented

## The problem

The Desk is the landing page and the screen a trader leaves open. Two of its
panels carry genuinely time-critical arrivals — **Flow Alerts** (published on the
options service's 1-minute GEX tick) and **Positions** (a new fill in any of the
three books). Today both arrive **silently and without visual emphasis**: the
panel repaints, a row appears at the top, and nothing distinguishes it from the
eight rows that were already there. A trader looking anywhere else misses it
entirely, and a trader looking *at* the panel has to diff it against memory.

The app already chimes for scanner signals (`main.play_alert`), but a chime says
only "something happened" — it does not say *what*, so it still costs a look.

## What we are building

1. **A spoken announcement** in a natural human voice naming the **ticker** and
   the **cause** — "S P Y. Crossover alert, calls over." — fired when a new entry
   appears in either panel.
2. **A 10-second neon glow** on the row itself, so the voice and the eye land on
   the same place.

## Decisions

| Question | Decision |
|---|---|
| Voice engine | **Server-side `edge-tts`** (free Microsoft neural voices, no API key) with a permanent on-disk phrase cache |
| Default voice | **`en-US-AriaNeural`**, switchable in Settings |
| What speaks | **All four** flow kinds (crossover, unusual activity, gamma flip, **big delta**) + **newly-opened positions** |
| What does *not* speak | Position **state** changes (flag moves) — they glow, silently |
| What glows | New rows in **both** panels, plus **flag changes** on Positions |
| Burst behaviour | **Newest only, then a count** — "…plus 5 more." |
| Cache warming | **Pre-warm at startup** over the watchlist × cause set |
| Scope | `/desk` only |

### Why edge-tts and not the obvious alternatives

Measured on this machine, not assumed:

- **Windows SAPI is not an option.** `System.Speech` reports only *Microsoft
  David / Zira / Mark Desktop* — the old concatenative voices. No Windows 11
  "Natural" neural voice is installed. These do not meet a "human voice" bar.
- **Browser `speechSynthesis` is browser-dependent.** In Edge it exposes the
  same Microsoft neural voices; in Chrome on this box it falls back to the
  robotic SAPI voices above. A feature whose core quality depends on which
  browser the launcher happened to open is not a feature.
- **A pre-generated clip library** works but bounds us to a known ticker set and
  needs a rebuild whenever the watchlist changes (`Top 20.xlsx` is gitignored and
  varies per machine).

`edge-tts` measured at **~600 ms per phrase, ~26 KB per mp3**, and pulls only
`aiohttp` (already a dependency) plus `tabulate`.

### `big_delta` deliberately breaks the quiet-live rule

`alerts.py` excludes `big_delta` from the chime/toast trigger set — it is
"quiet-live": on screen, but silent. **The voice does not honour that
exclusion**, by explicit decision. The reason the exclusion exists is that a
*chime* carrying no information is pure noise at `big_delta`'s frequency; an
announcement that names the ticker and the cause is not, because the cost of
ignoring it is zero. This divergence is intentional and must not be "fixed" into
consistency without revisiting it.

## Architecture

### `webgui/voice.py` (new)

```
pure builders (no TTS, no I/O)          cache layer                 playback
────────────────────────────            ───────────                 ────────
spell("$SPX")      -> "S P X"           ensure(text) -> url|None    <audio id="desk-voice">
flow_phrase(row)   -> "S P Y. Cross…"     sha1(voice|rate|text)     JS queue, chains on `ended`
position_phrase(r) -> "S P Y. New po…"    webgui/data/voice/*.mp3
more_tail(n)       -> "Plus 5 more."      synth on miss (~600ms)
```

- **Tickers are always spelled** — `SPY` → "S P Y", `$SPX` → "S P X" (the `$` is
  stripped, not spoken). This is squawk convention and it is also the only rule
  that works for an unbounded symbol set; "spy" as a word would be actively
  misleading.
- **The whole utterance is ONE clip**, tail included, so the cache key is the
  full sentence. Two concatenated clips (ticker + cause) would make the cache
  `O(tickers + causes)` instead of `O(tickers × causes)`, but at the cost of an
  audible seam. The cross product is ~120 phrases; disk is not the constraint.
- **Clips live in `webgui/data/voice/`** (gitignored, regenerates) mounted at
  `/voice`, **not** in `webgui/static/` — generated artifacts stay out of git.
- **`ensure` never raises.** No internet, or `edge_tts` absent → returns `None`
  → no speech, the glow still fires, the existing chime is untouched. Logged
  **once per process**, not per tick.

**On the Tier-1 rule.** `webgui` is documented as importing only `nicegui` +
`shared.bus` + `shared.contracts`. `voice.py` imports `edge_tts`, which is
neither an engine nor a Schwab caller — it is a presentation concern, the audio
equivalent of the bundled WAVs already in `webgui/static/sounds/`. The module
docstring says so, so the next audit does not read it as a violation.

### Announcement vocabulary

| Event | Utterance |
|---|---|
| crossover | "S P Y. Crossover alert, calls over." |
| uoa | "N D X. Unusual activity, put." |
| gamma_flip | "Q Q Q. Gamma flip, to negative." |
| big_delta | "A M D. Big delta, call." |
| new position | "S P Y. New position, put credit spread." |
| burst | "…Plus 5 more." folded into the same sentence |

The kind and side words come from `flow._KIND_LABEL` / `flow._SIDE_LABEL` —
**imported, not restated**. The Desk's governing principle is that it composes
and never re-derives; a spoken vocabulary that drifted from the printed one
would be the same class of bug as the documented sectors-vs-rotation split.

**One utterance per panel per tick**, so a tick is bounded at two clips. Flow and
Positions each get their own newest-plus-count.

### Change detection

Pure functions over the row lists, in `desk.py` beside the other pure builders:

- `new_ids(rows, seen)` — ids present now and not before.
- `flag_changes(rows, prev_flags)` — position ids whose `flag` moved.

State lives in the page-state dict: `seen_flow`, `seen_pos`, `pos_flags`, `glow`.

**First paint seeds all three silently.** Without this, navigating to the Desk
announces the entire day's alert list and lights every row — the exact trap
`main.py`'s watcher already documents ("Mark everything currently present as
alerted so each signal chimes once").

### The neon glow, and the repaint problem

`@keyframes deskNeon` — an inset ring plus outer glow decaying to nothing over
10 s — added to the page's existing `ui.add_css` escape hatch (`desk.py` already
injects `CONSOLE_KEYFRAMES_CSS`; a keyframes animation cannot be a utility
class).

**The non-obvious part.** `_paint_positions` calls `pos_body.clear()` and
rebuilds every row, and it runs whenever the paper account re-prices — which is
constant during market hours. A rebuilt element **restarts its CSS animation from
zero**, so a naive implementation glows forever, resetting every couple of
seconds and never expiring.

Fix: the glow **expiry** lives in page state keyed by row id, and each row
applies one of **ten static classes** `desk-neon-0 … desk-neon-9`, each carrying
a whole-second negative `animation-delay` so a rebuilt element **resumes** the
animation at the right point instead of restarting it.

Ten fixed classes rather than a computed `[animation-delay:-3.2s]`: the styling
standard's finite-set rule. Two hues only — cyan (`SPOT_HEX`) for new, amber
(`FLIP_HEX`) for a flag change.

### Playback, and the autoplay trap

A **separate `<audio id="desk-voice">`** from `main.py`'s shared
`<audio id="alert-audio">`. Sharing one element would let a scanner chime cut an
announcement off mid-sentence.

**Browsers block audio until a user gesture.** A freshly-loaded tab left alone
announces nothing, with no error anywhere — the `play()` promise simply rejects.
So the rejection is caught and a small muted-speaker chip appears in the Desk
header; one click unlocks audio and hides it. Without this affordance the feature
looks broken on every reload, and the failure is completely silent. (Settings
already carries the same caveat for the chime, and its Test sound button is the
existing unlock.)

### Settings

New `app_settings` keys, all in `DEFAULTS`:

| key | default |
|---|---|
| `voice_enabled` | `True` |
| `voice_name` | `"en-US-AriaNeural"` |
| `voice_volume` | `0.8` |

A "Spoken alerts" card on the Settings page: enable switch, voice select (the
six en-US neural voices), volume slider, and a **Test voice** button that doubles
as the audio unlock. The existing **`alert_market_hours_only`** gate is honoured
rather than duplicated — a desk that talks at 3 a.m. is a bug, and a second
market-hours switch beside the first is a drift hazard.

### Pre-warming

At webgui startup a background task synthesizes the current watchlist symbols ×
the cause set (~120 clips, ~3 MB, ~72 s of background work) so live alerts play
from a warm cache. The disk cache survives restarts, so this is a once-ever cost
per phrase, not per boot. Failure to pre-warm is not an error — it degrades to
the lazy path.

## Failure modes

| Failure | Behaviour |
|---|---|
| No internet / Azure endpoint down | `ensure` returns `None`; glow still fires; chime unaffected; logged once |
| `edge_tts` not installed | Same as above — the import is guarded |
| Browser autoplay blocked | Unlock chip in the Desk header |
| Cache dir unwritable | Same as no internet |
| Burst of 20 alerts | One utterance: newest + "plus 19 more" |
| Both panels fire in one tick | Two utterances, queued, flow first |

Every one of these degrades to **the app as it is today**, never to an error
dialog and never to a stuck page.

## Testing

`webgui/tests/test_voice.py` — the pure builders: spelling (`$SPX`, `SPY`,
lowercase, empty), phrase text for all four flow kinds and for positions, tail
counting and its plural/singular boundary, cache-key stability, and the
degrade-to-`None` path with `edge_tts` monkeypatched absent. **No network in
tests** — synthesis is monkeypatched.

`webgui/tests/test_desk.py` (extended) — `new_ids` / `flag_changes` over
add/remove/reorder/duplicate-id cases, first-paint suppression, glow-step
arithmetic across the full 0–10 s range and past expiry, and the
one-utterance-per-panel-per-tick bound.

`webgui/tests/test_no_inline_style.py` already covers `desk.py`; the new CSS goes
through the documented `ui.add_css` hatch, so the guard stays green.

## Documentation

- `docs/CHANGELOG.md` — a dated entry.
- `docs/webgui-routes.md` — the Desk's per-page detail gains the voice + glow behaviour.
- `webgui/page_help.py` — the Desk hover guide. This is the most-read prose in
  the app and the least likely thing to be touched when a feature moves; it is
  updated **in this change**, not later.
- `docs/manuals/` — the User Guide gains the Settings → Spoken alerts controls
  and the autoplay-unlock caveat, since both are user-visible behaviour.

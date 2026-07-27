# Rebrand: "Schwab Trading" → NeuralStrike

**Date:** 2026-07-27
**Scope:** webgui app identity — name, header lockup, brand assets. No routes,
services, cache keys, or data behavior change.

## Goal

Rename the application to **NeuralStrike** and put the supplied logo in the app,
matching the logo's font and colors.

## Decisions (user, 2026-07-27)

| Question | Choice |
|---|---|
| Where the logo appears | Header brand mark only |
| How far the logo's font/colors reach | The wordmark only |
| Rename scope | App UI + the launcher scripts |

## Assets

Source artwork: `D:\Telegram Desktop\NeuralSignal Logo.jpg` — 832×1248 JPEG,
portrait, black background, holding three things: the NS monogram, the
"NEURALSTRIKE" wordmark, and an "AI OPTION SIGNALS | TRADING IDEAS" tagline.

Two files land in a new `webgui/static/img/` (the directory is already served at
`/static` by `main.py`'s `app.add_static_files`):

| File | What | Why |
|---|---|---|
| `neuralstrike-logo.jpg` | The full lockup, copied verbatim | Brand source of truth; the mark is regenerated from it |
| `neuralstrike-mark.png` | 256×256 square crop of the monogram alone | What the header actually renders |

**Why crop.** The header brand tile is 28px square. The full portrait lockup
scaled into it would be an unreadable smudge, and its wordmark would compete with
the real wordmark rendered beside it. Cropping to the monogram gives a mark that
reads at 28px; the header text supplies the name. The crop is computed from the
artwork's content bounding box above the wordmark band, then squared about its
centre — so it is reproducible, not hand-tuned pixel guesses.

## Header lockup

The generic blue gradient square and its inline chart-glyph SVG are replaced by
the monogram image, with the name beside it as a two-tone wordmark.

Colors are **sampled from the artwork**, not eyeballed — the p50→p95 range of the
non-background pixels in each wordmark band (the low percentiles are anti-aliasing
against black and would read too dark):

| Element | Gradient | Sampled from |
|---|---|---|
| "NEURAL" | `#C9A356` → `#FBEAA0` | gold wordmark band, p50→p95 |
| "STRIKE" | `#2C6FB4` → `#35A3F5` | blue wordmark band, p50→p95 |

Rendered uppercase with tight tracking, matching the lockup.

**Font — an honest approximation.** The exact typeface cannot be identified from a
raster image. **Montserrat ExtraBold (800)** is a very close geometric match for
these letterforms, is free on Google Fonts, and follows the loading pattern the app
already uses for its body font. It is one config line to change if it is wrong.

The gradients are applied with `background-clip: text`, which needs raw CSS — and
raw CSS is required here anyway: the bundled Tailwind JIT does not reliably emit
arbitrary classes containing `rgba(...)` or gradients (a documented trap in this
codebase). The rules live in `main.py`'s `_NAV_CSS` alongside the existing
`.brand-tile` chrome.

## Font scope

Montserrat loads as a **brand** font used by the wordmark only. The body/data font
(`[typography].family`, IBM Plex) is untouched — a heavy display face would hurt
readability in the dense signal tables, which is the opposite of what this app is
for.

## Config

A new `[brand]` block in `config/theme.toml` (and `theme._DEFAULTS`, since
`load_theme` merges only known sections/keys) holds the name, the font family and
URL, and the four gradient stops. Editing the file and restarting restyles the
wordmark with no code change — consistent with how the rest of the app's styling
works.

**Deliberately NOT added to Settings → Appearance.** That editor's sections are
single-kind (`_THEME_SECTIONS` tags each as all-color or all-text) and `[brand]`
mixes colors with text knobs (name, font family, URL). Wiring it in would mean
reworking the editor for little gain.

## Rename points

| File | Count | What |
|---|---|---|
| `webgui/main.py` | 4 | `ui.run` title, browser-tab fallback, breadcrumb fallback, header label |
| `start_all.bat` | 3 | console window title + banner lines |
| `start_all_wt.bat` | 3 | console window title + banner lines |
| `stop_all.bat` | 1 | console window title |

## Explicitly unchanged

- **The repo folder name and every path** — renaming `D:\WebGUI Trading with
  Schwab` would break `repo_paths`, all launchers, and the venv.
- **The per-route favicon colors.** Each page gets its own colored square so
  several open tabs are tellable apart — a documented deliberate feature. A single
  logo favicon would make every tab identical.
- **The Deep Slate palette** everywhere outside the wordmark.
- Services, routes, cache keys, `webgui/static/sounds/`.

## Testing

The changed surface is a pure asset + label change, so the gate is the existing
`webgui` suite plus new unit tests for the two pure additions (`brand_wordmark_css`
and the mark-path resolver, which must degrade to no image when the asset is
absent), then a live browser pass on the rendered header.

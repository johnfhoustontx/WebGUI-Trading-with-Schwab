# Deep Slate — UI redesign adoption plan

**Date:** 2026-07-11 · **Branch:** `Using_Highcharts` · **Status:** Phases 1, 2, 3, 4 shipped
(palette + typography; dark shell; **density + flat tiles**; badge fills) + the **flat button
system**; only IBM Plex **Mono** numerics + the per-screen polish sweep remain.

## Buttons — flat "Deep Slate" (✅ SHIPPED 2026-07-11)

The app's 3D-gradient buttons were replaced with the mockup's flat set (verified against
the prototype's own button data objects). Four styles, as shared `theme.py` tokens:

- **`BTN_PRIMARY`** — solid blue accent (`#6b86ff`) + dark navy text (`#0b1024`) + a soft
  accent glow (`shadow-[0_4px_14px_-4px_rgba(107,134,255,.6)]`).
- **`BTN`** (secondary) — dark fill + faint hairline border + body text.
- **`BTN_DANGER`** (new) — ghost/outlined: red tint (`/13`) + red border (`/40`) + red text.
- **`BTN_DANGER_SOLID`** (new) — full red fill (`#e5595b`) + white text + red glow, for the
  Terminate / driver STOP stops only.
- The legacy **`BTN_3D` / `BTN_3D_DANGER`** names are kept as **aliases** → flat primary /
  ghost danger, so single-primary-action pages (Scanner Run scan, Swing Scan, Expected-Move
  Draw, Rescue Apply, Manuals Open) flatten with no per-site edit.
- **Per-site hierarchy fixes** (the mock uses one primary per group, the rest secondary):
  Paper Trades (Reload/Close → secondary, Analyze → primary, Delete → ghost), Captured
  (Reload → secondary, Refresh-marks → primary), Paper Portfolio (Reload/Run-entry →
  secondary, Run-manage → primary, Reset → ghost), Gamma (Refresh-now → primary,
  Explain/Analyze/Briefings → secondary), EOD (Generate → primary, Open-files → secondary),
  Driver (Refresh → secondary, Run-now → primary, STOP → **solid** danger), Terminate
  (Stop-all → **solid** danger). Calculator/Simulator already had the right split.
- The `bg-[#hex]/opacity` / `shadow-[…rgba…]` arbitraries **JIT-generate** (verified live).
  Token tests updated (BTN_3D no longer a gradient); **723 webgui tests green**; live-verified
  all four styles render (primary glow, ghost tint/border, solid `#e5595b`).

Source: the "Deep Slate" design handoff (`design_handoff_schwab_trading_redesign/` —
`README.md` token spec + `Schwab Trading.dc.html` 22-screen mock). A full visual
refresh of the app into a dense, business-modern trading terminal: deep charcoal-blue
chrome, a single blue accent (`#6b86ff`), semantic red/green P&L, IBM Plex type with
tabular-nums, bordered card tables. **No IA / feature / data change — pure visual.**

## Guiding principle

The app already has the plumbing this redesign needs: `config/theme.toml` → `theme.py`
Tailwind token vocabulary + app-wide typography/menu injection, and a Tailwind-first
component layer (`test_no_inline_style.py` guard). So Deep Slate is adopted as
**config + targeted polish, not a rewrite**. The prototype's inline-styled markup is a
*spec to translate into tokens*, never copied.

**Out of scope (user decision, 2026-07-11):** all data-viz **charts stay untouched** —
Highcharts option dicts for the Gamma heatmap/surface, Simulator replay panels, Expected
Move cone, Sector-Rotation RRG, Portfolio equity curve. The redesign shows these as
empty-states anyway. Also out of scope (unchanged by design): EOD/Analyze standalone
report documents, and the data-driven table-cell zone maps (score/heat/P&L).

---

## Token map — Deep Slate spec → this codebase

Every color knob below lives in `config/theme.toml`; `theme.py:build_tokens` turns it
into the `.classes()` token used app-wide. **Hairline borders** in the spec are
white-alpha (`rgba(255,255,255,0.06)`); the token layer builds Tailwind arbitrary
classes which we keep **hex-only** (the bundled JIT is unreliable with `rgba()`/`var()`
in a class), so they are adopted as **solid-hex approximations**.

| Deep Slate token | Hex (spec) | `theme.toml` knob | Consumed by |
|---|---|---|---|
| App background | `#0c1020` | `[palette].page_bg2` | `PAGE` |
| Sidebar / rail | `#0a0e1c` | `[palette].page_bg3` (edge) · `[menu].drawer_bg` (Ph2) | `PAGE`, nav |
| Top bar / raised panel | `#111731` | `[palette].page_bg1` (top glow) | `PAGE` |
| Card surface | `#0f1428` | `[palette].card_bg` | `CARD` |
| Table header / inset | `#141a30` | `[palette].input_bg` | `STRATEGY_BTN`, q-field |
| Hairline border | `rgba(255,255,255,.06)` | `[palette].card_border` = `#1c2340` (approx) | `CARD` |
| Page frame border | `rgba(255,255,255,.06)` | `[palette].page_border` = `#1b2138` (approx) | `PAGE` |
| Text — primary | `#eef1f6` | `[palette].title` / `input_text` | `LABEL` |
| Text — body | `#c7cee6` | `[palette].text` | `PAGE` base |
| Text — muted | `#8891ab` | `[palette].muted` | `MUTED` |
| Text — faint | `#6d76a0` | `[palette].icon` | `EYEBROW`, icons, inactive tabs |
| Blue accent | `#6b86ff` | `[palette].primary` · `focus` · `[buttons_3d].blue_mid` | `BTN_PRIMARY`, focus ring, `BTN_3D` |
| Primary hover | (deeper) | `[palette].primary_hover` = `#5877f2` | `BTN_PRIMARY` |
| Secondary button | `#141a30`-ish | `[palette].btn_bg` `#171e39` / `btn_hover` `#20284a` / `btn_border` `#2a3358` | `BTN` |
| Positive (text) | `#4dd6a0` | `[semantic].positive` · `[charts].green` | `TXT_POS`, Sentiment |
| Negative (text) | `#f08183` | `[semantic].negative` · `[charts].red` | `TXT_NEG`, Sentiment |
| Warning (amber) | `#e6b45a` | `[semantic].warning` · `[charts].yellow` · `[gauge].mid` | `TXT_WARN`, gauge |
| Neutral | `#9ba4c4` | `[semantic].neutral` · `[charts].flat` `#8891ab` | `TXT_NEUTRAL` |
| Gauge low / high | `#e5595b` / `#35c281` | `[gauge].low` / `high` | `pages/gauge.py` |
| "Improving" RRG accent | `#6b86ff` | `[charts].cyan` | Sentiment / Rotation |
| Danger 3D button | `#e5595b` fam | `[buttons_3d].red_*` | `BTN_3D_DANGER` |
| UI font | IBM Plex Sans | `[typography].family` + `font_url` | app-wide (`main._layout`) |
| Tabular numerics | tabular-nums | `[typography].numeric = "tabular"` | app-wide body |

---

## Phase 1 — Palette + typography via config (✅ SHIPPED 2026-07-11)

Pure config + a small config-driven font-load hook. **No component/page edits.**

- `config/theme.toml` — `[palette] [semantic] [buttons_3d] [gauge] [charts]` swapped to
  the Deep Slate values above; `[typography]` set `family = 'IBM Plex Sans'…`,
  `numeric = "tabular"`, and a new `font_url` = the IBM Plex (Sans + Mono) Google Fonts
  URL.
- `webgui/pages/options/theme.py` — two new `[typography]` knobs (`font_url`, `numeric`,
  both default `""` = stock render); `build_typography_css` emits
  `body{font-variant-numeric:tabular-nums}` when `numeric` is set; new
  `build_font_head_html` → the preconnect + `<link>` for `font_url` (or `""`); exported
  as `FONT_HEAD_HTML`.
- `webgui/main.py:_layout` — injects `FONT_HEAD_HTML` via `ui.add_head_html` so the web
  font actually loads.
- Tests: `test_theme.py` gains `font_url` + `numeric` coverage; three brittle tests that
  pinned the OLD default hexes onto the live-config tokens (`CARD`, `TXT_*`,
  `BTN_3D_DANGER`) were rewritten to assert the token **mapping against freshly-built
  defaults** (re-theme-safe). Same brittleness fixed in `test_sentiment.py` (9 tests →
  reference the module's config-derived `S.TXT_*`/`S.BG_*`) and `test_gauge.py` (warm-tone
  middle band, palette-relative). **716 webgui tests green.**

**Apply:** restart the webgui (More → System Status → Restart on the "Web GUI" card, then
hard-refresh). The whole content area — cards, buttons, gauges, tables, Sentiment/
Rotation value colors — moves to Deep Slate + IBM Plex at once. This is the ~70%-of-the-
visual-delta spike; evaluate it before committing to Phases 2–5.

**Known limitation carried into later phases:** the app **shell** (`[menu]`) is left at
the stock look this phase — see Phase 2.

---

## Phase 2 — App shell (header + sidebar + tab strip) (✅ SHIPPED 2026-07-11)

Full match to the mockup shell. What shipped:

- **Accent/header decouple.** New `[menu].header_bg` knob → `build_nav_css` emits
  `.q-header{background:…}`, so `[menu].accent` (`ui.colors(primary)`) drives the active
  pill/tab + Quasar controls while the header bar stays dark `#111731`. `[menu]` set to
  `accent=#6b86ff`, `header_bg=#111731`, `drawer_bg=#0a0e1c`, `text=#98a1c0`,
  `hover_bg=rgba(107,134,255,.08)`, `title=#565f7d`.
- **Header** (`main._layout`): brand tile (blue-gradient + line-chart glyph) + wordmark;
  right side = `{Section} · {Tab}` breadcrumb (`breadcrumb_parts`) + a green **MARKET
  OPEN** / muted **MARKET CLOSED** pill driven by the real `alerts.in_market_hours`
  gate (`market_status_parts`). Menu toggle + help fab kept.
- **Rail**: "WORKSPACE" caption; nav items now render a **dot** (blue `#6b86ff` when
  active, faint `#3c4560` when not) instead of an icon, with the active item a subtle
  navy-tint pill (`rgba(107,134,255,.13)`) + light label; **Session P&L** pinned at the
  bottom (defensive read of `cache:options:paper_account`, sign-colored via
  `session_pnl_parts`).
- **Tab strip**: converted from folder-tabs to Deep Slate **pill-in-container** — a
  `#111731` rounded container with the active pill a `rgba(107,134,255,.16)` tint /
  `#dbe2ff`; subtabs the same one size smaller on a `#0f1428` inset.
- Pure helpers (`breadcrumb_parts` / `market_status_parts` / `session_pnl_parts`) +
  `[menu].header_bg` are unit-tested; **720 webgui tests green**; live-verified end-to-end
  (header `#111731`, rail `#0a0e1c`, active dot `#6b86ff`, breadcrumb "Options · Gamma",
  pill tabs, MARKET pill following the hours gate).

### Notes / follow-ups from the shell work

- The `icon` arg on the nav builders is retained but no longer rendered (dots replaced
  icons per the mock). The prior per-page favicon colors (`_TAB_COLOR`) are unaffected.
- Bottom **ticker** footer left as-is (already a dark Deep-Slate-compatible marquee) —
  restyle to the mock's amber-dot + right-side sentiment is Phase 5 polish.

### Original design note (accent/header coupling — now resolved above)

- **Accent/header coupling.** `[menu].accent` feeds `ui.colors(primary=…)`, which recolors
  BOTH the header bar AND the active nav pill (both ride the Quasar primary). Deep Slate
  wants a **dark** header (`#111731`) with blue ONLY on the active pill — so we cannot just
  set `accent` to the blue (that paints the whole header blue, the exact "flat bright-blue
  header" the redesign moves away from). Fix: give the header its own dark bg via CSS
  (`.q-header`/the header container) and keep `.nav-active`'s blue pill via `--q-primary`,
  decoupling the two. Small, contained code change in `main.py` + `_NAV_CSS`.
- Set `[menu].drawer_bg = #0a0e1c` (rail), `text`/`title` to the muted tiers, `hover_bg`
  to `rgba(107,134,255,.10)` (CSS path — rgba is fine here, it's raw CSS not a class).
- Sidebar item states: active `rgba(107,134,255,.13)` bg + blue dot + white label;
  inactive faint dot `#3c4560` + `#98a1c0` label. Bottom "Session P&L" stat.
- Tab strip: `#111731` rounded-12 pill container; active pill `rgba(107,134,255,.16)` /
  `#dbe2ff` / 600. Maps onto `.compact-tabs` / `.compact-subtabs` (already the app's tab
  chrome).
- Top bar: brand tile (blue gradient + line-chart glyph), wordmark, breadcrumb
  `{Section} · {Tab}`, green "MARKET OPEN" pill. Bottom ticker restyle (already exists —
  `pages/ticker.py`).

Deliverable: `[menu]` populated + header-bg decouple. Guarded so all-default `[menu]`
still emits nothing (existing `test_menu_defaults_emit_no_rules`).

---

## Phase 3 — Density + flat tiles (✅ DENSITY + TILES SHIPPED 2026-07-11)

- **Type scale (config).** `config/theme.toml [typography]` dropped to the Deep Slate scale:
  titles 20→**16**, subtitles 16→**14**, sections 14→**13**, body 14→**13**, small 12→**11**.
  This cascades density app-wide via `build_typography_css` (no per-page edits). Live-verified
  body 13px / title 16px.
- **Table headers (`main._TABLE_CSS`).** The global sticky header is now the Deep Slate inset
  `#141a30` with **uppercase, faint (`#6d76a0`), 10.5px / .06em** column labels, plus faint
  `rgba(255,255,255,.04)` row dividers. The per-page sticky-header `#1d1d1d` colors
  (captured/paper/rescue/driver/calculator) were aligned to `#141a30`. Live-verified.
- **Flat tiles.** `theme.TILE_3D` was flattened from the raised 3D bevel to
  `rounded-[12px] border border-[<card_border>] shadow-none` (still additive — tiles keep their
  own bg/color). Affects the Calculator summary tiles, the shared detail-panel metric tiles, and
  the Sentiment signal tiles. The name is kept (legacy call sites). Live-verified on a q-card:
  12px radius + hairline border + **no shadow** (overrides the Quasar card bevel). **723 tests green.**

### Remaining in Phase 3 — IBM Plex **Mono** numerics (planned)

Deep Slate is tighter (done) and also puts **every number in IBM Plex Mono**, not just tabular-nums.

- **Density.** Spec type scale is smaller (screen title 16/600, table cell 12.5, column
  header 10.5 uppercase `.07em`, row padding `8px 15px`). Nudge `[typography]` sizes down
  and tighten table cell padding (`_TABLE_CSS`) — do this **screen-batch by screen-batch**
  and screenshot each, since global size drops can crowd dense tables. (Deliberately NOT
  done in Phase 1 to keep the palette spike zero-risk.)
- **Mono numerics.** Phase 1 gives tabular-nums on the UI font. Full Deep Slate wants
  numbers/IDs/timestamps/tickers/cache-keys in IBM Plex **Mono**. That's a per-component
  rollout (a `.num`-equivalent token, e.g. `NUM = "font-['IBM_Plex_Mono'] tabular-nums"`,
  applied to numeric `ui.table` columns + stat values). Sweep by screen using the column
  inventory in `trading-data.js`. Add the token to `theme.py`; no new config needed.

---

## Phase 4 — Semantic badge system (✅ SHIPPED 2026-07-11)

Shared badge tokens + wired the four badge surfaces the mock shows as pills.

- **Shared tokens** (`theme.py:build_tokens`, from the semantic palette):
  `BADGE_POS` / `BADGE_WARN` / `BADGE_NEG` = `bg-[<hex>]/15 text-[<hex>] rounded-[6px]`
  (translucent tint + colored fg), plus `BADGE_ACCENT` (OPEN, `#a9b6ff`) and
  `BADGE_MUTED` (closed, `bg-white/5 text-[#8891ab]`). The `bg-[#hex]/opacity` +
  `text-[#hex]` form **JIT-generates** (verified live — the earlier `var(...)` caveat does
  not apply to hex/opacity or rgba). Exported as module constants.
- **Wired surfaces:** Captured **Rec** (`rec_class` → tint tokens, dropped the old solid-
  bright + `text-[#111]` dark-text); Rescue **Heat** (`heat_bg_class` → POS/WARN/NEG by the
  `<25 / <50 / ≥50` zones, dropped `text-[#111]`); Paper **Status** (new
  `status_badge_class` + a `body-cell-status` slot — OPEN blue-accent / closed grey); Fills
  **Side** (new `side_badge_class` + a `body-cell-side` slot — SELL green / BUY red).
- Swing **Bias** and Rescue **State** stay colored **text** (the mock shows them as text,
  not pills); alert **count** badges stay red (a live-alert signal, not a semantic pill).
- Tests: badge tokens + `status_badge_class` / `side_badge_class` unit-tested; the five
  brittle tests pinning old literal classes rewritten to reference the tokens.
  **723 webgui tests green**; JIT-verified all five tokens render as translucent pills
  (exact fg hex + 15% tint + 6px radius).

### Original spec (for reference)

Deep Slate specifies exact badge fills (bg/fg) that the current app rendered more plainly:

- HOLD `rgba(224,162,60,.15)`/`#e6b45a` · CUT `rgba(229,89,91,.16)`/`#f08183` ·
  TAKE_PROFIT `rgba(53,194,129,.15)`/`#4dd6a0` · OPEN `rgba(107,134,255,.14)`/`#a9b6ff` ·
  EXPIRED `rgba(255,255,255,.05)`/`#8891ab` · SELL `…green` / BUY `…red` · nav/tab badge
  `rgba(107,134,255,.18)`/`#a9b6ff`.
- These are **data-driven table-cell colors** — the existing pattern maps a finite state
  → a fixed Tailwind class (`rec_class`/`verdict_class`/`pnl_class`/status maps in
  `scanner.py`/`paper.py`/`captured.py`/`rescue.py`/`driver.py`). Extend those local maps
  to the Deep Slate badge fills (pill radius 6px, `TILE_3D`-free flat pills). Keep them as
  fixed classes (not config) per the Tailwind-first rule — these are semantic-state maps,
  not palette knobs.

---

## Phase 5 — Per-screen polish sweep

Screen-by-screen fidelity pass against the mock (`Schwab Trading.dc.html`) + README §
"Sections & Screens". For each: match card radius (14) / padding, column set, empty-state
copy, right-rail detail panels, footer count/last-update bars. Batches (mirror the nav):

1. **Options** (10 screens) — Scanner (0-DTE/Swing segmented + right Trade-detail rail),
   Paper Trades, Captured Signals (grade dot + sparkline), Paper Portfolio (7 stat cards +
   fills log), Calculator (3-col form/rail/results), Swing Scanner (inferred-view line),
   Gamma (inner sub-tabs + chart empty-state — **chart untouched**), Simulator (Replay/
   What-if/IV-shock + scrub — **charts untouched**), Expected Move (**chart untouched**),
   Rescue (heat/state badges).
2. **Sentiment** — dual gauges (CSS-conic option vs keep the Highcharts gauge — decide;
   the Highcharts gauge already follows `[gauge]` so **default = keep it**), Signals 2×2
   tiles, sector table, Sector Rotation quadrant table + FROM/INTO lists (RRG scatter
   untouched).
3. **Market Dashboard** — 150px quote tiles, sign-tinted; MAG7 composite tile. Already
   close; align tile bg/border to spec (`rgba(53,194,129,.09)` bg / `.25` border).
4. **Trade Analyzer** — 3-card verdict row (Position/Investor/Markov) + 4 stat cards;
   band-probability strip (CSS gradient — buildable, not a chart).
5. **Portfolio** — Holdings/Sectors/Performance (equity curve untouched).
6. **Claude Trades** — status banner, red Day-P&L progress bar, scorecard tables.
7. **More** — EOD, System Status (freshness rows), Settings (toggles 34×20), Manuals,
   Terminate (red-tinted warning card).

Each batch: translate to tokens, keep zero inline styles (`test_no_inline_style.py`),
screenshot-verify, tests green.

---

## Sequencing & risk

- **Ship order:** Phase 1 (done) → 2 (shell) → 4 (badges, cheap high-impact) → 3 (density,
  needs care) → 5 (polish sweep). 3 before 5 if density is wanted globally first.
- **Lowest risk:** 1, 4. **Highest care:** 3 (global size changes can crowd tables) and the
  Phase-2 header decouple.
- **Reversibility:** Phase 1 is a single `config/theme.toml` edit — revert by restoring the
  prior hexes (git). Everything downstream is additive token work behind the existing guard
  tests.

# Social Cards Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One grid-safe 1200×630 card (drawn 2×) for every blog entry and every posted trade idea, shown on the site and gathered on a private Social Cards page that absorbs Post to X.

**Architecture:** A shared Pillow drawing kit (`shared/card_draw.py`) serves two renderers, one per service: `options_svc/trade_idea_card.py` (rewritten layout) and a new `blog_svc/card.py`. Each service writes PNG + JPEG into the served site tree and publishes a small cache view of its cards; the Tier-1 page merges the two views, serves the files through one private route that reads only paths a view names, and prefills the absorbed X composer.

**Tech Stack:** Python 3.11, Pillow 13 (already in `requirements.lock`), NiceGUI, Redis via `shared.bus`, pytest.

**Spec:** [`docs/plans/2026-11-08-social-cards-design.md`](2026-11-08-social-cards-design.md) — read it first; this plan argues from it.

## Global Constraints

- Canvas: layout grid 1200×630, drawn at `SCALE = 2`, so every file is **2400×1260**.
- Instagram grid strip: layout x **363.75 – 836.25**, full height. Essentials inside it; nothing essential outside it.
- Blog card palette (Nocturne): ground `#161826`, text `#e9e9ed`, accent `#9184d9`, muted `#9a9bb0`.
- Trade idea card palette: `card_kit`'s `BG`/`CARD`/`EDGE`/`TITLE`/`MUTED`/`TONES`, brand blue `(107, 134, 255)`, chevrons `(238, 241, 246)`.
- Face: Inter from `deploy/site/assets/inter-latin.woff2`, weights by name; on failure fall back, never raise.
- **Never raise on a post path.** A card that fails: the trade idea posts text-only as today; the blog entry publishes without a card.
- Every tunable goes in `config/social_cards.toml` with a `webgui/config_schema.py` entry (STANDING RULE).
- Tier 1 draws nothing and imports no Pillow. `shared.social_cards` joins the Tier-1 allow-list (stdlib + `repo_paths` + `shared.config_toml` only).
- Generated site files stay gitignored (`deploy/site/blog/`, `deploy/site/ideas/`).
- Past posts get cards for the site but are **not listed** on Social Cards: a card is listed only when its post time is on or after `[cards] listed_from`.
- No `.style()`, no inline `style=`, no raw `ui.button`/`ui.dialog`/`ui.notify`/`ui.table` on the page (`test_no_inline_style.py`, `test_ui_kit_guard.py`).

## Review Focus

1. **A blog title of 120+ characters, or one unbroken 40-character word** — must shrink, then end in "…", never spill out of the strip or raise. (Task 5 test.)
2. **A trade idea whose structure has 1, 2 or 4 legs, or a debit (negative `entry_cash`)** — the left wing must lay out every count without overlapping the breakeven tile. (Task 3 test.)
3. **A request to the private image route for a path no view names** (`../`, an absolute path, a real site file such as `index.html`) — must 404. (Task 7 test.)
4. **The site publish switch off (`[site] trade_ideas = false`)** — trade ideas still post with the new card; Social Cards shows no idea rows and says why. (Tasks 4 and 7 tests.)
5. **An unpublished blog entry** — its folder, including `card.png`, `card.jpg`, `card.json`, is removed; today's `_remove_entry` removes only two files and would leave the folder. (Task 6 test.)

---

### Task 1: Shared drawing kit, with `trade_idea_card` moved onto it

**Files:**
- Create: `shared/card_draw.py`
- Modify: `services/options_svc/trade_idea_card.py` (delete its `font`, `_s`, `_box`, `_Canvas`, `_mix`, `_mark`; import them)
- Test: `shared/tests/test_card_draw.py`; existing `services/options_svc/tests/test_trade_idea.py` must stay green

**Interfaces:**
- Produces:
  - `SCALE = 2`, `WIDTH = 1200`, `HEIGHT = 630`, `GRID_STRIP = (363.75, 836.25)`
  - `font(size: float, weight: str = "regular")` — Inter at `round(size*SCALE)` px; `ImageFont.load_default(size=px)` on any failure
  - `class Canvas(im)` with `text(x, y, s, size, weight="regular", fill=(255,255,255), anchor="la", spacing=0.0) -> float`, `width(s, size, weight="regular", spacing=0.0) -> float`, `rrect(x0, y0, x1, y1, r, fill=None, outline=None, width=1)`, `line(pts, fill, width=1)`, `pill(x, y, s, size, fg, ground, weight="bold", pad_x=9, h=24, spacing=1.2) -> float`, and `drawn: list[tuple[str, float, float, float, float]]` — every `text` call appends `(s, x0, y0, x1, y1)` in layout px
  - `mix(a, b, t) -> tuple`
  - `mark(c: Canvas, x, y, size, *, rule: tuple, chevrons: tuple)` — THE FLIP from the 64-unit geometry
  - `new_image(ground: tuple, width=WIDTH, height=HEIGHT)` → RGB image at `width*SCALE × height*SCALE`
  - `png_bytes(im) -> bytes` (optimize=True); `jpeg_from_png(png: bytes, quality: int) -> bytes | None` (RGB, `progressive=True`; None on failure)
  - `in_strip(x0: float, x1: float) -> bool`

- [ ] **Step 1: Write the failing tests** — `test_new_image_is_2400_by_1260`, `test_text_records_its_box` (`c.text(400, 100, "SPY", 96, "extrabold")` → `c.drawn[-1][0] == "SPY"` and `x0 == 400`, `x1 > 400`), `test_in_strip_bounds` (`in_strip(364, 836)` True; `in_strip(363, 500)` False; `in_strip(500, 837)` False), `test_jpeg_from_png_round_trips` (JPEG opens at the PNG's size), `test_jpeg_from_png_bad_bytes_is_none`, `test_font_falls_back_without_inter` (monkeypatch the Inter path to a missing file; `font(20)` still returns a font).
- [ ] **Step 2: Run** `.venv/Scripts/python -m pytest shared/tests/test_card_draw.py -q` — FAIL (no module).
- [ ] **Step 3: Implement `shared/card_draw.py`** by moving the bodies from `trade_idea_card.py`; `pill` takes `ground` instead of reading `K.CARD`. Imports: stdlib, `functools`, `io`, `pathlib`, `repo_paths`, PIL (lazily inside functions, as today).
- [ ] **Step 4: Repoint `trade_idea_card.py`** to the kit (no layout change in this task; `WIDTH, HEIGHT` stay 1200, 675 here).
- [ ] **Step 5: Run** `shared/tests/test_card_draw.py` and `services/options_svc/tests/test_trade_idea.py` — PASS; render the SPY iron condor fixture and confirm by eye it is unchanged.
- [ ] **Step 6: Commit** `refactor(cards): shared drawing kit for the card renderers`.

### Task 2: Config and the shared Social Cards contract

**Files:**
- Create: `config/social_cards.toml`, `shared/social_cards.py`
- Modify: `webgui/config_schema.py`
- Test: `shared/tests/test_social_cards.py`

**Interfaces:**
- Produces (`shared/social_cards.py`, stdlib + `repo_paths` + `shared.config_toml` only):
  - `VIEW_BLOG = "blog:social_cards"`, `VIEW_IDEAS = "options:social_cards"`
  - `config() -> dict`, defaults: `{"cards": {"jpeg_quality": 90, "listed_from": "2026-11-09"}, "blog": {"title_max_px": 50, "title_min_px": 34, "title_max_lines": 4, "summary_px": 20, "summary_max_lines": 3}, "caption": {"max_hashtags": 4}}`
  - `blog_key(slug) -> str` (`"blog:<slug>"`), `idea_key(day: str, stem: str) -> str` (`"idea:<day>-<stem>"`), `KEY_RE`
  - `listed(at_iso: str, cfg: dict | None = None) -> bool` — the post's Central date ≥ `listed_from`; False for an unparseable time
  - `row(key, kind, at, title, caption, link, png, jpg) -> dict` with exactly those keys plus `listed`
  - `hashtag(tag: str) -> str | None` — `#` + alphanumerics of the tag (`"0-DTE"` → `"#0DTE"`), None when empty
  - `blog_caption(title, summary, address, tags, max_hashtags) -> str` — lines: title; summary (omitted when blank); `Read it at <address>`; hashtags space-joined (omitted when none)
  - `merged(blog_payload, ideas_payload) -> list[dict]` — rows from both `{"cards": [...]}` payloads with `listed` true, newest `at` first; malformed rows dropped
  - `read_card(site_root, rel_path: str, rows: list[dict]) -> bytes | None` — only a `png`/`jpg` value of a row in `rows`, resolved under `site_root`, a regular non-symlink file ending `.png`/`.jpg`

- [ ] **Step 1: Write the failing tests** — `test_defaults_without_file`, `test_listed_on_and_after_cutoff` (`"2026-11-09T08:40:00-06:00"` True, `"2026-11-08T15:00:00-06:00"` False, `"garbage"` False), `test_hashtag_strips_punctuation`, `test_blog_caption_lines` (exact string for title "T", summary "S", address "https://neuralstrike.co/blog/t/", tags ["Gamma", "0-DTE"] → `"T\n\nS\n\nRead it at https://neuralstrike.co/blog/t/\n\n#Gamma #0DTE"`), `test_blog_caption_no_summary_no_tags`, `test_merged_newest_first_listed_only`, `test_read_card_refuses_unlisted_paths` (`"../x.png"`, `"index.html"`, an absolute path, a path not in rows → None; a listed real file → its bytes), `test_import_set` (module imports only the allowed set, as `test_public_scan.py` pins its own).
- [ ] **Step 2: Run** `.venv/Scripts/python -m pytest shared/tests/test_social_cards.py -q` — FAIL.
- [ ] **Step 3: Implement** `config/social_cards.toml` (the defaults above, commented) and `shared/social_cards.py` through `config_toml.toml_loader`.
- [ ] **Step 4: Add the six keys** to `webgui/config_schema.py`.
- [ ] **Step 5: Run** the new test and `webgui/tests/test_config_schema.py` — PASS.
- [ ] **Step 6: Commit** `feat(social cards): config and the shared contract`.

### Task 3: The trade idea card, 1200×630 and grid-safe

**Files:**
- Modify: `services/options_svc/trade_idea_card.py`
- Test: `services/options_svc/tests/test_trade_idea_card.py` (new); update the size assertion at `test_trade_idea.py:362`

**Interfaces:**
- Consumes: Task 1's kit.
- Produces: `render_trade_idea_png(idea, *, now=None, footer="neuralstrike.co") -> bytes | None` (same signature), `WIDTH, HEIGHT = 1200, 630`; `CARD_FIELDS: tuple[str, ...]` = `("symbol", "type", "label", "bias", "legs", "expiration", "dte", "spot", "grade", "score", "pop_pct", "entry_cash", "max_profit", "max_loss", "breakevens", "em")`; `card_facts(idea) -> dict` (those keys only); `layout(idea, now) -> Canvas` (draws and returns the canvas, so tests read `drawn`).

Geometry (layout px), from the approved mockup:
- Header: lockup at (44, 34), mark 30; `TRADE IDEA` centred at y 40 in brand blue; `<DAY MON D · HH:MM CT>` right-aligned at x 1156.
- Middle (x 384–816): ticker centred, 96 extrabold, top 82; `<label> · $<spot>` centred, 26 medium, top 186; bias pill centred at 226; payoff tile 384–816 × 268–448 (existing `_chart` drawn into it); grade tile 384–816 × 460–544.
- Left wing (x 44–344): `LEGS` at 104; leg rows 38 tall from 132, step 46 (side pill, `1×`, strike, PUT/CALL right-aligned); expiry · DTE under the last row; breakeven tile 44–344 × 440–532.
- Right wing (x 856–1156): max risk 104–234, max profit 250–380, probability of profit 396–532; values 44 bold.
- Foot at y 572: the commissions note left, `footer` right.

- [ ] **Step 1: Write the failing tests**
  - `test_png_is_2400_by_1260`
  - `test_essentials_inside_the_strip`: for the SPY IC fixture, every `drawn` entry whose text is the ticker, the `label · $spot` line, the grade word or the score lies `in_strip`
  - `test_wings_hold_the_rest`: the max risk, max profit, POP and breakeven figures are drawn with `x0 >= 856` or `x1 <= 344`
  - `test_every_figure_today_is_still_printed`: the set of texts drawn includes each leg strike, `$378`, `$122`, `78%`, `738.78 / 776.22`, `Good`, `70`
  - `test_leg_counts_fit` (parametrised over 1, 2 and 4 legs and a debit long call): no leg row's box overlaps the breakeven tile (y1 of the last row + expiry line < 440)
  - `test_card_facts_render_identically`: `render(idea) == render(card_facts(idea))`, byte for byte, at a fixed `now`
- [ ] **Step 2: Run** `.venv/Scripts/python -m pytest services/options_svc/tests/test_trade_idea_card.py -q` — FAIL.
- [ ] **Step 3: Rewrite** `_header`, `_left`, `_stats`, `_grade`, `_render` to the geometry above; `_chart` keeps its maths and draws into the given box.
- [ ] **Step 4: Run** both test files — PASS. Render the four fixtures (IC, PCS, long put, long call) to the scratchpad and look at each; then crop each to the strip and look again.
- [ ] **Step 5: Commit** `feat(trade idea): the 1200x630 grid-safe card`.

### Task 4: Trade idea cards on the site, and the ideas view

**Files:**
- Modify: `services/options_svc/site_ideas.py`, `services/options_svc/handlers.py` (`_publish_trade_idea_site`, the results refresh near line 3159, service start), `deploy/site/assets/ideas.js`, `deploy/site/assets/site.css`
- Test: `services/options_svc/tests/test_site_ideas.py`, `deploy/tests/test_site.py`

**Interfaces:**
- Consumes: `card_draw.jpeg_from_png`, `social_cards.{config, idea_key, row, listed, VIEW_IDEAS}`, `trade_idea_card.card_facts`.
- Produces:
  - manifest row gains `"jpg"` (`ideas/<day>/<stem>.jpg`), `"shape": "wide"`, `"card": card_facts(idea)`; `_REF_RE` accepts `jpg`
  - `site_ideas.card_rows(manifest) -> list[dict]` — one `social_cards.row` per idea with a `jpg`: key `idea_key(day, stem)`, kind `"idea"`, at = the post time, title `"<symbol> <label>"`, caption = `alt`, link `https://<SITE_HOST>/ideas.html`, png = `full`, jpg = `jpg`
  - `handlers.publish_idea_cards(bus) -> None` — reads the manifest, `cache_set("cache:options:social_cards", {"cards": rows, "site_enabled": bool}, event=..., skip_unchanged=True)`; never raises

- [ ] **Step 1: Write the failing tests** — `test_publish_writes_jpeg_beside_png`, `test_manifest_row_has_jpg_shape_and_card`, `test_ref_re_accepts_jpg`, `test_card_rows_from_manifest` (exact row for the fixture), `test_card_rows_skip_old_rows_without_jpg`, `test_publish_idea_cards_with_site_off` (`site_enabled` False, `cards` empty), and in `deploy/tests/test_site.py` `test_idea_card_shape_follows_the_row` (ideas.js sets `height = 630` and a `ns-card-wide` class when `shape == "wide"`, else 675; site.css has `.ns-idea-card img.ns-card-wide { aspect-ratio: 40 / 21 }`).
- [ ] **Step 2: Run** `.venv/Scripts/python -m pytest services/options_svc/tests/test_site_ideas.py -q` and `deploy/tests/test_site.py` — FAIL.
- [ ] **Step 3: Implement** in `site_ideas._publish` (JPEG via `jpeg_from_png(png, config()["cards"]["jpeg_quality"])`; a failed JPEG leaves `jpg` out, the post still lands), `card_rows`, `publish_idea_cards`; call it after `_publish_trade_idea_site`, after each results refresh, and once at service start.
- [ ] **Step 4: Update** `ideas.js` `card()` and `site.css` as tested.
- [ ] **Step 5: Run** the suites — PASS. Run `services/options_svc` suite whole: compare the failing set with before.
- [ ] **Step 6: Commit** `feat(trade idea): JPEG, card facts and the ideas card view`.

### Task 5: The blog card renderer

**Files:**
- Create: `services/blog_svc/card.py`
- Test: `services/blog_svc/tests/test_card.py`

**Interfaces:**
- Consumes: Task 1's kit, `social_cards.config()`.
- Produces: `RENDER_VERSION = 1`; `digest(entry: dict) -> str` (sha256 of `RENDER_VERSION`, title, summary, tags, `published_at`); `layout(entry: dict) -> Canvas`; `render_blog_card(entry: dict) -> bytes | None` (PNG; never raises); `split_title(title: str) -> tuple[str, str]` (accent on the second half, split at the word boundary nearest the middle by character count; one word → all plain).

Geometry (layout px), from the approved mockup: lockup centred at y 62 (mark 50, `NEURALSTRIKE` 22 medium, tracking .2em); `BLOG · <MON D, YYYY>` centred at 140, 17 semibold, accent; title centred in x 384–816 from y 184, `title_max_px` down to `title_min_px` in steps of 2 until it fits `title_max_lines`, then cut with "…"; summary centred in 384–816 from 24px under the title, `summary_px`, up to `summary_max_lines` with "…"; wings: the mark at 560 and 7% over the ground at x −250 and x 890 (cut by the edges), `neuralstrike.co/blog` at (60, 568) muted, tags as outlined pills right-aligned to x 1140 at y 560.

- [ ] **Step 1: Write the failing tests**
  - `test_png_is_2400_by_1260`
  - `test_title_and_summary_inside_the_strip` (every drawn line of both)
  - `test_long_title_shrinks_then_ellipsis` (a 160-character title → size `title_min_px`, ≤ `title_max_lines` lines, last ends "…", all in strip)
  - `test_unbroken_long_word_is_cut_not_spilled` (a 40-character word)
  - `test_no_summary_no_tags` (nothing drawn for them; no raise)
  - `test_split_title` (`"Why the gamma flip moved forty points in a day"` → `("Why the gamma flip moved", "forty points in a day")`; `"Charm"` → `("Charm", "")`)
  - `test_digest_moves_with_each_field_and_version`
  - `test_render_never_raises` (entry `{}` → None or bytes, no exception)
- [ ] **Step 2: Run** from the repo root `.venv/Scripts/python -m pytest services/blog_svc/tests/test_card.py -q` — FAIL.
- [ ] **Step 3: Implement** `services/blog_svc/card.py`.
- [ ] **Step 4: Run** — PASS. Render the three mockup titles to the scratchpad, look at each whole and cropped to the strip.
- [ ] **Step 5: Commit** `feat(blog): the blog card renderer`.

### Task 6: Blog cards on the site, and the blog view

**Files:**
- Modify: `services/blog_svc/sitewriter.py`, `services/blog_svc/handlers.py` (`publish_views`), `deploy/site/assets/site.css`
- Test: `services/blog_svc/tests/test_sitewriter.py`, `services/blog_svc/tests/test_handlers.py`

**Interfaces:**
- Consumes: `card.render_blog_card`, `card.digest`, `card_draw.jpeg_from_png`, `social_cards.{row, blog_key, blog_caption, config, VIEW_BLOG}`.
- Produces:
  - entry folder files `card.png`, `card.jpg`, `card.json` (`{"digest": ...}`); constants `CARD_PNG, CARD_JPG, CARD_META`
  - `_write_card(folder, row, report) -> bool` — draws only when `card.json`'s digest differs; a failure is `_fail`-reported and the entry still publishes
  - `entry_page(parts, entry, site_host, *, card: bool = False)` — with `card`, adds `og:image` = `https://<host>/blog/<slug>/card.png`, `og:image:width` 2400, `og:image:height` 1260, `og:image:alt` = the title, `twitter:card` `summary_large_image`, and `<img class="ns-blog-card" src="blog/<slug>/card.png" width="1200" height="630" alt="<title>">` above the frame
  - `_remove_entry` also removes the three card files
  - `sitewriter.card_rows(root, store) -> list[dict]` — one `social_cards.row` per entry whose `card.png` and `card.jpg` exist: key `blog_key(slug)`, kind `"blog"`, at `published_at`, title, caption `blog_caption(...)`, link the entry address, png/jpg site-relative
  - `publish_views` also sets `cache:blog:social_cards` = `{"cards": card_rows(...)}`, `skip_unchanged=True`

- [ ] **Step 1: Write the failing tests** — `test_rebuild_writes_card_files`, `test_rebuild_is_idempotent_with_cards` (second run writes 0), `test_changed_title_redraws_card`, `test_card_failure_still_publishes_entry` (monkeypatch `render_blog_card` → None: page written, no `og:image`), `test_entry_page_with_card_has_og_and_img` (exact tag strings; `og:image` absolute; `og:image" content=` matched with its closing quote — the substring trap in `test_site.py`'s history), `test_unpublish_removes_card_files_and_folder`, `test_card_rows_shape`, `test_publish_views_sets_card_view`.
- [ ] **Step 2: Run** each blog test file on its own from the repo root — FAIL.
- [ ] **Step 3: Implement** in `sitewriter.py` (`_write_entry` calls `_write_card` before writing the page and passes `card=` its result or the file's presence) and `handlers.publish_views`; add `.ns-blog-card` (block, full width, `aspect-ratio: 40 / 21`, radius and border from the site tokens) to `site.css`.
- [ ] **Step 4: Run** the `services/blog_svc` suite — compare the failing set with before.
- [ ] **Step 5: Commit** `feat(blog): a card on every entry page and the blog card view`.

### Task 7: The Social Cards page, absorbing Post to X

**Files:**
- Create: `webgui/pages/social_cards.py`
- Modify: `webgui/pages/x_post.py` (becomes the composer builder), `webgui/main.py` (`MORE_CHILDREN`, the nav colour map, `@_page("/social")`, `/x` → redirect, the image route), `webgui/page_help.py`, `webgui/tests/test_shell.py`, `webgui/tests/test_no_inline_style.py`
- Test: `webgui/tests/test_social_cards.py`

**Interfaces:**
- Consumes: `social_cards.{VIEW_BLOG, VIEW_IDEAS, merged, read_card}`, `x_post.command`.
- Produces:
  - `x_post.Composer` with `fill(text: str, link: str, tags: str, image: bytes | None, name: str) -> None`; `x_post.build() -> Composer` draws the composer, preview, confirm and Recent posts exactly as `render()` does today; `render()` is removed
  - `social_cards.render() -> None`: header "Social Cards"; **Cards** section (one row per `merged(...)` card: thumbnail from the route, kind chip, time in Central, title; buttons **Download**, **Copy caption**, **Post to X**); then `x_post.build()`
  - Post to X: reads the PNG through `read_card(repo_paths.SITE_ROOT, row["png"], rows)` off the event loop, calls `composer.fill(row["caption"], row["link"], "", png, ...)`, scrolls to the composer; nothing is sent until the composer's own confirm
  - Empty state: "No cards yet." plus, when the ideas view says `site_enabled` false, "Trade idea cards appear here only while trade ideas are published to the site (Settings → General)."
  - route `GET /social/card/{key}/{fmt}` (`fmt` in `png`, `jpg`): finds the row by `key` in the two views, `read_card`, `Response` with `image/png` / `image/jpeg`, `Content-Disposition: attachment; filename="<key with : as ->.<fmt>"` when `?download=1`; 404 otherwise
  - `MORE_CHILDREN = [("/eod", "EOD Report", "summarize"), ("/blog", "Blog", "article"), ("/social", "Social Cards", "share")]`; `GET /x` → `RedirectResponse("/social", 308)`

- [ ] **Step 1: Write the failing tests** — `test_rows_merge_and_hide_unlisted`, `test_route_serves_listed_png_and_jpg`, `test_route_404s_unknown_key_and_traversal` (Review Focus 3), `test_download_sets_attachment_name`, `test_post_to_x_fills_composer_without_sending` (no `bus_client.request` call until confirm), `test_empty_state_explains_site_switch` (Review Focus 4), `test_x_redirects_to_social`; update `test_shell.py` (route list: `/social` in, `/x` out of the page routes, More strip order) and `test_no_inline_style.py` (`social_cards.py` in the list).
- [ ] **Step 2: Run** `.venv/Scripts/python -m pytest tests/test_social_cards.py tests/test_shell.py -q` from `webgui/` — FAIL.
- [ ] **Step 3: Implement** `x_post.build`/`Composer`, `pages/social_cards.py` (kit only; `watch_view` on both views), the route and the nav changes in `main.py`; move the `/x` `page_help` entry to `/social` and rewrite it for the page.
- [ ] **Step 4: Run** the `webgui` suite whole — compare the failing set with before. Then `tools/ui_harness.py` with both views seeded from the Task 4/6 fixtures: look at the page, press each button, confirm Post to X shows the confirm with the card attached.
- [ ] **Step 5: Commit** `feat(webgui): Social Cards, absorbing Post to X`.

### Task 8: Redraw the past trade ideas on the site

**Files:**
- Create: `tools/redraw_idea_cards.py`
- Test: `tools/tests/test_redraw_idea_cards.py`

**Interfaces:**
- Consumes: the manifest (`site_ideas`), `idea_ledger.site_rows()`, `site_ideas.ledger_key`, `trade_idea.economics`, `trade_idea._CREDIT_BIAS`, `trade_idea_card.render_trade_idea_png`, `card_draw.jpeg_from_png`, `site_ideas.webp_copy`.
- Produces: `facts_for(row: dict, ledger: dict) -> tuple[dict | None, str]` — the `CARD_FIELDS` dict, or None and the missing figure's name; `main(argv) -> int` with `--root PATH` (default `SITE_ROOT`) and `--write` (default is a dry run that prints one line per idea).

Sources per field: manifest row → symbol, label, type, legs, expiration, entry_cash, max_loss, max_profit, spot, posted; `alt` → `pop_pct` (`POP (\d+)%`); ledger row → `score` (`idea_score`), `grade` (`idea_grade`, else the row's); `economics(legs, entry_cash)` → breakevens; `dte` = expiration − post date; bias = `_CREDIT_BIAS[type]` for a credit type, `bullish`/`bearish` for a single long call/put, else **missing**; `em` → None (the band is a drawing, not a printed figure). Any printed figure missing → the idea keeps its old image. Writes PNG, JPEG and WebP over the same stems, sets the row's `jpg`, `shape`, `card`; the manifest under `site_ideas._LOCK`, written atomically.

- [ ] **Step 1: Write the failing tests** — `test_facts_from_manifest_alt_and_ledger` (the SPY IC fixture → exact dict), `test_missing_score_keeps_old_image`, `test_directional_bias_from_single_leg`, `test_dry_run_writes_nothing`, `test_write_replaces_files_and_updates_row`, `test_redrawn_row_is_never_listed` (posted before `listed_from` → `listed` False in `card_rows`).
- [ ] **Step 2: Run** `.venv/Scripts/python -m pytest tools/tests/test_redraw_idea_cards.py -q` — FAIL.
- [ ] **Step 3: Implement** the tool.
- [ ] **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `feat(tools): redraw past trade ideas in the new card`.

### Task 9: Documentation, invariants and the whole-suite check

**Files:**
- Modify: `CLAUDE.md` (Tier-1 allow-list: `shared.social_cards`; the config file list: `social_cards`; the More strip wording if present), `docs/webgui-routes.md` (`/social` new, `/x` now a redirect, `/blog` "after Post to X" corrected), `docs/reference/blog.md` (the card files, `card.json`, unpublish), `docs/manuals/user-guide/*.md` and `docs/manuals/reference-guide/reference-guide.md` (Post to X → Social Cards; posting to Instagram by hand from it), `docs/manuals/api-reference/api-reference.md` (the two views, the route), `docs/CHANGELOG.md`, the design doc (link the plan)
- Test: `tests/test_claude_md_size.py` must stay green

- [ ] **Step 1: Write the docs above**; rebuild the manuals with `docs/manuals/build_docs.py`.
- [ ] **Step 2: Run every suite** per `docs/reference/testing.md` (each service suite on its own from the repo root; `webgui/`, `deploy/`, `shared/`, `tools/` from their folders) — the failing set and the skipped set must equal the baseline (none known).
- [ ] **Step 3: Commit** `docs(social cards): routes, manuals, reference and changelog`.

### After merge (operator-visible, not a code task)

- Set `[cards] listed_from` to the promote day before merging (it ships as `2026-11-09`).
- After the promote: run `tools/redraw_idea_cards.py --root <copy of deploy/site>` on prod first, compare each redrawn card with its original, then `--write` against the real site.
- On the first live trade idea: look at it on X, Discord, Telegram and the site; log what each showed in the CHANGELOG.

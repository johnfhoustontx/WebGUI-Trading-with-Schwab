# Today's trade ideas on neuralstrike.co — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Every posted hourly trade idea also appears on neuralstrike.co — a strip of
the newest three on the home page and every card of the last six posting days on a
new `ideas.html`.

**Architecture:** After `run_trade_idea` posts, options_svc writes the card into the
served static tree (`deploy/site/ideas/<day>/`) plus a manifest (`deploy/site/ideas.json`);
a small static script renders the strip and the page from that manifest. Both paths are
gitignored generated state, like `live/*.webp`. Design:
[2026-09-29-site-trade-ideas-design.md](2026-09-29-site-trade-ideas-design.md).

**Tech stack:** Python 3.11, Pillow (WebP copy), `shared.config_toml` layered TOML,
static HTML/CSS/vanilla JS, Caddy, pytest.

**Test commands** (from the worktree root; a worktree has no venv, so the local
Windows venv is `"D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe"` — call it `$PY`):

```
$PY -m pytest services/options_svc/tests/test_site_ideas.py services/options_svc/tests/test_trade_idea.py -q
$PY -m pytest shared/notify -q
$PY -m pytest deploy -q
(cd webgui && $PY -m pytest tests/test_config_schema.py tests/test_config_editor.py tests/test_settings.py -q)
$PY -m pytest tools/tests/test_backfill_site_ideas.py -q
```

---

### Task 1: `[site]` config in `config/notify.toml`

**Files:** Modify `config/notify.toml`, `shared/notify/switches.py`,
`webgui/config_schema.py`. Test `shared/notify/tests/test_switches.py`.

1. Failing tests:
   ```python
   def test_site_settings_defaults_when_the_section_is_missing(tmp_path):
       load, _ = switches._make_loader(tmp_path / "none.toml")
       assert switches.site_settings(load) == {"trade_ideas": True, "keep_days": 6}

   def test_site_settings_rejects_bad_values_one_key_at_a_time(tmp_path):
       p = tmp_path / "n.toml"
       p.write_text('[site]\ntrade_ideas = "yes"\nkeep_days = 0\n')
       load, _ = switches._make_loader(p)
       assert switches.site_settings(load) == {"trade_ideas": True, "keep_days": 6}

   def test_site_settings_reads_the_file(tmp_path):
       p = tmp_path / "n.toml"
       p.write_text("[site]\ntrade_ideas = false\nkeep_days = 3\n")
       load, _ = switches._make_loader(p)
       assert switches.site_settings(load) == {"trade_ideas": False, "keep_days": 3}
   ```
2. Implement `site_settings(load=None)` in `switches.py` (`_SITE_DEFAULTS =
   {"trade_ideas": True, "keep_days": 6}`, bool must be a real bool, keep_days an int
   1..30, never raises) and add `"site": dict(_SITE_DEFAULTS)` to the loader defaults.
3. Add to `notify.toml`:
   ```toml
   # The public site (neuralstrike.co/ideas.html + the home page strip). Each posted
   # trade idea's card is also written into deploy/site/ideas/ (gitignored).
   [site]
   trade_ideas = true             # off = post as usual, publish nothing to the site
   keep_days = 6                  # posting days kept on the site (today + 5)
   ```
4. Catalogue both keys in `_NOTIFY` (new `Section("Public site", …)`); run the
   webgui config tests (they fail on an uncatalogued key).
5. Commit `feat(notify): [site] switch for publishing trade ideas to the website`.

### Task 2: `services/options_svc/site_ideas.py`

**Files:** Create the module and `services/options_svc/tests/test_site_ideas.py`.

Public surface:

```python
SITE_ROOT = pathlib.Path(repo_paths.SITE_ROOT)   # tests redirect this
IDEAS_DIR = "ideas"; MANIFEST = "ideas.json"; WEB_WIDTH = 1200

def slug(symbol) -> str            # "$SPX" -> "SPX"; anything non-alnum -> "-"
def entry(symbol, label, grade, caption_text, now) -> dict
    # {"time": "14:35", "symbol", "label", "grade", "alt",
    #  "img": "ideas/<day>/<HHMM>-<SLUG>.webp", "full": "ideas/<day>/<HHMM>-<SLUG>.png"}
def merge_manifest(manifest, day, new_entry, keep_days, updated) -> dict
    # pure: validates the old manifest (anything malformed -> start empty), replaces an
    # entry with the same "full" (a re-post of one slot), ideas newest first, days
    # newest first, keeps the newest keep_days days
def webp_copy(png, width=WEB_WIDTH) -> bytes | None     # None on any failure
def publish(idea, png, caption_text, now, *, root=None, keep_days=6) -> bool
    # writes png, webp (falls back to img = the png when webp fails), manifest
    # (atomic: tmp + os.replace), then prunes ideas/<YYYY-MM-DD> folders not in the
    # manifest. Never raises; False on failure.
```

Tests (write first, watch them fail):
- `slug("$SPX") == "SPX"`, `slug("BRK/B") == "BRK-B"`.
- `merge_manifest(None, …)` → one day, one idea; a second idea the same day sorts
  newest first; the same `full` twice keeps one; 7 days with `keep_days=6` drops the
  oldest; a malformed old manifest (`{"days": "x"}`, a list, `None`) starts clean.
- `publish` into `tmp_path` writes `ideas/2026-09-29/1435-MU.png`, a `.webp` whose
  width is 1200, and `ideas.json` whose first idea's `img`/`full` point at them.
- `publish` prunes `ideas/2026-09-01` once it falls out, and never touches
  `ideas/notes.txt` or an undated folder.
- `publish` with `png=b"not a png"` still writes the png and the manifest, with
  `img == full` (no webp), and returns True; `publish` into an unwritable root returns
  False and raises nothing.

Commit `feat(options): publish trade idea cards into the static site tree`.

### Task 3: wire into `run_trade_idea`

**Files:** Modify `services/options_svc/handlers.py`,
`services/options_svc/tests/conftest.py`, `services/options_svc/tests/test_trade_idea.py`.

1. Autouse fixture in the options_svc conftest:
   `monkeypatch.setattr(site_ideas, "SITE_ROOT", tmp_path / "site")` — otherwise any
   test that posts writes into the real `deploy/site`.
2. Failing tests (use the existing `idea_ready` fixture):
   - posted with a PNG → `tmp site/ideas.json` lists the idea's symbol; result has
     `site: True`.
   - `site_settings` returning `trade_ideas: False` → no manifest, `site` is False.
   - `site_ideas.publish` raising → `status == "posted"` still, `site` False.
   - `send_trade_idea` False → nothing published.
   - `trade_idea_png` returning None (text-only post) → nothing published.
3. Implement `_publish_trade_idea_site(idea, png, now) -> bool` beside
   `_post_trade_idea_x`, called in the `if sent:` branch **after** the X post, only when
   `png`. It reads `switches.site_settings()` at call time, calls
   `site_ideas.publish(idea, png, trade_idea.caption(idea), now, keep_days=…)`, and
   routes any exception through `_degrade.degraded("options.run_trade_idea.site")`.
4. Commit `feat(options): posted trade ideas appear on the public site`.

### Task 4: gitignore + Caddy

**Files:** `.gitignore`, `deploy/caddy/generate_caddyfile.py`,
`deploy/caddy/tests/test_caddyfile.py`, `deploy/tests/test_site.py`.

1. Tests: `git check-ignore` pins `deploy/site/ideas/2026-09-29/1435-MU.png` and
   `deploy/site/ideas.json`; the Caddyfile's `@revalidate` matches `*.json`, and
   `@ideas path /ideas/*` carries `max-age=86400, must-revalidate`.
2. Add `deploy/site/ideas/` and `deploy/site/ideas.json` to `.gitignore` beside
   `reports/`, with the promote-refuses-a-dirty-tree reason.
3. Add `*.json` to `@revalidate`, and an `@ideas` block with `IDEAS_MAX_AGE = 86400`.
4. Commit `feat(site): ignore and cache the generated trade ideas`.

⚠ The Caddyfile on the box is installed by the operator (sudo). Until it is, the
script's `fetch(…, {cache: "no-cache"})` still revalidates the manifest, so nothing
shows stale; only the image caching is heuristic.

### Task 5: the site — `ideas.js`, `ideas.html`, the home strip, the nav

**Files:** Create `deploy/site/assets/ideas.js`, `deploy/site/ideas.html`. Modify
`deploy/site/index.html`, `gallery.html`, `live.html`, `report.html`,
`sitemap.txt`, `assets/site.css`, `deploy/tests/test_site.py`.

1. Tests first in `test_site.py`:
   - `"ideas.html"` joins `PAGES` (link, app-host and origin guards) and
     `MARK_LARGE_FILES`; `"ideas/"` joins `GENERATED_REF_PREFIXES`.
   - every page but the glossary has `href="ideas.html"` **inside its `<nav>`**.
   - `index.html` has one `data-ideas-strip` element carrying the `hidden` attribute
     (it only appears once the script has something to show) and loads
     `assets/ideas.js`; `ideas.html` has `data-ideas-page` and loads it too.
   - `ideas.js` fetches `ideas.json` with `cache: "no-cache"`, names the
     `America/Chicago` zone, and never assigns `innerHTML`.
   - `ideas.html` carries the footer disclaimer verbatim (add it to that test's list).
   - `sitemap.txt` lists `https://neuralstrike.co/ideas.html`.
2. `ideas.js` (one IIFE, DOM built with `createElement`/`textContent` only):
   - `todayCT()` via `Intl.DateTimeFormat("en-CA", {timeZone: "America/Chicago"})`.
   - `dayName(date)` → "Today" when it is today in CT, else "Monday, Sep 28".
   - `validIdea(i)` keeps an idea only if `img`/`full` match
     `^ideas/\d{4}-\d{2}-\d{2}/[\w-]+\.(png|webp)$`.
   - Strip: newest day, newest 3 ideas; heading "Today's trade ideas" or "Monday's
     trade ideas"; link "See all N →" to `ideas.html?day=<date>`; unhide. Missing or
     empty manifest → stays hidden.
   - Page: a day picker (`<button aria-pressed>` per day), `?day=` deep link, grid of
     cards (`<a href=full><img src=img alt=alt loading=lazy></a>` + a line
     "14:35 CT · MU · Put credit spread · Good"); empty → "No trade ideas have been
     posted yet. The first goes out at 08:35 CT on the next trading day."
3. `ideas.html` from `report.html`'s frame: crumb "/ Trade ideas", lede stating the
   schedule, the mount, the footer, `<script src="assets/ideas.js" defer>`.
4. Home: a `<section id="ideas" data-ideas-strip hidden>` after the fact band.
   Nav link "Trade ideas" next to "Market report" on index, gallery, live, report.
5. CSS: `.ns-ideas-grid` (auto-fill minmax(320px,1fr)), `.ns-idea-card`,
   `.ns-idea-meta`, `.ns-day-picker` reusing the live-tile look.
6. Commit `feat(site): today's trade ideas on the home page and ideas.html`.

### Task 6: backfill tool

**Files:** Create `tools/backfill_site_ideas.py`, `tools/tests/test_backfill_site_ideas.py`.

- `parse_archive_name("trade-idea-2026-09-29-1435-MU.png")` → `(datetime, "MU")`.
- `parse_caption("Trade idea: MU Put credit spread · Sep 21 … · Grade Good · …")` →
  `("Put credit spread", "Good")`; anything else → `("", "")`.
- `main(archive_dir, site_root, keep_days)` walks the newest `keep_days` day folders
  oldest-first and calls `site_ideas.publish` per card. Tests run over a tmp archive.
- Commit `feat(tools): backfill the site's trade ideas from the card archive`.

### Task 7: docs

CLAUDE.md (generated-state paragraph beside `reports/`), User Guide (where the ideas
appear and the `[site]` switch), CHANGELOG entry. Commit `docs: trade ideas on the site`.

### Task 8: verify

- Run every suite listed above; compare failing sets against `main`.
- Serve `deploy/site` locally (`$PY -m http.server` in the directory) with a sample
  `ideas.json` + cards from the backfill run, in the Browser pane: the strip, "See all",
  the day picker, `?day=`, a phone width, and the missing-manifest case (strip hidden).

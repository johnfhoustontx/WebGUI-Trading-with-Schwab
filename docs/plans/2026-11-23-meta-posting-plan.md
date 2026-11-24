# Facebook and Instagram posting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every posted trade idea and every market report a permanent page on neuralstrike.co, then post each one to the Facebook Page (a link post) and Instagram (the card + the address written out) from `options_svc`, beside the X post.

**Architecture:** Part 1 (Tasks 1–6) adds two never-pruned site folders, `t/<id>/` and `r/<id>/`, written by `options_svc` before any post, and points X at them. Part 2 (Tasks 7–11) adds `shared/notify/meta_post.py`, the one path to Meta's Graph API, modelled on `shared/notify/x_post.py`, called from the same two places X is. Part 1 is promoted before Part 2 starts.

**Tech Stack:** Python 3.11, `requests`, Pillow, the static site under `deploy/site/` (vanilla JS), Caddy, Redis through `shared.bus`, pytest with fakeredis.

**Spec:** [docs/plans/2026-11-23-meta-posting-design.md](2026-11-23-meta-posting-design.md)

## Global Constraints

- `VENV` below means `"D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe"` (the venv lives in the main checkout; this is a worktree). Run service suites from the repo root, one service per run (`$VENV -m pytest services/options_svc`), never `pytest services`.
- Never raises on a post path; a guard of 15+ lines calls `_degrade.degraded("<area>")`.
- A page, card or Meta failure never costs the Discord, Telegram, X or site post, and the reverse.
- Every Facebook/Instagram call goes through `meta_post.post`. Nothing else imports a Graph client or calls `graph.facebook.com`.
- Tunables in `config/meta_post.toml` (loader `toml_loader`, entry in `webgui/config_schema.py`). Credentials, Page id, Instagram id, `enabled`, `dry_run` in `shared/notifications.json`'s `meta` block (gitignored; the dev/pytest suppression in `channels.load_config` already zeroes every `enabled`).
- Ships `enabled = false`, `dry_run = true`.
- `deploy/site/t/` and `deploy/site/r/` are gitignored generated state and are never pruned. The app never writes a tracked file (`sitemap.txt`, `robots.txt`, `ideas.html`).
- Every file the site serves is written atomically with mode 0644 (`site_ideas._write_atomic`).
- Page addresses: `https://<SITE_HOST>/t/<YYYY-MM-DD>-<HHMM>-<SYMBOL>/` and `https://<SITE_HOST>/r/<YYYY-MM-DD>-<n>-<slot>/`. Never hard-code the host: `repo_paths.SITE_HOST`.
- Instagram: ≤ 5 hashtags, caption ≤ 2,200 characters, image a JPEG at a public URL. Daily caps per network, Central day.
- No test counts, SHAs or "verified live" in CLAUDE.md. A heredoc never writes a test file.
- Write files with the editor, not `cat <<EOF`.

## Review Focus

1. **A symbol with a sigil or slash** (`$SPX`, `BRK/B`) — the page id uses `site_ideas.slug` (`SPX`, `BRK-B`) and the hashtag drops every non-alphanumeric (`#SPX`, `#BRKB`). Test in Task 2 and Task 7.
2. **The same slot posted twice** (`merge_manifest` replaces the same `full`) — the page is rewritten in place, never a second folder, and Meta's `meta_ideas` memory refuses a second post. Tests in Task 4 and Task 9.
3. **The site switch off** (`switches.site_settings()["trade_ideas"]` False) — no page; X and Meta link the site home (`x.link`), never a missing page. Test in Task 6 and Task 9.
4. **One network fails, the other posts** — the failure is logged on its own; for a report only the failed network is retried by the next enqueue. Test in Task 8 and Task 9.
5. **A report replaced before its command runs** (`latest.txt` names a newer report) — no `r/` page is written and the posts link `report.html`. Test in Task 5.

---

## Part 1 — the permanent pages

### Task 1: Lift the page parts reader into `shared/site_nav`

`options_svc` must build a site page with the tracked menu, icons, stylesheets and footer, and cannot import `blog_svc`.

**Files:**
- Modify: `shared/site_nav.py` (add `page_parts`)
- Modify: `services/blog_svc/sitewriter.py:91-157` (`read_parts` becomes a call to it; keep `NAV_RE` and `read_parts` names — `deploy/tests/test_site.py` pins `NAV_RE`)
- Test: `shared/tests/test_site_nav.py`

**Interfaces:**
- Produces: `site_nav.page_parts(root, template: str) -> dict | None` returning `{"nav", "icons", "styles", "footer"}` exactly as `sitewriter.read_parts` does today, same refusals (no single nav, no footer, no icon, no `assets/site.css`, a `<script` in any piece).

- [ ] **Step 1: Write the failing tests** in `shared/tests/test_site_nav.py`: `test_page_parts_reads_the_tracked_ideas_page` (against `repo_paths.SITE_ROOT`, template `"ideas.html"`: result not None, `'aria-current' in parts["nav"]`, any `'assets/site.css'` in `parts["styles"]`), and `test_page_parts_refuses_a_page_with_two_menus` (tmp_path page with two `<nav class="ns-nav">…</nav>` → `None`).
- [ ] **Step 2: Run** `$VENV -m pytest shared/tests/test_site_nav.py -k page_parts` — expect FAIL (`AttributeError: page_parts`).
- [ ] **Step 3: Implement** `page_parts` by moving `read_parts`' body and its regexes into `site_nav`; `sitewriter.read_parts(root)` returns `site_nav.page_parts(root, TEMPLATE)`, `sitewriter.NAV_RE = site_nav.PAGE_NAV_RE`.
- [ ] **Step 4: Run** `$VENV -m pytest shared/tests/test_site_nav.py` and `$VENV -m pytest services/blog_svc` and `$VENV -m pytest deploy/tests` — expect PASS, no new failures.
- [ ] **Step 5: Commit** `refactor(site): page parts reader moves to shared.site_nav`.

### Task 2: `services/options_svc/trade_pages.py` — one trade's page

**Files:**
- Create: `services/options_svc/trade_pages.py`
- Test: `services/options_svc/tests/test_trade_pages.py`

**Interfaces:**
- Consumes: `site_nav.page_parts` (Task 1); `site_ideas._write_atomic`, `site_ideas.webp_copy`; `shared.card_draw.jpeg_from_png`; a manifest row as `site_ideas._publish` builds it (`full`, `img`, `jpg`, `time`, `symbol`, `label`, `grade`, `alt`, `legs`, `type`, `expiration`, `entry_cash`, `max_loss`, `max_profit`, `spot`, `posted`, `card`, `result`, `shape`).
- Produces:
  - `TRADE_DIR = "t"`, `TEMPLATE = "ideas.html"`, `PAGE, DATA, CARD_JPG, CARD_WEBP = "index.html", "trade.json", "card.jpg", "card.webp"`, `SITEMAP = "sitemap.txt"`, `OPEN_LIST = "open.json"`.
  - `page_id(row) -> str | None` — `"ideas/2026-11-23/0930-SPY.png"` → `"2026-11-23-0930-SPY"`; None for a `full` that does not match `site_ideas._REF_RE`.
  - `address(pid, site_host=None) -> str` — `https://<host>/t/<pid>/`.
  - `page_row(row, pid) -> dict` — PURE: the row with `img` → `t/<pid>/card.webp`, `full` → `t/<pid>/card.jpg`, `jpg` → same, `page` → `t/<pid>/`.
  - `page_html(parts, row, pid, site_host) -> str` — PURE.
  - `is_final(row, *, in_manifest: bool, ledger_row: dict | None) -> bool` — PURE, the design's terminal rule.
  - `write(root, row, png) -> str | None` — writes the folder; returns `address(pid)` or None. Never raises.
  - `rewrite(root, pid, row) -> bool` — rewrites `trade.json` only when its bytes change. Never raises.
  - `open_ids(root) -> list[str]`, `set_open(root, ids)`, `write_sitemap(root) -> None`.

- [ ] **Step 1: Write the failing tests** in `test_trade_pages.py` (use `tmp_path` as the site root, copy the tracked `deploy/site/ideas.html` into it, and the `_png`/`_idea` fixture shapes from `test_site_ideas.py`):
  - `test_page_id_is_the_day_and_the_stem`: `page_id({"full": "ideas/2026-11-23/0930-SPY.png"}) == "2026-11-23-0930-SPY"`; `page_id({"full": "../x.png"}) is None`.
  - `test_page_id_of_an_index_and_a_slash_symbol`: rows built by `site_ideas.entry("$SPX", …)` and `entry("BRK/B", …)` give ids ending `-SPX` and `-BRK-B`.
  - `test_address_uses_the_site_host`: `address("2026-11-23-0930-SPY", "neuralstrike.co") == "https://neuralstrike.co/t/2026-11-23-0930-SPY/"`.
  - `test_page_html_carries_the_preview_tags`: contains `og:url` = the address, `og:image` = `https://neuralstrike.co/t/<pid>/card.jpg`, `og:image:width" content="1200"`, `og:image:height` = the JPEG's real height (`630` for a 2400×1260 card), `twitter:card" content="summary_large_image"`, `<base href="/">`, `data-idea-page`, `data-src="/t/<pid>/trade.json"`, `<script src="assets/ideas.js" defer>`, and the caption text HTML-escaped (a `<` in `alt` arrives as `&lt;`).
  - `test_page_html_writes_the_trade_out`: the structure label, each leg (`Short 590 PUT`-style, matching `trade_idea.leg_text`), the expiration, max risk and max profit through `trade_idea.money`, and the grade appear in the static body.
  - `test_write_makes_the_folder`: `write(root, row, png)` returns the address; `t/<pid>/` holds `index.html`, `trade.json`, `card.jpg` (1200 wide, decodes as JPEG), `card.webp`; `json.loads(trade.json)["img"] == f"t/{pid}/card.webp"`; files are mode 0644 on POSIX (skip on Windows).
  - `test_write_twice_keeps_one_folder`: two writes of the same row → one folder, `index.html` bytes equal.
  - `test_write_never_raises`: a root that is a file → returns None.
  - `test_rewrite_only_when_changed`: `rewrite` returns False on an identical row, True after `result` changes, and `trade.json` then holds the new result.
  - `test_is_final_rule`: True for `result.status` in `("target","stop","trail","expired","closed")`; True when `in_manifest=False` and `ledger_row is None`; False for `status == "open"` in the manifest; False for an out-of-manifest row whose `ledger_row["status"] == "OPEN"`.
  - `test_sitemap_lists_every_page`: two folders → `t/sitemap.txt` holds both addresses, newest first, one per line.
  - `test_open_list_round_trips`: `set_open(root, ["a","b"])` then `open_ids(root) == ["a","b"]`; a missing or broken `open.json` reads `[]`.
- [ ] **Step 2: Run** `$VENV -m pytest services/options_svc/tests/test_trade_pages.py` — expect FAIL (module missing).
- [ ] **Step 3: Implement** the module. The page follows `sitewriter.entry_page`'s head (escape every value with `html.escape(quote=True)`; `<base href="/">` first; `canonical` = address); body is the menu (`parts["nav"]`), a header (eyebrow `Trade idea`, `h1` = `"<SYMBOL> <label>"`, the post time and links to `ideas.html` and `index.html`), `<main data-idea-page data-src="/t/<pid>/trade.json">` holding a `<noscript>` with the caption, the static facts as a `<dl class="ns-idea-facts">`, the footer, then `ideas.js`, `market-clock.js`, `market-glow.js` script tags as `ideas.html` has them. The JPEG and WebP are 1200 wide (`site_ideas.webp_copy`; the JPEG through `card_draw.jpeg_from_png` of the resized PNG at `social_cards.config()["cards"]["jpeg_quality"]`). A page shows exactly the row's fields and nothing it lacks.
- [ ] **Step 4: Run** the same command — expect PASS.
- [ ] **Step 5: Commit** `feat(site): a page per trade idea`.

### Task 3: `ideas.js` renders one trade, and the list links to it

**Files:**
- Modify: `deploy/site/assets/ideas.js`
- Modify: `deploy/site/assets/site.css` (only if the single card needs a width rule; `.ns-idea-facts` styling)
- Test: `deploy/tests/test_site.py`

**Interfaces:**
- Consumes: `trade.json` as `trade_pages.page_row` writes it (Task 2).
- Produces: a `[data-idea-page]` element is filled with ONE `card(idea, true)` and its `resultBlock`, from `fetch(el.dataset.src, {cache: "no-cache"})`. On `ideas.html` and the home strip, a row carrying `page` (matching `/^t\/\d{4}-\d{2}-\d{2}-\d{4}-[A-Za-z0-9-]+\/$/`) links its card to `"/" + page` instead of `full`, same tab.

- [ ] **Step 1: Write the failing test** `test_ideas_js_knows_the_trade_page` in `deploy/tests/test_site.py`: the file contains `data-idea-page`, `dataset.src`, and the `page` regex above; and `test_trade_page_ref_accepts_the_page_images`: the JS accepts `t/<pid>/card.webp` / `card.jpg` for the single page (a second regex `PAGE_REF`).
- [ ] **Step 2: Run** `$VENV -m pytest deploy/tests/test_site.py -k "trade_page or idea_page"` — expect FAIL.
- [ ] **Step 3: Implement** in `ideas.js`: `PAGE_REF`, `PAGE_LINK` regexes; `renderOne(root, idea)`; `start` also runs when `[data-idea-page]` exists (its own fetch, not `ideas.json`); `card()` uses `idea.page` when it matches `PAGE_LINK`.
- [ ] **Step 4: Run** `$VENV -m pytest deploy/tests` — expect PASS.
- [ ] **Step 5: Look at it.** In the scratchpad, copy `deploy/site/` and a folder from Task 2's `write` (synthetic row, open result) into one tree; serve it with `$VENV -m http.server 8765 --bind 127.0.0.1 --directory <tree>`; open `http://127.0.0.1:8765/t/<pid>/` in the built-in browser. Expect the menu, the card, the result block worded as on `ideas.html`, the facts list, no console errors; `ideas.html` links its card to the page. Stop the server.
- [ ] **Step 6: Commit** `feat(site): ideas.js draws a trade's own page and links to it`.

### Task 4: `site_ideas` writes and keeps the pages

**Files:**
- Modify: `services/options_svc/site_ideas.py` (`_publish`, `refresh`)
- Modify: `.gitignore` (add `deploy/site/t/` and `deploy/site/r/` beside `deploy/site/ideas/`)
- Modify: `deploy/site/robots.txt` (two `Sitemap:` lines: `/t/sitemap.txt`, `/r/sitemap.txt`, with a comment like the blog's)
- Modify: `deploy/caddy/generate_caddyfile.py` (`@trade_pages path /t/* /r/*`: `index.html`/`trade.json` revalidate, images `IDEAS_MAX_AGE`), `deploy/caddy/tests/test_caddyfile.py`
- Modify: `services/options_svc/app.py` or the scheduler start hook (call `trade_pages.write_sitemap(SITE_ROOT)` once at start, so the sitemap exists empty; Task 5 adds `report_pages.write_sitemap` beside it)
- Test: `services/options_svc/tests/test_site_ideas.py`, `services/options_svc/tests/test_site_idea_results.py`

**Interfaces:**
- Consumes: `trade_pages.write`, `rewrite`, `is_final`, `open_ids`, `set_open`, `write_sitemap`, `page_row` (Task 2); `ledger_result`, `_facts_of`, `ledger_key`, `_withhold_single` (existing).
- Produces: a manifest row carries `page: "t/<pid>/"` when its page was written. `site_ideas.page_address(row, root=None) -> str | None` returns the address when `t/<pid>/index.html` exists. `publish` still returns bool.

- [ ] **Step 1: Write the failing tests**:
  - `test_publish_writes_the_trade_page` (test_site_ideas.py): after `publish`, `ideas.json`'s row has `page == f"t/{pid}/"`, the folder exists, and `pid` is in `open_ids(root)`.
  - `test_a_page_failure_still_publishes` : monkeypatch `trade_pages.write` to return None → `publish` True, row has no `page`.
  - `test_prune_never_touches_pages`: publish on 7 posting days with `keep_days=6` → the first day's `ideas/<day>` is gone, its `t/<pid>/` remains.
  - `test_refresh_rewrites_a_changed_page` (test_site_idea_results.py): a refresh that moves a result rewrites that page's `trade.json` with the same result `ideas.json` now shows.
  - `test_a_page_outside_the_manifest_follows_the_ledger`: a pid in `open.json`, not in `ideas.json`, whose Ledger row (via `ledger_fn`) is CLOSED with `realized_pnl` → `trade.json`'s result equals `ledger_result(...)` and the pid leaves `open.json`.
  - `test_a_page_with_no_ledger_row_is_finalised_once`: out of the manifest, no Ledger row → `trade.json` gains `"untracked": true`, pid leaves `open.json`, a second refresh writes nothing (mtime unchanged).
  - `test_withheld_single_leg_matches_ideas_json`: with `_withhold_single` True, a single-leg idea's page result equals the manifest's (both `withheld`, no `pnl`).
  - Caddy: `test_trade_and_report_pages_have_cache_rules`: the generated file contains `/t/*` and `/r/*` matchers.
- [ ] **Step 2: Run** `$VENV -m pytest services/options_svc/tests/test_site_ideas.py services/options_svc/tests/test_site_idea_results.py` and `$VENV -m pytest deploy/caddy/tests` — expect the new tests FAIL.
- [ ] **Step 3: Implement.** In `_publish`, after `_social_card`: `addr = trade_pages.write(root, row, png)`; on success set `row["page"]` and add the pid to the open list. In `refresh`, under `_LOCK` after `out` is computed: for every manifest row whose `result` changed, `trade_pages.rewrite(root, pid, trade_pages.page_row(row, pid))`; then for each pid in `open_ids` not in the manifest, read its `trade.json`, compute `ledger_result(_facts_of(r), ledger_row, r.get("result"), withhold_single=withhold)` when the Ledger was read, rewrite, and drop it from the open list when `is_final`. Drop manifest pids from the open list when final. Call `write_sitemap` after a page is added. The refresh's early return (`not symbols and not settle and work == before`) must not skip the out-of-manifest pass while `open_ids` is non-empty.
- [ ] **Step 4: Run** the Step 2 commands, then `$VENV -m pytest services/options_svc` — expect PASS, failing set unchanged.
- [ ] **Step 5: Commit** `feat(site): trade pages are written at post time and kept current`.

### Task 5: `services/options_svc/report_pages.py` — one report's page

**Files:**
- Create: `services/options_svc/report_pages.py`
- Modify: the start hook from Task 4 (also call `report_pages.write_sitemap(SITE_ROOT)`)
- Test: `services/options_svc/tests/test_report_pages.py`

**Interfaces:**
- Consumes: `site_nav.page_parts(root, "report.html")`, `site_nav.framed_src`, `report_summary.REPORTS_DIR` layout (`latest.html`, `latest.txt` = `"<day> <n> <slot> <as_of>"`), the report payload (`report_date`, `slot`, `slot_label`, `as_of`, `headline`), `card_draw.jpeg_from_png`, `site_ideas._write_atomic`.
- Produces:
  - `REPORT_DIR = "r"`; `page_id(latest_txt: str) -> str | None` — `"2026-11-23 3 midday 11:30"` → `"2026-11-23-3-midday"`; None when malformed.
  - `address(pid, site_host=None) -> str` — `https://<host>/r/<pid>/`.
  - `write(root, report, png, *, reports_dir=None) -> str | None` — never raises; writes `r/<pid>/report.html` (the copy of `latest.html`), `index.html` (the `report.html` page with the frame's `src` set to `site_nav.framed_src("r/<pid>/report.html")`, title and preview tags for this report), `card.jpg`; returns the address. Writes nothing and returns the address when `r/<pid>/index.html` already exists. Returns None when `latest.txt`'s day/slot/as-of differ from the payload's.
  - `write_sitemap(root) -> None` — `r/sitemap.txt`.

- [ ] **Step 1: Write the failing tests** (tmp_path site root with the tracked `report.html` copied in and a `reports/` folder holding a fixture `latest.html` + `latest.txt`; a PNG from `report_card.render_report_png` of a fixture payload):
  - `test_page_id_from_latest_txt`: the example above; `page_id("garbage") is None`.
  - `test_write_makes_the_page`: returns `https://neuralstrike.co/r/2026-11-23-3-midday/`; `report.html` equals `latest.html` byte for byte; `index.html` holds `og:image` → `/r/<pid>/card.jpg`, `og:title` with the slot label and headline (escaped), and `src="r/<pid>/report.html#ns-framed"`.
  - `test_write_is_idempotent`: a second `write` changes no mtime and returns the same address.
  - `test_a_replaced_report_writes_nothing`: `latest.txt` names a later slot than the payload → None, no folder.
  - `test_write_never_raises`: no `report.html` template → None.
- [ ] **Step 2: Run** `$VENV -m pytest services/options_svc/tests/test_report_pages.py` — expect FAIL.
- [ ] **Step 3: Implement** the module. Build `index.html` from the tracked `report.html` bytes, replacing only the `<title>`, adding the preview tags before `</head>`, adding `<base href="/">` first in `<head>`, and replacing the iframe `src`; refuse (None) when the template's single `<iframe class="ns-report-frame"` is not found.
- [ ] **Step 4: Run** the same command — expect PASS.
- [ ] **Step 5: Commit** `feat(site): a page per market report`.

### Task 6: X links the new pages

**Files:**
- Modify: `services/options_svc/handlers.py` (`run_trade_idea` order; `_post_trade_idea_x`; `_publish_trade_idea_site`; `run_x_post_report`)
- Modify: `services/options_svc/trade_idea.py` (`x_text(idea, x_cfg, *, today, link=None)`)
- Test: `services/options_svc/tests/test_trade_idea.py`, `services/options_svc/tests/test_x_report_pages.py` (new)

**Interfaces:**
- Consumes: `site_ideas.page_address` (Task 4), `report_pages.write` (Task 5).
- Produces: `_publish_trade_idea_site(idea, png, now) -> str | None` (the page address, or None); `result["site"]` stays a bool (`address is not None`) and `result["page"]` holds the address. `run_trade_idea` order becomes: private sends → site publish → X → `publish_idea_cards` → Ledger. `x_text(..., link=…)` uses `link` when given, else `x_cfg["link"]`. `run_x_post_report` calls `report_pages.write` after the age gate and before drawing for X; the post links its address, else `report_url`, else `x.link`. The X PNG is the same bytes `report_pages.write` saved as `card.jpg`.

- [ ] **Step 1: Write the failing tests**:
  - `test_x_text_links_the_trade_page`: `x_text(idea, {"link": "https://neuralstrike.co"}, today=…, link="https://neuralstrike.co/t/2026-11-23-0930-SPY/")` contains the page address, not the bare home.
  - `test_trade_idea_posts_x_with_the_page` (test_x_report_pages.py): patch `push_notify.send_trade_idea` → True, `x_post.post` to capture its text, `site_ideas.SITE_ROOT` to tmp; run `run_trade_idea` with a fixture scan → the captured text holds `/t/`.
  - `test_site_switch_off_links_home`: `switches.site_settings` → `trade_ideas: False` → X text holds `x.link`, and no `t/` folder.
  - `test_report_posts_x_with_its_page`: a fixture report on disk, `run_x_post_report` with a fresh mtime → captured text holds `/r/2026-…/`.
- [ ] **Step 2: Run** `$VENV -m pytest services/options_svc/tests/test_trade_idea.py services/options_svc/tests/test_x_report_pages.py` — expect FAIL.
- [ ] **Step 3: Implement** the reorder and the link plumbing as in Interfaces.
- [ ] **Step 4: Run** `$VENV -m pytest services/options_svc` — expect PASS, failing set unchanged.
- [ ] **Step 5: Docs for Part 1** in the same commit: `docs/reference/public-live-screens.md` (the `t/` and `r/` folders, never pruned, who writes them, the sitemaps), `docs/CHANGELOG.md` entry, User Guide section on trade and report pages, `page_help.py` only if a private page's text names `ideas.html` as the X link.
- [ ] **Step 6: Commit** `feat(x): trade and report posts link their own pages`.

**Checkpoint — promote Part 1.** Merge to `main`, push, and hand the operator the promote command and, after it, the Caddy step (`deploy/caddy/generate_caddyfile.py --install` and a Caddy reload — the operator runs it; no passwordless sudo). Before Part 2: paste one live trade page into Facebook's Sharing Debugger and confirm the card preview; record it in the CHANGELOG.

---

## Part 2 — the Meta poster

### Task 7: Config, captions and hashtags

**Files:**
- Create: `config/meta_post.toml`, `shared/meta_text.py`
- Modify: `repo_paths.py` (`META_POST_TOML = REPO_ROOT / "config" / "meta_post.toml"`, `META_POSTS_LOG = OPTIONS_SCANNER / "data" / "meta_posts.jsonl"`)
- Modify: `shared/notify/channels.py` `_DEFAULTS` (add `"meta": {"enabled": False, "dry_run": True, "page_id": "", "ig_user_id": "", "access_token": ""}`, and the same non-dict fallback `x` has)
- Modify: `shared/notifications.example.json` (the `meta` block, empty values)
- Modify: `webgui/config_schema.py` (`_META_POST` ConfigFile, added to `FILES`)
- Test: `shared/tests/test_meta_text.py`, `webgui/tests/test_config_schema.py` (existing guard)

**Interfaces:**
- Produces:
  - `meta_text.config()` / `reset_cache()` with `DEFAULTS`:
    ```
    graph = {version = "v21.0", timeout_sec = 30, ig_poll_tries = 5, ig_poll_sec = 2}
    facebook = {enabled = true, daily_cap = 20, max_tags = 2}
    instagram = {enabled = true, daily_cap = 20, max_tags = 5, max_chars = 2200}
    kinds = {trade_idea = true, report = true}
    hashtags.instagram = {trade_idea = ["#optionstrading","#stockmarket","#trading","#options"], report = ["#stockmarket","#trading","#stocks","#investing","#SPY"]}
    hashtags.facebook  = {trade_idea = ["#optionstrading"], report = ["#stockmarket","#optionstrading"]}
    caption = {link_line_idea = "Full trade: {address}", link_line_report = "Full report: {address}", bio_line = "Link in bio."}
    ```
    (`version` is the build-time current Graph version; check Meta's changelog and set it.)
  - `meta_text.hashtag(symbol) -> str | None` — `"$SPX"` → `"#SPX"`, `"BRK/B"` → `"#BRKB"`, `""` → None.
  - `meta_text.tags(kind, network, symbol=None, cfg=None) -> list[str]` — derived `#<SYMBOL>` first, then the configured list, de-duplicated case-insensitively, capped at the network's `max_tags`.
  - `meta_text.facebook_text(body, tags) -> str` — body, blank line, tags joined by spaces.
  - `meta_text.instagram_text(body, kind, address, tags, cfg=None) -> str` — body, blank line, the link line with the address written WITHOUT `https://`, the bio line, blank line, tags; trimmed to `max_chars` by cutting the body (with `…`), never the address or the tags.

- [ ] **Step 1: Write the failing tests** in `shared/tests/test_meta_text.py`: `test_hashtag_strips_sigils` (`$SPX`→`#SPX`, `BRK/B`→`#BRKB`, `""`→None); `test_instagram_tags_cap_at_five` (`tags("trade_idea","instagram","SPY")` == `["#SPY","#optionstrading","#stockmarket","#trading","#options"]`); `test_tags_dedupe_case_insensitively` (`tags("report","instagram","SPY")` has one `#SPY`); `test_facebook_tags_cap_at_two`; `test_instagram_text_writes_the_address_out` (contains `Full trade: neuralstrike.co/t/2026-11-23-0930-SPY/` and `Link in bio.`, no `https://`); `test_instagram_text_cuts_the_body_not_the_tags` (a 3,000-char body → result ≤ 2,200, ends with the tags, contains the address); `test_config_defaults_on_a_missing_file` (monkeypatch the path to a missing file → `config()["instagram"]["max_tags"] == 5`).
- [ ] **Step 2: Run** `$VENV -m pytest shared/tests/test_meta_text.py` — expect FAIL.
- [ ] **Step 3: Implement** `shared/meta_text.py` (stdlib + `repo_paths` + `shared.config_toml` only), the TOML with comments in the style of `config/social_cards.toml`, the schema entry (every key; `restart=("options_svc",)`), the channels defaults and the example file.
- [ ] **Step 4: Run** `$VENV -m pytest shared/tests/test_meta_text.py shared/notify/tests` and `cd webgui && $VENV -m pytest tests/test_config_schema.py` — expect PASS.
- [ ] **Step 5: Commit** `feat(meta): captions, hashtags and config for Facebook and Instagram`.

### Task 8: `shared/notify/meta_post.py` — the one path to Meta

**Files:**
- Create: `shared/notify/meta_post.py`
- Test: `shared/notify/tests/test_meta_post.py`

**Interfaces:**
- Consumes: `channels.load_config()["meta"]`, `meta_text.config()`, `repo_paths.META_POSTS_LOG`, a bus with `cache_get`/`cache_set`.
- Produces:
  - `LOG_KEY = "cache:options:meta_log"`, `COUNT_KEY = "cache:options:meta_count"`, `LOG_KEEP = 100`, `KINDS = ("trade_idea", "report")`, `NETWORKS = ("facebook", "instagram")`.
  - `post(bus, *, kind, fb_text, ig_text, link, image_url, networks=NETWORKS, now=None, config=None, session=None, meta=None) -> dict` returning `{"dry_run": bool, "facebook": R, "instagram": R}` where each `R = {"status": "posted"|"unknown"|"failed"|"refused"|"dry_run"|"skipped", "id": str|None, "reason": str|None}`. A network not in `networks` is `skipped`. Never raises. `session` is a `requests.Session`-like object (tests pass a fake); default `requests.Session()`.
  - `posted_today(bus, network, now) -> int`.
- Rules (from the design): Facebook = `POST {graph}/{page_id}/feed` with `message`, `link`, `access_token`. Instagram = `POST {graph}/{ig_user_id}/media` with `image_url`, `caption`, then poll `GET {graph}/{container}?fields=status_code` up to `ig_poll_tries` until `FINISHED` (an `ERROR`/`EXPIRED`/never-finished container is `failed`), then `POST {graph}/{ig_user_id}/media_publish` with `creation_id`. A 4xx/JSON `error` = `failed` with `"<status>: <error.message[:200]>"`; Graph error code 190 = `failed` with reason `"token expired or revoked"`. A timeout, connection error or 5xx on `feed` or `media_publish` = `unknown` (counted, never retried); on `media` or the poll = `failed`. The token never appears in a log line, a reason or the log entry (`_sanitized` like `x_post`). One `threading.RLock` over the whole call. Each network: refused if master `enabled` false, the network or kind disabled, no credentials, or its daily cap reached; `dry_run` validates everything but the network and returns `dry_run` for each. Each network writes its own log entry `{at, kind, network, status, reason, id, link, meta}` to `LOG_KEY` and appends to `META_POSTS_LOG`.

- [ ] **Step 1: Write the failing tests** with a `FakeSession` recording calls and returning scripted responses (`.status_code`, `.json()`), and the `shared/notify/tests/conftest.py` fake bus:
  - `test_disabled_refuses_both` — `config={"meta": {"enabled": False}}` → both `refused`, reason `disabled`, no session call.
  - `test_dry_run_calls_nothing` — both `dry_run`, zero calls, two log entries.
  - `test_facebook_link_post` — one POST to `…/<page_id>/feed` with `link` and `message`; status `posted`, `id` from the answer.
  - `test_instagram_two_steps` — `media` → `{"id": "c1"}`, poll → `FINISHED`, `media_publish` → `{"id": "m1"}` → `posted`, `id == "m1"`; the `media` call carried `image_url` and `caption`.
  - `test_instagram_container_never_ready_is_failed_and_publish_not_called`.
  - `test_publish_timeout_is_unknown_and_counted` — `media_publish` raises `requests.Timeout` → `unknown`, `posted_today(bus, "instagram", now) == 1`.
  - `test_one_network_failing_does_not_stop_the_other` — feed returns 400 with a JSON `error` → facebook `failed`, instagram still `posted`.
  - `test_expired_token_says_so` — `{"error": {"code": 190, "message": "…"}}` → reason `token expired or revoked`.
  - `test_daily_cap_per_network` — facebook cap 1 with one already counted → facebook `refused`, instagram `posted`.
  - `test_token_never_logged` — a token `"SECRET123"`; after a failing call neither the log entries, the jsonl file nor `caplog.text` contain it. (Also add `meta_post` to `shared/notify/tests/test_no_secret_in_logs.py` the way `x_post` is covered.)
  - `test_unknown_kind_refused` — `kind="marketing"` → both `refused`.
  - `test_networks_argument_skips` — `networks=("instagram",)` → facebook `skipped`, no feed call.
- [ ] **Step 2: Run** `$VENV -m pytest shared/notify/tests/test_meta_post.py` — expect FAIL.
- [ ] **Step 3: Implement** the module per Interfaces and Rules; redirect `META_POSTS_LOG` in tests the way `test_jsonl_isolation.py` does for X.
- [ ] **Step 4: Run** `$VENV -m pytest shared/notify/tests` — expect PASS.
- [ ] **Step 5: Commit** `feat(meta): the one path to Facebook and Instagram`.

### Task 9: Post trade ideas and reports to Meta from `options_svc`

**Files:**
- Modify: `services/options_svc/handlers.py`
- Test: `services/options_svc/tests/test_meta_wiring.py` (new)

**Interfaces:**
- Consumes: `meta_post.post` (Task 8), `meta_text` (Task 7), `_publish_trade_idea_site` → address (Task 6), `report_pages.write` → address (Task 5), `trade_pages.page_id` (Task 2).
- Produces:
  - `META_IDEAS_KEY = "cache:options:meta_ideas"`, `META_REPORTS_KEY = "cache:options:meta_reports"`, each `{"facebook": [ids…], "instagram": [ids…]}`, newest first, last 100.
  - `_post_trade_idea_meta(bus, idea, address, now) -> dict` — after X in `run_trade_idea`; skips (returns `{"skipped": "no page"}`) when `address` is None, because Instagram has no image URL without the page and a Facebook link post needs a page; networks already holding the idea's pid in `META_IDEAS_KEY` are not sent; a network whose result is `posted` or `unknown` adds the pid. `result["meta"]` holds the outcome. Never raises (`_degrade.degraded("options.run_trade_idea.meta")`).
  - In `run_x_post_report`: after the X post (and whether or not X was already done for this report — the X dedup return must no longer return before Meta runs, and `report_pages.write` moves ahead of both dedup checks, after the age gate), `_post_report_meta(bus, report, address, now)` with the report identity as key in `META_REPORTS_KEY`, same age gate, skipped when `address` is None. Image URL = `address + "card.jpg"`.
  - Facebook text: `meta_text.facebook_text(trade_idea.caption(idea), tags(...,"facebook", symbol))`; report: `f"{slot_label}: {headline}"`. Instagram text: `meta_text.instagram_text(...)`.

- [ ] **Step 1: Write the failing tests** (patch `meta_post.post` to capture its kwargs and return scripted results; tmp site root):
  - `test_trade_idea_posts_to_meta_with_its_page` — `link` is the `/t/<pid>/` address, `image_url` is `…/t/<pid>/card.jpg`, `ig_text` contains `neuralstrike.co/t/`.
  - `test_trade_idea_meta_runs_after_x_and_x_failure_does_not_stop_it` — `x_post.post` raises → `meta_post.post` still called; `result["x"]["ok"] is False`.
  - `test_meta_failure_does_not_touch_the_ledger_or_cards` — `meta_post.post` raises → `result["ledger"]` and `publish_idea_cards` still run.
  - `test_the_same_idea_is_not_posted_twice` — second call with the same pid after a `posted` result → `networks` passed is `()` (or `post` not called).
  - `test_no_page_no_meta_post` — site switch off → `meta_post.post` not called, `result["meta"] == {"skipped": "no page"}`.
  - `test_report_posts_to_meta_once_per_network` — first call: facebook `failed`, instagram `posted`; second enqueue of the same report → `networks == ("facebook",)`; X not posted again.
  - `test_stale_report_not_posted_to_meta` — mtime older than the age gate → not called.
- [ ] **Step 2: Run** `$VENV -m pytest services/options_svc/tests/test_meta_wiring.py` — expect FAIL.
- [ ] **Step 3: Implement** as in Interfaces.
- [ ] **Step 4: Run** `$VENV -m pytest services/options_svc` — expect PASS, failing set unchanged.
- [ ] **Step 5: Commit** `feat(meta): trade ideas and market reports post to Facebook and Instagram`.

### Task 10: Social Cards shows the Facebook and Instagram log

**Files:**
- Modify: `webgui/pages/x_post.py` (a `meta_log_rows(payload) -> list[dict]` beside `log_rows`, and a second table under Recent posts titled `Facebook and Instagram`, read from `options:meta_log` with `bus_client.read`)
- Modify: `webgui/page_help.py` (the Social Cards entry: what the new table shows, that posting is automatic and starts in dry run)
- Test: `webgui/tests/test_x_post_page.py`

**Interfaces:**
- Consumes: `cache:options:meta_log` entries (Task 8).
- Produces: rows `{"when", "network" ("Facebook"/"Instagram"), "kind" ("Trade idea"/"Market report"), "status", "reason", "link"}`; a `posted` Facebook row links `https://facebook.com/<id>`; an Instagram row links the page address.

- [ ] **Step 1: Write the failing tests**: `test_meta_log_rows_names_the_network` (a scripted payload of one facebook `posted` and one instagram `dry_run` → two rows with those words); `test_meta_log_rows_tolerates_garbage` (`None`, `{"posts": 5}`, a row missing keys → `[]` or the good rows only).
- [ ] **Step 2: Run** `cd webgui && $VENV -m pytest tests/test_x_post_page.py` — expect FAIL.
- [ ] **Step 3: Implement** with `kit.table`, no inline style.
- [ ] **Step 4: Run** `cd webgui && $VENV -m pytest .` — expect PASS, failing set unchanged.
- [ ] **Step 5: Look at it** on the page harness (`tools/ui_harness.py`, loopback, with a fake `options:meta_log`): the table renders under Recent posts. Stop the harness.
- [ ] **Step 6: Commit** `feat(social): the Facebook and Instagram post log`.

### Task 11: Docs and the one-path rule

**Files:**
- Modify: `CLAUDE.md` (one line under Secrets/Running, beside the X rule: "Facebook and Instagram have ONE posting path (`shared/notify/meta_post.post`), called only from `options_svc`"; the `shared/notifications.json` row's "Holds" gains Meta)
- Modify: `docs/CHANGELOG.md`, `docs/manuals/` User Guide (setup steps 1–5 from the design, dry run, reading the log), API Reference (`cache:options:meta_log`, `meta_ideas`, `meta_reports`, `config/meta_post.toml`, the `meta` block), Technical Reference (caps, the hashtag rule)
- Test: `tests/test_claude_md_size.py` (existing)

- [ ] **Step 1: Write** the edits above; rebuild the manuals with `build_docs.py`.
- [ ] **Step 2: Run** `$VENV -m pytest tests/test_claude_md_size.py` and the manuals build — expect PASS / no error.
- [ ] **Step 3: Commit** `docs(meta): Facebook and Instagram posting`.

**Hand-off after Part 2's promote:** the operator does setup steps 1–5 of the design (Meta app, System User token, ids into `shared/notifications.json` on vps2, the Instagram bio link, a day on `dry_run`), confirms Instagram's hashtag cap in its own composer, then sets `enabled: true`, `dry_run: false`. On the first live post, record both accounts' appearance in the CHANGELOG.

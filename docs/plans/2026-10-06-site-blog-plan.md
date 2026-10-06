# A Blog on neuralstrike.co, fed from Claude Chat — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** A Blog page on neuralstrike.co whose entries are self-contained HTML
documents written in Claude Chat, received as drafts (through a custom connector
or an upload), approved in the private app, and served sandboxed.

**Architecture:** A new Tier-2 service, `blog_svc`, owns drafts and entries,
cleans each document, copies its typefaces onto the box and is the only writer
of `deploy/site/blog/`. The private app's `/blog` page drives it on `cmd:blog`.
A separate internet-facing process, `blog_gate`, speaks MCP and OAuth on its own
hostname and can only append to `cmd:blog_inbox`. Design:
[2026-10-06-site-blog-design.md](2026-10-06-site-blog-design.md). **Read it first.**

**Tech stack:** Python 3.11, FastAPI/uvicorn (`services/_scaffold.make_app`),
`lxml` (cleaning), `requests` (typeface fetch), SQLite, `shared.config_toml`,
NiceGUI page kit, static HTML/CSS/vanilla JS, Caddy, the `mcp` Python SDK
(phase 2), pytest.

**Test commands** (from the worktree root; a worktree has no venv, so
`$PY` = `"D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe"`). Each
service suite runs on its own, never `pytest services`:

```
$PY -m pytest shared/tests/test_blog_inbox.py -q
$PY -m pytest services/blog_svc -q
$PY -m pytest services/blog_gate -q
$PY -m pytest deploy -q
$PY -m pytest tests -q
$PY -m pytest tools/tests/test_backup_local.py -q
(cd webgui && $PY -m pytest tests/test_blog.py tests/test_shell.py tests/test_no_inline_style.py tests/test_ui_kit_guard.py tests/test_config_schema.py tests/test_page_help.py -q)
```

## Rules for whoever executes this

- **Write test files with the editor, never a heredoc.** A heredoc turns `\b`
  into a backspace byte and the test asserts nothing.
- **Never weaken an existing assertion to get green.** If a guard test fails,
  the change is wrong or the guard needs a deliberate, explained edit. Report
  every changed assertion in the commit body.
- **Compare the failing SET against a clean run, never the count.**
- `F401`/`F811` are reported at commit and never auto-fixed; the editor hook
  runs `ruff --fix`, so add an import in the same edit that uses it.
- No `.style(...)`, no inline `style=`, no raw `ui.button` / `ui.dialog` /
  `ui.notify` / `ui.table` in a page: use `pages/ui_kit.py`.
- Never name a service module after a stdlib module (`secrets`, `token`,
  `types`, `html`, `site`). **The site writer is `sitewriter.py`, not `site.py`.**
- Every tunable goes in `config/blog.toml` and the catalogue
  (`webgui/config_schema.py`); `test_config_schema.py` fails on an uncatalogued key.
- A swallowed exception in a guard of 15 lines or more calls
  `_degrade.degraded("<area>")`.
- Commit after every task. Stage by explicit path (another session may share
  the index).

---

# Phase 1 — the blog without the connector

### Task 1: paths, port, config file, ignores

**Files:** Modify `repo_paths.py`, `config/ports.toml`, `.gitignore`. Create
`config/blog.toml`. Test `tests/test_blog_paths.py` (new).

1. Failing tests:
   ```python
   import subprocess
   import repo_paths

   def test_the_blog_service_has_a_port_and_so_a_unit():
       # components() builds a unit from every SERVICE_PORTS key.
       assert repo_paths.SERVICE_PORTS["blog"] == 8217

   def test_the_blog_paths_live_under_the_service_and_the_site():
       assert repo_paths.BLOG_DATA == repo_paths.REPO_ROOT / "services" / "blog_svc" / "data"
       assert repo_paths.BLOG_DB == repo_paths.BLOG_DATA / "blog.db"
       assert repo_paths.BLOG_TOML == repo_paths.REPO_ROOT / "config" / "blog.toml"

   def test_generated_blog_state_is_never_committed():
       for probe in ("deploy/site/blog/x/index.html", "deploy/site/blog.json",
                     "services/blog_svc/data/blog.db"):
           res = subprocess.run(["git", "check-ignore", "-q", probe],
                                cwd=repo_paths.REPO_ROOT, capture_output=True)
           assert res.returncode == 0, f"{probe} is not gitignored"
   ```
2. Run: `$PY -m pytest tests/test_blog_paths.py -q` — expect 3 failures.
3. `config/ports.toml` `[services]`: `blog = 8217   # the site blog (blog_svc)`.
4. `repo_paths.py`: `BLOG_TOML` beside `EDGE_TOML`; `BLOG_DATA`, `BLOG_DB`
   beside the other service stores (find `NEWS_DB` and follow it).
5. `.gitignore`, beside the `ideas` block, with the same style of comment:
   ```
   services/blog_svc/data/
   deploy/site/blog/
   deploy/site/blog.json
   ```
6. `config/blog.toml` (comment every key, as `config/news.toml` does):
   ```toml
   [site]
   enabled = true          # off = drafts still arrive, nothing is written to the site
   republish_min = 30      # how often the views are re-published (heals a flushed Redis)

   [limits]
   max_html_kb = 512       # largest document accepted, before cleaning
   max_drafts = 20         # drafts waiting at once; a new one is refused past this
   submissions_per_hour = 12
   title_chars = 140
   summary_chars = 300
   max_tags = 6
   tag_chars = 24
   slug_chars = 80
   max_wait_sec = 120      # an inbox request older than this is answered "expired"
   answer_keep_sec = 120   # how long a connector answer key lives

   [fonts]
   enabled = true
   subsets = ["latin", "latin-ext"]
   max_files = 24
   max_file_kb = 400
   timeout_sec = 10
   ```
7. Run the test (3 pass), then `$PY -m pytest tests -q` and compare the failing
   set with a clean run: `test_systemd_units.py` and the Status page's
   `restart_spec` equality may now expect a `blog_svc` label. Fix by ADDING the
   label where the other six live (`webgui/pages/status.py` `svc_labels`,
   `config_schema.py` service constants), never by loosening the test.
8. Commit `feat(blog): port, paths, config file and ignores for the site blog`.

### Task 2: `shared/blog_inbox.py`

**Files:** Create `shared/blog_inbox.py`, `shared/tests/test_blog_inbox.py`.
Model: `shared/public_scan.py` and its test (read both first).

Public surface:

```python
INBOX_STREAM = "cmd:blog_inbox"     # the gate's ONLY write (phase 2 ACL names it)
OWNER_DOMAIN = "blog"               # cmd:blog: the private app's commands
VIEW_DRAFTS, VIEW_POSTS, VIEW_RESULT = "blog:drafts", "blog:posts", "blog:result"
def answer_view(request_id) -> str        # "blog:answer:<id>"; id must match ID_RE
ID_RE = re.compile(r"^[0-9a-f]{16}$")     # draft ids and request ids
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
RESERVED_SLUGS = frozenset({"fonts"})     # blog/fonts/ is the typeface folder
FONT_NAME_RE = re.compile(r"^[0-9a-f]{20}\.woff2$")

# One definition for the three places that must agree: the frame the service
# writes, the header the edge sends, the private preview.
ENTRY_SANDBOX = "allow-same-origin allow-popups allow-popups-to-escape-sandbox"
ENTRY_CSP = ("default-src 'none'; style-src 'unsafe-inline'; font-src 'self'; "
             "img-src 'self' data:; base-uri 'none'; form-action 'none'; "
             f"frame-ancestors 'self'; sandbox {ENTRY_SANDBOX}")

def clean_slug(raw) -> str | None         # lower, SLUG_RE, <= slug_chars, not reserved
def slugify(title) -> str                 # ascii-fold, non-alnum -> "-", "entry" when empty
def clean_fields(raw) -> dict             # {"title","summary","tags","slug"}: str-only,
                                          # trimmed, cut to the limits; unusable -> "" / []
def html_ok(html) -> bool                 # a non-empty str of <= max_html_kb (UTF-8 bytes)
def new_id() -> str                       # secrets.token_hex(8)
def submit_command(html, fields, *, source, request_id) -> dict | None
def revise_command(draft_id, html, fields, *, request_id) -> dict | None
def owner_command(kind, request_id, **args) -> dict | None   # publish/discard/unpublish
load, reset = toml_loader(BLOG_TOML, DEFAULTS, label="blog.toml")
def limits() -> dict; def fonts() -> dict; def site() -> dict   # validated, never raise
```

Failing tests (write all, watch them fail, then implement):

```python
def test_the_inbox_stream_is_its_own_and_never_the_owner_stream():
    assert bi.INBOX_STREAM == "cmd:blog_inbox"
    assert bi.INBOX_STREAM != f"cmd:{bi.OWNER_DOMAIN}"

@pytest.mark.parametrize("raw", ["", None, "Fonts", "fonts", "../x", "a--b", "-a",
                                 "a_b", "a b", "a/b", "x" * 200, "ü"])
def test_a_slug_that_could_escape_or_collide_is_refused(raw):
    assert bi.clean_slug(raw) is None

def test_slugify_always_returns_a_usable_slug():
    for title in ("Nuclear Stocks Thesis", "  ", "¿Qué?", "a/b\\c", "x" * 500, None):
        assert bi.clean_slug(bi.slugify(title)) is not None

def test_fields_are_strings_cut_to_the_limits_and_nothing_else_survives():
    f = bi.clean_fields({"title": " T " * 200, "summary": 7, "tags": ["a", 3, "b" * 99] * 9,
                         "slug": "../etc", "html": "<x>", "extra": 1})
    assert set(f) == {"title", "summary", "tags", "slug"}
    assert len(f["title"]) <= bi.limits()["title_chars"] and f["summary"] == ""
    assert len(f["tags"]) <= bi.limits()["max_tags"] and f["slug"] == ""

def test_a_document_over_the_limit_builds_no_command(monkeypatch):
    big = "x" * (bi.limits()["max_html_kb"] * 1024 + 1)
    assert bi.submit_command(big, {}, source="upload", request_id=bi.new_id()) is None

@pytest.mark.parametrize("source", ["chat", "upload"])
def test_a_submit_command_carries_the_document_and_cleaned_fields(source): ...
def test_an_unknown_source_or_a_bad_request_id_builds_no_command(): ...
def test_there_is_no_publish_command_for_the_inbox():
    """owner_command builds publish/discard/unpublish; nothing here puts one on
    INBOX_STREAM. The service enforces it; this pins the builder."""
    assert bi.owner_command("publish", bi.new_id(), draft_id=bi.new_id())["type"] == "publish"
    assert bi.owner_command("delete_everything", bi.new_id()) is None

def test_the_sandbox_never_allows_scripts():
    assert "allow-scripts" not in bi.ENTRY_SANDBOX and "allow-forms" not in bi.ENTRY_SANDBOX
    assert "sandbox " + bi.ENTRY_SANDBOX in bi.ENTRY_CSP and "default-src 'none'" in bi.ENTRY_CSP

def test_the_module_imports_nothing_but_stdlib_config_and_paths():
    """On the Tier-1 allow-list: no engine, no bus, no service. Copy the
    subprocess import-set check from shared/tests/test_public_scan.py."""

def test_bad_config_values_read_as_the_shipped_ones(tmp_path): ...
```

Steps: tests → fail → implement → pass → commit
`feat(blog): shared request builders, validators and config for the blog`.

### Task 3: `services/blog_svc/clean.py`

**Files:** Create `services/blog_svc/__init__.py`, `clean.py`,
`tests/__init__.py`, `tests/conftest.py` (copy `services/news_svc/tests/conftest.py`),
`tests/test_clean.py`, `tests/fixtures/entry_like_the_example.html` (a
**synthetic** 2–3 KB document shaped like the operator's: claude.ai wrapper
head, three Google Fonts `<link>`s, a `<style>` with light/dark tokens, an
inline `<svg>`, a table, outbound anchors. Do NOT commit the operator's file).

Public surface:

```python
@dataclass(frozen=True)
class Cleaned:
    html: str            # a complete document: doctype, head, body
    title: str           # <title>, else first <h1>, else ""
    summary: str         # first <p> text, cut to limits()["summary_chars"]
    removed: dict        # {"script": 2, "form": 1, "handler": 3, "link": 3, ...}
    font_links: tuple    # the fonts.googleapis.com/css2 hrefs found, in order

FONT_CSS_MARK = "/*blog-fonts*/"   # the empty <style> fonts.py fills in
def clean(html: str) -> Cleaned
```

Rules (each is a test):

- Parse with `lxml.html.document_fromstring`. Rebuild
  `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>…</title><style>/*blog-fonts*/</style>…every kept <style>, document order…</head><body>…</body></html>`.
- **Dropped with their content:** `script noscript iframe frame frameset object
  embed applet form input button select textarea option template audio video
  source track canvas img picture map area base meta link dialog`, SVG
  `script foreignObject image use animate animateMotion animateTransform set a`.
- **Kept HTML:** `a abbr article aside b blockquote br caption cite code col
  colgroup dd del details div dl dt em figcaption figure footer h1 h2 h3 h4 h5
  h6 header hr i ins kbd li main mark nav ol p pre q s section small span
  strong sub summary sup table tbody td tfoot th thead time tr u ul var wbr`.
- **Kept SVG:** `svg g path line rect circle ellipse polyline polygon text
  tspan title desc defs linearGradient radialGradient stop clipPath marker`.
- **Any other element is unwrapped** (`drop_tag()`): its text survives.
- **Attributes are an allow-list** (module constant `ATTRS`): `class id title
  lang dir role style colspan rowspan scope headers datetime open start
  reversed`, `aria-*`, `data-*`, `href` on `a` only, and the SVG geometry and
  paint attributes (`viewBox d x y x1 y1 x2 y2 cx cy r rx ry width height fill
  stroke stroke-width stroke-dasharray stroke-linecap stroke-linejoin opacity
  fill-opacity stroke-opacity transform points text-anchor font-size
  font-weight font-family dx dy offset stop-color stop-opacity gradientUnits
  gradientTransform preserveAspectRatio xmlns clip-path marker-start marker-end
  letter-spacing`). Everything else goes; every dropped `on*` counts as `handler`.
- **`href`:** kept only for `http:`, `https:`, `mailto:` and `#fragment`
  (compare after stripping whitespace and control characters and lowercasing
  the scheme). An outbound `a` gets `target="_blank" rel="noopener noreferrer"`;
  a fragment link gets neither. Any other `href` is removed, the text stays.
- **CSS, in `<style>` text and `style=` values:** remove every `@import …;`,
  replace every `url(…)` with `none`, remove `expression(`, `-moz-binding`,
  `behavior:`. (Typeface `url()`s are added later by `fonts.py`, into the
  marked `<style>` only.)
- `font_links`: `link[rel~=stylesheet]` whose href starts
  `https://fonts.googleapis.com/css2?`, collected BEFORE links are dropped.
- Never raises: input lxml cannot parse returns a `Cleaned` with an empty body
  and `removed == {"unparseable": 1}`.

Failing tests — the hostile corpus is the point; parametrize it:

```python
HOSTILE = [
    "<script>alert(1)</script>", "<SCRIPT SRC=//x></SCRIPT>", "<svg><script>1</script></svg>",
    "<img src=x onerror=alert(1)>", "<p onclick='x()'>t</p>", "<a href='javascript:alert(1)'>x</a>",
    "<a href=' JaVaScRiPt:alert(1)'>x</a>", "<a href='data:text/html,<script>1</script>'>x</a>",
    "<iframe src='https://e.com'></iframe>", "<object data=x></object>", "<embed src=x>",
    "<form action=//e.com><input name=a><button>go</button></form>",
    "<base href='https://e.com/'>", "<meta http-equiv=refresh content='0;url=//e.com'>",
    "<link rel=stylesheet href='https://e.com/x.css'>",
    "<style>@import url(https://e.com/x.css); p{background:url(https://e.com/t.gif)}</style>",
    "<p style='background:url(//e.com/t.gif)'>t</p>", "<style>p{behavior:url(x.htc)}</style>",
    "<svg><foreignObject><iframe src=x></iframe></foreignObject></svg>",
    "<svg><use href='https://e.com/x.svg#a'/></svg>", "<svg><a href='javascript:1'><text>x</text></a></svg>",
    "<style></style><script>1</script>", "<math><mtext><script>1</script></mtext></math>",
    "<details ontoggle=alert(1) open>x</details>", "<div data-x='1' srcdoc='<script>1</script>'>x</div>",
]

@pytest.mark.parametrize("payload", HOSTILE)
def test_nothing_hostile_survives(payload):
    out = clean.clean(f"<html><body><h1>T</h1>{payload}<p>kept</p></body></html>").html.lower()
    assert "kept" in out
    for needle in ("<script", "javascript:", "onerror", "onclick", "ontoggle", "<iframe",
                   "<object", "<embed", "<form", "<input", "<button", "<base", "http-equiv",
                   "<link", "@import", "e.com", "srcdoc", "<foreignobject", "<use", "behavior"):
        assert needle not in out, f"{needle!r} survived {payload!r}"

def test_the_example_shaped_entry_keeps_its_design():
    c = clean.clean(FIXTURE.read_text(encoding="utf-8"))
    assert c.title == "Nuclear Stocks Thesis" and len(c.font_links) == 1
    assert "<svg" in c.html and "<table" in c.html and "--accent" in c.html
    assert c.html.count('target="_blank"') == c.html.count('rel="noopener noreferrer"') > 0
    assert c.removed.get("link") == 3 and "fonts.googleapis.com" not in c.html

def test_the_download_wrapper_is_replaced_by_one_clean_shell(): ...   # one <head>, one <title>
def test_a_fragment_with_no_html_or_body_is_accepted(): ...            # what the chat sends
def test_a_fragment_link_is_not_opened_in_a_new_tab(): ...
def test_removed_counts_what_was_removed(): ...                        # {"script": 2, "handler": 1}
def test_unparseable_input_never_raises(): ...                         # "", "\x00", 5 MB of "<"
def test_cleaning_is_idempotent():                                     # clean(clean(x).html).html == clean(x).html
```

Steps: tests → fail → implement → pass → commit
`feat(blog): the document cleaner, against a hostile corpus`.

### Task 4: `services/blog_svc/fonts.py`

**Files:** Create `fonts.py`, `tests/test_fonts.py`.

```python
CSS_HOST, FILE_HOST = "fonts.googleapis.com", "fonts.gstatic.com"
@dataclass(frozen=True)
class Fonts:
    css: str          # @font-face rules pointing at ../fonts/<name>, "" when none
    files: dict       # {"<20 hex>.woff2": bytes}
    note: str         # "" or one sentence for the operator ("Typefaces could not be fetched …")

def http_fetch(url, *, headers, timeout, max_bytes) -> bytes   # requests, no redirects,
                                                               # host re-checked, size-capped
def localize(font_links, *, fetch=http_fetch, cfg=None) -> Fonts
def apply(html, fonts: Fonts) -> str     # replaces clean.FONT_CSS_MARK with fonts.css
```

Rules, each a test (the suite cannot reach the network: every test passes a
fake `fetch` that records its calls):

- Only an `https://fonts.googleapis.com/css2?…` link is fetched; anything else
  is skipped with a note. Sent with a current desktop Chrome `User-Agent`
  (Google serves `woff2` only to one).
- The returned CSS is split on `/* <subset> */ @font-face { … }`. A block is
  kept only if its subset is in `cfg["subsets"]`, it holds **exactly one**
  `url(`, and that URL is `https://fonts.gstatic.com/…` ending `.woff2`.
- Each file: fetched with `max_bytes = max_file_kb * 1024`; must start with
  `b"wOF2"`; named `sha256(bytes).hexdigest()[:20] + ".woff2"`; the block's URL
  is rewritten to `../fonts/<name>`.
- At most `max_files` files in total; past it, the rest are dropped and noted.
- Any exception from `fetch` → that link contributes nothing, `note` says so,
  **nothing raises**. `cfg["enabled"] is False` → `Fonts("", {}, "")`.
- `http_fetch` refuses a URL whose host is neither constant, and any redirect.

```python
def test_only_googles_two_hosts_are_ever_fetched(): ...       # assert every recorded URL's host
def test_only_the_configured_subsets_are_kept(): ...
def test_a_block_with_a_second_url_is_dropped(): ...
def test_a_file_that_is_not_woff2_is_dropped(): ...
def test_the_file_cap_holds_and_is_noted(): ...
def test_a_failed_fetch_never_raises_and_says_so(): ...
def test_the_same_bytes_get_the_same_name(): ...
def test_apply_fills_the_marked_style_and_nothing_else(): ...
def test_http_fetch_refuses_another_host_and_a_redirect(monkeypatch): ...   # monkeypatch requests
```

Commit `feat(blog): copy an entry's typefaces onto the box`.

### Task 5: `services/blog_svc/store.py`

**Files:** Create `store.py`, `tests/test_store.py`. Model the connection
handling on `services/news_svc/store.py` (`db_path=None` resolved at call time;
every write `BEGIN IMMEDIATE`).

```python
class Store:
    def __init__(self, db_path=None, data_dir=None)   # None -> repo_paths at CALL time
    def add_draft(self, draft: dict, html: str, font_files: dict) -> None
        # writes staging/<id>/entry.html and fonts/<name> (tmp + os.replace), then the row
    def replace_draft(self, draft_id, draft, html, font_files) -> bool
    def draft(self, draft_id) -> dict | None
    def drafts(self) -> list            # newest first
    def draft_html(self, draft_id) -> str | None
    def discard(self, draft_id) -> bool                 # row + staging folder
    def publish(self, draft_id, fields, now) -> dict | None
        # moves staging/<id>/entry.html to published/<slug>/entry.html, upserts the
        # entry (published_at kept on a revision, updated_at = now), deletes the draft
    def entry(self, slug) -> dict | None
    def entries(self) -> list           # newest published_at first
    def entry_html(self, slug) -> str | None
    def unpublish(self, slug) -> bool
    def fonts_in_use(self) -> set       # across drafts and entries
    def prune_fonts(self) -> int        # deletes fonts/<name> no draft or entry names
    def count_submissions_since(self, iso) -> int; def note_submission(self, iso) -> None
```

Schema: `drafts(id PK, source, revises, slug, title, summary, tags, removed,
fonts, font_note, bytes, received_at)`, `entries(slug PK, title, summary, tags,
fonts, published_at, updated_at)`, `submissions(at)`, `kv(key PK, value)` (the
connection state, used in phase 2). JSON columns are `json.dumps` text.

Every path is built from an id matching `blog_inbox.ID_RE`, a slug that
`clean_slug` accepts, or a name matching `FONT_NAME_RE`; anything else raises
`ValueError` **before** touching disk.

```python
def test_a_draft_round_trips_with_its_document(tmp_path): ...
def test_publish_moves_the_document_and_removes_the_draft(tmp_path): ...
def test_a_revision_keeps_the_first_publication_date(tmp_path): ...
def test_publishing_a_new_draft_onto_a_taken_slug_is_refused(tmp_path): ...   # returns None
def test_unpublish_removes_the_entry_and_its_document(tmp_path): ...
def test_a_font_still_named_by_a_draft_is_not_pruned(tmp_path): ...
@pytest.mark.parametrize("bad", ["../x", "a/b", "", "fonts", "A", "0" * 15])
def test_no_path_is_built_from_an_unvalidated_name(tmp_path, bad): ...        # ValueError, disk untouched
def test_the_default_paths_resolve_at_call_time(monkeypatch, tmp_path): ...   # monkeypatch repo_paths
```

Commit `feat(blog): the draft and entry store`.

### Task 6: `services/blog_svc/sitewriter.py`

**Files:** Create `sitewriter.py`, `tests/test_sitewriter.py`. Model:
`services/options_svc/site_ideas.py` (atomic writes, pruning, never raising).

```python
SITE_ROOT = pathlib.Path(repo_paths.SITE_ROOT)   # tests redirect this (and a conftest
                                                 # fixture does it for the whole suite)
MANIFEST, BLOG_DIR, FONTS_DIR, SITEMAP = "blog.json", "blog", "fonts", "sitemap.txt"
NAV_RE = re.compile(r'<nav class="ns-nav.*?</nav>', re.S)

def read_nav(root) -> str | None         # the <nav> block of the tracked blog.html, verbatim
def manifest(entries, updated) -> dict   # PURE: {"updated", "entries": [{"slug","title",
                                         # "summary","tags","published","updated"}]}, newest first
def entry_page(nav, entry, site_host) -> str   # PURE: the whole entry page
def write_entry(root, store, slug) -> None
def remove_entry(root, slug) -> None
def rebuild(root, store, now) -> bool    # every entry page, the fonts in use, the manifest,
                                         # blog/sitemap.txt; removes blog/<x> not in the store
```

`entry_page` rules:

- `<base href="/">` first in `<head>`, so the tracked menu's relative links
  and `assets/site.css` resolve from `blog/<slug>/`.
- `<title>NeuralStrike — {title}</title>`, `<meta name="description">`,
  `og:title` / `og:description` / `og:type=article` / `og:url`,
  `<link rel="canonical" href="https://{site_host}/blog/{slug}/">`, the three
  icon links and two stylesheets every site page declares.
- The menu is `nav` **byte for byte**.
- A compact header (the document carries its own headline): eyebrow `Blog`,
  the title as a small `<h1 class="ns-blog-crumb">`, then one line: the date,
  `All entries` (`blog.html`), `Open the entry on its own page`.
- `<iframe class="ns-report-frame" sandbox="{ENTRY_SANDBOX}" src="blog/{slug}/entry.html" title="{title}">`.
- The site's footer disclaimer, verbatim (`deploy/tests/test_site.py` `DISCLAIMER`).
- **No `<script>`.** Every interpolated value goes through `html.escape(…, quote=True)`.

`rebuild` writes each file to a temporary name and `os.replace`s it, deletes
only `blog/<name>` folders whose name `clean_slug` accepts and the store does
not hold, deletes only `blog/fonts/<name>` matching `FONT_NAME_RE` and unused,
returns False (and calls `_degrade.degraded("blog.site")`) on any failure, and
is a no-op returning True when `[site] enabled` is false or `read_nav` is None
(a checkout whose `blog.html` is missing must not publish menu-less pages).

```python
def test_the_entry_page_carries_the_tracked_menu_byte_for_byte(tmp_path): ...
def test_the_entry_page_escapes_every_field():
    page = sw.entry_page(NAV, {"slug": "a", "title": '"><script>x</script>',
                               "summary": "<b>", "tags": [], "published_at": ISO}, "h.co")
    assert "<script" not in page.split("</nav>")[1]
def test_the_frame_is_sandboxed_with_the_shared_tokens(): ...
def test_the_entry_page_runs_nothing(): ...                       # no "<script" at all
def test_the_manifest_is_newest_first_and_holds_no_html(): ...
def test_rebuild_writes_pages_fonts_manifest_and_sitemap(tmp_path): ...
def test_rebuild_removes_an_unpublished_entry_and_only_that(tmp_path):
    # a stray file "blog/README" and a folder "blog/Not_A_Slug" must survive
def test_rebuild_without_the_tracked_page_writes_nothing(tmp_path): ...
def test_a_failed_write_returns_false_and_raises_nothing(tmp_path, monkeypatch): ...
def test_site_disabled_writes_nothing(tmp_path, monkeypatch): ...
```

Add to `tests/conftest.py` an autouse fixture redirecting
`sitewriter.SITE_ROOT` and the store's default paths to `tmp_path`, so no test
can write into this checkout (the options_svc conftest does the same for
`site_ideas`).

Commit `feat(blog): write entry pages, the manifest and the sitemap`.

### Task 7: `services/blog_svc/handlers.py`, `scheduler.py`, `app.py`

**Files:** Create the three modules, `tests/test_handlers.py`, `tests/test_app.py`.
Read first: `services/options_svc/finder_public.py` `handle` (the age check on
an extra stream, lines ~183–210) and `services/news_svc/app.py`.

```python
OWNER_COMMANDS = {"draft_submit": _cmd_submit, "publish": _cmd_publish,
                  "discard": _cmd_discard, "unpublish": _cmd_unpublish}
INBOX_COMMANDS = {"draft_submit": _cmd_submit, "draft_revise": _cmd_revise}

def handle_command(bus, command) -> None    # cmd:blog
def handle_inbox(bus, command) -> None      # cmd:blog_inbox: age, rate and count limits first
def publish_views(bus, store) -> None       # blog:drafts, blog:posts (skip_unchanged, no ts)
def answer(bus, request_id, command, ok, message, **extra) -> None
    # ALWAYS writes blog:result; also blog:answer:<id> with ttl=answer_keep_sec
```

- `_cmd_submit` / `_cmd_revise`: re-validate with `blog_inbox` (never trust
  the builder ran), `clean.clean`, `fonts.localize`, `fonts.apply`, default
  the title, summary and slug from the cleaned document, store, republish views.
  A draft whose cleaned body is empty is refused ("the document had no content").
  `source` is forced to `"chat"` on the inbox path and `"upload"` on the owner path.
- `_cmd_publish`: fields from the command (title, summary, slug, tags — the
  operator's edits), `store.publish`, `sitewriter.rebuild`, republish views.
  If `rebuild` returns False the entry stays published in the store and the
  answer says the site write failed.
- Every command answers, including a refusal. Messages are whole sentences
  for the operator's screen and never contain an exception's text.
- Inbox limits, in order, each answered without touching the cleaner:
  `expired` (older than `max_wait_sec`), `rate` (`submissions_per_hour`),
  `full` (`max_drafts`).
- `scheduler.loop`: first pass `sitewriter.rebuild` + `publish_views`; then
  `publish_views` every `[site] republish_min`.
- `app.py`: `make_app("blog", scheduler=scheduler.loop,
  command_handler=handlers.handle_command,
  extra_consumers=((blog_inbox.INBOX_STREAM, handlers.handle_inbox),))`, uvicorn
  on `SERVICE_PORTS["blog"]`, host `127.0.0.1`.

```python
def test_an_upload_becomes_a_draft_and_never_an_entry(bus, store): ...
def test_publish_writes_the_site_and_empties_the_draft(bus, store, site): ...
def test_the_inbox_cannot_publish_discard_or_unpublish(bus, store):
    for kind in ("publish", "discard", "unpublish"):
        assert kind not in handlers.INBOX_COMMANDS
        handlers.handle_inbox(bus, _cmd(kind, draft_id=DRAFT))
    assert store.entries() == [] and store.draft(DRAFT) is not None
def test_an_inbox_draft_is_labelled_chat_whatever_it_claims(bus, store): ...
def test_an_expired_inbox_request_is_answered_and_not_cleaned(bus, store, monkeypatch): ...
def test_the_hourly_limit_and_the_draft_cap_refuse(bus, store): ...
def test_every_command_is_answered(bus, store): ...        # parametrize ok and refused paths
def test_no_answer_carries_an_exception_text(bus, store, monkeypatch): ...
def test_no_view_carries_a_document(bus, store):
    # json.dumps of blog:drafts / blog:posts / blog:result contains no "<"
def test_a_failed_site_write_is_reported_and_degrades(bus, store, monkeypatch): ...
def test_the_app_reads_both_streams(): ...                 # /health lists cmd:blog and cmd:blog_inbox
```

Run `$PY -m pytest services/blog_svc -q`, `$PY -m pytest tests -q` and
`$PY -m pytest services/tests/test_no_silent_degrades.py -q` (it now walks
this service). Commit
`feat(blog): the blog service - commands, views and its two streams`.

### Task 8: the site — `blog.html`, `blog.js`, the menu, the sitemap

**Files:** Create `deploy/site/blog.html`, `deploy/site/assets/blog.js`.
Modify `deploy/site/assets/site.css`, every page in `deploy/site/*.html` (the
menu), `deploy/site/sitemap.txt`, `deploy/site/robots.txt`,
`deploy/tests/test_site.py`.

1. Tests first, in `test_site.py`:
   - `PAGES` and `MARK_LARGE_FILES` gain `"blog.html"`;
     `GENERATED_REF_PREFIXES` gains `"blog/"` with a comment naming
     `services/blog_svc/sitewriter.py` and the test that pins those paths.
   - Update the "ALL six pages" comment in every page's menu to seven.
   - New section "Blog":
     ```python
     def test_the_blog_is_in_every_menu_between_trade_ideas_and_the_glossary():
         for name in PAGES:
             nav = _nav(name)
             assert nav.index('href="ideas.html"') < nav.index('href="blog.html"') \
                 < nav.index('href="glossary.html"'), name
     def test_the_blog_page_marks_itself_current(): ...
     def test_the_blog_page_draws_from_the_manifest_and_says_so_when_empty(): ...
         # data-blog-page, data-blog-list, data-blog-empty hidden, assets/blog.js deferred
     def test_the_blog_script_builds_text_never_markup():
         js = _text("assets/blog.js")
         assert "innerHTML" not in js and "document.write" not in js
         assert 'cache: "no-cache"' in js and "textContent" in js
     def test_the_blog_script_reaches_nothing_off_origin(): ...
     def test_the_blog_is_in_the_sitemap_and_robots_names_the_entry_sitemap(): ...
     def test_the_generated_blog_is_never_committed(): ...     # git check-ignore
     ```
   - Read `test_each_page_carries_its_own_content_without_scripting` and
     follow what `ideas.html` does to satisfy it (static heading and lede).
2. `blog.html`: copy `ideas.html`; change title, description, the comment
   block (what is generated, by which module, why gitignored), `aria-current`,
   and `<main data-blog-page>` with an eyebrow, an `<h1>`, a lede in plain
   words ("Longer pieces on the market … each written with Claude and
   published after review."), `<div class="ns-blog-list" data-blog-list>` and
   `<p class="ns-live-note" data-blog-empty hidden>No entries have been
   published yet.</p>`. **No timestamp**, for the reason `ideas.html` gives.
3. `assets/blog.js`: fetch `blog.json` with `cache: "no-cache"`; drop any row
   whose `slug` fails `/^[a-z0-9]+(?:-[a-z0-9]+)*$/` or whose title is not a
   non-empty string; for each row build
   `<a class="ns-blog-card" href="blog/<slug>/">` holding the title, a
   `<time>` (formatted in `en-US`, UTC noon, as `ideas.js` does), the summary
   and the tags — `createElement` and `textContent` only. Missing or malformed
   manifest, or no rows: unhide the empty note.
4. `site.css`: `.ns-blog-list`, `.ns-blog-card` (title, date, summary, tags),
   `.ns-blog-crumb`; reuse the existing tokens, add no colour literal that a
   token already names.
5. Add `<a class="ns-navlink" href="blog.html">Blog</a>` after Trade ideas in
   **every** page's menu (the byte-identity test compares them).
6. `sitemap.txt`: `https://neuralstrike.co/blog.html`. `robots.txt`: a second
   line `Sitemap: https://neuralstrike.co/blog/sitemap.txt`.
7. `$PY -m pytest deploy -q`; compare the failing set with a clean run.
8. Commit `feat(site): the Blog page and its menu entry`.

### Task 9: the edge — the `/blog/` policy

**Files:** Modify `deploy/caddy/generate_caddyfile.py`,
`deploy/caddy/tests/test_caddyfile.py`.

1. Failing tests:
   ```python
   def test_a_blog_entry_is_served_under_the_shared_policy(cfg):
       from shared import blog_inbox
       pub = _block(cfg, SITE_HOST)                      # use the file's own block helper
       assert "@blog_entries path_regexp" in pub
       assert f'header @blog_entries Content-Security-Policy "{blog_inbox.ENTRY_CSP}"' in pub
   def test_the_blog_policy_names_only_entry_documents(cfg): ...   # ^/blog/[a-z0-9-]+/entry\.html$
   def test_the_blog_policy_allows_no_script_and_no_form(cfg): ...
   ```
   and extend `test_caching_is_declared_only_where_the_files_are` /
   `test_every_matcher_the_cache_rules_name_is_defined` only as far as the new
   matcher requires.
2. In `_public_block`, after the `@ideas` rule, with a comment explaining the
   three isolation layers and that the string is `shared.blog_inbox.ENTRY_CSP`:
   ```
   @blog_entries path_regexp ^/blog/[a-z0-9-]+/entry\.html$
   header @blog_entries Content-Security-Policy "{ENTRY_CSP}"
   ```
   The generated file must stay pure ASCII (a test pins it). Blog typefaces
   are already covered by the `*.woff2` rule; say so in the comment.
3. `$PY -m pytest deploy -q`. Commit `feat(edge): a no-script policy on blog entries`.

### Task 10: the private page — `/blog`

**Files:** Create `webgui/pages/blog.py`, `webgui/tests/test_blog.py`. Modify
`webgui/main.py`, `webgui/tests/test_shell.py`,
`webgui/tests/test_no_inline_style.py`, `webgui/config_schema.py`,
`webgui/page_help.py`. Model: `webgui/pages/x_post.py` (upload, confirm, busy
button held until the view moves) and `webgui/pages/desk.py:1912` (an
`ui.element("iframe")`).

1. Pure helpers first, each with a test in `test_blog.py`:
   ```python
   VIEW_DRAFTS, VIEW_POSTS, VIEW_RESULT            # from shared.blog_inbox
   def draft_rows(payload) -> list     # tolerant of None / junk; never raises
   def post_rows(payload, site_host) -> list       # adds "url": https://<host>/blog/<slug>/
   def removed_text(removed) -> str    # {"script": 2, "form": 1} -> "Removed 2 scripts and 1 form"
                                       # {} -> "Nothing was removed"
   def source_text(source) -> str      # "chat" -> "Claude Chat", "upload" -> "Uploaded file"
   def size_text(n) -> str             # bytes -> "35 KB" (through pages/fmt)
   def decode_upload(data: bytes) -> str | None    # UTF-8 (BOM tolerated) or None
   def preview_src(draft_id) -> str    # "/blog/preview/<id>/entry.html"; "" for a bad id
   ```
   Tests include: junk payloads; `removed_text` singular/plural; `decode_upload`
   on UTF-16 and binary; `preview_src("../x") == ""`; and the Tier-1 rule:
   ```python
   def test_the_page_imports_no_service_and_no_engine():
       src = (PAGES / "blog.py").read_text(encoding="utf-8")
       assert "services." not in src and "sqlite3" not in src and "lxml" not in src
   ```
2. `render()` with the kit: `kit.page`, `kit.header("Blog", view=VIEW_DRAFTS)`.
   - **Upload** card: `ui.upload(auto_upload=True, max_file_size=…)` accepting
     `.html,text/html`; on upload, `decode_upload`, then
     `bus_client.request(OWNER_DOMAIN, blog_inbox.submit_command(html, {}, source="upload", request_id=new_id()))`.
     A `None` command (too large) or undecodable file → `kit.toast("warn", …)`.
   - **Drafts waiting**: one card per draft — source, size, received time
     (Central), `removed_text`, the font note when present; `kit.text_field`s
     for title, summary, address and tags; buttons Preview, Publish
     (`kit.confirm`), Discard (`kit.confirm`). Publish sends the fields as
     edited. `kit.empty("No drafts are waiting.")` otherwise.
   - **Preview**: a `kit` dialog holding
     `ui.element("iframe").props(f'sandbox="{ENTRY_SANDBOX}"')` with an
     explicit height class; `src` set on open and reset to `about:blank` on close.
   - **Published**: `kit.table` — title, published date, address; row actions
     Open on the site (new tab) and Unpublish (`kit.confirm`).
   - Watch `VIEW_DRAFTS`, `VIEW_POSTS` and `VIEW_RESULT` with
     `pages.view_watch.watch_view`; a new `VIEW_RESULT` toasts its `message`
     (`"ok"` → `"info"`, else `"warn"`) and releases the busy button. Wrap
     every handler and timer in `pages.ui_guard`.
   - Labels are whole words from the reader's side ("Address" not "Slug",
     "Claude Chat" not "chat").
3. `main.py`:
   - `MORE_CHILDREN`: `("/blog", "Blog", "article")` after Post to X;
     `_TAB_COLOR["/blog"]`; `@_page("/blog")` inside `_layout("/blog", "Blog")`.
   - Two raw routes, beside `/eod/file`, each validating before touching disk:
     ```python
     @app.get("/blog/preview/fonts/{name}")       # FONT_NAME_RE -> FileResponse font/woff2
     @app.get("/blog/preview/{draft_id}/entry.html")   # ID_RE -> HTMLResponse,
         # headers={"Content-Security-Policy": blog_inbox.ENTRY_CSP}
     ```
     Paths come from `repo_paths.BLOG_DATA`. Anything else is a 404 with a
     plain body. Register the fonts route FIRST (it would otherwise match
     `{draft_id}`).
   - Tests (in `test_blog.py`, through `main.app` with the test client the
     other raw-route tests use; if none exists, test the two route FUNCTIONS
     directly with `tmp_path` via monkeypatched `repo_paths.BLOG_DATA`):
     a good id serves the staged file with the CSP header; `..%2f`, an
     over-long id and an unknown id are 404; the fonts route refuses a name
     that is not 20 hex + `.woff2`.
4. `test_shell.py`: `/blog` in the expected routes. `test_no_inline_style.py`:
   `blog.py` in the page list.
5. `config_schema.py`: a `_BLOG = ConfigFile(name="blog.toml", title="Site
   blog", icon="article", restart=(BLOG,), …)` with a section per table and a
   field per key (plain-language help, bounds matching the validators in
   `shared/blog_inbox.py`); add `BLOG = "blog_svc"` beside `NEWS` and its
   label; append `_BLOG` to `FILES`.
6. `page_help.py`: a `/blog` guide — what a draft is, that nothing publishes
   without Publish, what cleaning removes, what Unpublish does. Run
   `tests/test_page_help.py`.
7. Run the webgui command at the top of this plan. Commit
   `feat(webgui): the Blog page - upload, preview, publish`.

### Task 11: the guards that list things

**Files:** Modify `.github/workflows/ci.yml`,
`tests/test_ci_covers_every_suite.py` (only if it carries its own list),
`pyrightconfig.json` (**no change — do not widen it**; just confirm the
`typecheck` job is still clean).

1. `ci.yml`: a `blog_svc` row beside `news_svc`
   (`python -m pytest services/blog_svc -q`).
2. `$PY -m pytest tests tools/tests/test_backup_local.py -q`. The backup test
   derives data trees from `.gitignore`; `services/blog_svc/data/` must appear
   as swept. If it lists trees by hand anywhere, add it there.
3. Commit `ci: run the blog service suite`.

### Task 12: see it work

There is no dev environment. Two substitutes, both required before a promote.

1. **The private page**, in `tools/ui_harness.py` (read its docstring): fake
   bus, the real `blog_svc` handlers, `BLOG_DATA` and `SITE_ROOT` under a temp
   folder, the typeface fetch faked to return one small real `woff2` (any
   `deploy/site/assets/inter-latin.woff2` bytes). Confirm the port is free
   first (a failed bind is silent). Upload the synthetic fixture; check the
   draft card, the removed-items line, Preview (typeface applied inside the
   frame, no console errors), Publish, the Published table, Unpublish.
   Screenshot the draft card and the preview.
2. **The site**: a throwaway static server over the temp `SITE_ROOT` (copy
   `deploy/site` into it first) that sends `ENTRY_CSP` on
   `/blog/*/entry.html` — a 20-line `http.server` subclass in the scratchpad,
   not committed. Check `blog.html` (list, empty state with the manifest
   removed), an entry page (menu works, frame fills, typeface loads under the
   sandbox, an outbound link opens a new tab, light and dark via
   `resize_window colorScheme`), and the entry document opened directly (same
   policy applies; no script runs: add `<script>document.title="RAN"</script>`
   to a copy and confirm the title does not change).
3. Stop both servers. If the typeface does not load in the frame, **stop and
   report**; do not add `allow-scripts` or loosen the policy to get there.
4. Also upload the operator's real example
   (`E:\Users\john_\Downloads\Nuclear Stocks Thesis.html`) through the harness,
   with the real `http_fetch`, and compare the preview with the file opened
   directly. This is a local check only; the file is not committed.

### Task 13: phase 1 docs

**Files:** `docs/CHANGELOG.md`, `docs/webgui-routes.md`, `docs/reference/blog.md`
(new: the isolation layers and why `allow-same-origin` without
`allow-scripts`; the store layout; what cleaning removes), the User Guide and
Reference Guide (`/blog`), the Technical Reference (what is cleaned, where
typefaces come from), `docs/dev-prod-environments.md` (the one operator step:
regenerate and reload Caddy), `CLAUDE.md` **edited in place**: seven services
and port 8217 wherever six and the port list are stated, `shared.blog_inbox`
on the Tier-1 allow-list with its pin test, `blog` in the config-file list, one
line under the web tier linking the reference doc. Then
`$PY -m pytest tests/test_claude_md_size.py -q`, and rebuild the manuals
(`docs/manuals/build_docs.py`). Commit
`docs: the site blog - manuals, routes, changelog, reference`.

**Phase 1 is promotable here.** After the promote the operator regenerates
and reloads Caddy; until then entries are served without the policy header
(cleaning and the frame's sandbox still apply).

---

# Phase 2 — the connector

### Task 14: pin `mcp` and confirm its API

The design assumes the `mcp` SDK's server-side OAuth
(`mcp.server.auth.provider.OAuthAuthorizationServerProvider`,
`mcp.server.auth.settings.AuthSettings` / `ClientRegistrationOptions`,
`FastMCP(auth_server_provider=…, auth=…, stateless_http=True,
json_response=True, transport_security=…)`, `custom_route`,
`streamable_http_app()`). **Confirm against the installed package, not memory.**

1. `pip download mcp --no-deps -d <scratchpad>` to read the current version and
   its `Requires-Dist`; choose the newest 1.x whose requirements the lock
   already satisfies or nearly so.
2. Add `mcp==<version>` to `requirements.txt` with a comment, and to
   `requirements.lock` **by hand**, with any new transitive pins. Never
   `pip freeze`.
3. Install into the local venv; in a scratch script build a `FastMCP` with a
   stub provider and print its routes. Record in `docs/reference/blog.md` the
   exact route paths (`/authorize`, `/token`, `/register`, the two
   `.well-known` paths, `/mcp`) — Task 20's edge rule names them.
4. Confirm `python -c "import services.blog_svc.app"` still imports with `mcp`
   absent from its import graph (the service must not need it).
5. Commit `build: pin the mcp SDK for the blog connector`.

If the installed API differs from the names above, **stop and report** before
writing Task 18 against a guess.

### Task 15: the gate's host, port, environment and unit

**Files:** Modify `repo_paths.py`, `config/ports.toml`,
`deploy/systemd/generate_units.py`, `.gitignore`, `tools/backup_local.py`,
`webgui/pages/status.py`. Tests: `tests/test_blog_paths.py`,
`tests/test_systemd_units.py`, `tests/test_env_profile.py`,
`tools/tests/test_backup_local.py`.

1. Failing tests: `BLOG_GATE_PORT == 8502` and is offset in a dev profile
   (extend the `_derive_ports` test); `MCP_HOST == f"mcp.{SITE_HOST}"` and is
   overridable by `mcp_host` in the env marker; `components()` includes
   `("blog_gate", BLOG_GATE_PORT, "services/blog_gate/app.py")`; that unit's
   `EnvironmentFile` is `.env.blog` and **not** `.env`; `.env.blog` is
   gitignored and in `EXTRA_FILES`; the Status card's restart name equals the
   component.
2. `ports.toml` top level: `blog_gate = 8502` with a comment (internet-facing,
   offset like `nicegui_live`). `_derive_ports`: `"blog_gate_port"`.
3. `generate_units`: replace the `is_live` env-file switch with a small map
   `{"webgui_live": _live_env_file, "blog_gate": _blog_env_file}`; write
   `_blog_env_file`'s docstring on the model of `_live_env_file`'s (blast
   radius: this process needs `REDIS_BLOG_URL` alone). The gate depends on
   Redis only, like `webgui_live`. No memory cap.
4. Commit `feat(blog): a unit, port and environment file for the connector gate`.

### Task 16: the connection, in `blog_svc`

**Files:** Modify `shared/blog_inbox.py`, `services/blog_svc/store.py`,
`handlers.py`. Create `services/blog_svc/connection.py`,
`tests/test_connection.py`. Add `[connect]` to `config/blog.toml` and the
catalogue:

```toml
[connect]
code_ttl_min = 10
code_tries = 5
access_token_min = 60
refresh_token_days = 30
redirect_uris = ["https://claude.ai/api/mcp/auth_callback",
                 "https://claude.com/api/mcp/auth_callback"]
```

```python
# shared/blog_inbox.py
VIEW_CONNECT = "blog:connect"
def req_hash(req_id) -> str                       # sha256 hex; what a view may name
def connect_request_command(req_id, client_name, redirect_uri, code) -> dict | None
def connect() -> dict                             # validated [connect]

# services/blog_svc/connection.py  (pure state transitions over a dict kept in store.kv)
def request(state, req_id, client_name, redirect_uri, code, now) -> dict
def approve(state, typed_code, now, cfg) -> tuple[dict, bool]   # bumps epoch on success
def revoke(state, now) -> dict                                  # bumps epoch
def view(state, now, cfg) -> dict
    # {"epoch", "connected": {"client_name","since"} | None,
    #  "pending": [{"client_name","redirect_uri","received"}],
    #  "approved": {"<req_hash>": iso}}
```

Handlers: `connect_request` on the **inbox** table; `connect_approve` and
`connect_revoke` on the **owner** table only.

```python
def test_the_right_code_approves_and_bumps_the_epoch(): ...
def test_a_wrong_code_approves_nothing_and_five_void_every_pending_request(): ...
def test_an_expired_request_cannot_be_approved(): ...
def test_a_code_is_compared_in_constant_time_and_stored_hashed(): ...   # hmac.compare_digest; no code in kv
def test_the_view_never_names_a_code_or_a_request_id():
    v = json.dumps(connection.view(state, NOW, CFG))
    assert CODE not in v and REQ_ID not in v and bi.req_hash(REQ_ID) in v
def test_revoke_bumps_the_epoch_and_clears_the_connection(): ...
def test_the_inbox_cannot_approve_or_revoke(bus, store): ...
def test_approval_is_single_use(): ...
```

Commit `feat(blog): approve and revoke the connector from the service`.

### Task 17: the gate's token store

**Files:** Create `services/blog_gate/__init__.py`, `oauth_store.py`,
`tests/__init__.py`, `tests/conftest.py`, `tests/test_oauth_store.py`.

```python
class OAuthStore:                  # one JSON file, mode 0600, tmp + os.replace, a threading.Lock
    def __init__(self, path=None)                  # None -> BLOG_GATE_DATA / "oauth.json" at call time
    def save_client(self, info: dict) -> None      # keeps the newest MAX_CLIENTS (20)
    def client(self, client_id) -> dict | None
    def add_token(self, kind, token, client_id, epoch, expires_at) -> None   # stores sha256(token)
    def token(self, kind, token) -> dict | None    # None when missing or expired
    def drop_token(self, token) -> None
    def drop_epochs_below(self, epoch) -> int
```

```python
def test_no_token_is_stored_in_clear(tmp_path): ...       # the token string is not in the file
def test_the_file_is_owner_only(tmp_path): ...            # skip the mode assert on Windows
def test_an_expired_token_reads_as_absent(tmp_path): ...
def test_registrations_are_capped(tmp_path): ...
def test_a_torn_or_missing_file_reads_as_empty(tmp_path): ...
```

`services/blog_gate/data/` joins `.gitignore` (and so the backup sweep).
Commit `feat(blog-gate): the token store`.

### Task 18: the gate's sign-in

**Files:** Create `services/blog_gate/provider.py`, `connect_page.py`,
`tests/test_provider.py`, `tests/test_connect_page.py`.

`provider.GateProvider` implements the SDK protocol confirmed in Task 14:

- `register_client`: refused unless EVERY redirect URI is in
  `[connect] redirect_uris` (raise the SDK's registration error).
- `authorize(client, params)`: `req_id = secrets.token_urlsafe(32)`; an
  8-character code from an unambiguous alphabet (no `0O1I`), shown as `XXXX-XXXX`;
  keep `{client, params, code, created}` in memory (bounded: newest 20,
  expired dropped); `XADD` `connect_request_command(...)`; return
  `https://{MCP_HOST}/connect?req=<req_id>`.
- `load_access_token`: the store's token, AND its epoch must equal the epoch
  in `cache:blog:connect`; a missing view refuses (fail closed).
- `exchange_authorization_code` / `exchange_refresh_token`: tokens are
  `secrets.token_urlsafe(32)`, issued at the CURRENT epoch; refresh tokens
  rotate; lifetimes from `[connect]`.
- `revoke_token`: drop it.

`connect_page` (three `custom_route`s, plain `HTMLResponse`, no script but one
`<meta http-equiv="refresh" content="3">` on the waiting page):

- `GET /connect?req=` — unknown or expired → a plain "This request has
  expired. Start again from Claude." Otherwise the code, the app's address
  (`https://{APP_HOST}/blog`) and one sentence: type this code on the Blog page.
  When `req_hash(req)` is in the view's `approved` → 302 to `/connect/done?req=`.
- `GET /connect/done?req=` — approved, fresh and unused → mint the
  authorization code, drop the pending request, 302 to the client's
  `redirect_uri` with `code` and `state` (use the SDK's redirect helper).
  Anything else → the expired page.
- `GET /health` → `{"up": true}`.

```python
def test_a_client_with_any_unlisted_redirect_uri_is_refused(): ...
def test_authorize_sends_one_request_and_returns_the_gates_own_page(): ...
def test_the_code_page_shows_the_code_only_to_the_request_id(): ...
def test_done_before_approval_issues_nothing(): ...
def test_done_after_approval_redirects_to_the_registered_callback_once(): ...
def test_a_token_from_an_older_epoch_is_refused(): ...
def test_a_missing_connection_view_refuses_every_token(): ...
def test_a_refresh_rotates_and_the_old_refresh_token_is_dead(): ...
def test_pending_requests_are_bounded(): ...
```

Commit `feat(blog-gate): OAuth sign-in approved from the private app`.

### Task 19: the gate's tools and entrypoint

**Files:** Create `services/blog_gate/tools.py`, `acl.py`, `app.py`,
`tests/test_tools.py`, `tests/test_acl.py`, `tests/test_app.py`. Add the
`blog_gate` CI row.

- `tools.py`: `submit_draft(html, title="", summary="", tags=None, slug="")`,
  `revise_draft(draft_id, html, …)`, `list_entries()`, `get_entry(slug="",
  draft_id="")`. Each tool's docstring is what Claude Chat reads: say that a
  submission becomes a draft the owner must approve, that the whole document
  goes in `html`, and that the tool cannot publish. A write builds the command
  with `shared.blog_inbox`, `XADD`s it, then polls `answer_view(request_id)`
  for up to `max_wait_sec` and returns the service's `message`. `list_entries`
  reads the two views. `get_entry` validates the id or slug, checks it is in
  the views, and reads the document from `repo_paths.BLOG_DATA`.
- `acl.py`: `require_url(environ, env_name)` and `require_write_scope(client,
  env_name)` on the model of `webgui/live_main.py` `require_acl_url` /
  `write_probe` / `require_read_only` (**copy the logic; the gate may not
  import `webgui`**). Prod refuses to serve unless `REDIS_BLOG_URL` is set,
  names a non-default user, a `SET` on a cache key is refused, an `XADD` on
  `cmd:blog` is refused, and an `XADD` on `cmd:blog_inbox` is **allowed** (use
  a `{"type": "probe"}` command the service ignores).
- `app.py`: build the `FastMCP` (`stateless_http=True`, `json_response=True`,
  `transport_security` allowing `MCP_HOST`, issuer `https://{MCP_HOST}`,
  resource `https://{MCP_HOST}/mcp`), register tools and routes, run uvicorn on
  `127.0.0.1:BLOG_GATE_PORT`. Importable without side effects.

```python
def test_a_submission_is_one_inbox_command_and_returns_the_services_answer(): ...
def test_an_oversized_document_is_refused_before_anything_is_written(): ...
def test_a_tool_times_out_with_a_sentence_not_an_exception(): ...
def test_get_entry_reads_only_an_id_or_slug_the_views_name(tmp_path): ...   # "../x" reads nothing
def test_there_is_no_tool_that_publishes():
    assert set(tools.TOOL_NAMES) == {"submit_draft", "revise_draft", "list_entries", "get_entry"}
def test_the_gate_imports_nothing_from_the_service_or_the_webgui():
    # subprocess: import services.blog_gate.app; assert no "services.blog_svc",
    # "webgui", "main", "lxml" key in sys.modules
def test_prod_refuses_a_credential_that_can_write_elsewhere(): ...          # the three probes
def test_the_mcp_route_refuses_a_request_with_no_token(): ...               # 401 + WWW-Authenticate
```

Commit `feat(blog-gate): the four connector tools and the least-privilege start`.

### Task 20: the edge — the fourth hostname

**Files:** Modify `deploy/caddy/generate_caddyfile.py`, `config/edge.toml`,
`deploy/caddy/tests/test_caddyfile.py`, `webgui/config_schema.py`.

```toml
[mcp]
restrict = true                       # admit only allow_ranges on the machine paths
allow_ranges = ["160.79.104.0/21"]    # Anthropic's documented outbound range
body_kb = 1024                        # largest request body the edge passes on
```

`_mcp_block()` (docstring: why a separate origin; the range narrows, OAuth is
the credential; the paths are the ones Task 14 recorded):

```
{MCP_HOST} {
    encode zstd gzip
    header Strict-Transport-Security "{HSTS}"
    handle /robots.txt { … Disallow: / … }
    handle /health { respond 404 }
    @machine_outsider {
        path /mcp /mcp/* /token /register /revoke
        not remote_ip {ranges}
    }
    respond @machine_outsider 403
    request_body { max_size {body_kb}KB }
    handle { reverse_proxy 127.0.0.1:{BLOG_GATE_PORT} { header_up X-Edge 1 } }
}
```

Tests: the host is served and proxied to `BLOG_GATE_PORT` from `repo_paths`;
the machine paths are range-limited and `/authorize` and `/connect` are not;
`restrict = false` emits no matcher; a malformed range or an empty list turns
the filter **off with a loud comment in the output** rather than emitting a
directive Caddy rejects (state this choice in the docstring: a rejected reload
keeps the OLD config, which would silently leave the previous rule in place);
the edge-header count and `test_every_hostname_is_served` include the new
block; the app host is not named in it; pure ASCII. Catalogue `[mcp]`.

Commit `feat(edge): the connector's hostname, limited to Anthropic's range`.

### Task 21: the Connector panel

**Files:** Modify `webgui/pages/blog.py`, `webgui/tests/test_blog.py`,
`webgui/page_help.py`.

A card on `/blog`: `connection_text(view)` → "Connected to Claude since
<date>" / "Not connected"; the pending requests (client name, callback
address, received time) when any; a `kit.text_field("Code")` + Approve; Revoke
behind `kit.confirm`. Approve sends `owner_command("connect_approve", …,
code=…)`; the result toast says approved or "That code did not match".
Tests: `connection_text` over every state and junk; the code is normalised
(`upper`, hyphen and spaces dropped) before sending; no view field is rendered
as HTML.

Commit `feat(webgui): approve and revoke the Claude connector from the Blog page`.

### Task 22: phase 2 docs and the runbook

`docs/dev-prod-environments.md` gains a section "The blog connector", with the
steps in order and each command on one line (the operator runs them from
PowerShell over ssh: no inner double quotes):

1. DNS: an `A` record for `mcp.<site host>` to the box.
2. The Redis user (model on §2 step 4b; `CONFIG REWRITE` afterwards, as that
   section explains):
   `ACL SETUSER blog on >PASSWORD resetkeys resetchannels -@all +@connection +get +mget +exists +ttl +pttl %R~cache:blog:* (%W~cmd:blog_inbox +xadd)`
   — check the exact read commands `Bus.cache_get` issues before finalising
   this line, and prove it with the gate's own start-up probe.
3. `.env.blog` (mode 0600): `REDIS_BLOG_URL=redis://blog:PASSWORD@127.0.0.1:6379/0`.
4. Regenerate and reload Caddy.
5. Promote (the unit is generated and started by it).
6. claude.ai → Settings → Connectors → Add custom connector →
   `https://mcp.<site host>/mcp` → Connect → type the code on the Blog page.

Also: `docs/reference/blog.md` (the sign-in sequence, why the code is typed,
what each view may hold), the API Reference (the four tools, the two streams,
the views), `docs/SECURITY.md` (a third internet-facing process and its
controls), CHANGELOG, `CLAUDE.md` in place (the gate beside the public live
process: its own entrypoint, env file, stream and ACL user; "paper-only, no
order route" is unchanged). Rebuild the manuals. Commit
`docs: the blog connector - runbook, security, reference`.

### Task 23: prove the connector

1. Locally: start `blog_svc` handlers and the gate against one fake bus in a
   scratch script; drive the whole sign-in with the SDK's own client (register
   → authorize → read the code off `/connect` → approve through
   `handlers.handle_command` → `/connect/done` → token → `tools/list` →
   `submit_draft`) and assert a draft exists and no entry does. Keep it as
   `services/blog_gate/tests/test_end_to_end.py` if it runs without a network.
2. After the operator's steps and the promote, on prod: the gate's journal
   shows the ACL proof passing; `curl -s -o /dev/null -w "%{http_code}"
   https://mcp.<site host>/mcp` from a non-Anthropic address is `403`;
   `/.well-known/oauth-authorization-server` answers; connect from claude.ai;
   ask Claude Chat to submit a short test entry; approve it on `/blog`; open it
   on the site; unpublish it.
3. Report what was verified where, and what was not.

---

## Order and promotes

Tasks 1–13 are one promotable unit (the blog with upload). Tasks 14–23 are a
second. A promote stops the whole target, so default to 15:25–16:15 CT on a
trading day unless the operator says "now"; the command is in `CLAUDE.md` and
nothing goes after it.

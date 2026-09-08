# Gallery Refresh and Live-Market Glow Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Swap the site's Live-screens/Gallery nav prominence, glow the Live-screens link while the market is open, and recapture the gallery's screenshots every trading day so they carry the current branding.

**Architecture:** Three independent changes to the public site. The nav swap is markup. The glow is client-side progressive enhancement over a holiday list generated from `shared/market_calendar`. The gallery refresh is a new capture tool that mints a session cookie locally and drives headless Chrome against the **private** app on `:8500` — because the branding lives in the app header and the public live shell has none.

**Tech Stack:** Static HTML/CSS/JS (no framework, no build step), Python 3.11, headless Chrome, Pillow, systemd user timers, pytest.

**Design doc:** `docs/plans/2026-09-08-gallery-refresh-and-live-glow-design.md` — read it first; it records why the obvious cheap answer (reuse the live captures) cannot work.

---

## Before you start

**Environment.** Windows worktree. Python is at
`D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe` — a worktree has **no
venv of its own**, so the absolute path is required.

```bash
cd webgui && "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest -q -rf
```

Root suites: `"D:/.../python.exe" -m pytest tests deploy tools/tests shared/tests -q -rf`
Lint: `... -m ruff check .` · Types: `... -m pyright`
Commit with `git -c commit.gpgsign=false commit ...`, ending each message with:
`Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`

**Baselines — measure before you start and compare the failing SET by node ID,
never the count.** This repo has a documented incident where two real regressions
hid behind two tests flipping to skipped while the total held steady.

⚠ **Do not preview from this worktree.** It has no `config/env.local.toml`, so
`repo_paths` resolves it to **prod** and it would bind `:8500` — where the live
prod stack is. `deploy/site` is the exception: it is static files, so serve it
locally with the `site` launch config (`.claude/launch.json`) and verify there.

⚠ **The site's three constraints are pinned by `deploy/tests/test_site.py`** —
nothing dynamic, no link to the app host, no third-party origin. Read that file
before touching `deploy/site/`.

---

## Task 1: The nav swap

**Files:**
- Modify: `deploy/site/index.html:83-90`, `deploy/site/gallery.html`, `deploy/site/live.html`
- Test: `deploy/tests/test_site.py`

**Step 1: Write the failing test**

```python
def test_live_screens_is_the_primary_call_to_action(cfg=None):
    """The nav's own comment records that Live Screens was the design's primary
    button and lost the slot only because the page was an empty placeholder.
    It is not a placeholder any more, so this pins the swap back -- structurally,
    on the class, rather than on copy a later edit would break."""
    text = _markup("index.html")
    live = re.search(r'<a[^>]*href="live\.html"[^>]*>', text)
    gallery = re.search(r'<a[^>]*href="gallery\.html"[^>]*>[^<]*App gallery', text)
    assert live, "no live.html link in the index nav"
    assert "btn-primary" in live.group(0), "Live screens is not the primary button"
    assert gallery and "btn-primary" not in gallery.group(0), (
        "App gallery still carries the primary treatment")
```

⚠ `_markup()` strips comments — check its definition at the top of
`test_site.py` before relying on it. The nav comment mentions both hrefs, so a
raw-text search would match inside it and pass for the wrong reason.

**Step 2: Run it and watch it fail**

```bash
"D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest deploy/tests/test_site.py -k primary -q
```

Expected: `AssertionError: Live screens is not the primary button`.

**Step 3: Swap them in `index.html`**

Exchange the two elements' classes so `live.html` carries
`class="btn btn-primary"` (keep its `style="white-space: nowrap;"`) and
`gallery.html` becomes `class="ns-navlink"`. Keep source order reading
`… Glossary · App gallery · Live screens` so the primary button stays last, which
is where it is now.

**Step 4: Correct the comment IN PLACE**

The existing comment is now false. Replace it — do **not** append a note under
stale text; that is the house rule in CLAUDE.md's maintenance banner:

```html
<!-- Live Screens is the design's primary button, and now earns it: the page
     served an empty placeholder until 2026-09-07, so the gallery held the slot
     rather than pointing the site's most prominent control at an empty room. -->
```

**Step 5: Match the ordering on the other two pages**

`gallery.html` and `live.html` each carry a short nav. Put Live screens ahead of
Gallery in both so the three pages read consistently. On `live.html` the current
page is the crumb, so it has no self-link — only `gallery.html`'s order changes
there.

**Step 6: Verify locally, then run and commit**

```bash
"D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest deploy -q -rf
```

Serve `deploy/site` (launch config `site`, port 8790) and confirm the button
reads correctly at desktop and mobile widths — the nav wraps, and the primary
button is the thing that must not wrap.

```bash
git add deploy/site deploy/tests
git commit -m "feat(site): Live screens takes the primary slot back"
```

---

## Task 2: The generated holiday list

**Files:**
- Create: `tools/generate_market_clock.py`
- Create: `deploy/site/assets/market-clock.js` (generated, but **committed** — see below)
- Test: `tools/tests/test_generate_market_clock.py`

**Why a generator rather than a literal:** CLAUDE.md is explicit — *"Do not add a
new holiday literal or window constant anywhere"* — and `shared/market_calendar`
derives NYSE holidays algorithmically. A hand list in JavaScript would be an
eleventh copy that silently rots.

**⚠ This generated file IS committed**, unlike the captures in Task 4. It changes
about once a year, a stale one degrades to a wrong glow rather than a broken
page, and committing it means the site works from a fresh clone. Regenerating is
a deliberate act, not a timer.

**Step 1: Write the failing test**

```python
def test_the_emitted_holidays_match_the_calendar_module():
    """The whole point of generating this file. A hand-maintained list in
    JavaScript would be an eleventh copy of a calendar this repo deliberately
    derives -- and it would rot silently, because nothing renders red when a
    marketing page glows on Thanksgiving."""
    import json, re
    from shared import market_calendar as mc
    from tools import generate_market_clock as g

    js = g.render()
    emitted = set(json.loads(re.search(r"HOLIDAYS\s*=\s*(\[[^\]]*\])", js).group(1)))
    year = g.CURRENT_YEAR
    expected = {d.isoformat() for y in (year, year + 1) for d in mc.nyse_holidays(y)}
    assert emitted == expected


def test_it_emits_two_years_so_a_january_visitor_is_covered():
    """A file generated in December that carried only that year would be blind on
    2 January -- the first trading day of the next one."""
    from tools import generate_market_clock as g
    js = g.render()
    assert str(g.CURRENT_YEAR) in js and str(g.CURRENT_YEAR + 1) in js


def test_the_early_close_gap_is_declared_in_the_file():
    """DECIDED 2026-09-08: half-days are NOT handled. The NYSE closes at 13:00 ET
    on ~3 afternoons a year and nothing in this repo knows it -- there is no
    is_half_day anywhere. The glow is decoration with no downstream consumer, so
    the gap was accepted; this asserts it is WRITTEN DOWN rather than left for
    someone to find on Black Friday."""
    from tools import generate_market_clock as g
    js = g.render()
    assert "13:00" in js and "early close" in js.lower()
```

**Step 2: Run and watch it fail**

**Step 3: Implement `tools/generate_market_clock.py`**

- `render()` is **pure** and returns the JS source as a string — that is what
  makes it testable without touching disk.
- `main()` writes it to `deploy/site/assets/market-clock.js`.
- Emit `HOLIDAYS` as a JSON array of `YYYY-MM-DD` strings for the current and
  next year, from `mc.nyse_holidays()`.
- Emit the regular-session bounds from `config/sessions.toml`
  (`[sessions.regular]` is `08:30`–`15:00` **CT**; the JS works in ET, so convert
  — 09:30–16:00 ET — or read and convert in the generator and emit ET literals
  with a comment saying so). **Do not hardcode 09:30/16:00 without deriving
  them**; that is the second copy the config exists to prevent.
- Carry a header comment stating the file is generated, by what, and that early
  closes are not handled.

**Step 4: Run, then generate the file and commit both**

```bash
"D:/.../python.exe" -m pytest tools/tests/test_generate_market_clock.py -q -rf
"D:/.../python.exe" -m tools.generate_market_clock
git add tools/generate_market_clock.py tools/tests deploy/site/assets/market-clock.js
git commit -m "feat(site): generate the market clock's holiday list from the calendar"
```

---

## Task 3: The glow

**Files:**
- Modify: `deploy/site/assets/market-clock.js` (the runtime half, hand-written; the generator emits only the data block)
- Modify: `deploy/site/assets/site.css`
- Modify: `deploy/site/index.html` (load the script)
- Test: `deploy/tests/test_site.py`

**⚠ Design point:** keep the generated data and the hand-written logic in one
file only if the generator rewrites the whole file. If it does, the logic lives
in the generator as a template string. **Simpler alternative: two files** —
`market-holidays.js` (generated, data only) and `market-clock.js` (hand-written
logic). Prefer two files; a generator that owns hand-written logic is a generator
people stop regenerating.

**Step 1: Write the failing tests**

```python
def test_the_live_link_can_be_glowed(cfg=None):
    """Structural: the CSS rule and the class the script toggles must agree."""
    css = _css("assets/site.css")
    assert ".ns-market-open" in css
    js = (SITE / "assets" / "market-clock.js").read_text(encoding="utf-8")
    assert "ns-market-open" in js, "the script and the stylesheet disagree on the class"


def test_the_glow_is_green(cfg=None):
    """The one thing the request actually specifies."""
    css = _css("assets/site.css")
    rule = re.search(r"\.ns-market-open\s*\{[^}]*\}", css).group(0)
    assert "box-shadow" in rule or "filter" in rule
    # a green channel dominant in the glow colour
    assert re.search(r"#[0-9a-f]{0,2}[a-f89][0-9a-f]{3}|rgba?\(\s*\d+\s*,\s*(1\d\d|2\d\d)", rule, re.I)


def test_the_page_still_carries_its_content_without_scripting(pages):
    """Already asserted globally, re-stated here because this task ADDS a script:
    the glow is decoration and nothing else may come to depend on the script."""
    text = _markup("index.html")
    assert 'href="live.html"' in text and "<nav" in text
```

**Step 2: Run and watch fail**

**Step 3: Write the runtime**

`deploy/site/assets/market-clock.js`, loaded with `defer`:

- Read the session bounds and holiday list from the generated data file.
- Compute *now* in `America/New_York` with
  `new Intl.DateTimeFormat("en-CA", {timeZone: "America/New_York", ...})` and
  `formatToParts` — this gives the exchange's own wall clock regardless of the
  visitor's timezone, which a UTC-offset calculation would get wrong twice a year.
- Open ⟺ weekday **and** not in `HOLIDAYS` **and** the time is within bounds.
- Toggle `ns-market-open` on the Live screens link; re-evaluate on a timer
  (60 s is ample) so a page left open crosses the open and the close.
- **A comment stating the early-close gap**, matching the test in Task 2.
- Wrap the whole thing so any failure leaves the link un-glowed rather than
  throwing — decoration must never break a page.

**Step 4: The CSS**

Add `.ns-market-open` to `site.css` using the existing Nocturne tokens where they
fit. A green neon glow is a `box-shadow` with a soft outer spread plus a slightly
brighter text colour; look at how `.btn-primary` is already built and stay inside
that visual language rather than inventing a new one.

⚠ Respect `prefers-reduced-motion` if you animate the glow. A steady glow needs
no guard; a pulsing one does.

**Step 5: Load it from `index.html`** with `defer`, beside the existing script.

**Step 6: Verify locally — this is the part tests cannot do**

Serve `deploy/site` and check the glow **in both states**. You cannot wait for a
market open, so force it: temporarily evaluate the predicate against a fixed
date in the console, confirm the class toggles, and confirm the link looks right
lit and unlit. Report what you saw.

**Step 7: Run the suites and commit**

```bash
"D:/.../python.exe" -m pytest deploy -q -rf
git add deploy/site deploy/tests
git commit -m "feat(site): the Live screens link glows while the market is open"
```

---

## Task 4: Gallery capture — the screen map

**Files:**
- Create: `deploy/site/gallery_screens.py` **or** `tools/gallery_screens.py` — pure data
- Test: `tools/tests/test_gallery_screens.py`

Mirror `webgui/live_screens.py`: pure data, no imports beyond `dataclasses`, so
the capture tool, the gallery HTML and the tests all read one source.

**Step 1: Write the failing tests**

```python
FORBIDDEN = {"/terminate", "/settings", "/status", "/login"}

def test_there_are_fifteen_screens_and_twentytwo_shots():
    import gallery_screens as g
    assert len(g.SCREENS) == 15
    assert sum(len(s.shots) for s in g.SCREENS) == 22


def test_daily_briefings_is_gone():
    """Dropped by decision 2026-09-08; its two shots go with it."""
    import gallery_screens as g
    assert not any("Briefing" in s.title for s in g.SCREENS)


def test_every_route_is_one_the_app_actually_registers():
    """A mis-mapped route does not fail anything -- it silently publishes the
    WRONG screenshot. That already happened once in this design: 'Where the
    Market Stands' was assumed to be a /sentiment variant and is actually
    /sentiment/bullbear."""
    import main  # noqa: F401 -- registers the @_page routes
    from nicegui import Client
    import gallery_screens as g
    registered = set(Client.page_routes.values())
    for s in g.SCREENS:
        for shot in s.shots:
            path = shot.route.split("?")[0]
            assert path in registered, f"{s.title}: {path} is not a registered route"


def test_no_screen_captures_a_control_surface():
    import gallery_screens as g
    paths = {sh.route.split("?")[0] for s in g.SCREENS for sh in s.shots}
    assert paths & FORBIDDEN == set()
```

**Step 2: Run and watch fail**

**Step 3: Write the table**

Fifteen screens, twenty-two shots, from the design doc's map. Each shot carries
its route and its output filename (keep the existing `imageN.webp` names so
`gallery.html`'s references do not all have to change).

⚠ **Screen 12 (Strategy Calculator, 5 shots) is INFERRED** from its captions —
"Build the structure", "Expected moves", "Volatility vs the Greeks",
"What-if: pricing". **Open `image14/15/16/17/18.webp` and confirm each against
the real page before committing the mapping.** A wrong guess here publishes the
wrong screenshot silently.

**Step 4: Run and commit**

---

## Task 5: The authenticated capture tool

**Files:**
- Create: `tools/capture_gallery_shots.py`
- Test: `tools/tests/test_capture_gallery_shots.py`

Model it on `tools/capture_live_shots.py` — same shape: a pure `targets()`, a
pure window gate, a thin subprocess call. Read that file first.

**Step 1: Write the failing tests**

```python
def test_it_captures_every_shot_in_the_map():
    import gallery_screens as g
    from tools import capture_gallery_shots as c
    assert len(c.targets()) == sum(len(s.shots) for s in g.SCREENS)


def test_it_captures_from_loopback_never_the_public_host():
    import repo_paths
    from tools import capture_gallery_shots as c
    for url, _out in c.targets():
        assert url.startswith(f"http://127.0.0.1:{repo_paths.NICEGUI_PORT}")
        assert repo_paths.APP_HOST not in url
        assert repo_paths.LIVE_HOST not in url


def test_a_missing_auth_store_refuses_rather_than_capturing_a_login_form(monkeypatch, tmp_path):
    """THE ONE THAT MATTERS. Without a valid session every route 303s to /login,
    and a run that ignored that would cheerfully publish 22 screenshots of a
    login form over the real gallery."""
    from tools import capture_gallery_shots as c
    monkeypatch.setattr(c, "AUTH_STORE", tmp_path / "nope.json")
    with pytest.raises(SystemExit):
        c.main()


def test_the_session_cookie_is_minted_not_hardcoded():
    """Source-level: a checked-in token would be a credential in git."""
    src = pathlib.Path("tools/capture_gallery_shots.py").read_text(encoding="utf-8")
    assert "mint_token" in src
    assert not re.search(r'ns_session\s*=\s*["\'][A-Za-z0-9._-]{16,}', src)
```

**Step 2: Run and watch fail**

**Step 3: Implement**

- `AUTH_STORE = repo_paths.REPO_ROOT / "shared" / "webgui_auth.json"` — match
  `webgui/auth_store.DEFAULT_PATH` rather than restating the path if you can
  import it cleanly.
- Load credentials, `auth.mint_token(creds.session_secret, kind=auth.KIND_SESSION, epoch=creds.epoch)`.
- Pass the cookie to Chrome. `--headless --screenshot` cannot set cookies, so
  either write a cookie file into a throwaway `--user-data-dir` profile, or use
  `--headless=new` with a small CDP step. **Try the profile route first** and say
  which worked; if neither does, stop and report rather than adding a browser
  automation dependency (there is none in `requirements.lock` today, and a new
  one must go in the lock by hand or it ships to prod missing).
- **Verify the session actually worked**: after the first capture, assert the
  rendered page is not the login form. A DOM marker only the app emits is the
  right check; HTTP 200 is not, because the login page is also a 200.
- One screen failing → log and continue, exit 0. Auth failing → exit non-zero.
- Atomic publish: temp file + `os.replace`, refuse an empty render.
- Convert PNG → WebP with Pillow (already in `requirements.txt` **and** the lock).

**Step 4: Run and commit**

---

## Task 6: Gitignore the shots, and the site-test exemption

**Files:**
- Modify: `.gitignore`
- Modify: `deploy/tests/test_site.py`
- Delete: `deploy/site/assets/shots/image23.webp`, `image24.webp` (Daily Briefings)

**⚠ Why this task exists.** The shots are tracked today. A daily rewrite on prod
would dirty the tree, and `tools/promote.sh` refuses a dirty tree **before**
stopping anything. Same shape, and same fix, as `deploy/site/live/`.

**Step 1: Write the failing test**

```python
def test_the_gallery_shots_are_generated_state(cfg=None):
    """Tracked, they would dirty prod's tree the moment the capture timer first
    fires, and promote.sh refuses a dirty tree."""
    ignored = pathlib.Path(".gitignore").read_text(encoding="utf-8")
    assert "deploy/site/assets/shots/" in ignored
```

**Step 2–4:** Add the ignore with the reason as a comment; `git rm --cached` the
existing shots so they stop being tracked while staying on disk; extend
`GENERATED_REF_PREFIXES` in `test_site.py` to cover `assets/shots/` — **an
exemption with a comment, never a deletion**, because
`test_every_internal_reference_resolves_to_a_file` is what catches a renamed
screenshot.

⚠ `test_the_gallery_references_every_shot_on_disk` will need rethinking once the
directory can legitimately be empty. Make it assert against the **capture map**
rather than the filesystem.

**Step 5: Commit.** State in the message that a fresh clone now has no gallery
images until the timer runs.

---

## Task 7: Regenerate `gallery.html`

**Files:**
- Modify: `deploy/site/gallery.html`
- Modify: `deploy/tests/test_site.py`

Remove the Daily Briefings panel and its rail row (16 → 15 screens), and rewrite
every `<img width/height>` to the uniform capture size so
`test_every_image_declares_its_size` keeps holding and the layout does not shift.

⚠ `test_the_gallery_has_all_sixteen_screen_titles_in_its_source` asserts **16**
`<h2>` and names `Daily Briefings` explicitly. Update it to 15 and drop that
name — and rename the test, because a test called `sixteen` asserting fifteen is
how the next reader is misled.

⚠ `gallery.js` pairs the rail and the panels **by index** and bails if they
disagree. Removing a panel must remove its rail row. `test_the_rail_and_the_panels_are_the_same_length`
covers this — run it.

---

## Task 8: Schedule it

**Files:**
- Modify: `config/sessions.toml`
- Modify: `deploy/systemd/generate_units.py`
- Test: `tests/test_systemd_units.py`

Add `[slots.gallery_capture]` at ~09:00 CT — half an hour after the 08:30 CT
open, so the screens carry live data rather than a pre-open blank. Comment it
with what it costs: 22 page loads against the local app, no Schwab call, no
Claude call.

Generate `trading-<env>-gallery-capture.{service,timer}`, `Type=oneshot`,
`OnCalendar` matching the slot, `Persistent=false`, storm cap in **`[Unit]`**,
`TimeoutStartSec` derived from the shot count rather than typed.

⚠ **No `MemoryMax`.** Only `webgui_live` carries one, for a documented reason;
CLAUDE.md says explicitly not to add it to other units as a drive-by.

---

## Task 9: Verify on the box

Tests are structural. This feature's real surface is 22 screenshots and a glow.

1. Land on `main` and promote — never `git pull` in the prod checkout.
2. Run the capture by hand, in-window, and **look at several images**: the
   header lockup must show the chevron mark and the new tracking, which is the
   entire point.
3. Confirm none is a login form.
4. Load `neuralstrike.co/gallery.html` and check the panels against the rail.
5. Check the glow during market hours, and again after 16:00 ET.
6. `git status` in the prod checkout must be **clean** — that is the promote trap.

---

## Task 10: Documentation

`docs/CHANGELOG.md` gets the dated entry. `CLAUDE.md` gets only durable
invariants — the capture tool can authenticate as the owner; the shots are
gitignored generated state; the market clock's holiday list is generated and its
early-close gap is deliberate. Per the maintenance banner, a shipped feature is
**not** an entry there.

Check `docs/manuals/` for anything that counts gallery screens.

---

## Definition of done

- [ ] Live screens is the primary button; the stale comment is corrected in place.
- [ ] The link glows during market hours and not outside them, verified in a browser in both states.
- [ ] The holiday list is generated from `shared/market_calendar` and pinned against it by test.
- [ ] The early-close gap is written into the code and asserted.
- [ ] 22 shots across 15 screens capture from the private app with the current lockup.
- [ ] A missing or stale auth store makes the run fail loudly rather than publishing login forms.
- [ ] The shots are gitignored; prod's tree stays clean across a capture.
- [ ] `gallery.html` has 15 panels, 15 rail rows, and correct declared image sizes.
- [ ] Every suite's failing **set** is unchanged from baseline, compared by node ID.

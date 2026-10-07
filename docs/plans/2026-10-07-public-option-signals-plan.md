# Public Option Signals Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Publish the Market Scanner on the public live origin as "Option Signals" at `/signals`, first in the site's Tools menu, without Run scan or any owner-only control.

**Architecture:** `scanner.render()` gates its owner-only pieces on `shell.is_public()` / `shell.may_enqueue()`; nothing is copied into a second module. The owner's ledger caps are refused at one chokepoint (`checks_feed.read_context`). On the public origin every visitor draws from one process-wide build of the day's rows (`scanner_shared`), which is read-only.

**Tech Stack:** NiceGUI page modules under `webgui/pages`, `webgui/live_screens.py` (the published table), static HTML under `deploy/site`, pytest.

**Design:** `docs/plans/2026-10-07-public-option-signals-design.md`.

**Running tests** (Windows worktree; the venv is the main checkout's):

```bash
cd webgui && "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest tests/<file> -q
"D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest deploy -q     # from the repo root
```

**Rules for every task:** write test files with the editor, never a heredoc.
Never weaken an existing assertion to make a test pass; if one fails, the code
is wrong or the assertion is about a count this plan changes on purpose (those
are named below). Compare the failing SET against the baseline, not the count.

---

### Task 1: The caps chokepoint

**Files:**
- Modify: `webgui/pages/options/checks_feed.py` (`read_context`)
- Test: `webgui/tests/test_checks_feed_public.py` (create)

**Step 1: failing tests.** Seed `cache:options:ledger_caps` on the fake bus
(`bus_client.reset()`, then `bus_client.bus().cache_set(...)`), clear
`checks_feed._memos`, then:

- `test_the_private_origin_reads_the_ledger_caps`: `read_context()["caps"]` is the seeded dict.
- `test_the_public_origin_never_reads_the_ledger_caps`: after
  `shell.publish({})`, `read_context()["caps"] is None` AND
  `read_context(caps=True)["caps"] is None`; monkeypatch `checks_feed._gated`
  to record its views and assert `CAPS_VIEW` was never asked for. Always
  `shell.unpublish()` in a fixture's teardown.

**Step 2:** run, expect the public test to FAIL (caps is the seeded dict).

**Step 3: implement.** `import shell as _shell` at the top of `checks_feed.py`;
in `read_context`:

```python
    # The public origin never reads the owner's ledger, whatever the caller
    # asks for: the Trade detail panel reads a context of its own when its page
    # holds none, so the refusal has to live here and not at each caller.
    caps = caps and not _shell.is_public()
```

Extend the docstring's `caps=False` paragraph with that sentence.

**Step 4:** run both, expect PASS. **Step 5:** commit
`feat(live): the checklist never reads the ledger caps on the public origin`.

---

### Task 2: The shared build

**Files:**
- Create: `webgui/pages/options/scanner_shared.py`
- Test: `webgui/tests/test_scanner_shared.py` (create)

A single-slot memo keyed on object IDENTITY. `bus_client.read_shared` and
`checks_feed._gated` hand back the same object until a view's version moves, so
identity is an exact "has this input changed" test with no probe/payload race.

```python
"""One built scan for every visitor on the public origin. ..."""
import threading
import time

_lock = threading.Lock()
_slot = {"parts": None, "built": None, "at": 0.0}


def get(parts, build, *, max_age, now=time.monotonic):
    """``build()``'s result, shared: the SAME object for every caller whose
    ``parts`` are the same objects (``is``) as the last build's, until it is
    ``max_age`` seconds old. Callers arriving together wait on one build.

    ⚠ READ-ONLY. Every visitor's tab holds what this returns."""
    parts = tuple(parts)
    with _lock:
        hit = _slot["parts"]
        if (hit is not None and len(hit) == len(parts)
                and all(a is b for a, b in zip(hit, parts))
                and now() - _slot["at"] < max_age):
            return _slot["built"]
        built = build()
        _slot.update(parts=parts, built=built, at=now())
        return built


def reset():
    """Empty the slot (tests)."""
    with _lock:
        _slot.update(parts=None, built=None, at=0.0)
```

**Tests** (each calls `reset()` first; `now` is injected):
- same parts → `build` called once, the same object returned twice;
- one part replaced by an EQUAL but distinct object → rebuilt (identity, not equality);
- same parts, clock advanced past `max_age` → rebuilt; just under → not;
- two threads arriving together → `build` called once (a `threading.Event` holds the first build open);
- `build` raising leaves the slot unchanged (the next call builds again).

Commit `feat(live): a process-wide slot for one shared scanner build`.

---

### Task 3: The scanner's pure pieces

**Files:**
- Modify: `webgui/pages/options/scanner.py`, `webgui/pages/options/checks_table.py`
- Test: `webgui/tests/test_scanner_public.py` (create)

**3a. `_build_populate(day_env, live, ctx=None, *, public=False)`.** After
`stamp_stale(...)` and before `stamp_checks(...)`:

```python
        if public:
            close_paper(rows[key])
```

```python
def close_paper(rows):
    """Close every row's Paper gate. The public origin offers no Paper button,
    and the checklist's Paper book line is drawn only for a row whose gate is
    open (``checks._book``) - closed, the line is ABSENT rather than grey."""
    for r in rows:
        r["_allow_paper"] = False
    return rows
```

Tests: with a live PCS signal and a ctx whose `caps` is a real dict,
`_build_populate(..., public=True)` rows carry `_allow_paper is False` and no
check with key `book` (drive the real `checks_feed.checks_for` on the row's
candidate); the private build of the same input keeps `_allow_paper True`.

**3b. `_read_and_build_shared()`** (blocking; `run.io_bound` only):

```python
_NO_VIEW: dict = {}     # ONE object for "absent", so identity holds across calls


def _read_and_build_shared():
    from . import checks_feed, scanner_shared
    day_env = bus_client.read_shared(_DAY_VIEW) or _NO_VIEW
    live = bus_client.read_shared(_LIVE_VIEW) or _NO_VIEW
    ctx = checks_feed.read_context()
    return scanner_shared.get(
        (day_env, live, ctx.get("regime"), ctx.get("calibration")),
        lambda: _build_populate(day_env, live, ctx, public=True),
        max_age=checks_feed.TABLE_REFRESH_SEC)
```

Tests (fake bus seeded with today's day union, `scanner_shared.reset()`,
`shell.publish`): two calls return the SAME dict; after another
`cache_set` of the day view a call returns a different one; the rows carry no
`_new` key and `_allow_paper False`.

**3c. `page_rows(page, selected_id, *, shared)`**: what a table is sent.

```python
def page_rows(page, selected_id, *, shared):
    """The rows a table is sent for one page. ``shared`` rows belong to every
    visitor, so the selected-row accent goes on COPIES of this one page; the
    private page's rows are its own and were stamped in place."""
    if not shared:
        return page
    return [{**r, "_selected": selected_id is not None and r.get("id") == selected_id}
            for r in page]
```

Tests: shared → the input dicts are unchanged (no `_selected` key appears on
them) and exactly the selected copy is marked; not shared → the same list object.

**3d. `checks_table.ONLY_CLEAR_TIP_PUBLIC`** =
`"Hide rows with a block, a caution or a feed that hasn't loaded"` (the private
tip names the paper book, which the public page never checks). Test: the word
"paper" is not in it; the private tip is unchanged.

Commit `feat(scanner): the pure pieces of a public render`.

---

### Task 4: Gate `render()`

**Files:**
- Modify: `webgui/pages/options/scanner.py` (`render`), `webgui/pages/options/detail.py`
- Test: `webgui/tests/test_scanner_public.py`

At the top of `render()`, after `import shell as _shell`:

```python
    _public = _shell.is_public()
    _may_enqueue = _shell.may_enqueue()
```

Then, in order down the function:

1. **Run scan.** `scan_btn = (kit.button("Run scan", ...) if _may_enqueue else None)`.
   `_request_scan` opens with `if not _may_enqueue:` / `return` (exactly that
   shape; `test_live_commands._guarded` reads it). `scan_btn.on_click(...)` and
   both `kit.set_busy(scan_btn, ...)` calls run only when `scan_btn is not None`.
2. **Only clear tip.** `_ONLY_CLEAR_TIP` privately, `ONLY_CLEAR_TIP_PUBLIC` publicly.
3. **Detail panel.** `detail.render(width=290, actions=not _public)`; the three
   footer buttons are built only `if not _public`; `paper_btn = None` otherwise
   and `_remember` skips `paper_btn.set_visibility` when it is None.
4. **Paper results.** `handoff.watch_paper_results()` only `if not _public`.
5. **Probe views.** Leave `checks_feed.CAPS_VIEW` out of `_probe_views` when public.
6. **`_show_page`.** `table.rows = page_rows(page, sel["id"], shared=_public)`.
7. **`_paint_tables`.** `kit.mark_selected(shown, sel["id"])` only `if not _public`.
8. **`_apply_populate`.** The `new_ids_for_paint` / `stamp_new` pair only
   `if not _public` (the seen-set is neither read nor written).
9. **Loads.** `_reader = _read_and_build_shared if _public else _read_and_build`;
   `_initial_load` and `_rebuild` call `run.io_bound(_reader)`. `_restamp`
   opens with `if _public: await _rebuild(); return` (a public re-stamp is a
   fresh shared build, never a per-tab copy of every row).

`detail.render(width=360, actions=True)`: when `actions` is False the footer is
a bare `ui.element("div")` (no border, no padding), still handed to `_Handle`
so `update` / `clear` / `set_open` keep working. `detail._build_cards`: the
"Max contracts" row is drawn only when `not _shell.is_public()`
(`import shell as _shell` at the top of `detail.py`).

**Tests** (render with the real `ui` as `test_live_commands._button_texts`
does; a `published` fixture that calls `shell.publish(live_screens.PUBLIC_ROUTES)`
and `shell.unpublish()`):

| Public render | Private partner |
|---|---|
| no "Run scan", "Paper trade", "Calculator", "Expected Move" button | all four present |
| "Why no trade?" button present | present |
| `handoff.watch_paper_results` (monkeypatched) not called | called once |
| `scanner._SEEN` unchanged after `_apply`-driven paint (drive `new_ids_for_paint` via monkeypatch: not called) | called |
| `detail._build_cards({"max_contracts": 3, ...})` draws no "Max contracts" label | draws it |
| the 0-DTE / Swing / Directional tabs, both view switches and the family checkboxes are built | same |

Then run `tests/test_live_commands.py` (still green: the screen is not
published yet, so this proves nothing about the scanner until Task 5) and the
whole `tests/test_options_scanner.py` + `tests/test_options_detail.py`.

Commit `feat(scanner): render() leaves the owner's controls out on the public origin`.

---

### Task 5: Publish the screen

**Files:**
- Modify: `webgui/live_screens.py`, `webgui/tests/test_live_screens.py`
- Test: `webgui/tests/test_live_screens.py`, `test_live_commands.py`, `test_live_navigation.py`, `test_live_main.py`

**Step 1:** change `test_there_are_exactly_seventeen_screens` to eighteen
(rename it, add the dated line to its comment) and add:

```python
def test_option_signals_is_the_scanner_with_no_tile_and_no_pin():
    import live_screens
    s = next(x for x in live_screens.SCREENS if x.slug == "signals")
    assert (s.route, s.title, s.module, s.private_route) == (
        "/signals", "Option Signals", "options.scanner", "/options/scanner")
    assert s.tile is False and s.kwargs == {} and s.settings == {}
```

**Step 2:** run, expect FAIL. **Step 3:** add the entry at the head of the
Tools block in `SCREENS` (before `finder`), with a comment saying it is a pure
reader, what it leaves out, and that its gate is the process's origin rather
than a `public=True` argument; update the module docstring's count.

**Step 4:** run the four live test files. `test_live_commands` now walks
`options.scanner` and must pass on the gate from Task 4.

Commit `feat(live): publish the Market Scanner as Option Signals at /signals`.

---

### Task 6: The site's Tools menu and counts

**Files:**
- Modify: `deploy/site/{index,live,gallery,glossary,ideas,blog,report}.html`, `deploy/tests/test_site.py`

**Step 1 (tests):** `TOOLS` gains `("signals", "Option Signals")` FIRST;
`_WORDS` gains `6: "six"` and `18: "eighteen"`; add

```python
def test_the_tools_menu_lists_the_tools_in_order():
    """Option Signals leads the menu (the owner's instruction, 2026-10-07)."""
    for name in TOOLS_PAGES:
        titles = re.findall(r'<span class="ns-menu-title">(.*?)</span>', _tools_menu(name))
        assert titles == [title for _, title in TOOLS], f"{name}: {titles}"
```

**Step 2:** run `pytest deploy -q`, expect the new and count tests to FAIL.

**Step 3:** in every page's menu, before the Strategy Finder item:

```html
        <a class="ns-menu-item" href="https://live.neuralstrike.co/signals">
          <span class="ns-menu-title">Option Signals</span>
          <span class="ns-menu-desc">Every trade the scanner qualifies today</span>
        </a>
```

`live.html`: the lede becomes "Eighteen screens … Twelve are the readings … Six
are tools: Option Signals lists every trade the scanner qualifies through the
session, the Strategy Finder ranks …" and the meta description "Eighteen
NeuralStrike screens … plus six tools that find, rank, repair, price and
simulate a trade and follow the news behind it." `index.html`'s meta:
"Eighteen live screens and six tools".

**Step 4:** `pytest deploy -q` green. Commit
`feat(site): Option Signals leads the Tools menu`.

---

### Task 7: Documentation

- `docs/reference/public-live-screens.md`: seventeen → eighteen (both places);
  a paragraph for Option Signals (what it leaves out and where each is enforced;
  the shared build and its read-only rule).
- `docs/webgui-routes.md`: the route count sentence, a table row for `/signals`,
  and a "Published as" note in the `/options/scanner` section.
- Manuals: `user-guide.md` (the public live screens section),
  `technical-reference.md` (the unit table's "seventeen"), `reference-guide.md`
  (the Market Scanner tab: "the public copy is `live.neuralstrike.co/signals`,
  less …"). Rebuild with `docs/manuals/build_docs.py`.
- `CLAUDE.md`, "The public live screens": one bullet - a published page that
  holds a large view shares ONE build per process and never stamps a shared row.
- `docs/CHANGELOG.md`: the entry, newest first.

Run `cd webgui && pytest tests/test_docs_cover_the_ui.py tests/test_page_help.py -q`
and the repo-root `pytest tests -q` (the CLAUDE.md size ceiling). Commit
`docs: the public Option Signals screen`.

---

### Task 8: Verify

1. Whole `webgui` suite and `deploy` suite; compare the failing set with the baseline.
2. Render the public page in a browser. `tools/ui_harness.py` gains a
   `--public` flag (calls `shell.publish(live_screens.PUBLIC_ROUTES)` and
   `bus_client.set_read_only(True)` before the page import; its own test in
   `tools/tests` if that folder tests the harness). Seed today's day union, a
   live scan and a funnel; check: three tabs, both switches, a row click fills
   the panel with no footer buttons, no Run scan, "Why no trade?" opens, a
   second browser tab's selection does not light a row in the first.
3. Stop the harness.
4. Request a code review (superpowers:requesting-code-review), then finish the
   branch (superpowers:finishing-a-development-branch).

# Panel-level scrolling Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Move the dashboard's sideways scroll from the document into each panel, with the identity column pinned, so a narrow window never costs the reader the panel heading or the symbol.

**Architecture:** A pure width helper derives each panel's minimum width by parsing its own `grid-template-columns` string; a shared CSS block in `webgui/shell.py` turns panels into horizontal scroll containers with a sticky first cell. Page content is unchanged, so the public live screens and the private app stay identical.

**Tech Stack:** NiceGUI/Quasar + Tailwind arbitrary-value classes, pytest, Selenium (dev-only, for width measurement).

**Design:** [`2026-09-08-panel-scroll-design.md`](2026-09-08-panel-scroll-design.md)

---

## Environment

- **Python:** `D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe`
- **Root set:** `"D:/.../python.exe" -m pytest tests deploy tools/tests shared/tests -q -rf` — baseline **1484 passed**
- **webgui:** `cd webgui` then `"D:/.../python.exe" -m pytest . -q -rf` — baseline **3510 passed, 1 skipped**
- **Lint:** `"D:/.../python.exe" -m ruff check .`
- Commit with `git -c commit.gpgsign=false commit ...`, ending with
  `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`
- ⚠ Compare the failing **set** by node ID and the **skipped** set, never the counts.

## The arithmetic this plan turns on

`desk.py` states two numbers independently, and they are the proof the formula below is right — reproduce them before trusting anything else:

```
POS_GRID floors:  64+53+42+144+53+53+126+36+94+60           = 725   ("the TEN POS_GRID minmax floors summed", line ~2901)
gaps:             (10 tracks - 1) x COL_GAP_PX(8)           =  72
panel padding:    PANEL_PAD_PX (2 border + 32 px-4 + 8 px-1)=  42
                                                        panel = 839   ("at exactly 1877 the panels measure 839px")
page boundary:    839*2 + PANEL_GUTTER_PX(20) + DESK_CHROME_PX(164) + DESK_SCROLLBAR_PX(15) = 1877
```

---

## Task 1: Measure which screens actually clip

**Files:** Create `tools/measure_screen_widths.py`, `requirements-dev.txt` (modify)

Only the Desk's clipping is established. Do not change thirteen screens on an assumption.

**Step 1: Add Selenium as a DEV dependency**

⚠ `requirements-dev.txt` ONLY. **Never `requirements.lock`** — prod installs the lock and does not test layout, and CLAUDE.md documents that a wrong lock ships breakage to prod. Append:

```
selenium>=4.20        # width measurement for the panel-scroll work (dev only)
```

Install: `"D:/.../python.exe" -m pip install -r requirements-dev.txt`

**Step 2: Write the measurement tool**

`tools/measure_screen_widths.py` — a CLI, not a test (it needs a running app and a network):

```python
"""Report which published screens overflow the document, and by how much.

Chrome's --headless --screenshot cannot report element widths and the Claude
Browser pane returns viewport 0 on this app (the trap CLAUDE.md documents), so
this drives a real browser and asks the DOM. Dev-only: Selenium is in
requirements-dev.txt and never in requirements.lock.
"""
```

It must, for each URL and each width in `[1280, 1366, 1440, 1600, 1920, 2560]`:
- set the window to that width,
- read `document.documentElement.scrollWidth`, `clientWidth`, and — importantly — **print the viewport beside every measurement**, because a zero viewport is the known way these numbers turn into fiction,
- report `overflow = scrollWidth - clientWidth`.

**Step 3: Run it against the fourteen live screens**

```bash
"D:/.../python.exe" tools/measure_screen_widths.py --base https://live.neuralstrike.co
```

Expected: the Desk overflows below ~1877px. **Record the real per-screen table in the commit message** — it is the input to Task 4, and a guess there changes the wrong files.

**Step 4: Commit**

```bash
git add tools/measure_screen_widths.py requirements-dev.txt
git commit -m "tools: measure which published screens overflow, and at what width"
```

---

## Task 2: Derive a panel's minimum width from its own grid string

**Files:** Create `webgui/pages/panel_scroll.py`, Test: `webgui/tests/test_panel_scroll.py`

**Step 1: Write the failing tests**

```python
import panel_scroll                      # webgui/ is on sys.path via conftest
from pages import desk


def test_track_floors_reads_fixed_and_minmax_tracks():
    """A track is either a bare px width or minmax(<floor>, <weight>fr); the
    floor is what a CSS grid refuses to shrink below, so it is what decides
    whether a panel clips."""
    grid = "grid grid-cols-[64px_minmax(53px,0.8fr)_minmax(42px,0.6fr)] gap-x-[8px] w-full"
    assert panel_scroll.track_floors(grid) == [64, 53, 42]


def test_the_pos_grid_min_width_reproduces_the_number_desk_measured():
    """⚠ THE ANCHOR. desk.py states both numbers independently: its ten POS_GRID
    floors sum to 725, and the panel measures 839px at the 1877px boundary.
    If this function is right it must land on 839 without being told."""
    assert sum(panel_scroll.track_floors(desk.POS_GRID)) == 725
    assert panel_scroll.panel_min_width_px(desk.POS_GRID) == 839


def test_the_min_width_moves_when_a_floor_moves():
    """⚠ THE DISCRIMINATING TEST. A typed 839 satisfies the assertion above on
    the day it is written and stops being true the first time a track floor
    changes -- which is exactly the drift desk.py warns about ("keep this sum
    current when a floor moves")."""
    before = panel_scroll.panel_min_width_px(desk.POS_GRID)
    widened = desk.POS_GRID.replace("64px_", "164px_", 1)
    assert panel_scroll.panel_min_width_px(widened) == before + 100


def test_every_desk_panel_fits_the_budget_it_is_measured_against():
    """A panel whose floors oversubscribe PANEL_BUDGET_PX does not reflow, it
    CLIPS -- which is the whole failure this work is about."""
    for grid in (desk.DEALER_GRID, desk.BOARD_GRID, desk.FLOW_GRID, desk.POS_GRID):
        assert panel_scroll.panel_min_width_px(grid) <= desk.PANEL_BUDGET_PX
```

**Step 2: Run them and watch them fail**

```bash
cd webgui && "D:/.../python.exe" -m pytest tests/test_panel_scroll.py -q
```
Expected: `ModuleNotFoundError: No module named 'panel_scroll'`.

**Step 3: Implement**

`webgui/pages/panel_scroll.py` — pure, imports only `re`:

```python
"""Panel width arithmetic, derived from the panel's own column tracks.

PURE and dependency-free on purpose: the numbers here decide whether a panel
clips, and they must be computable in a unit test with no browser.
"""
import re

# Mirrors desk.PANEL_PAD_PX / COL_GAP_PX. ⚠ Read them from the page rather than
# restating: two copies of a width constant is the drift this module exists to
# prevent.
_TRACK = re.compile(r"minmax\((\d+)px,|(?<![\w(])(\d+)px_")


def track_floors(grid_classes):
    """The px floor of every column track, in order. PURE."""
    ...


def panel_min_width_px(grid_classes, *, pad_px=None, gap_px=None):
    """Floors + inter-track gaps + the panel's own padding. PURE."""
    ...
```

⚠ **Do not restate `PANEL_PAD_PX` or `COL_GAP_PX`** — import them from `pages.desk`, or move both into this module and have `desk.py` import them back. Prefer the latter if it does not create a cycle: one home per constant.

**Step 4: Run to green, then lint**

**Step 5: Commit**

```bash
git add webgui/pages/panel_scroll.py webgui/tests/test_panel_scroll.py
git commit -m "feat(webgui): derive a panel's minimum width from its own track floors"
```

---

## Task 3: The shared scroll CSS

**Files:** Modify `webgui/shell.py`, Test: `webgui/tests/test_shell_seam.py`

**Step 1: Write the failing tests**

```python
def test_the_panel_scroll_css_contains_overflow_at_the_panel_not_the_page():
    css = shell.PANEL_SCROLL_CSS
    assert "overflow-x: auto" in css
    # ⚠ never on body/html: containing it at the panel is the entire point.
    assert "body" not in css and "html" not in css


def test_the_identity_cell_is_sticky_and_opaque():
    """A sticky cell with no background paints the scrolling numbers straight
    through it, which reads as corruption rather than as a pinned column."""
    css = shell.PANEL_SCROLL_CSS
    assert "position: sticky" in css and "left: 0" in css
    assert "background" in css and "z-index" in css
```

**Step 2: Run, watch fail, implement**

Add to `webgui/shell.py`, beside `TABLE_CSS`/`SUBTAB_CSS`, with a comment recording that this **reverses** `desk.py`'s earlier position and why (document scroll loses the labels; panel scroll keeps them).

```css
.ns-panel-scroll { overflow-x: auto; overscroll-behavior-x: contain; }
.ns-panel-scroll > * { min-width: max-content; }
.ns-panel-row > :first-child {
  position: sticky; left: 0; z-index: 2; background: <panel bg>;
}
```

⚠ The sticky cell's background must be the panel's own, not transparent, or the scrolling cells paint through it.

**Step 3: Inject it from BOTH entrypoints**

`main.py` `_layout` and `live_main.py` already inject `TABLE_CSS`/`SUBTAB_CSS`; add this beside them in both. A test should assert both files inject it — if only one does, the public and private screens drift, which is the invariant this design promised not to break.

**Step 4: Commit**

---

## Task 4: Apply it to the Desk, and correct the note

**Files:** Modify `webgui/pages/desk.py`

**Step 1: Verify the first track really is the identity column**

⚠ For each of the four panels, confirm the FIRST track is what identifies the row before pinning it. From the grids: Dealer `78px` (symbol), Board `52px` (score — **check whether score or symbol identifies a row here**; if it is the symbol, that is track 2 and pinning track 1 pins the wrong thing).

Record the finding. If a panel's identity column is not first, either pin two tracks or leave that panel unpinned — **do not pin the wrong column**, which is worse than not pinning.

**Step 2: Wrap each panel's rows in the scroll container**, apply `min-width` from `panel_scroll.panel_min_width_px(<GRID>)`, and add `ns-panel-row` to `_grid_head` and `_ROW`.

**Step 3: ⚠ Correct the `_GAP` note in place** (`desk.py` ~line 2031)

Replace — do not annotate — the sentence:

> `overflow-x-auto` was deliberately not used as the fallback: a dashboard you scroll sideways to read defeats the page's purpose.

with the current reasoning: the objection was right and its conclusion did not hold, because rejecting per-panel scroll relocated the scroll to the document, where the reader loses the heading and the identity column too. Panel scroll with a pinned identity column is what the objection actually asked for.

**Step 4: Full suites, then commit**

---

## Task 5: Apply to whatever else Task 1 measured

**Files:** whichever page modules Task 1 named

Only those. ⚠ Screens built on `ui.table` already contain overflow in `.q-table__middle` and need nothing — confirm per screen rather than assuming either way.

---

## Task 6: Verify live, then document

**Step 1:** Promote (⚠ `tools/promote.sh` only — never `git pull` in the prod checkout; see [[promote-timing-on-a-trading-day]], default 15:25–16:15 CT).

**Step 2:** Re-run `tools/measure_screen_widths.py`. The document overflow must be **0** at every width, while the panel still reports internal overflow — assert both halves; "nothing overflows anywhere" would pass over a panel that clips.

**Step 3:** Look at a screenshot at 1366px and confirm the symbol column stays put while the numbers scroll.

**Step 4:** `docs/CHANGELOG.md` entry. `CLAUDE.md` gets a line only if an invariant changed — a shipped feature is not an entry there.

---

## Definition of done

- [ ] The min-width is derived from the track floors, and mutating a floor moves it.
- [ ] `panel_min_width_px(POS_GRID) == 839`, matching the number `desk.py` measured.
- [ ] The document does not scroll sideways at any tested width; the panel does.
- [ ] The identity column stays pinned, and it is the RIGHT column on every panel.
- [ ] Both entrypoints inject the CSS — no public/private drift.
- [ ] The superseded `_GAP` note is corrected in place, not annotated.
- [ ] Selenium is in `requirements-dev.txt` and NOT in `requirements.lock`.
- [ ] Every suite's failing and skipped **sets** unchanged from baseline.

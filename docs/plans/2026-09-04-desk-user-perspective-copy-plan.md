# Desk User-Perspective Copy — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Reword `/desk` (and its `/desk/live` mirror) so every label says what the
number is *for* rather than which mechanism produced it, without touching a single
value or any arithmetic.

**Architecture:** All display copy is hoisted into module-level constants in
`webgui/pages/desk.py` — `PANEL_HEADS` (title + use-line, row caps interpolated) and
four column-header tuples. `webgui/desk_stream.py` consumes those constants instead
of restating them: the HTML skeleton is already `.format()`ed so titles and use-lines
interpolate directly, and the header labels reach the un-formatted `_JS` blob through
the existing `consts` injection (the same mechanism `CALL_HEX` / `PUT_HEX` already
use). `webgui/page_help.py` follows for the terms that moved.

**Tech Stack:** Python 3.11, NiceGUI, pytest. Tailwind-first — no `.style()`.

**Design:** [2026-09-04-desk-user-perspective-copy-design.md](2026-09-04-desk-user-perspective-copy-design.md)

**Test command (worktree — the venv lives in the main checkout):**

```bash
cd "D:/WebGUI Trading with Schwab/.claude/worktrees/ev-formula-app-application-a55575/webgui" && "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest tests/test_desk.py tests/test_desk_stream.py -q
```

**Baseline (measured 2026-09-04, before any change): 270 passed.** Compare the
failing *set*, never the count.

---

## Task 1: Hoist the panel heads and column headers into constants

Pure refactor — **no copy changes**. Every existing test must still pass, which is
what proves the hoist is faithful before any word moves.

**Files:**
- Modify: `webgui/pages/desk.py` — add constants near `_panel` (~line 2240); the four
  `_panel(...)` calls (~2546–2553); the four `_grid_head(...)` calls (~2695, 2775,
  2847, 2909)
- Test: `webgui/tests/test_desk.py`

**Step 1: Write the failing test**

```python
def test_panel_heads_carry_every_panel_and_the_caps_stay_interpolated():
    """The heads are DATA, so /desk and /desk/live can read one copy.

    The row caps must stay interpolated rather than written down: the old
    "HOTTEST {N}" subtitle existed in that shape precisely so a cap change could
    not leave a stale number on the panel.
    """
    heads = desk.PANEL_HEADS
    assert set(heads) == {"dealer", "board", "flow", "positions"}
    for key, (title, use_line) in heads.items():
        assert title == title.upper(), key
        assert use_line, key
    assert str(desk.BOARD_ROWS_N) in heads["board"][1]
    assert str(desk.FLOW_ROWS_N) in heads["flow"][1]


def test_column_headers_are_constants_one_per_grid_track():
    """A header tuple must have exactly as many labels as its grid has tracks."""
    for heads, grid in ((desk.DEALER_HEADS, desk.DEALER_GRID),
                        (desk.BOARD_HEADS, desk.BOARD_GRID),
                        (desk.FLOW_HEADS, desk.FLOW_GRID),
                        (desk.POS_HEADS, desk.POS_GRID)):
        tracks = grid.split("grid-cols-[")[1].split("]")[0].count("_") + 1
        assert len(heads) == tracks
```

**Step 2: Run it and watch it fail**

Expected: `AttributeError: module 'pages.desk' has no attribute 'PANEL_HEADS'`.

**Step 3: Add the constants**

Place directly above `_panel` in `desk.py`. Keep the existing wording verbatim in
this task — only the *location* changes:

```python
# The panel heads, as DATA rather than four `_panel(...)` argument lists.
#
# `/desk/live` renders the same four panels from its own HTML skeleton, and until
# this constant existed it restated the titles as literals and rebuilt two of the
# subtitles with its own `.format`. That is the drift this file's opening
# principle exists to prevent, and CLAUDE.md's "the mirror cannot drift" note
# covers the ROWS, not the heads. Both screens now read this.
#
# The row caps are INTERPOLATED, never written down — the property the old
# "HOTTEST {N}" subtitle had, and the reason it was built that way.
PANEL_HEADS = {
    "dealer": ("DEALER POSITIONING", " · ".join(DESK_SYMBOLS)),
    "board": ("OPPORTUNITY BOARD", f"HOTTEST {BOARD_ROWS_N}"),
    "flow": ("LIVE FLOW ALERTS", f"NEWEST {FLOW_ROWS_N}"),
    "positions": ("POSITIONS", " · ".join(b["source"] for b in BOOKS)),
}

# One label per grid track. Hoisted for the same reason as the heads above: the
# mirror carries its own copy of all four lists, and a rename that reached one
# screen and not the other would put two different words on one number.
DEALER_HEADS = ("SYMBOL", "SPOT", "GAMMA FLIP", "STRUCTURE MAP",
                "CALL WALL", "PUT WALL", "NET GEX / REGIME")
BOARD_HEADS = ("SCORE", "SYMBOL", "WHY", "ATM IV", "NET PREM", "P/C",
               "SIGNAL", "SETUP")
FLOW_HEADS = ("TIME", "SYMBOL", "DETAIL", "KIND")
POS_HEADS = ("BOOK", "SYMBOL", "STRAT", "EXPIRY", "ENTRY", "MARK",
             "STRIKES", "QTY", "UNREALIZED", "FLAG")
```

Then replace the four `_panel(...)` calls with `_panel(*PANEL_HEADS["dealer"])` etc.,
and the four `_grid_head(GRID, (...))` calls with `_grid_head(GRID, DEALER_HEADS)` etc.

**Step 4: Run the suite**

Expected: 272 passed (270 baseline + the 2 new). A failure here means the hoist
changed a word — fix the constant, not the test.

**Step 5: Commit**

```bash
git add webgui/pages/desk.py webgui/tests/test_desk.py
git commit -m "refactor(desk): panel heads and column labels become data"
```

---

## Task 2: The four use-lines

**Files:**
- Modify: `webgui/pages/desk.py` — `PANEL_HEADS`, `_panel` (~2240)
- Test: `webgui/tests/test_desk.py`

**Step 1: Write the failing tests**

```python
def test_use_lines_say_what_the_panel_is_for():
    heads = desk.PANEL_HEADS
    assert "dealers" in heads["dealer"][1]
    assert "where to start looking" in heads["board"][1]
    # The honest caveat the Flow panel owes its reader: Schwab publishes no
    # time-and-sales tape to this app, so nobody here knows who initiated.
    assert "not who initiated" in heads["flow"][1]
    assert "needs a decision" in heads["positions"][1]


def test_a_use_line_fits_the_panel_it_stands_in():
    """~125 characters at 11px in an 860px panel; 90 is the guard rail.

    Width, not height: this page already scrolls (see DESK_SCROLLBAR_PX), so a
    line too TALL costs nothing, while a line too WIDE overflows a panel that
    cannot reflow.
    """
    for key, (_title, use_line) in desk.PANEL_HEADS.items():
        assert len(use_line) <= 90, (key, len(use_line))


def test_the_dropped_facts_are_the_columns_underneath_them():
    """The symbol and book lists left the head because the SYMBOL and BOOK
    columns already print them. The sort order did NOT — it is real information
    and had to survive into the use-line."""
    heads = desk.PANEL_HEADS
    assert "$SPX" not in heads["dealer"][1]
    assert desk.PAPER_SOURCE not in heads["positions"][1]
    assert "hottest" in heads["board"][1].lower()
    assert "newest" in heads["flow"][1].lower()
```

**Step 2: Run and watch them fail.**

**Step 3: Rewrite the use-lines**

```python
PANEL_HEADS = {
    "dealer": ("DEALER POSITIONING",
               "Above the flip, dealers damp moves; below it they feed them."),
    "board": ("OPPORTUNITY BOARD",
              f"The {BOARD_ROWS_N} hottest names right now — where to start "
              f"looking."),
    "flow": ("LIVE FLOW ALERTS",
             f"The {FLOW_ROWS_N} newest unusual trades. Which side traded, "
             f"not who initiated."),
    "positions": ("POSITIONS",
                  "What you and Claude are holding, and what needs a decision."),
}
```

**Step 4: Render it on its own line**

In `_panel`, the use-line goes UNDER the title row, not in it. The existing
subtitle slot is `whitespace-nowrap` on the title row and would overflow the
narrow panels; and prose at the fact-subtitle's `.2em` tracking is unreadable.

```python
def _panel(title, use_line=""):
    """A console card with a titled head; returns the BODY container.

    ``use_line`` is prose and gets its OWN line under the rule — normal tracking,
    not the `.2em` a fact subtitle wears, and not the title row's nowrap slot,
    which would overflow the 508px-floor Flow panel.
    """
    with ui.column().classes(f"{CONSOLE_CARD} w-full px-4 pt-4 pb-4 gap-2"):
        with ui.column().classes(f"w-full gap-1 border-b {CONSOLE_RULE} pb-2"):
            ui.label(title).classes(
                f"{CONSOLE_DISPLAY} text-[19px] font-bold tracking-[.16em] "
                f"{CON_TXT}")
            if use_line:
                ui.label(use_line).classes(f"text-[11px] leading-snug "
                                           f"{CON_TXT_DIM}")
        body = ui.column().classes("w-full gap-0")
    return body
```

**Step 5: Run the suite.** Expected: all green.

**Step 6: Commit**

```bash
git add webgui/pages/desk.py webgui/tests/test_desk.py
git commit -m "feat(desk): each panel says what it is for, in a line of its own"
```

---

## Task 3: Header renames

Every rename is same-length-or-shorter against its documented track floor except
`NET PREM` -> `NET PREMIUM` (+22px; Board's floor 783 -> 805, still under
Positions' page-setting 839). `STRAT` and `QTY` are deliberately untouched: the code
records that on those two tracks *the head label binds*, and expanding both costs
~58px against 43px of slack, pushing the minimum window past the 1920px this page is
read at.

**Files:**
- Modify: `webgui/pages/desk.py` — the four header constants from Task 1
- Test: `webgui/tests/test_desk.py`

**Step 1: Write the failing tests**

```python
def test_headers_name_the_use_not_the_mechanism():
    assert desk.DEALER_HEADS == ("SYMBOL", "PRICE", "FLIP LEVEL",
                                 "PRICE VS WALLS", "CEILING", "FLOOR",
                                 "DEALER MODE")
    assert desk.BOARD_HEADS == ("SCORE", "SYMBOL", "WHY IT'S HOT", "ATM IV",
                                "NET PREMIUM", "P/C", "SIGNAL", "SETUP")
    assert desk.FLOW_HEADS == ("TIME", "SYMBOL", "WHAT TRADED", "ALERT TYPE")
    assert desk.POS_HEADS == ("BOOK", "SYMBOL", "STRAT", "EXPIRY", "ENTRY",
                              "MARK", "STRIKES", "QTY", "OPEN P&L", "STATUS")


def test_the_two_width_blocked_labels_stay_short():
    """STRAT and QTY are the two tracks the head label BINDS (see POS_GRID's
    floor notes). Expanding them costs ~58px against 43px of slack and would
    push the page's minimum window past the 1920px it is read at. This is a
    deferral with a reason, so it gets a test rather than a comment nobody
    reads."""
    assert "STRAT" in desk.POS_HEADS and "STRATEGY" not in desk.POS_HEADS
    assert "QTY" in desk.POS_HEADS and "CONTRACTS" not in desk.POS_HEADS


def test_trader_acronyms_survive_the_reword():
    """The standing rule is: spell out casual shortenings, keep trader
    acronyms. NET PREM was the casual one; ATM IV and P/C are not."""
    assert "ATM IV" in desk.BOARD_HEADS
    assert "P/C" in desk.BOARD_HEADS
```

**Step 2: Run and watch them fail.**

**Step 3: Apply the renames** to the four constants, and update the comment above
`DEALER_HEADS` noting `CEILING`/`FLOOR` keep their call/put identity in the cell
COLOUR (each wall is painted in its structure-map marker's hue) rather than in the
label.

**Step 4: Run the suite.** Fix the two tests from Task 1 that assert old labels if
they name them literally.

**Step 5: Commit**

```bash
git add webgui/pages/desk.py webgui/tests/test_desk.py
git commit -m "feat(desk): column labels name the use, not the mechanism"
```

---

## Task 4: Empty and waiting states

**Files:**
- Modify: `webgui/pages/desk.py` — `WAITING_OPTIONS` (~2038), `WAITING_BULLBEAR`
  (~685), the four inline placeholders (~2683, 2762, 2832, 2899), the stale-walls
  warning (~2689)
- Modify: `webgui/pages/sentiment_bullbear.py` — `WAITING` (~38), which
  `WAITING_BULLBEAR` mirrors by explicit comment
- Test: `webgui/tests/test_desk.py`

**Step 1: Write the failing tests**

```python
def test_waiting_copy_states_what_is_true_not_which_service_is_cold():
    """Off-hours this is the most-read text on the page, and naming an internal
    service makes a quiet market read as a fault."""
    assert "service" not in desk.WAITING_OPTIONS.lower()
    assert "hasn't published" in desk.WAITING_OPTIONS


def test_a_cold_feed_is_still_distinguishable_from_a_quiet_market():
    """The pre-existing invariant, restated because this task rewrites both
    sides of it: rendering the same words for both would make a dead service
    indistinguishable from a market with nothing to say."""
    assert desk.WAITING_BULLBEAR != desk.WAITING_OPTIONS
    assert "16:20" in desk.WAITING_BULLBEAR    # the nightly cascade, actionable
```

**Step 2: Run and watch them fail.**

**Step 3: Apply the copy** from the design's table. The stale-walls line keeps its
`fresh["label"]` interpolation:

```python
ui.label(
    f"Walls hidden — the gamma feed stopped updating "
    f"({fresh['label'].lower()}), so these levels would be out of date."
).classes(f"text-[10px] {CON_WARN} pb-1")
```

**Step 4: Run the FULL webgui suite** — `sentiment_bullbear.WAITING` has its own
tests outside `test_desk.py`.

**Step 5: Commit**

```bash
git add webgui/pages/desk.py webgui/pages/sentiment_bullbear.py webgui/tests/test_desk.py
git commit -m "feat(desk): empty states say what is true, not which service is cold"
```

---

## Task 5: The `/desk/live` mirror reads the same constants

**Files:**
- Modify: `webgui/desk_stream.py` — `_board` (~316), `_flow_panel` (~343),
  `_positions` (~374), `_dealer` (~276), `document()` (~949), the HTML skeleton
  (~1005–1022), `_JS` header lists (~834, 866, 893, 910)
- Test: `webgui/tests/test_desk_stream.py`

**Step 1: Write the failing tests**

```python
def test_the_mirror_takes_its_panel_heads_from_the_desk():
    for key in ("dealer", "board", "flow", "positions"):
        title, use_line = d.PANEL_HEADS[key]
        assert title in ds.document()
        assert use_line in ds.document()


def test_the_mirror_takes_its_column_labels_from_the_desk():
    """The header lists live in `_JS`, which is inserted verbatim rather than
    formatted — so they reach it through the same `consts` injection CALL_HEX
    already uses, not as a second set of literals."""
    doc = ds.document()
    for heads in (d.DEALER_HEADS, d.BOARD_HEADS, d.FLOW_HEADS, d.POS_HEADS):
        for label in heads:
            assert json.dumps(label)[1:-1] in doc, label
    # ...and the old words are gone from the mirror entirely.
    for gone in ("GAMMA FLIP", "CALL WALL", "PUT WALL", "NET GEX / REGIME",
                 "UNREALIZED", "STRUCTURE MAP"):
        assert gone not in doc, gone


def test_the_dealer_panel_finally_has_a_subtitle_slot():
    """It was the one panel with no `psub` element, so the mirror could not
    show its head even when the snapshot carried one."""
    assert 'id="dealer-sub"' in ds.document()
```

**Step 2: Run and watch them fail.**

**Step 3: Implement**

1. In each of the four `_*` panel builders, replace the restated subtitle with
   `_d.PANEL_HEADS[key][1]`, and give `_dealer` a `subtitle` key it currently lacks.
2. In `document()`, extend `consts` with the header lists — the mechanism already
   there for `CALL_HEX`:
   ```python
   consts += "const HEADS = {heads};\n".format(heads=json.dumps({
       "dealer": list(_d.DEALER_HEADS), "board": list(_d.BOARD_HEADS),
       "flow": list(_d.FLOW_HEADS), "positions": list(_d.POS_HEADS)}))
   ```
3. In `_JS`, keep the mirror-specific width percentages and zip them against the
   injected labels — the widths are that screen's layout, the labels are shared:
   ```javascript
   const tb = table(b, HEADS.dealer.map((l, i) => [l, [9,17,17,15,13,13,16][i]]));
   ```
4. In the HTML skeleton, add the four titles and use-lines as format keys, and give
   the Dealer panel the `psub` element it lacks:
   ```html
   <div class="phead"><div class="ptitle">{dealer_title}</div>
     <div class="psub" id="dealer-sub"></div></div>
   <div class="puse">{dealer_use}</div>
   ```
   Add a `.puse` rule to `_CSS` matching `/desk`'s 11px use-line, and pass the eight
   new keys in the closing `.format(...)`.

⚠ The HTML literal is `.format()`ed, so any literal brace added to it must be
doubled. `_JS` is inserted verbatim and must NOT have its braces touched.

**Step 4: Run the suite.**

**Step 5: Commit**

```bash
git add webgui/desk_stream.py webgui/tests/test_desk_stream.py
git commit -m "feat(desk/live): the mirror reads the Desk's own head and label copy"
```

---

## Task 6: `page_help.py` follows the words that moved

The manuals rot silently because nothing fails when they go stale. Four terms in the
`/desk` help entry now name things the screen no longer calls that.

**Files:**
- Modify: `webgui/page_help.py` — the `/desk` entry (~48–88)
- Test: `webgui/tests/test_desk.py`

**Step 1: Write the failing test**

```python
def test_the_desk_help_calls_things_what_the_screen_calls_them():
    """`term in text` is not coverage — this checks the RENAMED words are
    present and the superseded ones are gone, which is the pair that catches a
    half-applied rename."""
    import page_help
    text = page_help.PAGE_HELP["/desk"]
    for gone in ("a flag:", "call and put walls"):
        assert gone not in text, gone
    assert "ceiling" in text.lower() and "floor" in text.lower()
    assert "status" in text.lower()
```

**Step 2: Run and watch it fail.**

**Step 3: Update the help prose** — walls become the ceiling/floor pair the screen
names, `flag` becomes `status`, `unrealised profit and loss` becomes `open P&L`.
Keep every explanation; only the nouns change.

**Step 4: Run the full webgui suite.**

**Step 5: Commit**

```bash
git add webgui/page_help.py webgui/tests/test_desk.py
git commit -m "docs(desk): the hover guide calls things what the screen calls them"
```

---

## Task 7: Full-suite verification

**Step 1:** Run the whole webgui suite, not just the desk files:

```bash
cd "D:/WebGUI Trading with Schwab/.claude/worktrees/ev-formula-app-application-a55575/webgui" && "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest -q
```

**Step 2:** Compare the **failing set** against the pre-change run, name by name —
not the count. `-rf` is the repo default.

**Step 3:** Confirm `tests/test_no_inline_style.py` still passes — `_panel` gained an
element and it must carry Tailwind classes only.

---

## Out of scope, recorded so it is a deferral and not an omission

- `BIAS` / `SIGNAL` tile captions, the trend words, the regime words, the Bull/Bear
  headline sentence — all imported from `/sentiment`; rewording them changes that
  screen too, which is a separate decision.
- The Positions summary line (`OPEN 4 · UNREALIZED $90.00 · AT RISK 2`) and the
  top-strip captions.
- `STRAT` / `QTY`, blocked on the 1920px width budget (Task 3).

## Verification limit

Nothing here is browser-verified. A worktree has no `env.local.toml`, resolves to
prod and would bind `:8500` where the live stack is. The width claims are arithmetic
against the floors `desk.py` documents — `NET PREMIUM`'s +22px on the Board panel
wants eyeballing in dev before promote.

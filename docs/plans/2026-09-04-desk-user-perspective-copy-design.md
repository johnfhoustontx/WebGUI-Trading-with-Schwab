# Desk — rewording the screen from the reader's perspective (design)

**Date:** 2026-09-04
**Scope:** `/desk` and its standalone mirror `/desk/live`. First of a planned pass
across the app; the Desk goes first because it is the landing page.

## The problem

The Desk names **mechanisms**. `GAMMA FLIP`, `NET GEX / REGIME`, `STRUCTURE MAP`,
`UNREALIZED`, `WHY`, `KIND`, `Waiting for the options service…`. Every one of those
is the name of the thing that produced the number, not a statement of what the
reader does with it.

The knowledge is not missing — it is one hover away. `webgui/page_help.py`'s
`/desk` entry already translates the whole screen into use-language ("*Long gamma
· pins* means dealer hedging tends to **hold** price near those walls"). It is on
the wrong side of a tooltip.

This pass moves that voice onto the screen itself, without touching a single
value or any arithmetic.

## What is NOT changing, and why

The Desk's load-bearing principle is that it **composes, never restates** — every
word it prints is imported from the page that owns it, because a screen
aggregating ten pages is ten chances to reproduce the documented
`/sentiment/sectors` vs `/sentiment/rotation` contradiction.

So the following are out of scope here. They are not Desk copy; they are other
screens' copy that the Desk borrows, and rewording them is a decision about those
screens:

- `BIAS` / `SIGNAL` tile labels and descriptors — `pages/sentiment.SIGNAL_TILE_DEFS`
- the trend vocabulary — `pages/sentiment._TREND_SHORT`
- the regime words (Rallying / Balanced / Whipsaw / Stressed …) — mirrored in four
  tiers and pinned by `shared/tests/test_cross_tier_mirrors.py`
- the Bull / Bear headline sentence — `sentiment_bullbear.headline_line`
- the Positions summary line and the top-strip captions — deferred by choice, to
  keep this pass reviewable.

## 1. One source for the panel heads

The Desk's own principle does not currently hold for the panel **heads**:

| | today |
|---|---|
| `webgui/pages/desk.py` | titles + subtitles written inline in four `_panel(...)` calls |
| `webgui/desk_stream.py` | subtitles **rebuilt** (`"HOTTEST {}".format(_d.BOARD_ROWS_N)`, three sites) |
| `webgui/desk_stream.py` | titles **hardcoded** in the HTML literal, and the Dealer panel has no `psub` element at all |

CLAUDE.md records that the mirror "cannot drift from `/desk`" because `snapshot()`
returns strings built by `desk.py`'s own builders. That is true of the **rows**. It
is not true of the heads, and this pass would otherwise make the two screens
disagree on the same morning.

**Fix:** a module-level `PANEL_HEADS` in `desk.py` — one ordered mapping of
`key -> (title, use_line)`, row caps interpolated so the counts stay derived rather
than written down. `_panel()` reads it; `desk_stream.snapshot()` reads it; the
mirror gains the `#dealer-sub` element it lacks. A test asserts every title in the
mirror's HTML literal appears in `PANEL_HEADS`, so a literal cannot drift from it.

## 2. The use-lines

One line per panel, on its **own** line under the title row — not in the existing
subtitle slot, which is `whitespace-nowrap` on the title row and would overflow.
Normal tracking, not the fact-subtitle's `.2em`, which is unreadable on prose.

Height is affordable: line 1870 of `desk.py` records that this page is already
taller than any window it is read in, so the classic scrollbar is always present.
Width is comfortable: each panel gets **860px** at the 1920px window (508px is
Flow's *floor*, not its width), leaving ~125 characters at 11px. The longest line
below is 74.

| Panel | Use-line | Absorbs |
|---|---|---|
| Dealer Positioning | `Above the flip, dealers damp moves; below it they feed them.` | — |
| Opportunity Board | `The {BOARD_ROWS_N} hottest names right now — where to start looking.` | `HOTTEST 6` |
| Live Flow Alerts | `The {FLOW_ROWS_N} newest unusual trades. Which side traded, not who initiated.` | `NEWEST 9` |
| Positions | `What you and Claude are holding, and what needs a decision.` | — |

The sort order (`HOTTEST` / `NEWEST`) is real information and survives into the
use-line, still interpolated from the cap. The two dropped facts —
`$SPX · SPY · $NDX · QQQ` and `PAPER · CLAUDE · CAPTURED` — are literally the
`SYMBOL` and `BOOK` columns underneath them.

The Flow line keeps the honest caveat the page already owes the reader: Schwab
publishes no time-and-sales tape to this app, so nobody here knows who initiated.

## 3. Header renames

Constraint: on the Positions panel the code documents that **the head label binds
the track, not the value** (`STRAT 42px`, `QTY 36px`), and that panel's ten floors
set the page's minimum supported window — 1877px innerWidth against the 1920px
this is read at, i.e. 43px of slack. Below the minimum a CSS grid clips rather
than reflows.

Every rename below is same-length-or-shorter against its floor except `NET PREM`,
which is costed.

| Panel | Was | Now | Width |
|---|---|---|---|
| Dealer | `SPOT` | `PRICE` | value binds; free |
| Dealer | `GAMMA FLIP` | `FLIP LEVEL` | identical |
| Dealer | `STRUCTURE MAP` | `PRICE VS WALLS` | 112px into a 136px floor |
| Dealer | `CALL WALL` | `CEILING` | shorter |
| Dealer | `PUT WALL` | `FLOOR` | shorter |
| Dealer | `NET GEX / REGIME` | `DEALER MODE` | shorter |
| Board | `WHY` | `WHY IT'S HOT` | 96px into a 200px floor |
| Board | `NET PREM` | `NET PREMIUM` | **+22px**; floor 783 -> 805, still under Positions' 839 |
| Flow | `DETAIL` | `WHAT TRADED` | 88px into a 192px floor |
| Flow | `KIND` | `ALERT TYPE` | 80px into a 126px floor |
| Positions | `UNREALIZED` | `OPEN P&L` | shorter |
| Positions | `FLAG` | `STATUS` | shorter |

**Left alone, each for a stated reason:**

- `STRAT`, `QTY` — blocked on width. Expanding both costs ~58px against 43px of
  slack and would push the minimum window past the one this page is read at. This
  is a deferral, not an oversight; revisit if a track elsewhere is ever narrowed.
- `SCORE` — the code already records that `HOTNESS` does not fit the 52px track.
- `ATM IV`, `P/C` — trader acronyms, kept per the standing "spell out casual
  shortenings, keep trader acronyms" rule. `NET PREM` is a casual shortening, which
  is why it is the one expansion here.
- `SYMBOL`, `TIME`, `BOOK`, `EXPIRY`, `ENTRY`, `MARK`, `STRIKES`, `SIGNAL`, `SETUP`
  — already plain.

**Two judgement calls, recorded rather than buried:**

- `CEILING` / `FLOOR` say what the level is *for*, but drop the call/put naming
  that the cell's colour still encodes (each wall is painted in its marker's hue on
  the structure map). A call wall is also only a ceiling while price sits below it.
  Accepted deliberately: the reader's question is "what stops price here", and the
  colour plus the structure map still carry the side.
- `FLAG` -> `STATUS` means `page_help.py`'s matching sentence ("and a flag: **OK**,
  **Watch**, **At risk**, **Rescue**") must change in the same commit, or the help
  and the screen disagree — the exact rot the manuals section warns about.

## 4. Empty and waiting states

These name internal services rather than telling the reader what is true. Off-hours
this is the most-read text on the page, and "Waiting for the options service" reads
as a fault rather than as a quiet market.

| Was | Now |
|---|---|
| `Waiting for the options service…` | `No data yet — the options feed hasn't published this session.` |
| `No dealer positioning published for these symbols yet.` | `No dealer positioning yet — these levels appear once the gamma feed publishes.` |
| `No ranked symbols yet.` | `Nothing ranked yet — the board fills once the scanner runs.` |
| `No alerts today.` | `Nothing unusual has traded yet today.` |
| `No open positions.` | `Nothing open — no paper or Claude trades running.` |
| `Walls withheld — GEX feed {label}` | `Walls hidden — the gamma feed stopped updating ({label}), so these levels would be out of date.` |
| `WAITING_BULLBEAR` (nightly-cascade wording) | `No Bull / Bear map yet — it's rebuilt nightly at 16:20 CT.` |

Two invariants the existing code states and this must preserve:

- **A cold service and a quiet market must stay distinguishable.** `WAITING_OPTIONS`
  is not the same string as "nothing to report", and `test_desk.py` pins
  `WAITING_BULLBEAR != WAITING_OPTIONS`.
- `WAITING_BULLBEAR` deliberately mirrors `sentiment_bullbear.WAITING`. Both change,
  so the two screens keep saying the same thing.

## 5. Tests

- Existing: `test_desk.py` pins `WAITING_OPTIONS` at four call sites and the
  cold-vs-quiet distinction; `test_desk_stream.py` pins the `HOTTEST N` subtitle.
  Both move onto `PANEL_HEADS` and survive.
- New: the mirror's HTML titles must all appear in `PANEL_HEADS` (the anti-drift
  guard for a literal that cannot interpolate).
- New: every use-line stays under the measured character budget, so a later edit
  cannot silently overflow a panel.
- New: the row caps stay *interpolated* into the Board and Flow use-lines — the
  property the old `HOTTEST {N}` subtitle had, which is why it existed.

## 6. Verification

Arithmetic, not measurement. The width claims are computed against the floors
`desk.py` documents; `NET PREMIUM`'s +22px in particular wants eyeballing in dev
before promote. This cannot be previewed from a worktree — a worktree has no
`env.local.toml`, resolves to prod, and would bind `:8500` where the live stack is.

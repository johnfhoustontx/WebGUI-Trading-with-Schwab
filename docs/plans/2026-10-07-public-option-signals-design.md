# Public Option Signals: design

**Date:** 2026-10-07 · **Status:** approved by the owner the same day.

## What is being built

The Market Scanner (`/options/scanner` on the private app) is published on the
public live origin as **Option Signals**, at `live.neuralstrike.co/signals`. It
is the first entry in the site's Tools menu and has no tile on the live grid,
like the other Tools entries.

The 0-DTE, Swing and Directional tabs move as they are, with everything under
them: the Credit spreads / Other structures switch, the family checkboxes, the
Updated stamp, the status line, Only clear, "Why no trade?" and the Trade
detail panel. The Run scan button does not move.

## Owner decisions

| Question | Answer |
|---|---|
| Trade detail panel | Yes, read-only. No Calculator, Paper trade or Expected Move button |
| Checks column and Only clear | Yes, without the Paper book line |
| Realized results ("Signals like this", Track record) | Published |

Three choices were made without a question and accepted with the design:
"Why no trade?" stays (a pure read of the last scan's funnel); net credit and
max loss are shown and per-leg bid/ask is not (what the public Finder and the
trade-idea cards already publish); the route is `/signals`.

**Changed while building, and not what was approved.** The design as approved
said the panel's Greeks would be shown. The browser check showed the checklist
printing the bid-ask round trip as a share of the credit, and the existing
public Calculator and Simulator turned out to withhold delta, theta and that
cost line while the site's quotes switch (`public_scan.show_leg_quotes`, off
pending Schwab's redistribution terms, roadmap decision D2) is off. Option
Signals now follows the same switch: off, the panel draws no Greeks section and
the cost-to-trade line reads as not measured; on, the cost line, delta and the
short leg's IV come back. Theta and vega are never printed on the public
origin, as on the public Calculator. It publishes less than was approved, and
the owner's existing switch reverses it.

**Decided by the owner after the code review (2026-10-07).** The review
pointed out that the switch withheld what it named and not everything a quote
feeds: a credit spread's PoP is one minus its short delta, and a single-leg
row's Debit, Max loss and breakeven are that option's own price. The public
Finder prints the same columns on request; Option Signals would print them for
every watchlist symbol on every scan. The owner chose: while the switch is
off, a single-option row carries no price (table cells and panel); PoP stays;
a multi-leg row's net stays. `strategy_table.public_signal` does it, at the
build, before any row, panel or check reads the signal. The public Finder is
unchanged and stays part of roadmap decision D2.

## Approach

One module, gated on the origin. `scanner.render()` reads `shell.is_public()`
and `shell.may_enqueue()` once and skips the owner-only pieces. Flow Alerts,
the Opportunity Board and the Desk are published the same way, and a published
page built from the private page's own `render` cannot drift from it.

Two alternatives were ruled out. A separate `scanner_live.py` (the Strategy
Finder's shape) would copy about 500 lines of tab, table and paging wiring,
because with the answers above the public page is nearly the whole private
one. Extracting a shared page body is a larger refactor of the private page
than this change needs.

The gate reads the PROCESS's origin, never a `public=True` argument: a keyword
can be left off a `Screen` entry, and the result would be the private page on
an unauthenticated origin.

## The screen entry

```python
Screen("signals", "/signals", "Option Signals", "options.scanner",
       "/options/scanner", tile=False)
```

No settings pin and no `kwargs`. Adding `/options/scanner` to `PUBLIC_ROUTES`
changes no other published screen: the only page that links to the scanner is
`/symbol`, which is not published.

Nothing changes server-side. The page reads views `options_svc` already
publishes (`options:scan_day`, `options:scan`, `options:scan_funnel`,
`options:matrix`, `sentiment:regime`, `options:calibration`), all under the
live ACL user's existing `~cache:*` read. No new write path, no Caddy change,
no Schwab call.

## What the public page leaves out

| Left out | Why | Enforced by |
|---|---|---|
| Run scan | A rescan spends Schwab calls | The button is not built; `_request_scan` opens with `if not _may_enqueue: return`, which `test_live_commands.py` requires of every published module |
| Paper trade, Calculator, Expected Move | Paper writes the owner's book; the other two hand off through `handoff._pending`, one store every visitor would share | The footer buttons are not built |
| Paper-result toasts | `handoff.watch_paper_results` would toast the owner's Paper clicks to every visitor | The watcher is not started |
| Paper book check line | It reads `options:ledger_caps`, the owner's ledger | `checks_feed.read_context` never reads caps on the public origin, whatever the caller passes; every public row's `_allow_paper` is closed, so `checks._book` returns no line at all (absent, not grey) |
| "new" badges | `scanner._SEEN` is one set per process: one visitor's page load would clear every other visitor's badges | The seen-set is neither read nor written; no row is stamped `_new` |
| "Max contracts" in the panel | It is sized from the owner's per-trade risk limit | `detail._build_cards` does not draw the row when `shell.is_public()` |
| The cap's dollar figure in "Why no trade?" | The same limit; `funnel_view` prints it whenever an entry carries `max_risk_dollars` (no publisher stamps it today) | `scanner.funnel_cards(public=True)` reads the entry without that key |
| The Greeks section and the cost-to-trade figure, while the quotes switch is off | Read off a contract's own quote | `checks_feed.quotes_withheld`; `detail._build_cards` and `checks_feed.checks_for` each judge a copy without those fields |

The caps refusal sits in `checks_feed.read_context` rather than at each caller
because the Trade detail panel reads a context of its own when the page holds
none yet (`detail._Handle._load_checks`). One chokepoint covers the table
stamp, the re-stamp and that fallback.

## Memory: one build per scan, shared

The day union reaches about 4.5 MB of JSON by the close. The private page
reads it with `bus_client.read`, which hands each tab its own parse, and then
builds about five thousand row dicts per tab. That is right for one owner. The
public process has a memory cap (`MemoryHigh` 768M, `MemoryMax` 1G) and serves
anonymous traffic, so a per-tab copy is a cost a stranger controls.

On the public origin the page takes its rows from a process-wide build:

- `scanner_shared.get(parts, build, max_age=...)` returns the same built dict
  to every caller whose inputs are the same OBJECTS as the last build's, until
  the build is `checks_feed.TABLE_REFRESH_SEC` old (the Opportunity Board moves
  every minute and feeds the checks; the private page re-stamps on that same
  five-minute cadence). The inputs are the day union, the live scan, the regime
  and the calibration.
- Identity, not a version probe: the scanner's own version-gated copies of
  the two scan views (`scanner._shared_view`) and the checklist's gated reads
  hand back the same object until a view is republished, so there is no probe
  to race the payload it describes. The parse is shared too.
- The age limit is HALF a tab's own tick, and the key carries a `stamp`
  compared by value: today's date and whether quote figures are withheld. Both
  came from the code review. With the limit equal to the tick a lone visitor's
  board-fed checks were re-stamped every ten minutes, not five; and a build
  made at 23:57 was served as today's at 00:01.
- `scanner._shared_view` returns the object it already holds when the view's
  counter has moved ahead of its envelope, so a visitor polling in that gap
  does not trigger a rebuild of the old scan (also from the review).
- Not `bus_client.read_shared`, which the first cut used. It keeps the 48 views
  read most recently, and the public Gamma page alone can read that many: a
  day union dropped between two visitors' reads comes back as a different
  object, and the slot would build once per visitor. Found in self-review and
  reproduced by a test before it was fixed.
- One lock: visitors arriving together at a new scan wait on one build.
- What it returns is READ-ONLY. The page never stamps a shared row. The
  selected-row accent is stamped on a copy of the one page of rows the visitor
  is sent (`_show_page`), and a re-stamp on the public origin is a fresh shared
  build rather than a per-tab copy of every row.

The private path is unchanged: its own parse, its own rows, `kit.mark_selected`
on its own dicts.

The per-tab figure (roughly 20 MB by the close) is an estimate from the
payload's size, not a measurement. The shared build is cheap enough that the
estimate does not need to be right for it to be worth doing: `read_shared`'s
own docstring describes this exact case.

## Tests

- `test_live_screens.py`: seventeen screens become eighteen.
- `test_live_commands.py`: covers `options.scanner` by enumeration once the
  screen exists; `_request_scan` must carry the gate.
- `deploy/tests/test_site.py`: `TOOLS` gains `("signals", "Option Signals")`
  first, with a new assertion that the menu's order is `TOOLS`' order; `_WORDS`
  gains six and eighteen; the lede and both meta descriptions are reworded.
- New `webgui/tests/test_scanner_public.py`, rendering as the public origin:
  no Run scan, Paper trade, Calculator or Expected Move button; the paper
  watcher not started; `read_context` reads no caps; no row carries a Paper
  book line or `_new`; no "Max contracts"; a selection does not mutate a shared
  row. Each absence has a partner test that the private render still has it.
- New `webgui/tests/test_scanner_shared.py`: one build per version, a rebuild
  when a version moves or the build ages out, and an absent view not cached.

## Documentation

`docs/reference/public-live-screens.md` (seventeen to eighteen, a paragraph on
this screen and the shared build), `docs/webgui-routes.md`, the CHANGELOG, the
manuals that count or list the live screens, and `CLAUDE.md` only if a rule
changes (the shared-rows rule is one line under "The public live screens").

## Verification

There is no dev environment. The page is rendered as the public origin in
`tools/ui_harness.py` on a fake bus and checked in a browser. After the
promote, `live.neuralstrike.co/signals` is checked read-only.

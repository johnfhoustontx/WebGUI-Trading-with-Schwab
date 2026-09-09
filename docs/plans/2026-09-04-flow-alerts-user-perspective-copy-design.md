# Flow Alerts — rewording the screen from the reader's perspective (design)

**Date:** 2026-09-04
**Scope:** `/options/flow`. Second page in the pass that began with `/desk`
([design](2026-09-04-desk-user-perspective-copy-design.md)); it went next because
the Desk pass left it holding the app's other copy of
"Waiting for the options service…".

## The problem, and what is different here

The Desk named mechanisms in its *labels*. This page names a **detector** in its
data: `Crossover`, `Unusual activity`, `Gamma flip`, `Big delta` are the names of
the four things `options_svc` runs, not statements of what happened in the market.
A reader who has not read the help cannot tell from `Big delta · Call` whether
something bullish, large, or merely odd occurred.

Two things are already good and are left alone: the page's head line
("Today's options-flow alerts, newest first — click a row for dealer
positioning") is a use-line, which is what the Desk had to grow; and
`page_help.py`'s `/options/flow` entry explains all four detectors well.

## 1. The alert vocabulary

This is the substance of the change. The `(kind, side)` pair is what the row
prints, what the Desk's flow panel prints, and what the Desk **speaks aloud**.

| key | was | now |
|---|---|---|
| `crossover` | Crossover | **Premium shift** |
| `uoa` | Unusual activity | **Unusual volume** |
| `gamma_flip` | Gamma flip | **Hedging flipped** |
| `big_delta` | Big delta | **Outsized bet** |

**The sides move with them, or the rename is worse than not doing it.**
`Hedging flipped · To positive` is less legible than `Gamma flip · To positive`,
because "to positive" is only interpretable once you already know the subject is
gamma sign — the word the new kind name removes.

| key | was | now |
|---|---|---|
| `to_positive` | To positive | **Now damping** |
| `to_negative` | To negative | **Now amplifying** |

`Calls over` / `Puts over` / `Call` / `Put` are unchanged: they are what the
alert can honestly claim, and the "call or put, never bought or sold" caveat
depends on them staying exactly that literal.

### What this ripples into, and why none of it is silent

- **The Desk's flow panel** and **`/desk/live`** both print `flow_kind_text(row)`
  over `flow.alert_rows` output. They follow automatically — which is the point.
- **`webgui/voice.py` speaks these words.** `_ALL_CAUSES` and `CONTRACT_KINDS`
  restate the display labels deliberately (the prewarm runs before any page is
  built, so `voice` must import no `pages`). Both are guarded:
  `test_all_causes_cover_every_pair_the_flow_page_can_emit` and
  `test_the_prewarm_list_is_exactly_the_contract_less_pairs` recompute the
  expected set through `flow.alert_kind_label` / `flow.side_label`, so a rename
  **fails the suite** rather than desynchronising the audio.
- **The prewarmed clip cache** under `webgui/data/voice/` is keyed by phrase
  text, so the new phrases synthesize on first use and the old files are
  orphaned. Gitignored, self-healing, and worth one line in the CHANGELOG.

Spoken result: *"S P Y. Premium shift, calls over."* and
*"S P X. Hedging flipped, now damping."* — both say what happened rather than
which detector fired.

## 2. Column headers

Matched to `/desk`'s words for the same concepts, because the same number
labelled two ways on two screens is the drift the Desk pass just closed.

| was | now | why |
|---|---|---|
| `Type` | **Alert type** | the Desk's word for this column |
| `Detail` | **What traded** | the Desk's word for this column |
| `Alert` | **Summary** | it holds the service's own sentence; "Alert" beside "Alert type" named two different things one word |
| `Share` | **Share of flow** | "Share" alone does not say share *of what*; the help already says "how big a share" |
| `Time`, `Age`, `Symbol`, `Side` | unchanged | already plain |

No width constraint applies: this is a `ui.table`, not the Desk's fixed
`minmax()` grid, so a longer label reflows rather than clipping.

## 3. Status line and the empty state

`status_text` is the page's own cold-vs-quiet distinction and already draws it
correctly. Only the words change.

| was | now |
|---|---|
| `Waiting for the options service…` | `No data yet — the options feed hasn't published this session.` |
| `No flow alerts yet today · {date}` | `Nothing unusual has traded yet today · {date}` |
| `{n} alerts today · {date}` | unchanged |
| busy: `Loading today's alerts…` | unchanged |

⚠ **The first is a deliberate COPY of `desk.WAITING_OPTIONS`, not an import.**
`desk.py` imports `pages.options.flow`, so importing back is a cycle. It is
guarded by a test asserting the two are equal — the same pattern, and the same
justification, `voice._ALL_CAUSES` already carries.

The second matters more than it looks: an empty table with "No flow alerts yet
today" reads as a page that has nothing, where "Nothing unusual has traded yet
today" reads as a market that has done nothing — which is the true and more
useful statement, and is already the wording the Desk's empty flow panel uses.

## 4. Filter labels

`Type` → **Alert type** (matching its column), and the symbol filter's `All`
option is unchanged. The kind filter's options are the renamed labels, which they
follow automatically since it is built from `_KIND_LABEL`.

## 5. `page_help.py`

The four bolded detector names in the `/options/flow` entry become the new ones,
each keeping its existing explanation. The **Share** reference becomes **Share of
flow**. Guarded by the present-and-absent pair the Desk pass established, since
`term in text` alone cannot catch a half-applied rename.

## 6. Out of scope

The raw payload keys (`crossover`, `uoa`, `gamma_flip`, `big_delta`,
`to_positive`, …) do not change. They are the `options_svc` contract, the
`config/flow_alerts.toml` section names and the `_TONE` map's keys; renaming them
would be a cross-tier migration with no user-visible benefit, and this page's
whole design already separates the key from the word. The tone colours are
unchanged.

## 7. Verification

Not browser-verified — a worktree resolves to prod and would bind `:8500`.
Affected suites and their pre-change baseline: `test_flow_page.py`,
`test_voice.py`, `test_desk.py`, `test_desk_stream.py` — **411 passed**.

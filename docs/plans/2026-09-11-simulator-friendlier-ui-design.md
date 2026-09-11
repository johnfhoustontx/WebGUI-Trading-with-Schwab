# Simulator — a friendlier screen (design)

**Date:** 2026-09-11
**Page:** `/options/simulator` (+ `options_svc` `sim_run` / `sim_replay`)
**Scope:** the two slices the user approved from the UI review — **A** (readouts,
no service change) and **B** (fix the two charts). Layout collapse of the leg
builder was offered separately and is **out of scope**.

## Problem

The Simulator is a leg builder over three charts, and every number a trader wants
is reachable only by hovering a chart. On a phone, hover does not exist. Measured
against the three tracked gallery captures (`deploy/site/assets/shots/image16-18`):

- No readout states the entry credit, max profit, max loss or breakevens.
- The Price and Days sliders name their input and never their result.
- The Days slider is a fixed 0–30 whatever the position's own expiry.
- The IV-shock column chart puts dollar values and Greeks on one axis, so four of
  five categories draw as flat lines; and its values are **per share** while the
  What-if tab beside it is in **position dollars**.
- Replay never shows the position's own profit or loss, its x-axis is a bar index,
  and **its scrubber cannot move past bar 1** (`scrub_slider.max = …` sets a Python
  attribute; NiceGUI keeps `max` in `_props`, so the browser never sees it).
- Nothing says when the legs stop matching the strategy named above them — the
  capture shows "Credit spread — put" over a short leg that outlives its long leg.
- Copy describes the mechanism ("Fetch snapshot", "Select a contract…" from the
  single-contract era).

## Decisions

### A. Readouts (page-side only)

**New pure module `webgui/pages/options/sim_view.py`** — the page holds widgets and
wiring, the arithmetic is unit-tested without a browser (the `rotation_view` /
`rrg_view` pattern). It imports nothing from `nicegui`.

1. **Position tiles** in the controls card's empty third column (so they add no
   height; `flex-wrap` drops them below on a narrow screen). Six, always six:
   **Entry credit/debit · Max profit · Max loss · Breakeven(s) · Delta · Theta per
   day.**
   - Entry = `−whatif_baseline` (the service's model value of the position at
     today's spot and time). Labelled *model price*, because it is Black-Scholes at
     the chain's IV, not the market mid the Calculator uses.
   - Max profit / loss / breakevens are the **expiration payoff**, solved exactly:
     piecewise-linear with corners only at the strikes, so `{0} ∪ strikes` plus the
     slope beyond the last strike decides it (the Calculator's `max_loss_estimate`
     method). The slope is the net call quantity: net long ⇒ **Unlimited** profit,
     net short ⇒ **Unlimited** loss.
   - **Legs on more than one expiry** ⇒ all three read an em-dash with the reason,
     never a single-date figure that settles a live back leg at intrinsic. The hint
     points at the Days slider's first-expiry snap, where the service prices the
     back leg properly.
   - Delta and Theta come from the IV-shock **base** row — the position's Greeks at
     today's volatility — which `sim_run` already returns.
   - Every tile degrades to an em-dash, never a zero.
2. **Slider readouts.** One line under the What-if sliders:
   *"At 386.00 on Sep 28: profit $8,240"*, green or red. The P/L is interpolated on
   the curve the chart draws, so the line and the chart cannot disagree.
3. **Days slider fitted to the position.** Max = the longest leg's fractional days
   to its 16:00 ET close (the service's settlement convention), rounded up to the
   step. Step 1 day, or 0.25 when the longest leg is within 3 days. Snap buttons:
   **Now · Halfway · Expiry** (labelled **First expiry** when legs differ). A
   restored `dt` above the new max is clamped. Under 1 day the label reads hours.
4. **Structure warnings** under the strategy picker:
   - a short leg expiring after every long leg — *uncovered after <date>*;
   - net short calls — *losses are unlimited if the price rises*.
   And an **Edited** chip when the legs no longer match the picked template's
   shape (type/side/qty multiset + its near/far expiry pattern; strike edits do not
   count, matching `strategies.summary_code`).
   **Set all legs to** expiry select beside the picker, reusing
   `leg_editor.apply_expiry` — reverses the 2026-06-24 decision to leave it off the
   Simulator, because mixed expiries by accident are more common than by intent.
5. **Copy.** "Fetch snapshot" → **Load chain** (the Calculator's word). One empty
   state decision (`empty_state_text`) for What-if and Replay: no chain · a leg with
   no strike · a leg whose strike the loaded chain does not list · still pricing.

### B. The two charts (crosses into Tier 2)

6. **Position units, stated by the service.** `sim_run`'s `ivshock` rows and
   `sim_replay`'s Greeks are multiplied by the contract multiplier, as `whatif_rows`
   already are, and each payload carries `units: "position"`. The page normalizes a
   payload **without** the marker (a cache written before the upgrade) by applying
   the ×100 itself — one pure function, so a stale cache cannot render in
   per-share units under dollar labels.
7. **IV shock becomes a table**, not a chart: rows Position value · Delta · Gamma ·
   Theta per day · Vega, columns *Today's volatility · ×mult · Change*, change
   tinted by whether it helps the position. A headline sentence states the
   result: *"If volatility rises 50%, this position loses $1,050."*
8. **Replay shows the position.** `sim_replay` adds `value` (position $ per bar)
   and `pnl` (value − first bar's value: *if opened at the start of the window*).
   The page adds a **Profit / loss** panel directly under Price, with the What-if
   green/red threshold fill. **Rho is dropped from the panels** (it stays in the
   payload) — seven panels in the height would crowd all of them, and rho is the
   Greek that matters least at these tenors.
   - **x-axis:** bar indices are hidden; each session boundary's plotLine carries
     its date as a label, and a per-bar **category** gives the tooltip header the
     real timestamp.
   - **Scrubber:** fixed via `.props(f"max={n-1}")`, defaults to the **latest**
     bar when a new trace arrives, and its readout states the bar's price, P/L and
     delta: *"Aug 24 13:45 — price 356.20, profit $1,230, delta 145"*.

## Non-goals

Probability of profit; an expected-move snap on the price slider (the snapshot meta
carries no ATM IV); collapsing the leg builder; the Calculator.

## Testing

- `sim_view` is pure — every function TDD'd with sample dicts, including the
  degrade paths (NaN, mixed expiries, unpriced, legacy per-share payloads).
- Figure builders keep their existing tests; the IV-shock figure is deleted with
  its tests (its subject is gone).
- Service: `sim_run` ivshock scaled + marked; `sim_replay` value/pnl + scaled
  Greeks, against the existing fakes.
- Render tests: the tiles, snaps and warnings mount; the scrubber's **`_props`
  max** follows the trace — the assertion that would have caught the bug.
- Browser: the Simulator on the local stack if it can be run; otherwise say so.

## Docs touched

`webgui/page_help.py` (the Simulator guide + subtab tooltips), `docs/webgui-routes.md`,
the User Guide section, `docs/CHANGELOG.md`.

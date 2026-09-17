# Calculator — Rate my trade (design)

**Date:** 2026-09-16 · **Status:** approved by the operator; built 2026-09-16 ·
**Plan:** [2026-09-16-calc-rate-my-trade-plan.md](2026-09-16-calc-rate-my-trade-plan.md)

## The ask

> "I need an additional button in the calculator that will 'rate my trade'. This,
> when clicked will pop up a window that will give a recommendation just like the
> trade detail and will also include the grade and possibly a recommendation of Buy
> or Pass."

Two decisions were put to the operator and answered:

| question | answer |
|---|---|
| How is the word decided? | **Three words — BUY / CAUTION / PASS**, from the grade and the Go/No-Go checklist, with the reasons listed |
| What does the window hold? | **Verdict banner + the full Trade detail panel** the scanner and Strategy Finder already show |

## What exists, and the gap

Measured on `main` (c42b049) before designing:

* **The grade.** `options-scanner/strategy_scoring.score_strategy(signal, view,
  atm_iv, em_1sd, market_state=None)` scores ONE normalized candidate in place:
  `composite_score` (0.7 × quality + 0.3 × fit), `grade` ∈ **Strong / Good /
  Marginal / Weak**, `grade_reason` (`"Fails: …"` when a hard gate fails),
  `factor_scores`. The gate bars come from `_TYPE_PROFILE[type]`; an unknown type
  falls to the DEBIT bars.
* **The checklist.** `webgui/pages/options/checks.py` + `checks_feed.py` — the
  Go/No-Go lines (paper book, earnings, vol rank, cost, expected move, wall, gamma,
  direction) and `checks.summary()` → state **pos** (Clear) / **warn** (N cautions)
  / **muted** (unchecked, partly checked) / **neg** (Blocked). It is PURE over a
  candidate row plus a Redis context the page reads off the loop.
* **The panel.** `detail.render()` returns a self-contained handle;
  `handle.update(signal, candidate=…, ctx=…)` paints the contract, economics,
  checklist, flags and the collapsed Greeks / IV / score-factor sections.
* **The gap.** Nothing turns a HAND-BUILT leg set into a scored, stamped
  candidate. Every existing grade comes out of a scan (`swing_scan`,
  `income_scan`), which BUILDS its own candidates. There is no `calc_rate`, and no
  Buy/Pass word anywhere in the app.

So the feature is one new service command that makes the Calculator's legs look
like a Strategy Finder row, plus one dialog. Nothing is re-derived: the payoff,
the PoP, the scorer, the stamps, the checklist and the panel are the ones the
Finder already runs.

## 1. The button and the window

* **RATE MY TRADE** beside EXPECTED MOVE in the legs footer. Disabled (with a
  tooltip saying why) until a chain has landed and `leg_editor.legs_ready(legs)`.
* Clicking opens a `ui.dialog` that says **Rating…** while the service works, then:
  * **Verdict banner** — the word (BUY green / CAUTION amber / PASS red), the grade
    and the 0-100 composite, and a **"Why"** list: the failed gates from
    `grade_reason`, the checklist's caution or block lines, and a note when the
    structure is not one the scorer knows ("custom structure — judged against the
    debit bars").
  * **The Trade detail panel**, mounted inside the dialog, fed the rated row via
    `strategy_table.detail_signal` and judged as a candidate to OPEN
    (`detail.checklist_candidate(row, allow_paper=True)`), so the paper-book line
    runs too.
* ⚠ **The dialog is built at the page's root, never from the leg table.** A
  `ui.dialog` deletes itself when the slot it was built in is cleared (`swing.py`'s
  `_open_paper` note), and the leg table's container is cleared on every edit.
  (Found while building: NiceGUI 3 then mounts the dialog in the client LAYOUT, so
  it is not a descendant of the page's column — the tests reach it through
  `root.client.elements`.)

## 2. The verdict rule (PURE, `webgui/pages/options/rate_trade.py`)

| grade \ checklist | Clear (`pos`) | cautions or not fully checked (`warn`, `muted`) | Blocked (`neg`) |
|---|---|---|---|
| **Strong / Good** | **BUY** | **CAUTION** | **PASS** |
| **Marginal** | **CAUTION** | **PASS** | **PASS** |
| **Weak** (or no grade) | **PASS** | **PASS** | **PASS** |

* **A check that could not run is a caution, never a clear.** Otherwise a trade
  would earn BUY from missing data — the "never print a zero you did not read"
  rule, applied to a verdict.
* **No grade is PASS**, not CAUTION: a rating the service could not produce is not
  a mild endorsement.
* ⚠ **These thresholds are not fitted to outcomes.** They compose the grade and the
  checklist the app already trusts; the calibration bucket for hand-built trades
  does not exist. The page and the manuals say so.
* For a CREDIT trade "BUY" means *take the trade*. The word is the operator's; the
  help text says it once rather than renaming it.

## 3. Service: `calc_rate` on `options_svc`

`args = {request_id, symbol, structure, legs}` where `legs` are the Calculator's
normalized legs (`option_type`, `side`, `strike`, `expiry`, `qty`, `premium`) and
`structure` is the page's shape match (`sim_view.template_for(legs)`, or
`"CUSTOM"`).

New module **`services/options_svc/rate_trade.py`** (pure where it can be):

1. **Chain.** Read `cache:options:calc_chain` — the chain the Calculator already
   loaded, thinned to ten fields per contract, which is every field a leg needs.
   **No chain fetch.** Refused with a reason when it is missing or belongs to
   another symbol.
2. **Legs → Finder legs.** `option_type` → `kind`, `expiry` → `expiration`; the
   quote, Greeks, IV, OI and volume come from `strategy_scanner.extract_options`
   over that chain. **`mark` is the page's `premium`** when it has one — the
   operator is rating the trade at the price they entered — else the chain mark. A
   share leg is `_stock_leg(spot)` with the page's price. A leg whose contract the
   chain does not carry refuses the rating by name.
   * **Quantity is reduced to the structure's ratio** (every qty divided by the
     smallest), so a 10-lot and a 1-lot rate the same. R:R and PoP are scale-free;
     `capital` and the checklist's book line are not, and the book line judges one
     structure, as the Finder does.
3. **Structure → scorer type.** A table (`CALC_TO_SCORER`) maps each Calculator
   template code to the Finder's `(type, family, label, bias)` — `NAKED_PUT` →
   `SHORT_PUT`, `VERT_CALL_DEBIT` → `BULL_CALL`, the rest by name (`IC` stays `IC`,
   the type the Finder's adapted iron condor carries). `CUSTOM` keeps type `CUSTOM` (the DEBIT bars), family `CUSTOM`, bias from
   the sign of net delta, and sets `structure_known = False`.
4. **Row.** `strategy_scanner._assemble(...)` — the same payoff metrics, PoP and
   net Greeks the Finder's builders produce.
5. **Market view.** `se.fetch_price_history` (one call) → `calc_technicals`;
   `run_iv_analysis(client, symbol, price=spot, hist=hist, chain=chain)` (zero
   chain calls when the loaded chain covers ~20-45 DTE, else 1-2); `atm_iv` and the
   daily move derived exactly as `swing_scan` does (factored into a shared helper
   rather than copied); market state from `cache:sentiment:composite` as the swing
   handler reads it.
6. **Score.** `score_all([row], view, atm_iv, em_1sd, market_state, daily_move=dem)`
   — **without** `_passes_swing_cut` and **without** the volatility gate's drop: a
   weak or cheap-premium trade must still come back graded. The vol gate's verdict
   is carried instead as `vol_gate_blocks` so the banner can say it.
7. **Stamps.** `iv_rank`, `daily_em`, `structure_known`, `earnings_status` via `scan_earnings`, then
   `compute.stamp_candidate(row, trade_type="SWING", …)` — the Finder's own stamps.
8. **Publish** `cache:options:calc_rating` = `{request_id, symbol, legs, row,
   error}`; `error` is a reader's sentence, `row` None when it is set.

**Cost:** ~1-4 Schwab calls per click (history, possibly 1-2 IV chains, earnings),
and nothing unless clicked. **Replay:** added to `_REPLAY_GUARDED` — a replayed
rating is harmless but costs calls for nobody.

## 4. Webgui wiring

* `calculator.py` enqueues `calc_rate` with a fresh `request_id` and opens the
  dialog; a 1 s version poll of `options:calc_rating` paints only the payload whose
  `request_id` matches the open dialog. A second click while one is pending does
  nothing.
* **Timeout.** No answer in 30 s (`overlay.LOAD_TIMEOUT_SEC`) → the dialog says
  the rating service did not answer, instead of spinning.
* The checklist context is read off the loop by the panel itself (no `ctx`
  passed), exactly as on the Strategy Finder.

## 5. Not in scope

* The Simulator (the shared position means a Calculator rating already covers it).
* An AI narrative — the verdict is rule-based and explains itself from its inputs.
* Capturing ratings for calibration.

## 6. Tests

* PURE: the verdict table (all 12 cells + no grade + unknown state), the reasons
  list, the calc→scorer table (every `STRATEGY_TEMPLATES` code mapped; every mapped
  type a `_TYPE_PROFILE` key), leg conversion (premium beats chain mark, share leg,
  missing contract, qty ratio), request matching.
* Service: `calc_rate` over a fake bus + a fixture chain → a graded, stamped row;
  refusals (no chain, wrong symbol, unknown contract); a Weak trade still returned.
* Page: the button's enabled state, the enqueue, the dialog paint on a matching
  payload and not on a stale one, the timeout line.
* Cross-tier mirror: `shared/tests/test_cross_tier_mirrors.py` pins the service's
  map against the webgui's template codes (AST, no import).

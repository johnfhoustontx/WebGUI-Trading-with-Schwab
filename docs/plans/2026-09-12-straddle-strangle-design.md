# Straddle and strangle templates (D1)

*Design, 2026-09-12. Gap assessment item **D1**.*

## What the assessment asked for

> **D1. Straddle and strangle templates:** long and short, straddle and strangle,
> analysis only.

## Why this is four lines of data and not a feature

`webgui/pages/options/strategies.py` is the shared, PURE strategy/leg model both
the Calculator and the Simulator mount — `STRATEGY_TEMPLATES` maps a code to a
list of `(option_type, side, qty, strike_role, expiry_role)` specs, and
`build_default_legs` snaps those roles onto a real strike ladder. Nineteen
structures already live there, including both butterflies, both condors and the
calendars and diagonals.

So the templates are data:

| code | legs |
|---|---|
| `LONG_STRADDLE` | long call ATM + long put ATM |
| `SHORT_STRADDLE` | short call ATM + short put ATM |
| `LONG_STRANGLE` | long call `otm_up_1` + long put `otm_dn_1` |
| `SHORT_STRANGLE` | short call `otm_up_1` + short put `otm_dn_1` |

A **straddle** is both rights at one strike; a **strangle** straddles spot with
two, so it costs less and needs a bigger move. Both are single-expiry — the
two-expiry version is a calendar, which already exists separately.

They also need the four UI surfaces every other code has: a `STRATEGY_GROUPS`
entry (the dropdown), a `STRATEGY_MENU` entry (the cascading picker), and a
`_STRATEGY_FACTS` row carrying the cash-flow direction, the descriptor tags and
the one-line thesis. The leg-count chip is **derived** from the template, so it
cannot disagree with the legs actually built.

## ⚠ "Analysis only" is a rule, so it is tested rather than intended

The assessment's own **Don't** list names these structures explicitly:

> *Don't open undefined-risk structures in paper or the driver. That means naked
> calls, short straddles and short strangles.*

A template appearing in an allowlist is exactly how such a rule is lost, so the
guarantees are pinned in **two** places:

- **Tier 1** (`webgui/tests/test_straddle_strangle.py`): both short structures
  carry the `UNDEFINED RISK` tag — the same word `NAKED_CALL` wears, and what
  tells a reader on the Calculator that there is no wing behind the position —
  while the long ones do not, because a long straddle's loss is the premium.
- **Engine side** (`options-scanner/tests/test_straddle_analysis_only.py`): none
  of the four is in `shared.driver_policy.ALLOWED`, none is classified tradeable
  by `shared.structures`, none has a leg layout in
  `signal_repricer._LEG_LAYOUT` (so the repricer refuses to mark one rather than
  guessing), none has a `[structures.*]` exit-rule table, and
  `strategy_scanner`'s source contains none of the codes.

⚠ **Those two halves cannot live in one file**, and the reason is the
architecture rather than tidiness: the webgui suite has no `options-scanner` on
`sys.path`, because **Tier 1 imports no engines**. That is what makes "analysis
only" true rather than merely asserted — a page literally cannot reach the code
that opens a position.

## One thing worth recording about the Calculator

`summary_code` returns `"CUSTOM"` for anything outside `_ANALYTIC_CODES`, which
routes the payoff summary through the **numeric** path instead of a closed-form
formula. A straddle belongs there: its payoff is a V, unbounded on at least one
side, so there is no exact max-profit to report. Quietly adding it to the analytic
set would produce a confidently wrong summary card — the test asserts both the
`"CUSTOM"` result and, for non-vacuity, that the shortcut still fires for `PCS`.

# Exit rules for the Income Window's single-leg structures (gap assessment B1)

**Date:** 2026-09-11
**Closes:** gap assessment recommendation **B1**; playbook rules **X1**, **X3**, **X5**
**Follows:** A2/A3 (`2c3dea5`), which made these positions priceable and gave them
an *interim* exit policy — the profit target only — with the loss-side rules held
back "pending B1". This is B1. It settles that policy and writes it down as config.

---

## 1. The question

The paper book can hold two single-leg income structures: a cash-secured put
(`SHORT_PUT`, spelled `NAKED_PUT` on the Calculator/rescue side) and a covered
call (`COVERED_CALL`), both opened by hand from the Income Window
(`compute.open_income_position`). Until 2026-09-11 nothing could mark them, so
they had no exit rules at all. A2 gave them a mark and the +50% target.

The open question was the **loss side**. The credit-spread rule set — a 2×-credit
money stop, a `cut_dte` time stop, a delta stop — is not merely unproven for
these two structures. It is *inverted*:

| rule | on a credit spread | on these two |
|---|---|---|
| 2× credit money stop | the spread is going against you; cap the loss | on a **covered call**, a 2×-credit loss on the option IS the stock rallying. The shares hold that gain; the position is fine. Option Alpha's covered-call study found stops produced more losing trades, not fewer [OA-CC]. |
| delta stop | the short is being breached; get out | on a **cash-secured put**, a rising short delta means assignment is becoming likely — which is the wheel's *plan*, and the reason `equity_lots` and `_assign_shares` exist. |
| `cut_dte` time stop | close before gamma dominates | same: it fires precisely when the wheel would take the shares. |

So the answer is **no loss-side stop for either structure**, and it is a sourced
answer rather than a default.

⚠ A covered call's `unrealized_pnl` is **the option leg only** — nothing in this
app prices a bare share, which is why `/options/shares` dashes Mark and
Unrealized. Any money stop read off that number is measuring half the position.
That alone makes the money stop unsafe here, independent of the research.

## 2. What replaces the loss side: manage at 21 DTE

Removing the loss-side rules leaves a position that rides to expiry unless the
target is hit. That is the wheel, and for a cash-secured put it is deliberate.
But it also means a **profitable** position grinds out its last few percent
through the highest-gamma stretch of its life for no good reason.

**X5 is the best-supported management rule in the playbook** and is exactly the
fix: close or roll premium-selling trades at 21 DTE. OptionsPlay closes there
because gamma rises [OPR]; tastylive found managing at 21 days improved all three
strategies it tested [MM-ANAT].

Implemented as: **at or below `manage_dte`, a position in profit is closed; one
underwater is held.**

- Profitable → close. The remaining theta is not worth the gamma.
- Underwater → **hold**, because closing here would be the loss-side stop this
  design just removed, wearing a different name. The put takes assignment; the
  call's shares cover it.

This is the only *new* behaviour in B1, and it is one-directional by
construction: it can end a winner early, never realise a loser.

## 3. What is NOT changing, and why

**The profit target stays 0.50.** TradingBlock takes ~90% on a short put and
~95% on a covered call — but it pairs that with **rolling straight into the next
~45-DTE cycle**, and this app cannot roll a single leg automatically (see §4).
Adopting their target without their roll means holding ~20 extra days for the
last 40 points of a small credit, with the capital still tied up: strictly worse
capital efficiency than banking at 50% and re-selling. The per-structure table
carries `tp_frac`, so 0.90/0.95 is a one-line edit if the evidence changes.

**Spreads get no `manage_dte`.** X5 is written about premium selling generally,
and applying it to PCS/CCS/IC would change how every position in the app exits —
a large, unmeasured behaviour change riding along inside B1. Spreads already have
a working time rule (`cut_dte` = 2) plus the delta stops. A 21-DTE close for
spreads is a separate change with its own before/after measurement.

**The Rescue board's escalation bands stay global.** A cash-secured put 3× its
credit underwater should still light up the at-risk board even though nothing
will act on it automatically. Flagging is not acting, and the operator wants to
see it.

## 4. Rolling (X3), and why it is advisory

B1's rule table names rolling as the tested cash-secured put's repair (down, out,
or down-and-out for a credit) and the covered call's (up-and-out to keep the
shares). The paper Rescue board offered **none** of it: `build_roll_down`,
`build_roll_out` and `build_roll_down_out` all early-return for anything outside
PCS/CCS/IC, so a single-leg position's whole menu was "Close now".

The builders for exactly these rolls already exist — `rescue.single_candidates`,
written for ad-hoc naked shorts — and were simply never wired to the paper book.
B1 routes single-leg positions there and teaches it the two income spellings.

They stay **advisory** (`apply_kind: "advisory"`, the `build_inverted`
precedent). `paper_adjust.apply_roll` partitions `est_fill_legs` into a closing
*pair* and a reopening *pair* and books a spread reopen; making it single-leg-safe
is a change to the money path and belongs in its own commit. An advisory
candidate tells the operator what to do and costs nothing if it is wrong.

⚠ `apply_roll` also carried a **fourth copy** of the put-side membership test
(`strategy in ("PCS", "IC")`), which resolves a short put to `right = "CALL"`.
It was unreachable while single legs had no roll candidate; B1 fixes it as part
of the taxonomy below rather than leaving it armed behind a new feature.

## 5. Where the rules live

Two homes, matching the repo's own test for a shared module — *the value was
duplicated across modules that cannot import each other*:

**`config/trade_mgmt.toml` → `[structures.<STRATEGY>]`**, read through
`shared.trade_mgmt.structure_rules(strategy)`. Each table overlays `[stops]`, so
a structure names only what differs and every other rule is inherited rather
than restated. An unlisted structure resolves to `[stops]` unchanged with the
loss-side rules on — which is what every spread already does, so the table is
additive by construction.

`signal_recommender.recommend` now resolves its thresholds per position through
that accessor, replacing the hardcoded `PROFIT_TARGET_ONLY_STRATEGIES` tuple A2
shipped as the interim.

**`shared/structures.py`** — the structure *taxonomy*, which is not a stop rule
and does not belong in a rules file. It is one home for the sets that had spread
to **seven** copies across three tiers:

| site | named | drove |
|---|---|---|
| `rescue.PUT_SIDE_STRATEGIES` | put-side | proximity scoring, roll side |
| `paper_adjust.py:306` | put-side | the roll's option right — **and was wrong** |
| `signal_recommender._recoverable` | put-side | recovery cushion side |
| `rescue.SINGLE_LEG_STRATEGIES` | single-leg | commission leg count |
| `signal_recommender.PROFIT_TARGET_ONLY_STRATEGIES` | single-leg | the rule set |
| `signal_repricer.SHORT_PUT_STRATEGIES` + `COVERED_CALL_STRATEGY` | single-leg | pricing map |
| `paper_engine.SHORT_PUT_STRATEGIES` + `COVERED_CALL_STRATEGIES` | single-leg | assignment / call-away |

`webgui/pages/options/shares.COVERED_CALL_STRATEGIES` is the eighth and is left
alone: Tier 1 takes no `services.*` import, and widening the Tier-1 allow-list is
not part of B1. Its existing comment already records that it is a mirror.

## 6. Tests that discriminate

The trap this repo has hit repeatedly: a test asserting the new config equals the
old literal proves nothing, because they were equal before the change too. Each
piece therefore ships with a test that **moves the source** and requires the
consumer to follow — `monkeypatch` the accessor, `importlib.reload` the consumer
where it holds a module constant.

For the rules themselves, every income test is paired against a **credit spread
on identical numbers**, so the assertion is that the two structures diverge, not
merely that one of them returned something.

## 7. Sources

`[OA-CC]`, `[OPR]`, `[MM-ANAT]`, `[TB]` are as cited in
[the playbook](2026-09-11-options-strategy-playbook.md) §1.4 and its Sources
table; the per-structure rule table in
[the gap assessment](2026-09-11-options-strategy-gap-assessment.md) §B1 is the
brief this design implements.

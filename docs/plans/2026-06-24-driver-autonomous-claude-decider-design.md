# Claude Driver — Autonomous strategy-agnostic decision layer (design)

**Date:** 2026-06-24
**Branch:** `Using_Highcharts`
**Status:** Approved (brainstorm) — pending implementation plan

## 1. Premise & goal

The Claude Driver was *intended* to be **strategy-agnostic**: Claude makes the
trade decisions within hard risk guidelines, pursuing a daily profit objective.
What actually got built (`claude-driver/trade_selector.py`) is a **hardcoded
decision tree** — three fixed buckets (SPX 0-DTE IC/PCS/CCS, QQQ/SPY equity, MES
futures), fixed structures, fixed thresholds. That rigidity is *why the user only
sees equity trades*: when the options branch and futures branch hit their
hardcoded gates (OptionsAnalytics `:8200` down, VIX > 20, ML servers down), the
tree falls through to the one branch that always passes on a bare Schwab quote.

**This design replaces the rule tree with a decision layer where Claude actually
chooses**, within code-enforced guardrails, targeting **net $500/day**.

### Honest framing (load-bearing, not a disclaimer)

No decision-maker — Claude included — can guarantee $500 every day within prudent
risk; the market offers no daily withdrawal. The system **targets** $500/day: it
presses size when edge exists, **stands down** when it doesn't, **banks** the day
once the target is reached (so it can't give gains back), and is **hard-capped**
so a tail day can't exceed the existing loss limits. The point of this build is to
generate an **auditable paper track record** before anyone discusses live capital.

## 2. Decisions locked in during brainstorming

| Question | Decision |
|----------|----------|
| Real options-on-futures (`/ES`,`/MES` FOP)? | **Shelved.** Schwab's API serves no FOP chains and places only EQUITY/OPTION orders (futures are quote-only). Not adding a second broker now. |
| What do the option spreads trade? | **Multi-symbol scanner spreads** — the already-scored PCS/CCS/IC signals in `cache:options:scan`. |
| Autonomy level | **B — Claude decides → auto-executes in paper, no human approval gate.** Live (level C) is explicitly out of scope. |
| Cadence | Morning decision (09:28 ET) **+ 30-min intraday checkpoints**, halting on target/loss. |
| Exits | **Mechanical** for v1 (reuse the options paper engine's auto-manage). Claude owns entries + stand-down; Claude-managed exits = v2. |
| Paper accounting | **Reuse the options paper engine + `paper_account_db`** via the existing `cmd:options` `paper_create` path. |

## 3. Architecture

```
driver_svc scheduler (09:28 ET morning + every 30 min during RTH)
   │  reads cache:driver:control  ── master switch + halt flag (kill-switch)
   ▼
compute.run_cycle(checkpoint)
   ├─ build_packet()      gather decision context (§5 input)
   ├─ decider.decide()    Claude (claude-opus-4-8) → schema-validated trade set
   ├─ guardrails.clamp()  CODE validates / resizes / rejects every proposal (§6)
   ├─ execute survivors   enqueue cmd:options paper_create {signal, qty}  (§7)
   ├─ audit + publish      cache:driver:autonomous (monitor state + decision log)
   └─ HALT conditions      $500 banked │ daily loss cap │ VIX>25 │ master-off
                                   │ subscribe / version-poll
                                   ▼
/driver page  →  MONITOR + OVERRIDE  (decision log, open positions, day-P&L vs
                 $500, STOP button)  — repurposed from the APPROVE/SKIP queue
```

**Key property — Claude selects, it does not invent.** v1 Claude chooses, sizes,
and combines trades **from the scored scanner menu** (or stands down). It cannot
hallucinate a strike that doesn't exist or that the paper engine can't price —
every executed trade is a scanner signal the system already validated. This is a
core guardrail, not just a convenience.

**Fallback.** If the Anthropic call fails or returns nothing usable, the cycle
degrades to **stand-down** (no trade) — never to "trade blind." The legacy
`trade_selector` rule tree is retained as an optional secondary fallback behind a
flag, but the safe default is to do nothing on decider failure.

## 4. Strategy-agnostic, within an allowlist

"Any trade within the guidelines" for **v1** = **defined-risk option credit
spreads** (PCS / CCS / IC) sourced from the scanner. Equities are Schwab-tradeable
but have **no paper-accounting path today** (the paper engine is spread-oriented),
so they are **deferred to v2**. Claude still has real latitude: which of the N
scored spreads (any scanned symbol), how many, what size, whether to combine into
a basket, or to pass entirely. The "strategy-agnostic" win is that Claude is **no
longer bound** to "SPX 0-DTE IC only on a Grade-A morning."

## 5. The decision contract

### Input packet (`compute.build_packet`)
- **Market regime:** VIX / VIX1D / SPX spot; the day-grade signal inputs.
- **Directional context:** ML signals (when servers up) + GEX/charm snapshot.
- **The menu:** top-N scored PCS/CCS/IC from `cache:options:scan` — each carries
  `symbol, expiry, structure, strikes, credit, max_loss, pop, composite_score`.
- **Account state:** paper buying power / cash, open **driver** positions w/ live
  P&L (from `cache:options:paper_account`, filtered to `source="driver"` — §7).
- **Objective state:** today's realized+unrealized **driver** P&L, **remaining gap
  to $500**, remaining daily-risk budget.
- **The guardrail limits themselves** — so Claude proposes *inside* the envelope
  (limits are still re-enforced in code afterward; the model is never trusted).

### Output (Anthropic tool-use schema, validated; invalid → stand-down)
```jsonc
{
  "stand_down": false,              // true = make no trades this checkpoint
  "day_thesis": "…",                // one-line rationale for the whole decision
  "confidence": 0.0,                // 0..1
  "trades": [
    {
      "signal_id": "…",             // MUST reference a packet menu signal
      "structure": "PCS|CCS|IC",
      "quantity": 1,                // spreads; code re-clamps to risk budget
      "rationale": "…"
    }
  ]
}
```

## 6. Guardrails — code-enforced (`driver_svc/guardrails.py`, pure, unit-tested)

Claude proposes; **code is the final authority**. Every proposal is validated,
resized, or rejected:

- **Master switch + kill-switch.** `cache:driver:control` `{enabled: bool,
  halted: bool, reason}`. Default **disabled**. The scheduler runs the loop only
  when `enabled and not halted`. The `/driver` **STOP** button sets `halted`.
- **Stays paper.** `config.PAPER_TRADE` remains `True`; this service never flips
  it. Level C / live is a separate, deliberate future effort.
- **Per-trade max risk** ≤ a configured ceiling; **daily risk budget** = Σ open
  driver max-loss ≤ a configured cap. Quantity is **re-sized** to fit, not trusted.
- **Allowlist:** defined-risk spreads only (PCS/CCS/IC). No naked/undefined risk,
  no instruments off the menu, no equities (v1).
- **Auto-halt** when: today's driver P&L ≥ **+$500** (bank the day) · ≤ the
  existing `RISK_LIMITS.daily_max_loss` (−$250) · `VIX > VIX_MAX_TRADE` (25) ·
  weekly/monthly caps breached.
- **Max trades/day** and **max concurrent driver positions**.
- **Signal freshness** — reject a menu signal whose scan is stale (mirrors the
  rescue stale-price guard idea).
- **Full audit** — packet + raw model response + clamps-applied are logged to the
  decision log for every checkpoint (reviewable on `/driver`).

All threshold numbers live in one place (a new `driver_svc` config block or
`cache:driver:control` for the runtime-tunable ones), proposed as defaults in the
implementation plan and tuned on paper.

## 7. Paper execution & accounting

- **Execute** a surviving trade by enqueuing the existing command:
  `bus.enqueue_command("cmd:options", {type:"paper_create", args:{signal, qty}})`,
  where `signal` is the scanner signal dict (already on the menu) tagged with
  `source="driver"`, and `qty` is the guardrail-clamped size. No new execution
  engine — `options_svc` `compute.create_paper_trade` → `paper_trader` does the
  pricing/persist exactly as the manual "Send to Paper trade" flow does.
- **Attribution.** Tag driver-created trades `source="driver"` so the driver can
  isolate **its** contribution to the day's P&L (the paper account is shared with
  manual trades, rescue, and scanner auto-manage). The "$500 gap" is computed over
  driver-tagged positions only.
- **Exits (v1).** None added — the options service's existing 5-min paper
  **auto-manage** (50% profit target / stop / DTE-EOD close) handles exits for all
  paper positions, driver-tagged included. Claude does not manage exits in v1.

## 8. Cadence & halt (`driver_svc/scheduler.py`)

Extend the existing 30 s poll loop:
- **Morning** decision at 09:28 ET (existing `morning_due`).
- **Intraday checkpoints** every **30 min** during RTH (new `checkpoint_due`),
  each re-running `run_cycle` with fresh packet state.
- Each tick first reads `cache:driver:control`; if disabled/halted, the loop
  publishes state and does nothing else. Halt latches for the day once banked or
  a loss cap trips (re-armed next trading day).
- The scheduler **never** touches `config.PAPER_TRADE`.

## 9. `/driver` page — repurposed to monitor + override

Level B has no approval gate, so the page flips from a queue to a dashboard:
- **Master toggle** (Enable/Disable autonomous) + big **STOP** kill-switch.
- **Today:** day P&L vs the $500 target (progress), halt state + reason.
- **Open driver positions** (symbol/structure/credit/max-loss/live P&L).
- **Decision log:** each checkpoint's thesis, chosen trades, and what the
  guardrails clamped/rejected — the audit trail.
- Reads `cache:driver:autonomous`; writes `cache:driver:control` via commands.
  The legacy APPROVE/SKIP UI and the **Performance** view are kept (perf view
  unchanged; the morning-approval path remains available behind the flag).

## 10. Components — new vs. changed

**New**
- `services/driver_svc/decider.py` — `build_packet` helpers + the Anthropic call
  (`claude-opus-4-8`, configurable) + schema parse/validate. Defensive →
  stand-down on any failure.
- `services/driver_svc/guardrails.py` — **pure** clamp/validate/halt logic
  (TDD'd: the safety core).
- `shared/contracts/driver.py` → `AutonomousState` (monitor view) +
  `DriverControl` (enable/halt) contracts; cache keys `cache:driver:autonomous`,
  `cache:driver:control`.
- `anthropic` SDK dependency + API key wiring (first LLM call inside a service).

**Changed**
- `driver_svc/compute.py` — add `build_packet`, `run_cycle(checkpoint)`; the
  morning path routes through `decider → guardrails` instead of
  `trade_selector.select_trades` (legacy retained behind a flag).
- `driver_svc/handlers.py` — command handlers for `cycle`, `enable`, `disable`,
  `stop`; publish `cache:driver:autonomous`.
- `driver_svc/scheduler.py` — intraday `checkpoint_due` + control-key gating +
  halt latching.
- `webgui/pages/driver.py` — monitor/override UI (pure builders unit-tested).

**Unchanged:** the proxy, the options paper engine/`paper_account_db`, the scanner.

## 11. Testing

- **`guardrails.py`** — exhaustive pure unit tests: per-trade clamp, daily-budget
  resize, allowlist reject, each halt condition, off-menu/stale rejection,
  bank-the-day latch. This is where correctness must be proven.
- **`decider.py`** — schema parse/validate with fixture model responses
  (good / malformed / empty → stand-down); the Anthropic client is mocked.
- **`scheduler.py`** — pure `checkpoint_due` / halt-latch tests (no clock).
- **webgui** — pure monitor builders (progress, decision-log rows, control state).
- **End-to-end (Redis-driven, per the house pattern):** enqueue a `cycle` command
  with a seeded `cache:options:scan` + control-enabled, assert a `paper_create`
  lands on `cmd:options` and `cache:driver:autonomous` reflects it.
- Run service suites **per folder** (the documented `config`/`src` collision rule).

## 12. Scope — v1 vs. deferred

**v1 (this build):** autonomous *paper* selection/sizing over the scored scanner
spread menu; 30-min cadence; code guardrails + kill-switch; mechanical exits;
monitor page; full audit log.

**Deferred (v2+):** equities in the executable universe (needs a paper-equity
mechanism); Claude-managed exits / rolls; agentic tool-use loop (Claude queries
chains live vs. a single-shot schema call); parameterized custom spreads off-menu;
**level C / live** (a separate, deliberate effort with its own design).

## 13. Risks & mitigations

| Risk | Mitigation |
|------|-----------|
| LLM proposes reckless size | Code clamps size to risk budget; model never sizes its own risk. |
| LLM hallucinates a trade | v1 can only pick from the validated scanner menu; off-menu/stale → rejected. |
| Runaway loop / chasing losses | Hard daily/weekly/monthly halt latches + bank-at-$500 + master STOP. |
| Anthropic outage | Cycle degrades to stand-down (never blind trading). |
| Commingled paper P&L | `source="driver"` tagging isolates driver attribution. |
| "$500/day" misread as guaranteed | Explicit target-not-quota framing; stand-down is a valid decision. |
| API key handling | Treated like the other secrets (gitignored, never committed). |

## 14. Defaults adopted (overridable in the plan)

Existing `config.py` guidelines (the `$10k` base + `RISK_LIMITS`); autonomy **B**;
`claude-opus-4-8`; 30-min checkpoints; mechanical exits; master switch **off** by
default (the user explicitly enables). The $500/$10k tension is acknowledged — the
guardrails make it safe (it simply stands down more often), and capital/limits are
config-tunable.

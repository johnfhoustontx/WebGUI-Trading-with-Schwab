# Driver market-context block — Design

**Date:** 2026-07-08
**Branch:** `Using_Highcharts`
**Status:** Approved (brainstorming) → ready for implementation plan

## Goal

Give the autonomous Driver's Claude **decider** a compact **`market_read`** in its
decision packet so it reasons over **gamma structure, market breadth, and sentiment**
when it selects + sizes credit spreads. Today the model sees only `vix` (a hard-halt
input) plus the five-state `market_state` label *string*; it is blind to the gamma
flip/walls that literally define where a credit spread's short strikes are safe, to
breadth, and to the 0–10 sentiment magnitude.

This closes a loop already latent in the app: a Claude briefing (`gamma_analyze`)
writes a structured per-index gamma thesis **4×/day**, but the Driver's Claude trades
blind to it. The `market_read` feeds that thesis (plus breadth + sentiment) into the
Driver.

## Scope (what this unit is — and is NOT)

**This unit = the ① *context* lever only.** It is purely additive information the model
reasons over. It changes **no** hard rule:

- **NOT** a new guardrail / hard gate. The wall-aware rejection ("don't open a credit
  spread whose short strike sits on the wrong side of the protective wall") and the
  breadth halt are the ③ *gate* lever — a **deferred follow-up** that must be
  backtested (via the `validate_market_state.py`-style harness) before it can block a
  trade. `guardrails.py` is **untouched** in this unit.
- **NOT** a new schedule. It rides the existing 30-min autonomous checkpoint
  (`scheduler.checkpoint_due`) — the `market_read` is assembled inside the same
  `run_autonomous_cycle` that already runs there.
- **NOT** a change to how the menu is built, scored, or sized. The scanner menu
  (`cache:options:scan` → `_menu_item`) is unchanged.

**Design principle (matches the codebase):** the three levers, in ascending risk —
① context (safe, additive, no backtest needed) → ② ranking tilt (bounded, validate) →
③ hard gate (code-authoritative, validate hardest). We start at ①. Promote to ③ later,
only after it proves out.

## Decisions locked in brainstorming

1. **Field set = "focused market read"** (not "everything", not "gamma-only"):
   per-index gamma flip + call/put walls + what-if, the briefing's bias + headline +
   regime, market breadth + risk-on/off, and the sentiment 0–10 score + bias.
2. **Sourcing = "briefing + live spot":** the rich per-index gamma read comes from the
   freshest `gamma_analyze` briefing (pre-computed, code-authoritative EM); a **live
   per-index spot** is added to `fetch_market_context` so "distance to flip/wall" is
   computed against a fresh spot. Driver stays **Redis + one quote call** (3-tier clean).
3. **Prompt framing = "sharpen selection, keep pressing"** — the guidance must NOT
   undercut the Very Aggressive $500/day mandate (see §5).
4. **Observability = yes** — carry a one-line `market_read` summary onto each
   `/driver` decision-log row (§6).

## Architecture

The Driver remains a Tier-1/Tier-2 consumer that reads Redis caches + makes one proxy
quote call. All new reads are cache reads (or the extended quote); no engine imports,
no DB access, no cross-app module risk.

```
run_autonomous_cycle (handlers.py)
  ├─ read cache:driver:control                     (existing gate)
  ├─ read cache:options:scan                        (existing menu)
  ├─ read cache:options:driver_paper_account        (existing day P&L / positions)
  ├─ _read_market_state(bus)  → derived.trend       (existing five-state)
  │     └─ EXTEND: also pull live.composite.{total_score,bias}   (sentiment magnitude)
  ├─ NEW _read_latest_briefing(bus) → newest gamma_analyze `analysis` (today only)
  ├─ NEW _read_dashboard(bus)       → cache:market:dashboard
  └─ compute.run_cycle(...)
        └─ compute.build_packet(...)
              ├─ fetch_market_context()   → EXTEND: {vix,spx_spot,vix1d, spy_spot,qqq_spot}
              ├─ existing packet dict + market_state line
              └─ NEW packet["market_read"] = _market_read(briefing, dashboard, sentiment, spots)
                        (pure, testable; omitted entirely when all sources absent)
   → decider.decide(packet)  (packet serialized to the model as JSON)
```

### Data sources (each defensive → omit that slice on missing/empty)

| Source | Cache key(s) | Fields pulled |
|---|---|---|
| **Latest gamma briefing** | `cache:options:gamma_analyze_{premarket,open,midday,close}` (`CACHE_GAMMA_ANALYZE_SCHED`) → choose the newest by `generated_at` | top: `regime`, `bias` (−100..+100), `bias_label`, `headline`; per-index `indices[]`: `symbol`, `gamma_flip`, `call_wall`, `put_wall`, `max_pain`, `expected_move`, `pc_ratio`, `what_if{rally,selloff,chop}` |
| **Live per-index spot** | extend `compute.fetch_market_context` — add `SPY,QQQ` to the existing `$VIX,$SPX,$VIX1D` proxy `/quotes` call | fresh `spot` per index (flip/walls are sticky intraday; spot is not) |
| **Market dashboard** | `cache:market:dashboard` → `categories[].tiles[]` | the `$ADVN-$DECN` breadth-spread tile `last`, plus a derived `risk` label aggregated from tile `color_state`s |
| **Sentiment composite** | `cache:sentiment:composite` → `live.composite` (reuse the read `_read_market_state` already makes) | `total_score` (0–10), `bias` |

### Freshness / staleness

- The briefing is refreshed 4×/day, so mid-afternoon it can be a couple hours old;
  **flip/walls barely move intraday** (strikes are sticky), so pairing the briefing's
  flip/walls with a **live spot** keeps the actionable distances fresh.
- **Only a briefing whose `generated_at` is today (CT) is used** for the gamma slice.
  A prior-session briefing → drop the gamma lines (yesterday's walls mislead), but
  breadth / sentiment / live-spot still populate.
- The `market_read.as_of` field stamps the briefing slot + CT time so the model can
  discount staleness itself.

## Packet shape

The decider serializes the whole packet to the model as JSON (it strips only
`menu_by_id` in `run_cycle`), so a **structured dict** is both clean for the model and
directly unit-testable:

```python
packet["market_read"] = {
    "as_of": "midday 12:30 CT",
    "regime": "negative gamma below flip",
    "bias": -35, "bias_label": "bearish",
    "headline": "Dealers short gamma; downside air pockets below 5900.",
    "breadth_spread": -620, "risk": "risk_off",
    "sentiment_score": 4.1, "sentiment_bias": "bearish",
    "indices": [
        {"symbol": "$SPX", "spot": 5980, "flip": 6005, "put_wall": 5900,
         "call_wall": 6050, "max_pain": 5975, "exp_move": 46, "pc_ratio": 1.3,
         "posture": "below flip (negative gamma)",
         "what_if": {"rally": "...", "selloff": "...", "chop": "..."}},
        # SPY, QQQ ...
    ],
}
```

- `posture` is a short derived label from spot-vs-flip (e.g. `"below flip (negative
  gamma)"` / `"above flip (positive gamma)"`).
- `market_read` is added to the packet **only when non-empty**. If every source is
  absent, the key is omitted entirely → the packet is byte-identical to today
  (back-compat; existing e2e assertions unaffected).

## Wiring points (exact seams)

- **`compute.fetch_market_context`** — add `SPY,QQQ` to the quote symbols; return
  `spy_spot`, `qqq_spot` alongside `vix`, `spx_spot`, `vix1d`. Defensive → missing spot
  is `None`.
- **`compute._market_read(briefing, dashboard, sentiment, spots)`** — NEW pure builder
  producing the dict above. Per-index entries join the briefing's flip/walls/what-if
  with the live spot + derived `posture`. Returns `{}` when nothing usable → caller
  omits the key.
- **`compute._dashboard_risk_read(dashboard)`** — NEW pure helper → `{breadth_spread,
  risk}`; `risk` from a simple aggregate of tile `color_state`s (risk_on/off tilt),
  `breadth_spread` from the `$ADVN-$DECN` tile.
- **`compute.build_packet`** — assemble `market_read` and set `packet["market_read"]`
  when truthy (mirrors the existing additive `market_state` line at the same spot).
- **`handlers.run_autonomous_cycle`** — add `_read_latest_briefing(bus)` +
  `_read_dashboard(bus)` next to `_read_market_state`; extend the composite read to also
  return `live.composite.{total_score,bias}`; thread all into `compute.run_cycle` /
  `build_packet` via the existing `market` dict.
- **`decider._SYSTEM`** — the guidance paragraph in §5.

## §5 Decider prompt guidance (approved tone)

Appended to `_SYSTEM`. Framed to **sharpen selection, not add caution** — the Very
Aggressive $500/day mandate stands:

> A `market_read` may be present (gamma structure, breadth, sentiment). Use it to
> sharpen strike/side selection and conviction — prefer put-credit shorts below the put
> wall and call-credit shorts above the call wall; treat spot below the gamma flip
> (negative gamma) or risk-off / falling breadth as a reason to be **selective**, not to
> stand down. When the read is favorable, press toward the target. This is context to
> improve selection, NOT a mandate to trade less.

A decider unit test asserts the guidance text is present (no live call).

## §6 Observability

Carry a one-line `market_read` **summary string** onto each published
`AutonomousState.decisions[]` row (e.g. `"neg-gamma below flip · bias -35 · breadth
-620 risk-off · sent 4.1"`) so the `/driver` decision log shows what the model actually
saw. Small additive contract touch (`shared/contracts/driver.py` docstring only — the
`decisions` rows are already loose dicts). The `/driver` page renders it in the existing
decision-log row (a follow-up page tweak, or reuse the existing thesis line).

## Defensiveness / degradation (hard requirement)

Every source is optional and independently guarded:
- No briefing (cold start / holiday / prior-day only) → gamma slice omitted.
- No dashboard → breadth/risk omitted.
- No sentiment → score omitted.
- No live spot for an index → that index entry uses the briefing spot (or is dropped).
- **All absent → no `market_read` key** (byte-identical to today).
- The assembly **never raises** (mirrors the existing `_market_state_line` /
  `fetch_market_context` `try/except → {}` discipline). A `market_read` failure can
  never abort a cycle.

## Testing (TDD; no live Claude or proxy)

- `_market_read(...)` — assembly, per-index join + `posture`, degradation with each
  input missing, all-missing → `{}`.
- `_dashboard_risk_read(...)` — breadth spread extraction + risk aggregation.
- Briefing selection — newest-by-`generated_at`; **prior-day briefing dropped** (gamma
  omitted, rest kept).
- `fetch_market_context` — mocked proxy returns SPY/QQQ → `spy_spot`/`qqq_spot`
  populated; missing → `None`.
- `build_packet` — includes `market_read` when data present; **omits** it when absent
  (back-compat assertion).
- `decider` — `_SYSTEM` contains the guidance string.
- Extend the seeded `run_autonomous_cycle` e2e (`test_autonomous_e2e.py`) — with a
  seeded briefing + dashboard + sentiment, the resulting packet carries `market_read`;
  with none seeded, it does not (and the existing clamp/halt assertions still pass).

## Files

- `services/driver_svc/compute.py` — `fetch_market_context` (+SPY/QQQ), new
  `_market_read` + `_dashboard_risk_read`, `build_packet` wiring.
- `services/driver_svc/decider.py` — `_SYSTEM` guidance paragraph.
- `services/driver_svc/handlers.py` — briefing + dashboard reads; extend the composite
  read; merge into `market`.
- `services/driver_svc/tests/…` — the suites above.
- `shared/contracts/driver.py` — docstring note for the `decisions[]` `market_read`
  summary (no schema change; rows stay loose).
- (Optional, small) `webgui/pages/driver.py` — render the `market_read` summary in the
  decision-log row.

## Deferred follow-ups (explicitly out of this unit)

- **③ Wall-aware guardrail** — reject a credit spread whose short strike sits on the
  wrong side of the protective wall; plumbed through `guardrails.apply_guardrails` like
  the existing `vix` kwarg. Requires a backtest before it can gate.
- **③ Breadth halt** — strongly negative breadth + risk-off blocks new bull-side
  entries (generalizes the VIX halt).
- **② Scanner / Swing tilts** — `rr_delta` skew + breadth ranking nudges, and the
  `regime_filter` bridge-file → cache consistency fix (separate consumers, separate
  units).

## Restart note

After implementation, **restart `driver_svc`** (and it benefits from `options_svc` +
`market_svc` + `sentiment_svc` running so the briefing / dashboard / composite caches
are populated). PAPER ONLY — `config.PAPER_TRADE` stays True.

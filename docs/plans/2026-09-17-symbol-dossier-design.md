# Symbol Dossier — one screen per ticker (2026-09-17)

Type a ticker, get one screen: price against the flip, walls and expected move;
Vol Rank and IV vs HV; earnings date; regime and sector quadrant; today's signals
and flow alerts; your open positions in it. Today that is about eight pages.

It is the hub the other ideas surface on, so it is built as an **index over the
pages that own each fact**, never as a replacement for them. Every band ends in a
link out.

## What the cache already holds

Checked against the live payload shapes before designing, the way the Desk was —
the alternative is shipping permanently-empty columns.

| fact | source | new work |
|---|---|---|
| price, day %, spot vs gamma flip | `cache:options:matrix` row — `spot`, `day_pct`, `flip`, `gex_regime` | none |
| call wall / put wall / net GEX | same row — `call_wall`, `put_wall`, `net_gex` | none |
| ATM IV level + direction, dealer regime | same row — `atm_iv`, `iv_state`, `dealer_regime` | none |
| Vol Rank, earnings date | `cache:options:scan_funnel` → `symbols[SYM]` | none |
| market regime | `cache:sentiment:regime` | none |
| sector, industry, quadrant, rank | `cache:sentiment:bullbear` → `levels.stock[]` + the pure `bullbear.quadrant()` | none |
| today's signals | `cache:options:scan_day`, filtered on `symbol` | none |
| today's flow alerts | `cache:options:flow_alerts.alerts[]` — every row carries `symbol` | none |
| open positions | `paper_account`, `paper_trades`, `driver_paper_account`, `captured` | none |
| **IV vs HV** | **nowhere** — `hv_current` is computed and then dropped by the `ScanResult` projection | one funnel field |
| **expected move** | `cache:options:expected_move` is a single shared slot | derive inline |

Nine of eleven need **zero new commands** — one pipelined `read_versions` over six
views, exactly the pattern `desk.py:1598` already runs.

## Coverage is a property of the FACT, not of the symbol

`gex_collector.collection_symbols()` is `[collection] ∪ Top 20.xlsx`, and the scan
watchlist **is** that workbook, so the universes nest:

| state | example | cache gives | fetch supplies |
|---|---|---|---|
| **scanned** | MU, ORCL | everything | nothing |
| **collected** | `$VIX`, XLK | structure — no funnel account | Vol Rank, IV vs HV, earnings |
| **unknown** | anything typed | nothing, not even a quote | all of it |

`symbol_coverage(symbol, matrix, funnel)` is a pure function returning that state,
and every band reads it. A dossier that treated coverage as a property of the
*symbol* would either hide `$VIX`'s structure panel (it has one) or claim a Vol
Rank it never read.

## The on-demand path

A new `dossier` command on `cmd:options` → `compute.build_dossier(symbol)` →
**`cache:options:dossier:<SYMBOL>`**, per-symbol and TTL'd.

⚠ **Per-symbol, following `gamma_pub_key`, NOT a shared slot.** `cache:options:gamma`
is one symbol-agnostic key that `refresh_gamma_current` reads the symbol back out
of, and `cache:options:expected_move` is the same shape — both are a race between
two open tabs. `handlers.py:308` says so explicitly. A dossier on a shared slot
would hijack whatever the Gamma page was showing.

Three Schwab calls: `/quotes` (spot, day %), `/chains` via the existing
`compute._light_gex_context` (flip, walls, ATM IV, a derived EM), `/pricehistory`
1y daily (→ HV-30 → `iv_analysis.calc_iv_rank_percentile`). Earnings
(`shared.earnings.lookup`) and sector (`shared.sectors`) are local and free.

**The merge rule is that cache wins for any fact the cache holds.** The fetch only
fills gaps. The inverse would *degrade* a scanned symbol on refresh — replacing a
one-minute-fresh matrix row with a point-in-time snapshot.

Four guards:

- **15-minute per-symbol TTL** — a repeat lookup inside it spends nothing.
- **No refresh timer.** The page never re-enqueues on its poll. A fetch happens on
  navigation or an explicit Refresh, never on a clock.
- **`_is_stale_side_effect`** — it is a paid fetch, so it joins `gamma_analyze` and
  `rescue_apply` in `_REPLAY_GUARDED`. A fresh consumer group starts at id `0` and
  replays the backlog; that has already burned a day's API budget once.
- **Private-only.** The page is *not* added to `webgui/live_screens.py`. It enqueues
  commands, which `bus_client.set_read_only(True)` refuses — a published dossier
  would render permanently empty for every visitor.

On budget: the ":00/:15/:30/:45 belongs to the autoscan" rule is about *scheduled*
fan-outs colliding with the 92-chain GEX poll. This is one or two chains on a
click, not a cadence — in bounds, but stated rather than discovered later.

## The one Tier-2 field

`hv_current` and `current_iv` are added to the per-symbol funnel account
(`scanner_engine.py:1745-1753`). They are already computed by
`iv_analysis.run_iv_analysis` and carried in the scan's `iv_data` map; the
`ScanResult` projection drops them. One field each makes IV vs HV free for every
scanned symbol, leaving the fetch path to serve only off-watchlist names.

The ratio and its bands come from `strategy_scoring.py:370` — `>= 1.2` rich,
`<= 0.9` cheap — so the dossier's wording cannot disagree with the scorer's.

⚠ "Vol Rank" is not an IV rank. `iv_analysis.calc_iv_rank_percentile` places
current ATM IV inside the 52-week distribution of *realized* vol — a variance risk
premium reading. The screens renamed it in 2026-09-12 and this one keeps that word.

## Route and navigation

`/symbol`, with an optional query parameter —
`def symbol_page(symbol: str | None = None)`, the same shape as the existing
`options_gamma_page(view=None)`. That makes a dossier linkable and bookmarkable,
which the one-shot `handoff` stash deliberately is not.

⚠ A `@ui.page` signature is handed to FastAPI, so that parameter is settable by
anyone who reaches the app. It is intended here, but it feeds a Schwab symbol
lookup: it is uppercased, stripped, matched against `^[A-Z$.]{1,8}$`, and anything
else renders the not-found state without enqueueing.

**Rail placement: the caption-less leading block, beside Desk**, icon
`manage_search` (verified free against the existing set). Desk answers *what is
happening*; Symbol answers *tell me about X*. Those are the two entry points and
everything else in the rail is a workflow step. It preserves that block's property
of pages with a bare one-crumb breadcrumb, and it does not misfile a per-symbol
screen under a caption that says market-wide.

Links **in** stay minimal: the Opportunity Board row gains a dossier action, since
that is already the pick-a-symbol surface. The other seven symbol pages are a
follow-up, not this build.

## Layout — five bands, one question each

The Desk's admission criterion applies: a band earns its place by answering one
question at a glance.

```
┌─ SYMBOL  [MU]   $184.20  +1.8%       SCANNED · cache 14s      [Refresh] ─┐
├────────────────────────────┬─────────────────────────────────────────────┤
│ STRUCTURE                  │ VOLATILITY                                  │
│  put wall ─ flip ─▲─ call  │  Vol Rank 62                                │
│  above flip · net GEX 2.4B │  IV 34.1 vs HV 27.8  →  rich (1.23x)        │
│  charm grind               │  ATM IV 34.1 · rising                       │
│  → Dealer Positioning      │  Expected move ±$3.1 day  ±$7.4 week        │
│                            │  → Expected Move                            │
├────────────────────────────┴─────────────────────────────────────────────┤
│ CONTEXT   Balanced · Rallying │ Technology / Semis · Leading  #4 (was 9)  │
│           Earnings  Oct 23 · in 36 days                                   │
├────────────────────────────┬─────────────────────────────────────────────┤
│ TODAY'S SIGNALS (3)        │ FLOW ALERTS (5)                             │
│  rows + age + score trend  │  newest first                               │
│  → Market Scanner          │  → Flow Alerts                              │
├────────────────────────────┴─────────────────────────────────────────────┤
│ YOUR POSITION (2 open) · paper · driver · captured, with rescue state     │
│  → Paper Ledger / Rescue                                                  │
└───────────────────────────────────────────────────────────────────────────┘
```

Bands 1 and 2 sit side by side because both answer *what is it doing now*; band 3
is a thin full-width context strip; band 4 splits; band 5 is a table. Narrow
viewports stack — this is used from a phone, which is already why the rail's danger
button moved.

**No Highcharts, deliberately** — the same call the Desk made. Every chart here is
one click from the page that owns it, and its absence sidesteps the whole trap
class at once: the missing ResizeObserver, the 8px hidden-mount panel, and the
stock module breaking in-place `update()`.

Palette is the existing **`[console]`** section via `build_console_*`, shared with
`/sentiment` and the Desk. A page-scoped language exists when a screen genuinely
speaks one; this screen speaks the console's.

## Reuse, not reimplementation

`desk.structure_positions` (the flip/wall bar), `svg.gradient_bar_svg` for Vol Rank
as `detail.py:881` already does, `bullbear.quadrant()`, `flow.alert_rows()`,
`scanner.signal_rows` + `handoff.add_row_actions`, `overlay.build_loading_overlay()`
for the fetch wait, `inputs.should_load` for the symbol field's tab-out/Enter dedup.

`structure_positions` **moves to a new pure `webgui/pages/structure.py`**, with
`desk.py` importing it back by name — the `scorecard.py` precedent from 2026-09-12.
⚠ That move found two silent shadowing bugs (a re-assigned `PNL_*` triple, and a
`_money` collision with an unsigned local). Grep the destination for every imported
name before landing it.

## Empty states are per-band and they say different things

| condition | wording |
|---|---|
| not collected, fetch in flight | overlay + "Fetching MU…" |
| not collected, no quote came back | "No quote for XYZQ — check the symbol" |
| collected, feed cold | the shared `copy.WAITING_OPTIONS` line |
| collected and fine, nothing to report | "No signals for MU today" / "No open position in MU" |

The last row is the distinction `copy.py` exists to protect: a dead service and a
quiet tape must not print the same sentence. Any line more than one screen shows
goes in `copy.py`, which `test_shared_copy.py` enforces by reading page source.

⚠ Earnings keeps the three-valued coverage from `shared/earnings.py:110` —
`not_listed` renders "not covered", never "none scheduled". Conflating them is what
makes the gate fail open, and vendor coverage is genuinely patchy.

## Signal age and persistence

The signal rows carry the age and score-trend marks specified in
[the persistence design](2026-09-17-signal-persistence-design.md), plus the
sparkline that is affordable here and not on the scanner.

## Testing

Pure functions TDD'd: `symbol_coverage`, the five band fact-builders, the sparkline
builder. Standing guards: the route into `test_shell.py`'s expected set, the page
into `test_no_inline_style.py`, the new icon through the drawer distinctness test,
`test_nav_sections_partition_the_rail_with_nothing_lost_or_doubled` (the only thing
that catches a regrouping that drops an item), and the DOMPurify allow-list test on
the sparkline.

⚠ The funnel field is tested by monkeypatch + `importlib.reload`, not by asserting
`funnel.hv_current == iv_data.hv_current` — module constants bind at import, so the
equality version passes before the code is wired.

⚠ Band builders are driven from payloads the services actually publish. The
`get_quotes` envelope bug and the `_LEG_LAYOUT` fixture bug both shipped green
against invented fixtures.

No new dependency, so `requirements.lock` does not move.

## Verification

Additive — a new route, a new command, a new per-symbol key; nothing existing
changes shape. It verifies Redis-driven on prod, which is the most reliable check
for a 3-tier page:

```
Bus().enqueue_command("cmd:options", {"type": "dossier", "args": {"symbol": "ORCL"}})
Bus().cache_get("cache:options:dossier:ORCL")
```

⚠ There is no dev environment — `/home/administrator/dev` *is* prod. The page
itself goes through the local harness first. Promote 15:25–16:15 CT: `promote.sh`
stops the whole target, so the public stream drops and GEX slots are lost.

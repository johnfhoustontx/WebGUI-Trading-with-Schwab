# Calculator and Simulator on the public live screens: design

**Date:** 2026-09-21
**Status:** Approved (brainstormed with the owner the same day). Built 2026-09-21; not promoted.
**Ask:** publish the Calculator and the Simulator on `live.neuralstrike.co`,
beside the Strategy Finder and the Rescue form, linked from the site's Tools
menu. The owner's private pages stay as they are.

**Precedents:** the public Rescue form
([blueprint](2026-09-21-public-rescue-adhoc-roadmap.md)) and the public
Strategy Finder ([roadmap](2026-09-21-public-strategy-finder-roadmap.md)). This
design reuses their shape and the fixes the Rescue review forced.

---

## Decisions

| # | Question | Decision |
|---|---|---|
| Q1 | Live quotes, with Schwab's republishing terms still open | **Build the quoted version, gated by the existing off-by-default switch** (`public_scan.show_leg_quotes`, one switch for every public tool). While off, visitors type leg prices and see only derived results. |
| Q2 | Phasing | **Both together.** The Simulator opens with the visitor's Calculator position. **Nothing persists from earlier actions.** |
| Q3 | Architecture | **A: one public tools path** (new streams + worker), not the private commands, not math in the public process. |
| Q4 | Simulator scope | **Price & Time only.** No Volatility tab, no History tab, no replay request. |

## Why this is not another `Screen` row

- **Every private slot is single and shared.** The Calculator uses
  `cache:options:calc_chain` / `calc_result` / `calc_iv` / `calc_rating`; the
  Simulator `sim_chain` / `sim_meta` / `sim_result`. A public reader would show
  the owner's last symbol, and visitors would overwrite each other.
- **The Simulator's chain lives in the options service's memory**
  (`compute._SIM_SNAPSHOTS`, keyed by symbol). Public fetches written there
  would replace the owner's snapshot for that symbol mid-analysis.
- **The private pages restore state from module-level single-user stores**
  (`page_state`, `shared_position`). On the public process those would be
  shared by every visitor: one stranger's legs on another's screen.

## 1. Architecture

- **Two new public streams.**
  - `cmd:tools_public` carries requests that spend Schwab calls.
  - `cmd:tools_public_math` carries pure pricing.
  - Each has its own consumer loop in options_svc, so a snapshot fetch (the
    private one measured ~19 s) never stalls repricing, which fires on every
    edit. The public Redis user gains two write selectors (four in all).
- **One per-symbol public chain key, shared with Rescue.**
  `cache:options:pub_chain:<SYMBOL>` holds expirations and strikes (Rescue's
  current strikes list, renamed) plus bid / ask / mark / delta **only while the
  quotes switch is on** - the service decides at write time. Rescue ignores the
  quotes. One load per symbol serves all three tools.
- **One daily Schwab budget** for every tools request from Rescue, Calculator
  and Simulator. Math requests spend none: no daily cap, only a per-visitor
  hourly limit.
- **Per-request result keys**, hashed from the normalized request, plus a
  per-request answer key the page polls. No list of requests is kept.
- **Public Simulator snapshots in their own store**, never
  `compute._SIM_SNAPSHOTS`: at most 8 symbols, 15-minute life, shared by every
  visitor on a symbol; one more expiration merges into the held snapshot.
- **Pages:** `calculator.render(public=True)` → `calc_live.py`;
  `simulator.render(public=True)` → `sim_live.py`. Both are built from the
  private pages' own pieces.
- **Hand-off** in the visitor's own tab (NiceGUI tab storage).

## 2. Requests, costs and limits

| Request | Stream | Schwab cost | Answered from |
|---|---|---|---|
| Chain for a symbol | tools | ~3 calls | `pub_chain:<SYM>`, reused 60 min |
| One more expiration | tools | 1 call | merged into `pub_chain` |
| Price the legs (metric cards + P&L matrix: `compute.calc_compute`) | math | none | per-request result |
| Implied volatility (`compute.calc_iv` from the held chain's mark) | math | none | the percentage only |
| Rate My Trade | tools | several (price history, volatility, earnings) | per-request result, reused 15 min |
| Simulator snapshot | tools | chain + price history, **unmeasured** | public snapshot store |
| Price & Time sweep (`compute.sim_run`'s what-if half) | math | none | per-request result |

- Rate My Trade on the public page drops the checklist's **Paper book** line
  (it reads the owner's ledger), as the Finder does.
- Tools requests run in a new `[windows.tools_public]` (market hours). Math
  requests run any time against data already held.

## 3. Pages and hand-off

**Public Calculator (`calc_live.py`).**
- Kept: entry panel (symbol + Load, strategy menu incl. the three share-leg
  structures, expiration strip of every listed date), leg table, pricing
  assumptions, six metric cards, P&L matrix, Rate My Trade with its detail
  panel.
- Quotes switch **off**: no chain grid; strikes from strikes-only dropdowns; no
  Bid/Mark/Ask dropdown and no delta column; leg prices typed; the volatility
  assumption pre-filled from the service's implied value and editable.
- Quotes switch **on**: chain grid, price-source dropdown and delta column
  return, as on the private page.
- Dropped: the Expected Move hand-off (not published), the checklist's Paper
  book line, all restoring of earlier state.

**Public Simulator (`sim_live.py`).** The same entry panel and leg table, then
Price & Time alone: the days-ahead slider, the price-offset overlay (drawn in
the browser, no request), the what-if chart and its position tiles. No tab
strip, no volatility multiplier, no History.

**Hand-off.**
- The Calculator gains **Open in Simulator**, opening `/simulator` in the same
  tab (public screens have no navigation).
- On every change the Calculator writes `{symbol, legs, prices}` to that tab's
  NiceGUI tab storage: server memory, keyed by tab, never disk or Redis.
- The Simulator seeds from it when present and requests that symbol's snapshot
  once - arriving from the Calculator is the visitor's own action. Otherwise it
  opens on a default template and spends nothing, so a crawler or a visit from
  the Tools menu (a new tab) costs nothing.
- **The Calculator never reads tab storage**: it always opens fresh. A
  Simulator reload re-seeds from the Calculator's last write.
- Tab storage lifetime in the public process is cut from NiceGUI's default
  30 days to 1 hour.

**Site.** The Tools menu gains **Simulator**; **Calculator** becomes a link.
The live grid gains two tiles.

## 4. Error handling

- **Every request gets a worded answer**: done, cached, duplicate, throttled,
  closed, budget, not listed, off ladder, no options, expired, invalid, error,
  and a new **load first** (a math request for a symbol with no chain or
  snapshot held). No raw exception text reaches the page; the engine's
  `{"error": ...}` returns become a generic "error" and are never cached.
- **A failed fetch is remembered nowhere.** "No options" needs Schwab to have
  answered with an empty chain.
- **Math requests** have a per-visitor hourly limit (~600), an unchanged
  request is not re-sent while pending, and the Calculator's 0.3 s debounce
  stays.
- **Every field is validated before it is written and again in the worker**:
  symbol, expirations, strikes on the listed ladder, quantities, prices,
  volatility 0-500%, days ahead 0 to the longest leg. A share leg keeps a
  positive typed price as the visitor's own cost basis; only a share leg priced
  0 is filled with the held spot (the private `calculator.fill_stock_premiums`
  rule, `tools_public._fill_share_premiums`). A NaN, infinity or boolean
  refuses the request.
- A snapshot evicted since the page loaded answers **load first**; the page
  offers Load again.
- From the Rescue review, from the start: a held list counts only within its
  freshness window; one structure is capped per window (Rate My Trade,
  snapshot loads); the handler never raises.

## 5. Testing

- Validator tests with NaN / infinity / boolean for every field.
- Worker tests: each refusal proves no Schwab call ran, each mutation-checked.
- Page tests driven through the real poll timer.
- Source-level pins: exactly the new public write functions; `_may_enqueue` on
  every private enqueue in `calculator.py` and `simulator.py`
  (`test_live_commands.py` requires it of published modules); no read of the
  owner's keys.
- The public snapshot store never touches `compute._SIM_SNAPSHOTS`.
- The Calculator never reads tab storage; two visitors' tabs cannot see each
  other's position.
- Site tests for the two tiles and menu links.
- Local harness with the real worker and faked Schwab data; independent review
  before merging.

## 6. Rollout

1. Build on a worktree branch → review → fix → merge on the owner's word.
2. Before promoting: runbook §2 steps **4e** and **4f** (the two new ACL
   selectors). Rescue's 4d first.
3. Promote after the close.
4. Measure real snapshot and Rate My Trade costs in the next session; tune the
   budget in Settings → Configuration.

⚠ **Migration:** Rescue moves from `rescue_pub_ladder:<SYM>` to the shared
`pub_chain:<SYM>`. Safe only because Rescue is not promoted yet - both must
ship in the same promote.

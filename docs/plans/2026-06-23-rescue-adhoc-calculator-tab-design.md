# Ad-hoc Rescue → Calculator-style leg editor on its own Rescue tab

**Date:** 2026-06-23
**Branch:** `Using_Highcharts`
**Status:** Approved (forks chosen in-session).

## Problem

The ad-hoc rescue form (shipped 2026-06-22) is a small strategy+strike-dropdown
expansion. The user wants it to (1) use the **Calculator's input experience** — the
strategy picker + the full Type/Side/Expiry/Strike/Qty/Premium leg editor — and
(2) live on its **own tab** under Rescue.

## Decisions

- **Trade-defining inputs only** — strategy picker + Symbol + Load + Expiry +
  Contracts + the leg editor. Drop the Calculator's grid-only fields (IV%/Rate/
  IV Δ/Number-of-strikes/Price) — rescue reprices live and ignores them.
- **Credit structures only** — map the legs to PCS / CCS / IC (an iron fly → IC).
  If the legs aren't a recognized defined-risk credit structure, show a clear
  message instead of a broken advisory.
- **Backend unchanged** — `compute_rescue_adhoc(spec)` already takes the
  `{strategy, short/long/call strikes, expiration, quantity, entry_credit}` spec.
  The page maps leg-editor legs → that spec. The entire change is confined to
  `webgui/pages/options/rescue.py` + `webgui/tests/test_rescue.py`.

## Page (`webgui/pages/options/rescue.py`)

**Tabs.** `render()` gains a `ui.tabs` — **"At-Risk Board"** | **"Ad-hoc Trade"** —
+ `ui.tab_panels`:
- **Board panel:** the existing two-column layout (at-risk table left + advisory
  cards right, for board/captured selections) — unchanged behavior.
- **Ad-hoc panel:** a Calculator-style form (left/top) + its own advisory cards
  (right/below).

**Ad-hoc form (reuses shared components):**
- **Strategy picker** — `strategy_menu.build_strategy_menu(value="PCS", boxed=True)`;
  on change, seed the leg editor template (`editor.apply_template(code)`) like the
  Calculator's `_seed_template`.
- **Symbol** input + **Load** button → enqueue `calc_load`; a version-poll on
  `cache:options:calc_chain` (read the ~10 MB payload OFF-loop via
  `run.io_bound`, mirror `calculator.py`) populates the leg editor's expiry/strike
  dropdowns via `chain_expiries` / `chain_strikes` (already imported).
- **Expiry** select + **Contracts** number (scales leg qty, like the Calculator).
- **Leg editor** — `leg_editor.build_leg_editor(box, strikes_for=…, expiries_for=…,
  show_premium=True, header=True, on_change=…, spot_getter=…)`: the full Type/Side/
  Expiry/Strike/Qty/Premium table + **Add Leg**.
- **Compute rescue options** button.

**Advisory rendering (refactor).** Extract `_render_cards_into(container, advisory)`
+ `_render_one_card(container, card)` so each tab has its OWN cards container + poll:
board polls `options:rescue:<selected_id>`; ad-hoc polls `options:rescue:adhoc`.

**Compute flow.** Button → `adhoc_spec_from_legs(symbol, editor.get_legs())`; on
`{"error": …}` → `ui.notify(warning)`; else enqueue `{"type":"rescue_adhoc","args":
{"spec": spec}}` and let the ad-hoc poll render the advisory-only cards.

## Pure mapping — `adhoc_spec_from_legs(symbol, legs)`

Leg dict keys (from `leg_editor`): `option_type` ("call"/"put"), `side`
("long"/"short"), `strike`, `expiry`, `qty`, `premium` (per share).
- **Single expiration required** across all legs → else `{"error": "Rescue needs a
  single expiration (no calendars)."}`.
- Split puts/calls; find the short + long on each side.
- **Strategy inference (credit structures only):**
  - puts only, `put_short.strike > put_long.strike` → **PCS**
    (`short_strike=put_short`, `long_strike=put_long`).
  - calls only, `call_short.strike < call_long.strike` → **CCS**.
  - both a valid put credit spread AND a valid call credit spread → **IC**
    (iron fly = the same, shorts may share a strike).
  - anything else (single leg, only shorts, debit spread, ratio, wrong side count)
    → `{"error": "Rescue supports credit spreads and iron condors/flies …"}`.
- `entry_credit` (per share) = Σ short premiums − Σ long premiums (must be > 0 for a
  credit structure; ≤ 0 → error "not a net-credit structure").
- `quantity` = the short leg's qty (uniform assumed).
- Return the spec dict for `compute_rescue_adhoc`.

## Testing
- `adhoc_spec_from_legs`: PCS (2 puts), CCS (2 calls), IC/iron-fly (4 legs incl. the
  screenshot's NBIS fly), single-leg → error, debit spread → error, multi-expiry →
  error, net-debit → error.
- The two-tab `render()` verified by the shell smoke test (renders cold); existing
  rescue pure-builder tests stay green.

## Out of scope
- No change to `compute_rescue_adhoc` / the engine. No grid/IV fields. Advisory-only
  (unchanged). Non-credit structures are rejected, not best-efforted.

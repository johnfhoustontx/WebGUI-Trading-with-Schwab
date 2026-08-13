# Expected Move — current-day candle + by-expiration selection (design)

**Date:** 2026-08-12
**Page:** `/options/expected-move` (`webgui/pages/options/expected_move.py`)
**Service:** `services/options_svc` (:8211)

## Problem

Two defects, one feature, both surfaced from the live page.

**1. The current-day candle is never drawn.** This is a data defect, not a
rendering one. `_fetch_em_candles`'s daily branch calls
`proxy_client.get_price_history_every_day`, which requests Schwab's
`periodType=year&period=1&frequencyType=daily` form. Schwab ends a `period`-based
range at the **previous** trading day. Verified live on 2026-08-12: 252 candles,
last one `2026-08-11`. The live quote does carry today's session bar
(`openPrice` 774.71 / `highPrice` 774.9 / `lowPrice` 771.28 / `lastPrice` 772.30),
so the bar exists — it just isn't in the history endpoint's response.

**Second-order consequence.** `compute_expected_move` anchors the cone at
`candles[-1][0]` (yesterday) but sizes it from **today's** spot, so the cone
currently runs one day PAST the expiry. Appending today's candle fixes the anchor
and the overshoot together.

**2. Expiry and strike are free-text.** The page asks for `Expiry (YYYY-MM-DD)` as
a typed string and `Strike (optional)` as a raw number. Nothing validates either
against the symbol's actual chain, so a typo silently yields "No ATM IV for …".
The workflow the page wants is: pick a symbol, then pick from that symbol's real
expirations and real strikes.

## Design

### 1. Synthetic current-day candle (Tier 2)

New PURE helper in `services/options_svc/compute.py`:

```python
def today_candle(quote, last_ts_ms, now=None, holidays=None):
    """[ts_ms, o, h, l, c] for today's forming session bar, or None."""
```

Returns `None` unless ALL of:

- today is a trading day (weekday, not in `scheduler._HOLIDAYS`);
- local time is at/after **08:30 CT** — before the open the quote's `openPrice`
  is still the PREVIOUS session's open and would draw a false bar;
- the raw quote carries numeric `openPrice`/`highPrice`/`lowPrice`/`lastPrice`,
  all > 0;
- the last history candle's local date is strictly **before** today — so the
  helper is a no-op if Schwab ever starts including the forming bar.

The timestamp is today's local midnight in ms, matching the convention the daily
candles already use (verified: history candles are 00:00 local).

**Drawn from the open onward** (chosen over regular-hours-only): after 15:00 CT
the bar stays on the chart rather than vanishing until the next day's history
catches up.

`compute_expected_move` switches its spot fetch from
`_proxy.schwab_client.get_quote` (which normalizes to `last`/`high`/`low` and
**drops `openPrice`**) to the raw `_proxy.schwab_py_client.get_quotes([api])` —
the same unwrap `calc_load_symbol` already performs. Same `lastPrice` for spot,
plus the open the candle needs, in ONE call rather than two. The synthetic bar is
appended **only in `daily` mode**; the `dte<=2` intraday branch already reaches
today through `get_intraday_history`.

**Known approximations** (sub-tick on a 6-month chart, documented not fixed):
after 15:00 CT the bar's close is `lastPrice`, which includes post-market prints
(~$0.19 off the regular close on SPY at the time of writing); Schwab's
`highPrice`/`lowPrice` may include the extended-hours range.

### 2. Expiration + strike dropdowns

New command `em_chain` → `compute.em_chain_meta(symbol)` → **`cache:options:em_chain`**:

```
{symbol, api, spot,
 expirations: ["2026-08-14", ...],
 strikes: {"2026-08-14": [770.0, 771.0, ...]},
 error}
```

Reuses the existing `chain_expiries` / `chain_strikes` extraction over a
today→**+90d** `ALL` chain. (The Calculator uses +60d; 90 covers monthlies a
little further out without materially growing the fetch.) Strikes are **deduped
across call and put** — one ladder per expiry, because put-vs-call is the
toggle's job, not the ladder's.

A separate command + cache key, rather than reusing the Calculator's
`cache:options:calc_chain`: two pages sharing one chain key would fight over
symbol state and show each other's stale symbol.

Page changes (`webgui/pages/options/expected_move.py`, Tier-1, engine-free):

- **Symbol** stays a text input but gains the shared `inputs.should_load`
  tab-out/Enter trigger (the Calculator's idiom) → enqueues `em_chain`.
- **Expiry** becomes `ui.select(with_input=True)` populated from `expirations`;
  picking one **auto-enqueues** `expected_move`.
- **Strike** becomes `ui.select(with_input=True)` populated from
  `strikes[expiry]`; changing it **only repaints the plotLine locally**.
  `expected_move_figure(payload, legs=…)` grows an explicit `legs` override so
  the page can pass the current selection without a service round-trip — the
  strike draws a horizontal line and the candles/cone do not depend on it.
- Put/call toggle, Look-back select and **Draw** all stay; Draw becomes the
  force-refresh.
- **Handoff is preserved.** A stashed payload (Scanner / Paper / Captured /
  Calculator) still enqueues `expected_move` immediately AND fires `em_chain` in
  the background; when the chain lands, the handed expiry/strike are selected
  **without** triggering a redraw.

### 3. Testing

Pure + unit-tested per the house pattern:

- `today_candle` — trading-day gate, pre-open skip, duplicate-date no-op,
  missing/zero-field degradation, timestamp convention.
- `em_chain_meta` — extraction over a sample chain payload, call/put dedup,
  defensive empty on a failed fetch.
- `expected_move_figure(legs=…)` — the override wins over the payload's legs.
- Handler test for the `em_chain` command round-trip (cache key + event).

Everything defensive: a chain failure leaves the dropdowns empty and the manual
path working, and never raises.

## Decisions taken

| Decision | Choice | Why |
|---|---|---|
| Today's candle window | From the 08:30 CT open onward | Premarket `openPrice` is the prior session's — would draw a false bar. Keeping it after 15:00 avoids the chart losing today at 3pm. |
| Strike change | Repaint locally | The strike is only a plotLine; a chain refetch per change buys nothing. |
| Expiry source | New `em_chain` command + cache key | Reusing the Calculator's chain couples two pages to one symbol. |
| Chain window | today→+90d | Covers monthlies further than the Calculator's 60d at a similar fetch cost. |

# Strategy Finder — ask before loading a large chain

**Date:** 2026-09-14 · **Status:** approved design · **Route:** `/options/swing`
**Follows:** `2026-09-14-strategy-finder-whole-chain-design.md` (promoted as `6ae54bf`).

## Request

"Fix the DTE max box and the docs timing. For SPX (and other large chains) don't load the
complete chain, give me option what to load."

## Why

Live scans on prod after the whole-chain promote (2026-09-14, ~12:30 CT, range *All*):

| Symbol | Listed expirations | Wall time | Rows |
|---|---|---|---|
| NVDA | 25 | 13.5 s | 139 |
| SPY | 34 | 25.7–27.4 s | 162–167 |
| $SPX | 56 | **40.1 s** | 69 |

`cmd:options` runs one command at a time, so a $SPX scan also holds up Calculator and
Simulator loads, whose wait overlay gives up at 30 s. The docs still say "about 20 s"
(a synthetic figure). And the DTE max box is too narrow: its placeholder reads "no limi".

## Schwab labels every expiration

`/expirationchain` rows carry `expirationType` (measured on prod the same day):

| Symbol | Total | W (weekly) | S (standard monthly) | Q (quarterly) | M (month-end) | ≤ 30 d | ≤ 90 d |
|---|---|---|---|---|---|---|---|
| $SPX | 56 | 32 | 19 | 4 | 1 | 23 | 35 |
| $NDX | 47 | 27 | 15 | 4 | 1 | 23 | 32 |
| SPY | 34 | 16 | 13 | 4 | 1 | 14 | 19 |
| QQQ | 33 | 14 | 14 | 4 | 1 | 14 | 19 |
| IWM | 33 | 14 | 15 | 4 | — | 14 | 18 |
| NVDA | 25 | 10 | 15 | — | — | 9 | 13 |

The list costs 0.2–0.3 s and the scan already fetches it first.

## Operator decision

| Question | Choice |
|---|---|
| How to choose what to load | **Ask when the chain is large** — a chooser before any chain is fetched; small chains scan straight away |

## Design

### 1. A large chain answers with choices, not with a chain (service)

- **`LARGE_CHAIN_EXPIRIES = 30`.** The Finder's scan counts the listed expirations inside
  the requested DTE range (`dte_min` to `dte_max`, no upper bound when `null`), counting
  each expiration's DTE as **Schwab's `daysToExpiration`** from that list: the same
  number Schwab writes into the chain keys (`"YYYY-MM-DD:dte"`) the builders filter on.
  The host's calendar difference is a day off between 23:00 and 24:00 CT, and the fetch
  asks for exactly the chosen dates, so counting on it could silently drop a boundary
  expiry. The calendar difference is used only when a row carries no usable number.
  More than 30, and the request carries no `expiry_choice`: the service **does not fetch the chain**.
  It answers with `needs_choice: True`, `expiration_count`, `choices`, the symbol, the
  spot and the echoed params.
- **Four choices**, each counted inside the requested range, in this order:

  | key | label | expirations kept |
  |---|---|---|
  | `next_30` | Next 30 days | DTE ≤ 30 |
  | `next_90` | Next 90 days | DTE ≤ 90 |
  | `monthly` | Monthlies only | `expirationType == "S"` |
  | `all` | Everything | all of them |

  Each choice carries `count` and `est_seconds = count × SCAN_SEC_PER_EXPIRY` rounded half up (not to even), with
  `SCAN_SEC_PER_EXPIRY = 0.75` (live: SPY 26 s / 34, $SPX 40 s / 56). A choice with
  `count` 0 is still listed; the page draws it disabled.
- **With `expiry_choice`:** only that choice's expirations are fetched and built.
  Contiguous selections (next 30/90, everything) fetch in runs of up to 8 consecutive
  listed expiries as today; monthlies are not consecutive, so each is its own run (still 4
  at a time). The answer carries `expiry_choice` (the key applied), `expiration_count`
  (in range) and `expirations_scanned`, plus `choices`, so the page can reopen the
  chooser without a scan.
- **A choice is applied only to a large range.** If the range holds 30 or fewer
  expirations, the choice is ignored, everything in range is scanned, and the answer's
  `expiry_choice` is `null`. The page can then send a remembered choice without knowing the
  count in advance.
- `swing_scan(..., expiry_choice=None, ask_if_large=False)`: only the Finder's handler
  passes `ask_if_large=True`. **The Income Window never asks.** An unknown `expiry_choice`
  raises `ValueError`, which the handler's always-answer path turns into an error answer.
- **No expiration list** (the degraded fallback): nothing to count, so nothing to ask —
  today's bounded single fetch, `expiry_choice: null`.

### 2. The chooser (page)

The spinner ends; the top-pick area shows one card:

> **$SPX lists 56 expirations in this range.** Choose what to scan:
> [Next 30 days · 23 · ~17 s] [Next 90 days · 35 · ~26 s] [Monthlies only · 19 · ~14 s] [Everything · 56 · ~42 s]

- The summary strip shows the symbol and price, with the count line *"56 expirations —
  choose what to scan"*. The list is empty and says *"Choose which expirations to scan for
  $SPX."*
- A pick re-sends the same scan with `expiry_choice`; the spinner and count run as usual.
- **The pick is remembered per symbol** while the page is open (page state, not
  persisted): scanning $SPX again sends the same choice and does not ask.
- After a scan with a choice applied, the count line adds *"Scanned 19 of 56 expirations ·
  Monthlies only"* and a **Change** link that reopens the chooser from the answer's
  `choices` (no scan until a pick).
- A different symbol has no remembered pick, so it asks if its chain is large.

### 3. The DTE max box and the timing

- The DTE max box widens so *no limit* fits (measured in the harness; `w-20` clips it).
- Docs: the "about 20 s" figure becomes the live numbers — $SPX about 40 s, SPY about 26 s,
  NVDA about 14 s for the whole chain — and every place that describes the scan says a
  large chain asks first.

## Testing

- **Service:** typed expiration parsing (`W`/`S`/`Q`/`M`, junk rows dropped); counts
  inside a range (incl. `dte_max` null and a `dte_min` floor); the threshold at 30 vs 31;
  the chooser answer fetches **no** chain; each choice fetches exactly its expirations
  (monthly as separate runs); a choice on a small range is ignored; unknown choice → error
  answer; `ask_if_large=False` (Income) never asks; the no-list fallback never asks.
- **Page:** the chooser card and its four buttons (disabled at 0); a pick sends
  `expiry_choice`; the remembered pick per symbol, dropped for another symbol; the
  "Scanned N of M" line and Change reopening the chooser; the summary and empty-list lines
  on a `needs_choice` answer; the DTE max box width.
- **Afterwards:** the local harness with a typed 40-expiry chain; live $SPX and SPY on prod
  after the promote (chooser counts match the table above; *Monthlies only* wall time).

## Out of scope

Persisting the pick across sessions; changing the threshold per symbol; making the
command consumer concurrent; scoring changes (the long-dated short straddles at the top of
SPY are a separate question).

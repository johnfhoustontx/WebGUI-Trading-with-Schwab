# Flow Alerts screen — design (2026-08-09)

## Problem

Options-flow alerts are detected but never *displayed*. `options_svc` runs three
detectors on each 1-min GEX tick — premium crossover, contract-level unusual
activity (UOA), and dealer gamma-regime flip — pushes each fresh alert to the
phone, and publishes a day-scoped rolling list to `cache:options:flow_alerts`.

The webgui reads that list for one purpose only: the app-wide 2 s watcher chimes
and pops a toast for ids it hasn't acked. Miss the toast and the alert is gone —
there is no screen that answers "what has fired today?". The only durable trace
is the Opportunity Board's per-symbol *count* column, which tells you a symbol
fired without telling you what fired or when.

## Decision summary

| Question | Decision |
|---|---|
| History depth | **Today only**, but raise the 50-alert cap so a full day fits |
| Layout | **Chronological table**, newest first, with filters |
| Nav placement | **Rail page under Options**, beside Opportunity Board |
| Nav badge | **No** — the page is somewhere you go look |
| Row click | **Open Dealer Positioning** for that symbol |
| Time display | **Fired time + Age** ("2m ago"), age ticking live |

## Architecture

A **pure Tier-1 page**. It reads one existing cache key and nothing else — no new
service, no new command, no new cache key, no engine import. Two small changes
land in Tier 2 for reasons given below.

```
options_svc (1-min GEX tick)                    webgui
  run_flow_alerts()                               /options/flow
    detect crossover / UOA / gamma flip             version-poll options:flow_alerts
    push to phone                                   read payload off-loop on change
    publish cache:options:flow_alerts  ──────────>  render chronological table
                                                    row click → handoff → /options/gamma
```

### Tier-2 changes (two lines, `services/options_svc/handlers.py`)

1. **`_FLOW_ALERTS_MAX: 50 → 300`.** The published list is capped at 50, so on a
   busy day the morning's alerts are already dropped before anyone looks. With
   ~45 collected symbols, a 30-min crossover cooldown, a $5M UOA premium floor
   and 4 gamma-flip names, 300 comfortably holds a full session.

2. **UOA alerts gain a timestamp.** `flow_alerts.detect_uoa` emits
   strike/expiry/volume/OI/premium but **no `ts`**, and the drain loop in
   `run_flow_alerts` doesn't add one — crossover and gamma-flip alerts carry `ts`,
   UOA does not. A chronological table would show a blank Time cell for a third of
   the alert types. Fix: `a["ts"] = now_ts` in the UOA drain loop. This is a gap in
   the existing payload, not page-only work.

Both require an `options_svc` restart to take effect.

## The page — `webgui/pages/options/flow.py`

**Route** `/options/flow`, added to `OPTIONS_RAIL` in `main.py` as a third
standalone rail item under the Options group (icon `bolt` — a test pins the drawer
icons as non-empty and mutually distinct), plus a distinct `_TAB_COLOR` entry for
the per-route favicon. No tab strip (rail pages have none), no nav badge.

It is deliberately **not** an Options tab-strip entry: that strip encodes the
find → analyze → track → repair workflow over individual signals, whereas this is
a market-wide read across the whole collected universe — the same reason
Opportunity Board and Dealer Positioning are rail pages.

**Refresh.** Version-poll `options:flow_alerts` on a 2 s `ui.timer` using the cheap
`:ver` probe (`bus_client.read_version`); re-read the payload via
`run.io_bound` and rebuild rows **only** when the version moves. The **Age** cells
are recomputed on every tick against the existing rows — no re-read, no rebuild —
so age stays live without churning the table.

### Columns

**Time** (CT `HH:MM:SS`) · **Age** (`2m ago`) · **Symbol** · **Type** · **Side** ·
**Detail** · **Alert**.

`Type` is spelled in whole words — Crossover / Unusual activity / Gamma flip.
`Detail` is type-specific and compact:

| Type | Detail cell |
|---|---|
| crossover | `$1.2M calls vs $0.4M puts` |
| uoa | `0DTE 737C · 12,400 vol / 1,100 OI (11.3×) · $2.1M` |
| gamma_flip | `spot 6412 vs flip 6400` |

Rows tint by direction (bullish green / bearish red / neutral) via a **stamped
class field bound with `:class`** — Tailwind-first, mapped from the finite
`(type, side)` set, no `.style()` or `:style=` anywhere (the project's UI standard).

### Controls

Kind filter chips (Crossover / Unusual activity / Gamma flip, all on by default)
and a symbol dropdown populated from the day's own alerts with "All" default.
Filtering is client-side over the already-read rows, so toggling is instant.

A status line distinguishes the three states that otherwise look identical —
"no alerts yet today", "waiting for the options service", and a normal count —
the same distinction `net_prem_status_text` draws on the Gamma page.

### Click-through

Row click opens **Dealer Positioning** with that symbol loaded — where the Flow
chart, gamma flip and walls already live, which is the context every alert type is
asking you to check.

`pages/options/handoff.py` gains a `gamma` slot alongside its four existing ones:
`set_pending_gamma` / `take_pending_gamma` / `send_to_gamma(symbol)` (stash +
`ui.navigate.to("/options/gamma")`).

`gamma.render()` consumes it at its existing build-time symbol sync
(`gamma.py:2647`), which already points the dropdown at the cached symbol
**before** wiring `on_value_change`. The pending symbol wins over the cached one,
then one `_request_refresh()` moves the snapshot to it. This reuses the page's own
documented ordering rather than adding a second path.

## Pure builders (unit-tested)

`render()` stays thin; all logic is module-level and total over malformed input:

- `alert_rows(view)` → display rows, newest first
- `alert_kind_label(a)` → whole-word type label
- `alert_detail(a)` → the type-specific detail cell
- `tone_class(a)` → fixed Tailwind class from the finite direction set
- `fmt_time(ts)` / `age_text(ts, now)` → CT clock and elapsed
- `filter_rows(rows, kinds, symbol)`
- `status_text(view, now)`

## Testing

- `webgui/tests/test_flow_page.py` — the builders, including `None`/malformed
  payloads and a UOA alert missing `ts` (pre-fix rows must still render).
- `/options/flow` added to `test_shell.py`'s expected route set.
- The page added to `test_no_inline_style.py`.
- An `options_svc` test pinning the UOA timestamp and the raised cap.
- `page_help.py` entry so the rail item gets its hover tooltip.

## Out of scope (YAGNI)

No history or date picker (today only, by decision). No nav badge. No charts. No
per-symbol leaderboard. No **duration** tracking — how long a condition *held* is
genuinely useful for telling a real flip from a head-fake, but it isn't in the
payload and would require `options_svc` to track each condition's start and
re-check it every tick; explicitly deferred.

The existing toast, chime, phone push and Settings toggle are **unchanged**.

# EOD Report — Design (2026-06-18)

## What

A new **EOD Report** menu item in the webgui that produces two reports for the
trading day:

1. A **Summary** — a one-screen rollup of the day.
2. A **Detailed** report — full drill-down tables.

Each is viewable **in-app** (NiceGUI) and **exportable** as a self-contained
`.html` file. Generation is **manual** (a "Generate" button snapshots the current
caches) and each generated report is **archived** under a dated folder the page
lists for re-opening.

## Scope (content)

The report covers two domains (per the user's selection — portfolio and sentiment
are intentionally out of scope for v1):

- **Options activity** — scanner signals (`cache:options:scan`), captured signals
  with outcomes/drift/rec (`cache:options:captured`), paper trades
  (`cache:options:paper_trades`) and paper account P&L (`cache:options:paper_account`).
- **Driver trades & performance** — today's approved/skipped morning-agent trades
  (`cache:driver:approvals`) + win-rate / P&L-by-bucket (`cache:driver:performance`).

## Architecture — pure webgui page (no new service)

The EOD report is a **read-only aggregation of caches already published** by the
existing services. It needs no engine computation and no scheduling, so it lives
entirely in the webgui as a new leaf page. This honors the 3-tier rule that the
webgui imports only `nicegui` + `shared.bus` + `shared.contracts` — no app engine
imports, no new process, no new port.

Rejected alternatives:
- **New `eod_svc`** — overkill; it would only re-read the same caches. (YAGNI.)
- **Extend an existing service** — would cross-couple multiple domains into one
  service and muddy its single-domain ownership.

## Single-source report body

Following the existing `pages/options/gamma.py` Explain pattern (`EXPLAIN_CSS` +
`wrap_explain`), pure functions build each report as an **HTML fragment** plus a
shared **CSS string** — one source of truth feeding both outputs:

- **In-app:** `ui.add_css(EOD_CSS)` once + `ui.html(fragment)`. (NiceGUI's
  `ui.html` strips `<style>`/`<iframe>`, so CSS must go through `add_css` and we
  render a fragment, not a full document.)
- **Export file:** `wrap_document(fragment, css, title)` wraps the same fragment +
  CSS into a full standalone `<html><head><style>…</style></head><body>…</body>`
  document written to disk.

The only thing that differs between in-app and file is the **summary→detail link
target**: in-app it links to the route `/eod/detail`; in the exported file it
links to the relative `detail.html`. The fragment builder takes the link target as
a parameter.

## Pages / routes

- **`/eod` — Summary.** A top action bar (Generate · Download summary · Download
  detail), an **archive list** of past dated reports (each row opens that day's
  saved `summary.html`), and the summary fragment: paper-account day P&L, scanner
  signal count, captured-signal outcome counts, driver day grade + approved/skipped
  status, driver win-rate. Links to the detail report.
- **`/eod/detail` — Detailed.** The detail fragment: full tables — every captured
  signal (drift / outcome / rec), every paper trade + the paper account summary,
  scanner signals, driver proposed/executed trades with reasons, performance
  by bucket.

Both are added to `webgui/main.py`: a new `pages/eod.py` exposing `render()` and
`render_detail()`, two `@ui.page` routes inside `_layout`, and a new `FLAT_NAV`
entry `("/eod", "EOD Report", "summarize")`. Register both routes in
`test_shell.py`'s expected set.

## Generate + archive flow

"Generate" snapshots the caches at click time, builds both reports, and writes
`summary.html` + `detail.html` into `webgui/data/eod/<YYYY-MM-DD>/` (under the
already-gitignored `webgui/data/`). The summary page lists archived dates (newest
first) by scanning that directory; clicking a date opens its saved file. If a
report for the current date already exists, "Generate" overwrites it (last
snapshot of the day wins). The dated folder name comes from the CT trading date.

Builders are pure and take the snapshot dicts as input, so archiving is just
"build the documents, then write the strings to disk" — no live cache reads at
write time beyond the single snapshot taken when the button is pressed.

## Data shapes

The caches are read via the webgui `bus_client.read("<domain>:<view>")`:

- `options:scan` → `ScanResult` (`signals_0dte`, `signals_swing`, `vix_term_structure`).
- `options:captured`, `options:paper_trades`, `options:paper_account` → loose
  display dicts (same shapes the existing Options pages already render).
- `driver:approvals` → `ApprovalState` (`grade`, `grade_reasons`, `conditions`,
  `pnl_today`, `proposed_trades`, `status`, `decision`, `results`, `reasons`).
- `driver:performance` → `PerfReport` (`summary` dict + `trades` list).

Builders treat every field defensively (`.get(...)`, empty-list/dict fallbacks).

## Error handling

Every section degrades **independently**: a missing or empty cache renders a
"No data" note for that section rather than failing the whole report (matches how
the existing pages tolerate sparse/off-hours caches). The page never raises on a
missing cache; "Generate" still produces a valid (if sparse) document.

## Testing

Pure builders are unit-tested in `webgui/tests/test_eod.py` with sample cache
dicts:
- `summary_fragment(snapshot, detail_href)` / `detail_fragment(snapshot)`
- `wrap_document(fragment, css, title)`
- per-section row builders (captured rows, paper rows, scanner rows, driver trade
  rows, perf-by-bucket rows)
- `archive_dates(dir)` (lists/sorts dated subfolders)

`render()` / `render_detail()` stay thin (widgets + wiring) and are smoke-verified
by screenshot. Add the two routes to `test_shell.py`.

## Out of scope (v1)

- Portfolio P&L and market sentiment/rotation sections (deselected by the user).
- Auto-snapshot at market close (manual generation only for v1).
- PDF export, emailing, or any external publishing.

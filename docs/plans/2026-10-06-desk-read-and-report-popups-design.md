# The Desk: Market read and Market report as popups — design

**Date:** 2026-10-06. **Status:** approved by the operator the same day.

## The request

"In the Dashboard I need you to make the Market read and Market report as popup
windows." Three questions settled what that means:

| Question | Answer |
|---|---|
| Which page, and what happens to the two panels? | **The Desk.** The two panels come off the page and two buttons open them. The Market Dashboard is untouched. |
| What does the Market report popup show? | **The full report**, not the five highlights. |
| What kind of popup? | **An in-page dialog**, not a second browser window. |

The design below was then approved as written.

## What changes on the Desk

- The **Market read** card and the **MARKET SUMMARY** frame leave the page. The
  page ends at Headlines.
- Two buttons go in the page header's action row, top right: **Market read** and
  **Market report**. The top, not where the panels were: a popup should not need a
  scroll to the bottom of the page to open.
- Each button has a one-line status to its left, so the page still says something
  with both popups closed:
  - Market read: the head the panel printed, then the count: `12:45 CT · next
    13:00 · 2 tailwinds · 2 headwinds`. It greys when the reading is stale.
  - Market report: which report it is: `Market close report · 5 Oct · 16:20 CT`.

## The two popups

Both are `kit.info_dialog`: a title, a close button, no footer.

- **Market read.** What the panel drew: the use line, the count, the four column
  heads and the six rows. The same painter (`paint_read`) draws into the dialog
  and keeps doing so while it is open, on the same view and the same one-second
  clock, so a reading that stops arriving still greys.
- **Market report.** Which report it is, an "Open in a new tab" link, and the
  report itself in an `<iframe>`. A dialog's content is not in the browser's
  document while it is closed (checked on the page harness: no `iframe` element
  until the button is pressed), so a Desk nobody opens the report on never
  fetches it, and every open fetches it afresh. Nothing has to set or clear the
  address on open and close.

## What is lost, on purpose

- The five highlight points. The operator chose the full report over them.
- The six chips under them (Sentiment, Trend, Bias, Signal, Regime, Bull/Bear).
  Every one restated a reading on the strip at the top of the same page.

`summary_facts` and its chip helpers go with the frame; `report_facts` replaces
it with the three things the button and dialog need.

## Hidden states

- Market read: the button and its status are hidden when `read_hidden` says so
  (switched off, or the public origin and the reading is not marked public). With
  nothing published yet the button stays and the dialog says "No market read yet
  this session", as the panel did.
- Market report: hidden until a report with a usable address is published.

## The report's address

The summary view carries `report_url`, the site's wrapper page. That page is
itself a frame around `reports/latest.html`. The dialog frames the report
directly, so `market_svc` publishes one more field, **`frame_url`**:
`https://<site>/reports/latest.html?v=<report date>-<slot>`.

- The query is a version, not data: the file name never changes, so without it a
  dialog left open across a new report would show the old one under the new
  report's name. A changed address reloads the frame.
- Tier 1 hard-codes no host. It takes the address from the view, and draws it
  only when it is `https://`, the rule the link already follows.
- An older payload with no `frame_url` falls back to `report_url`.
- Checked 2026-10-06: the site sends no `X-Frame-Options` and no
  `Content-Security-Policy` for the report, and the app's own
  `frame-ancestors 'self'` restricts who frames the app, not what the app frames.

## The public Desk

The same module draws it, so it gets the same buttons and dialogs under the same
rules as today (`read_view_shown`, `read_hidden`). The report is the public
site's own page.

## The size ceiling

`desk.render` is at its line and nested-function ceilings. The frame and
`_paint_summary` come out; the popups are built by a module-level
`build_popups(actions)` and painted by module-level `paint_read` /
`paint_report`, wired with lambdas. The ceilings are lowered to the new size.

## Not in this change

- No second browser window.
- No change to the Market read's rules or schedule, or to the report.
- The ticker still reads the highlights from the same view.

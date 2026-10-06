# The Desk: Market read and Market report as popups — implementation plan

**Goal:** replace the Desk's Market read card and MARKET SUMMARY frame with two
header buttons that open them as in-page dialogs; the report dialog shows the
full report.

**Design:** `docs/plans/2026-10-06-desk-read-and-report-popups-design.md`.

**Rules that bind it:** `desk.render` may gain no line and no nested function;
no raw `ui.button` / `ui.dialog` (use `kit`); no inline style; Tier 1 hard-codes
no host; the view is the shared parse and is never edited; tests first.

---

### Task 1: the service publishes the report's frame address

- `shared/contracts/market.py`: `MarketSummary.frame_url: str = ""`.
- `services/market_svc/report_summary.py`: `FRAME_URL`, and `frame_url` in
  `parse_report`'s payload with `?v=<day>-<slot>` when both are known.
- Tests in `services/market_svc/tests/test_report_summary.py`: the address, the
  version changing with the report, no version when the stamp is unknown.

### Task 2: the page's facts

- `webgui/pages/desk.py`: `report_facts(view)` -> `{source, url, frame_url}`
  (https only; `frame_url` falls back to `url`); `read_status(header)`.
- Remove `summary_facts`, the chip helpers and their constants.
- Tests in `webgui/tests/test_desk_popups.py`; remove the chip and highlight
  tests in `test_desk.py` that describe the removed frame.

### Task 3: the popups

- `build_popups(actions)`: the two status labels and buttons in the header's
  action row, and the two dialogs. Module-level.
- `paint_read(popup, view, memo, force=True)` draws into the dialog and sets the
  status; hidden hides the button and its status.
- `paint_report(popup, view)`: status, link, visibility; the frame's address is
  set on open and cleared on hide.
- `render`: drop the two panels and `_paint_summary`; wire with lambdas; lower
  the ceilings in `test_render_size.py`.
- Tests: the page as drawn (buttons present, panels gone, dialog contents,
  hidden cases on both origins, the frame empty until opened).

### Task 4: the words

- `webgui/page_help.py`, the User Guide, Reference Guide, Technical Reference,
  `docs/webgui-routes.md`, `docs/CHANGELOG.md`; rebuild the manuals.

### Task 5: verify

- Every affected suite, lint, type check.
- The page harness: both buttons, both dialogs opened, phone width.

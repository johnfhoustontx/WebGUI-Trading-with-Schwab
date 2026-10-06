# Flow Alerts: the bought / sold estimate as a bar — plan

Design: [`2026-10-06-flow-sides-bar-design.md`](2026-10-06-flow-sides-bar-design.md).
One commit; every step is in `webgui/` or `docs/`.

1. **The builder.** `flow.sides_bar` and `_BAR_PARTS`, `BAR_CHARS`. Tests first
   (`test_flow_page.py`): what fits inside, a sliver goes beside, every share that
   traded is printed somewhere, a part with no volume has no segment, the volume on
   the since-alert bar, a narrower bar fits less, widths are the shares and tones the
   fixed classes, no tally no bar.
2. **The rows.** `alert_rows` stamps the bars, the raw shares and `sides_bought`;
   `followup_rows` the bar and `sides_bought`. None, never zero, without an estimate.
   The words change `unlabelled` to `unknown`.
3. **The table.** `_SIDES_SLOT` and `_FOLLOWUP_SIDES_SLOT` from one `_bar_line`; both
   `sides` columns sort on `sides_bought`.
4. **The Desk.** `flow_estimate_bars`, `_flow_bar`; `_flow_row` draws one line of
   bars under the detail. The existing source tests of that row are re-expressed for
   the bars, not dropped: only a row with an estimate is stacked, and the estimate
   cannot widen its track.
5. **On the harness.** Seed `options:flow_alerts`, `options:flow_sides`,
   `options:flow_followup`; render `options.flow` and `desk`; measure that no segment
   clips its text, how the side text wraps, and the Desk's row height. This step set
   `BAR_CHARS`, the 460 px floor and the Desk's one-line row.
6. **The words.** `page_help.py` (`/options/flow`, `/desk`), the User Guide, the
   Reference Guide, `webgui-routes.md`, the CHANGELOG; rebuild the two manuals. A
   test holds the help to the words on the bar.

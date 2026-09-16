# Desk MARKET SUMMARY from the market report — design

**Date:** 2026-09-16 · **Status:** built, awaiting promote

## Request

"In the Desk Market summary replace it with a summary of the market report, keeping it to the
highlights, up to 5 salient points. Also add a link to the latest report. The separate API calls
for the Market summary can then be discarded."

## Decisions

| Question | Decision |
|---|---|
| Where do the highlights come from? | The published report page, `deploy/site/reports/latest.html`, which already sits in the prod checkout. No new upload, no change to the report tooling. |
| What is a highlight? | The report's **section headlines** (`h2`), in report order, capped at five. Every one is written as a verdict by the report run (REPORT_GUIDE §4), so they are the report's own summary. A report with no sections falls back to its `h1`. |
| Is there a Claude call? | **No.** The report run already paid for the words; summarising them again would be a second call for a paraphrase. |
| Who reads the file? | `market_svc` (Tier 2), on its existing poll loop: a `stat` of `latest.html` + `latest.txt` per poll, a parse only when the stamp changes. Tier 1 keeps reading Redis only. |
| The link | `https://<SITE_HOST>/report.html` — the site page that frames the report — opened in a new tab, drawn only when the URL is https. |
| The six live chips | Kept. The "readings have changed since this was written" line is removed: it compared the chips with the Claude sentence's inputs, which no longer exist. |
| The ticker | Leads with the report's `h1` verdict instead of the Claude sentence. |

## Failure behaviour

- **No report file** → nothing is published; the Desk says "No market report published yet."
- **A page that does not parse** (no `h1`) → nothing is published and the last good summary stays;
  one WARNING, and the stamp is remembered so the same bytes are not re-read every 3 s.
- **`latest.txt` lagging `latest.html`** (the publisher renames them in that order) → the stamp covers
  both, so the next poll republishes with the right slot.

## The contract this adds

The report renderer lives outside this repo (`render.build`). The parser depends on `div.slotchip`
("<label> · <as_of>"), the first `h1`, and each section's `h2`. Recorded in CLAUDE.md beside the
existing `reports/latest.html` name contract.

## Removed

`compute.summary_facts`, `generate_summary`, the fact tables and reply checks, the fingerprint gate
(`SummaryGate`, `summary_due`, `record_summary`, `SUMMARY_MIN_GAP_SEC`, `SUMMARY_DAILY_CAP`),
`_make_summary_client`, `read_summary_packet`, and their tests. `MarketSummary` drops `narrative` and
`inputs` and gains `headline`, `highlights`, `slot`, `slot_label`, `report_date`, `report_url`.
Supersedes the summary half of
[`2026-09-10-desk-market-summary-design.md`](2026-09-10-desk-market-summary-design.md); its Regime
popup and chips stand.

## Follow-up outside this repo

The report collector (`remote_collect.py` on vps2) feeds `app_summary.narrative` from this cache into
each report run. That field is now absent; remove it from the collector and REPORT_GUIDE so a run does
not read the previous report's headline back as "the app's read".

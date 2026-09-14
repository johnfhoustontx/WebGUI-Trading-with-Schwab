# Market report on the public site — design

**Date:** 2026-09-14 · **Status:** built, awaiting promote

## Request

"Create a menu item on the website that points to and displays the latest report. This page
will be updated with the latest report." The website is the public marketing site,
`neuralstrike.co` (operator's choice, over the private app).

## What "the report" is

Five market assessments per NYSE trading day (pre-market 07:44, open 08:53, first hour 09:48,
lunch 11:36, close 15:29 CT), written by scheduled Claude desktop-app tasks on the operator's
Windows workstation. The tooling lives **outside this repo** in `D:\NeuralStrike Reports\tools`
(collector over ssh, renderer, publisher, run guide). Each report is a self-contained HTML
document plus a PDF printed from it.

## Shape

| Piece | Where | Tracked |
|---|---|---|
| `report.html` — site nav + short head + a same-origin `<iframe src="reports/latest.html">` | `deploy/site/` | yes |
| "Market report" nav link | `index.html`, `gallery.html`, `live.html` (glossary keeps its leaf nav) | yes |
| `reports/latest.html`, `reports/latest.txt`, `reports/NeuralStrike-<day>-<n>-<slot>.pdf` | `deploy/site/reports/` on the box | **no — gitignored** |

**Why a frame.** The report carries its own stylesheet, drawn for print. Inlining it would put
two stylesheets over one DOM; generating the whole page around it would move the site nav into a
generator outside the repo, where `deploy/tests/test_site.py` cannot see it. A same-origin frame
keeps the nav tracked and tested and the report whole. It scrolls on its own, so the page runs no
script (pinned). A plain link to `reports/latest.html` sits above it.

**Why gitignored.** Same reason as `live/*.webp`: an untracked upload in prod's checkout makes
`tools/promote.sh` refuse the next promote. The publisher refuses to upload until prod's checkout
reports `deploy/site/reports/latest.html` as ignored, so it cannot run ahead of this promote.

**Freshness without touching Caddy.** Caddy revalidates `*.html` but sends no freshness directive
for `.pdf`, so a re-uploaded `latest.pdf` could be served stale from heuristic cache. The PDF
therefore keeps its dated, slot-numbered name and the (revalidated) `latest.html` links to it.
The publisher keeps the newest 25 PDFs and deletes older ones. `.pdf` joins the served-root
suffix allow-list in `test_the_site_directory_holds_nothing_but_site_assets`.

**The site calls nobody.** The published copy is rendered in a site mode: no Google Fonts link,
the site's self-hosted Inter via `../assets/inter-latin*.woff2`, system monospace for figures.

**Publishing order.** PDF, then `latest.html`, then `latest.txt`, each uploaded as `.part` and
renamed, so a visitor never reads a half-written file or a page linking a missing PDF.
`latest.txt` (`<day> <n> <slot> <as_of>`) lets the publisher refuse to replace a later report of
the same day with an earlier slot's re-run unless `--force`.

## Deliberately not built

- **An archive page** of past reports. Not asked for; the dated PDFs are there if it is.
- **A Caddy `Cache-Control` rule for `/reports/`.** The dated PDF names make it unnecessary, and
  installing a Caddyfile needs sudo on the box.
- **Auto-height frame.** Would need a script; the viewport-sized frame is adequate.

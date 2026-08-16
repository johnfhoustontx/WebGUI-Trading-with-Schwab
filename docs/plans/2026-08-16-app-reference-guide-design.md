# App Reference Guide + manual refresh — design (2026-08-16)

## Problem

The app has grown to 13 rail items across three captioned sections, ~24 routes, and
several pages carrying their own subtab rows. The three existing manuals have not kept
pace:

- The **User Guide** documents an older navigation and never gained the Opportunity
  Board, Flow Alerts, Market Dashboard, Sector & Industry, RRG, Momentum or the
  Strategy Tools group.
- The **Technical Reference** predates `market_svc`, the market-regime display rename +
  direction axis, and the dev/prod environment split.
- The **API Reference** has no `market_svc` (:8215) section and its cache-key index and
  per-service command lists have drifted.

Separately, nothing in the documentation answers the question a user actually asks in
front of the app: *what is this tab for, why does it matter, and when do I open it?*
The User Guide explains **how to operate** a page; `docs/webgui-routes.md` explains
**what a route renders** for a maintainer. Neither explains **significance**.

## Audience

A trader with **moderate** markets and options knowledge. They know what a call and a
put are, what implied volatility means loosely, and what a credit spread is. They do
**not** necessarily know what dealer gamma exposure is, what a relative-rotation graph
shows, or why put/call ratios are read as a contrarian signal.

Consequences for the writing:

- Plain language. Define a term the first time it appears in the guide, in one sentence,
  before using it.
- Keep genuine trader vocabulary (GEX, IV, 0-DTE, credit spread) — the reader is a
  trader — but never assume the *mechanism* behind it is understood.
- **Cite external references** where a concept has a canonical public source (CBOE on
  VIX and put/call ratios, OCC on option settlement, the original RRG literature,
  Investopedia/Wikipedia for standard definitions). The reader should be able to go
  learn the concept independently of this app.
- No hype. Where a signal is weak or a number is known to be unreliable, say so — the
  repo already documents several of these and they belong in the guide.

## Deliverable 1 — the Reference Guide (a fourth manual)

`docs/manuals/reference-guide/reference-guide.md`, built by `build_docs.py` into HTML +
`.docx` like the other three, and registered in `webgui/pages/manuals.py` so it opens
in-app from **More → User Manuals**.

### Structure

1. **Summary page — "The app in one page."** The cohesive opener. It carries:
   - what the app is and the single idea it is built around;
   - the three-tier data flow, stated once in plain language;
   - the rail's three captioned sections reframed as the three questions the app
     answers — *What is the market doing?* (MARKETS) · *What should I trade?*
     (STRATEGY) · *What do I own?* (ACCOUNT);
   - a **workflow map** walking one trading day through the pages in the order a user
     would actually touch them;
   - a one-line-per-page index table with a "reach for this when…" column.
2. **One chapter per rail section** — MARKETS, STRATEGY, ACCOUNT, SYSTEM — with the
   per-page deep dives in rail order.
3. **Appendices** — subtab index; a single whole-app refresh-cadence table; the
   cross-page action map (Send to Calculator / Paper trade / Simulator / Dealer
   Positioning); glossary.

### Per-page template

Applied uniformly so the document is skimmable and a reader learns the shape once:

| Section | Contents |
|---|---|
| **What it is** | One paragraph, no jargon |
| **Where the data comes from** | Owning service, cache key, refresh cadence, off-hours behaviour |
| **Reading the screen** | Panel by panel, column by column, subtab by subtab |
| **Why it matters** | The edge it gives — stated honestly, including where it is weak |
| **When to use it** | Its slot in the trading workflow |
| **Caveats & gotchas** | The documented traps |
| **Related pages** | Cross-links, including hand-off buttons |

## Deliverables 2–4 — refresh the three existing manuals

- **User Guide** — add the undocumented pages, rewrite *The Interface* for the
  2026-08-16 captioned icon rail, refresh FAQ/troubleshooting, cross-link the Reference
  Guide for depth.
- **Technical Reference** — reconcile constants against code, add `market_svc`, the
  regime rename + direction axis, and the dev/prod split.
- **API Reference** — add `market_svc` (:8215), refresh per-service commands/views and
  the cache-key index, document the `:ver`/`:ts` side keys and
  `cache_set(skip_unchanged=…)`.

## Deliverable 5 — CLAUDE.md

Audit and **correct in place** per the file's own maintenance rules. Fix statements
found stale during research; add the Reference Guide to the docs list and to the
"where a fact belongs" table. No new feature narratives — those go to the CHANGELOG.

## Research method

Both stacks are already running (prod `:8500`, dev `:9500`). Research draws on:

- the page modules under `webgui/pages/` and the owning service's `compute`/`handlers`;
- `shared/contracts` for payload shapes and `shared/bus` for cadence semantics;
- `docs/webgui-routes.md`, `docs/CHANGELOG.md`, and the design/plan pairs under
  `docs/plans/`;
- **the running prod app on `:8500`**, navigated read-only, to capture what each screen
  actually shows with live data.

Nothing destructive is clicked in the live app — no scans enqueued, no Terminate, no
Stop All Services.

## Non-goals

- No code changes to the app beyond registering the new manual.
- No redesign of any page. This is documentation of what exists.
- No new screenshots beyond the ones the User Guide already carries, unless a page is
  impossible to describe without one.

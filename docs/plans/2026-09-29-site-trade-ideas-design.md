# Today's trade ideas on neuralstrike.co — design

**Date:** 2026-09-29 · **Status:** approved

## Ask

Show the trades proposed for the day on the public site, and choose where they go.

## Decisions

| Question | Answer | Why |
|---|---|---|
| Which trades? | Only ideas that were actually **posted** (`run_trade_idea` → `sent`) | They are already public on X, Discord and Telegram; the site adds no new disclosure. A skipped hour shows nothing. |
| Where? | A **strip on the home page** under the hero (newest 3) **plus `ideas.html`** (every card, day picker), linked from the nav on every page | Most visible, and a post can deep-link to the page. |
| What does a card look like? | The **posted PNG card itself** — time and symbol above, caption as alt text, click → full size | The site matches the social feed exactly; no second renderer to keep in agreement with the first. |
| History | **Today + the previous 5 posting days**; older day folders deleted | ~35 cards, a few MB on the box. |
| Where does the data live? | `deploy/site/ideas/<day>/…` + `deploy/site/ideas.json`, **gitignored, generated** | Same shape as `live/*.webp` and `reports/`: committed, it would dirty prod and `promote.sh` refuses a dirty tree. |

## Data flow

```
run_trade_idea ── sent ──► site_ideas.publish(SITE_ROOT, idea, png, caption, now)
                              ├─ ideas/<YYYY-MM-DD>/<HHMM>-<SYMBOL>.webp   1200 wide, for the page
                              ├─ ideas/<YYYY-MM-DD>/<HHMM>-<SYMBOL>.png    the full 2x card
                              ├─ ideas.json                                 manifest, atomic write
                              └─ prune day folders not in the manifest
static site: assets/ideas.js ── fetch ideas.json ──► home strip / ideas.html grid
```

- **Manifest** (`ideas.json`): `{"updated": iso, "days": [{"date": "2026-09-29",
  "ideas": [{"time": "14:35", "symbol": "MU", "type": "PCS", "grade": "Good",
  "img": "ideas/2026-09-29/1435-MU.webp", "full": "ideas/2026-09-29/1435-MU.png",
  "alt": "<caption>"}]}]}`, days newest first, ideas newest first. Building it is a
  pure function (`merge_manifest`) so ordering, a same-slot re-post (replaces, never
  duplicates) and the trim are unit-tested without a disk.
- **"Posting days", not a calendar.** A day exists in the manifest only if an idea
  posted that day, so keeping the newest N days needs no holiday list.
- **The manifest sits at the site root, not inside `ideas/`,** so Caddy can give it
  `no-cache` (with the pages) while the images under `/ideas/*` get a lifetime —
  one path, one Cache-Control rule.
- **Pruning deletes only folders named `YYYY-MM-DD`** under `ideas/`, never anything
  else, and never a day still in the manifest.
- **A text-only post (card render failed) publishes nothing** — there is no card to show.

## Rules that keep it safe

- **A site write never blocks or undoes a post.** It runs after the sends, never
  raises, and a failure goes through `_degrade.degraded("options.site_ideas")`.
- **Configurable** (`config/notify.toml` `[site]`): `trade_ideas = true` (the
  switch) and `keep_days = 6`, read at call time through `shared/notify/switches.py`
  and catalogued in `webgui/config_schema.py`.
- **Dev publishes nothing** — dev never posts (notifications are zeroed), and its
  `SITE_ROOT` is its own checkout, which nothing serves.

## Front end

- `assets/ideas.js` — one script, two mounts: `[data-ideas-strip]` (home: newest 3
  of the newest day) and `[data-ideas-page]` (`ideas.html`: day picker + grid).
  Fetches `ideas.json` with `cache: "no-cache"`.
- **The newest day is labelled with its own date.** "Today" appears only when the
  date is today in America/Chicago; before the first post the strip shows the last
  session, called "Monday's trade ideas", never "today".
- Missing or malformed manifest → the strip hides itself entirely; the page says no
  ideas have been posted yet. Never an empty frame.
- `ideas.html` reuses the site frame (nav, footer); **Trade ideas** joins the nav on
  every page between Market report and Glossary, and `sitemap.txt`.
- The page states the schedule: one idea an hour, 08:35–14:35 CT, chosen from the
  live Market Scanner.

## Caddy

- `@revalidate` gains `*.json` (the manifest).
- `@ideas path /ideas/*` → `max-age=86400, must-revalidate`. File names carry the day
  and minute, so a card never changes under its name; a day is the safe lifetime.

## Backfill

`tools/backfill_site_ideas.py` republishes the last `keep_days` days from
`TRADE_IDEAS_DIR` (the `.png` + `.txt` archive already on disk) so the page is not
empty on day one. The archive holds no grade/type, so a backfilled entry reads them
from the filename and caption where it can and leaves them blank otherwise.

## Testing

- `merge_manifest`: ordering, same-slot replace, trim to N days, malformed input.
- `publish`: writes webp + png + manifest into a tmp site root; prunes only dated
  folders; a failure returns False and raises nothing.
- `run_trade_idea`: publishes only when `sent` and a card exists; a publish that
  raises leaves `status == "posted"`.
- `.gitignore` pins `deploy/site/ideas/` and `deploy/site/ideas.json`; the Caddyfile
  test pins both headers.
- Front end: serve `deploy/site` locally over a sample manifest (that is exactly what
  prod does for this tree) and check the strip, the page, the day picker and the
  missing-manifest case.

## Not done

- **How each idea did** (a live mark / P&L per card). That needs Schwab reads and a
  public view — a live-screen feature, phase 2 if wanted.

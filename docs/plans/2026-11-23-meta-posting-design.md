# Facebook and Instagram posting — design

2026-11-23. Every posted trade idea and every market report goes to the
NeuralStrike Facebook Page and Instagram account automatically, each post leading
with a link to its own page on neuralstrike.co.

## Intent

The operator has set up Meta Business Suite so the Facebook Page and the
Instagram account are one place. They want four things posted there without
touching it (the operator, 2026-11-23):

1. every daily trade idea, with a link to the trade,
2. every market report, with a link to the report,
3. a link to the website,
4. the hashtags with the greatest audience reach.

**Why: site traffic** (the operator, 2026-11-23: "Site traffic, build a page per
trade"). A post exists to bring a reader to neuralstrike.co, so every post leads
with its link, and the link must still work when someone finds the post weeks
later.

**What success looks like:** within a minute of a trade idea posting to X, the
same card is on the Facebook Page as a link post whose preview opens that trade's
own page, and on Instagram with the address written out; each market report does
the same with a page for that report; and a link from a month-old post still
opens the trade or report it named.

## Goals

1. **A page per trade** on the site, at a permanent address, showing the card,
   the trade and its result as it develops.
2. **A page per market report** at a permanent address. Today `report.html`
   always shows the latest report.
3. **Automatic posting to Facebook and Instagram** of every posted trade idea and
   every market report, from `options_svc`, beside the X post.
4. **Every post carries neuralstrike.co.** On Facebook the link is the post
   itself; on Instagram, where captions are not clickable, the address is written
   out and the caption points to the link in the bio.
5. **Hashtags from an editable list**, sized to each network's limit.
6. **X uses the new links too**: its trade idea and report posts link the trade's
   or report's own page instead of `ideas.html` / `report.html`.

## Non-goals

- Ad-hoc marketing posts to Meta (X has `/social`'s composer; Meta does not get
  one in this change).
- Blog entries on Meta. They already have cards; they are a later addition on the
  same path.
- Reels, Stories, carousels, or a separate portrait image. The Social Cards
  design chose one 1.91:1 card; Instagram accepts 1.91:1.
- Replying to comments or reading analytics.
- An approval queue. As with X, Discord and Telegram, posts go straight out; a
  dry-run mode, per-kind switches and a kill switch replace a queue.
- Posting ideas from before launch.

## Anti-goals (a failure even if it works)

- **A link that dies.** Any address a post carries must keep working; nothing
  under the new folders is ever pruned.
- **A double post** after a restart or a replayed command.
- **Meta costing another post.** A Meta failure must never stop the Discord,
  Telegram or X post, nor the site publish, and the reverse.
- **A second posting path to Meta.** One module talks to the Graph API.
- **A page that shows what the site withholds.** A per-trade page shows exactly
  what `ideas.html` shows for the same idea, under the same switch
  (`public_scan.show_leg_quotes` / `ledger_result(withhold_single=…)`).

## Constraints (from the project's rules)

- Every outbound post leaves from `options_svc`, as every X post does.
  `market_svc` detects reports and enqueues; it does not post.
- Configurable by default: Graph API version, per-kind switches, hashtag lists
  and caps, the daily cap, the caption wording's tunables — in a `config/*.toml`
  with `config_schema.py` entries. **Credentials and the Page and Instagram ids**
  go in the gitignored `shared/notifications.json` `meta` block, never in config
  or git.
- Never raises on the post path; failures are logged and counted
  (`_degrade.degraded`), never silent.
- The new site folders are generated, gitignored state, like `ideas/` and
  `blog/`; being gitignored puts them in the nightly backup automatically.
- Dev and pytest are muted by the existing notification suppression
  (`shared/notify/channels.load_config`); the suite never reaches Meta (the
  conftest network guard).
- Docs in the same change: `webgui-routes.md` if any private page changes,
  `docs/reference/public-live-screens.md` (the new site folders),
  `page_help.py`, the User Guide and API Reference (the new commands and
  config), the CHANGELOG, and one CLAUDE.md line: "every Facebook/Instagram
  post goes through `meta_post.post`".

## Approaches considered

1. **Straight to Meta's Graph API from `options_svc`, beside the X poster —
   chosen by the operator.** Same safeguards as X, no new vendor, no monthly fee;
   full control of the Facebook link-post shape.
2. A paid posting service (Buffer, Ayrshare). Handles Meta's sign-in, but adds a
   fee, a company holding the accounts' keys, and less control of the post shape.
3. A Zapier/Make job watching a feed. Least code, but it fails where the stack
   cannot see it, and the double-post protection would have to be rebuilt there.

Meta Business Suite's "one post to both" is a feature of its own composer only.
Through the API a Facebook post and an Instagram post are two calls; the result
on the two accounts is the same.

## Part 1 — the permanent pages (ships first)

This part also improves X, and ships and is promoted before Part 2.

### The page per trade

- **Address:** `https://neuralstrike.co/t/<YYYY-MM-DD>-<HHMM>-<SYMBOL>/` — the
  idea's existing file stem with its date (the Social Cards key
  `idea:<YYYY-MM-DD>-<HHMM>-<SYMBOL>` minus the prefix). A folder holding
  `index.html`, as blog entries do, so Caddy's `file_server` serves the clean
  address with no new rewrite.
- **Written by** `site_ideas.publish` at post time, before any post goes out
  (Facebook reads the page's preview tags when the link is posted). A write
  failure leaves the post linking `ideas.html`, as today: a page must never cost
  a post.
- **Folder:** `deploy/site/t/<id>/` — `index.html`, the card as a 1200-wide
  JPEG (the preview image and the Instagram image's lasting copy) and the WebP the
  page shows. Never pruned; gitignored.
- **What it shows:** the card; the trade written out (structure, legs, expiry,
  entry, max risk, max profit, breakeven, probability of profit, grade); the post
  time; the **result** in the same words and figures `ideas.html` uses for this
  idea; a link to all trade ideas and to the site's home. The site's top bar
  (`shared/site_nav`) and its palette. **Source:** the manifest row
  `_publish` builds already holds all of it — `entry_facts` (legs, structure,
  expiry, entry cash, max loss and profit, spot, post time) and `card`
  (`trade_idea_card.card_facts`: breakevens, probability of profit, grade). The
  page is rendered from that row and nothing else, so it can never show a figure
  `ideas.json` lacks.
- **Preview tags:** `og:title`, `og:description` (the caption), absolute
  `og:image` with real width and height, `og:url`, `twitter:card =
  summary_large_image`.
- **The result keeps updating until the idea closes.** While the idea is in
  `ideas.json`, `site_ideas.refresh` rewrites its page when its result changes.
  After it leaves the manifest (`keep_days`, 6 posting days), its result comes
  from the Paper Ledger row that follows every posted idea to its close
  (`idea_ledger.site_rows`, `mark_cycle`), and the page is rewritten when that
  row changes. Once closed, the page is final and never written again.
- **When a page is final** (the terminal rule): its result is closed (target,
  stop or settled at expiry) in the manifest or in the Ledger row; or it has
  left the manifest and has no Ledger row (an idea booked while
  `[idea_trades] enabled` was off). In the second case the page keeps the last
  result it showed, says it is no longer tracked, and is written once more and
  then never again. A page is rewritten only when its rendered result changes.
- **The rewrite is driven by** the existing `site_results_due` cadence
  (`[site] refresh_min`), from the same `refresh_site_idea_results` pass, so no
  new scheduler branch.
- **Index:** a generated `t/sitemap.txt` (all trade pages), on the blog's
  pattern: the tracked `robots.txt` gains a `Sitemap:` line for it, and
  `options_svc` writes it (empty if need be) at start. The tracked
  `sitemap.txt` is never written by the app (that would dirty prod's checkout
  and block a promote).
- **Serving:** `file_server` already serves any folder under the site root, as
  it does `blog/`. The Caddy generator gains `Cache-Control` rules for `/t/*`
  and `/r/*` (pages revalidate, images get the `ideas/` lifetime). This is a
  Caddyfile change, so the promote is followed by
  `generate_caddyfile.py --install`, run by the operator.
- **Facebook's preview is the card as posted.** Facebook caches its first read
  of the page; the preview is not re-read when the result changes. That is
  intended: the preview shows what was posted, the page shows how it went.

### The page per report

- **Address:** `https://neuralstrike.co/r/<day>-<n>-<slot>/` — the report's own
  stamp (`latest.txt`'s `"<day> <n> <slot> <as_of>"`, minus the as-of), the
  same stamp the dated PDF names carry.
- **Written by `options_svc`**, in the `x_post_report` handler, before any
  post: `market_svc` is unchanged and still only enqueues. The handler already
  has the report payload and already draws its card (`report_card`, which imports
  `options_svc`'s `card_kit` and `trade_idea_card`, so it stays where it is). It
  writes a lasting copy of `latest.html`, a page around it built the way
  `report.html` frames `latest.html`, and the card as a JPEG, all with the same
  preview tags as a trade page. X and Meta then post the new address.
- **The copy is the right report:** the handler copies `latest.html` only when
  `latest.txt`'s stamp equals the command's stamp. If a newer report has replaced
  it, no page is written and the posts link `report.html` (that report is past
  its age gate anyway).
- **Idempotent:** a folder that already exists for the stamp is not rewritten, so
  `market_svc`'s re-enqueue on every restart writes nothing. A failed write
  falls back to `report.html`; it never stops a post.
- **Folder:** `deploy/site/r/<stamp>/`, with an `r/sitemap.txt` like the trade
  pages'. Never pruned; gitignored.

### X follows

`trade_idea.x_text` and `run_x_post_report` link the new pages when they exist,
and the old addresses when they do not.

## Part 2 — the Meta poster

### `shared/notify/meta_post.py` — the one path to Meta

Modelled on `shared/notify/x_post.py`:

- `post(bus, *, kind, caption_fb, caption_ig, link, image_url, now, config,
  meta) -> {ok, facebook: {...}, instagram: {...}, dry_run}`. Each network is
  attempted on its own; one failing does not stop the other.
- **Facebook:** a **link post** — `POST /{page-id}/feed` with `message` and
  `link`. The preview (the card, from the page's `og:image`) is clickable and
  opens the trade's or report's page.
- **Instagram:** `POST /{ig-user-id}/media` with `image_url` (the JPEG on the
  site) and `caption`, then `POST /{ig-user-id}/media_publish` with the returned
  container id. If the container is not ready, poll its `status_code` a few
  times; an unfinished container is a failure, never a retry of the publish.
  The two steps are recorded separately: a failed `media` call is a failure (no
  post can exist), while a `media_publish` that times out or errors with a 5xx
  is `unknown`, logged with its container id, and never retried, so a retry
  cannot make a second post.
- **Never raises.** Every attempt, refusal and dry run is logged to
  `cache:options:meta_log` (last 100) and appended to a `meta_posts.jsonl` on
  disk, with the network, kind, outcome, the post id or the error.
- **`KINDS` allow-list:** `trade_idea`, `report`.
- **Switches:** `enabled`, `dry_run`, per network (`facebook`, `instagram`)
  and per kind. Ships `enabled = false` and `dry_run = true`.
- **Daily cap** per network, a Redis counter keyed by the Central day (Instagram's
  API allows 50 posts a rolling 24 hours; 10 ideas and 5 reports a day sit well
  under it).
- **An answer that is neither yes nor no** (a timeout, a 5xx) is `unknown`: it
  counts as posted for dedup and is never sent again, as X's unconfirmed post is.
- **Credentials:** the `meta` block of `shared/notifications.json` — the Page
  access token (a non-expiring System User token from Business Manager), the Page
  id and the Instagram business account id. A token Meta rejects as expired is
  logged with that reason, so it shows on the log rather than as silence.

### Where it is called

- **Trade ideas:** in `run_trade_idea`, after the X post, through a
  `_post_trade_idea_meta` twin of `_post_trade_idea_x`, wrapped so nothing it
  does reaches the other sends. Its outcome is added to `cache:options:trade_idea`
  as `meta`. **Dedup key:** the idea's id, `<YYYY-MM-DD>-<HHMM>-<SYMBOL>`, held
  in `cache:options:meta_ideas` (the last 100) per network; an id already there
  as posted or `unknown` is not sent again. (The scheduler's in-memory
  `trade_idea_ran` set is lost on a restart, so it is not enough on its own.)
- **Reports:** in `run_x_post_report`'s path, after the X post, with its OWN
  posted-report memory (`cache:options:meta_reports`), so a Meta retry never
  reposts to X and a failed X post never blocks Meta. Same 45-minute age gate.
- Both are covered by the existing replay guards (`_REPLAY_GUARDED`).

### Captions

- **Facebook:** `Trade idea: SPY Bull Put Spread · …` (the existing
  `trade_idea.caption`), then the hashtags. The link is the post, so it is not
  repeated in the text. A report: `<slot label>: <verdict headline>`, then tags.
- **Instagram:** the same line, then `Full trade: neuralstrike.co/t/<id>` (or
  `Full report: neuralstrike.co/r/<stamp>`) written out, then `Link in bio.`,
  then the hashtags. ≤ 2,200 characters.
- **The website link** (goal 4) is in every post: it is the Facebook link post
  and the Instagram caption's address. The operator sets the Instagram bio link
  to `neuralstrike.co/ideas.html` by hand (one-time setup, below).

### Hashtags

Instagram allows **5 hashtags per post** since December 2025 (Instagram, as
reported by Social Media Today; some guides still say 30, so the cap is config,
not code). No Meta API reports a hashtag's reach, so the list is researched and
edited by the operator, not computed.

Starting list (third-party post counts, 2026; big tags paired with a niche one,
the usual advice for reach):

| Network | Tags |
|---|---|
| Instagram, trade idea | `#<SYMBOL>` (derived) · `#optionstrading` · `#stockmarket` · `#trading` · `#options` |
| Instagram, report | `#stockmarket` · `#trading` · `#stocks` · `#investing` · `#SPY` |
| Facebook, either | `#<SYMBOL>` or `#stockmarket` · `#optionstrading` (2 — hashtags do little on Facebook) |

Instagram does not index `$` cashtags, so a symbol is `#SPY`, not `$SPY`; an index
symbol drops its `$` (`$SPX` → `#SPX`). Per-network caps (`max_tags`) and lists
live in config. The X lists are unchanged.

### One-time setup the operator does (Claude cannot enter credentials)

1. Create a Meta developer app (Business type) owned by the business.
2. In Business Manager, a System User with the Page and the Instagram account as
   assets; generate its token with `pages_manage_posts`,
   `pages_read_engagement`, `instagram_basic`, `instagram_content_publish`.
   Posting to the business's own assets works with the app in development mode;
   no App Review.
3. Put the token, Page id and Instagram account id in `shared/notifications.json`
   on vps2.
4. Set the Instagram bio link to `neuralstrike.co/ideas.html`.
5. Run a day on `dry_run`, read the log, then turn it on.

## Calls made by Claude, not the operator

- Part 1 ships and is promoted before Part 2.
- The address shapes `/t/<date>-<HHMM>-<SYMBOL>/` and `/r/<day>-<n>-<slot>/`.
- A Facebook **link post**, not a photo post with a link in the text: the whole
  preview is clickable, which serves traffic. (Photo posts are said to reach
  further; the goal is clicks.)
- The per-trade page keeps its result current through the Ledger after the idea
  leaves `ideas.json`.
- The 1200-wide JPEG on the page doubles as the Instagram image; the 2400-wide
  PNG and JPEG stay under `ideas/<day>/` and are pruned as today. Growth: about
  10 trade folders a day at roughly 150 KB each — well under 1 GB a year.
- Meta gets its own report-dedup memory rather than sharing X's.
- Ships off and in dry run.
- The hashtag lists above.

- `options_svc` writes the report pages, not `market_svc`, so the card, the page
  and the posts happen in one handler in order.
- The terminal rule for a trade page, its rewrite on the existing results
  cadence, and the generated `t/` and `r/` sitemaps.
- Facebook's preview stays the card as posted.
- Meta's dedup memories, per network: `cache:options:meta_ideas` and
  `cache:options:meta_reports`.

## Left to the builder

- The per-trade page's markup and its sharing with `ideas.js`'s wording.
- The Graph API version (config, current at build time).
- The exact `Cache-Control` lifetimes for `/t/*` and `/r/*`.

## Checks the builder must make

- Post one trade page link into Facebook's Sharing Debugger and confirm the
  preview is the card at full width.
- On the first live post, look at both accounts and record the result in the
  CHANGELOG: the Facebook preview, the Instagram image's crop, the caption.
- Prove a restart and a replayed command post nothing twice, against a fake Graph
  API.
- Prove a Meta outage leaves the Discord, Telegram, X and site paths unchanged.
- Confirm the per-trade page and `ideas.html` show the same result for the same
  idea, with the quotes switch on and off.
- Confirm Instagram's current hashtag cap in its own composer before turning
  Instagram on.

# X posting — market reports, hourly trade ideas, ad-hoc marketing — design

**Date:** 2026-09-22 · **Status:** approved, not built

## Ask

Post three kinds of content to the NeuralStrike X account:

1. the five daily market reports,
2. the hourly trade ideas,
3. ad-hoc marketing material,

each carrying popular hashtags so the posts surface in similar feeds.

## Decisions

| Question | Answer | Why |
|---|---|---|
| Approval before posting? | **No** — automatic posts go straight to X | Matches the Discord/Telegram trade-idea post; a log + kill switch + per-kind `enabled` replace a queue. |
| Report post shape | Branded 1200×675 card + `"<slot> market report: <verdict>"` + link | A card reads far better in the feed than a bare link preview; a thread spends the daily allowance. |
| Ad-hoc authoring | A new private page `/x` (More → Post to X) | Usable from the phone; no ssh. |
| Old per-signal X poster | **Removed** (`notify_twitter`, `twitter_signal_text`, `send_twitter`) | The hourly trade idea is the curated feed; the old one would compete for the same daily allowance. Its credentials carry over. |
| Hashtags | Per-kind config list + derived tags (cashtag, `#0DTE`), capped by `max_tags` | X indexes cashtags like hashtags; X's own guidance favours few tags, so the cap is tunable. |

## 1. `shared/notify/x_post.py` — the one path to X

`post(text, image_png=None, *, kind) -> {ok, id, url, error, dry_run}`

- Media: v2 `POST /2/media/upload` (tweepy's `media_upload` is the retiring v1.1
  endpoint), then v2 `POST /2/tweets` with `media.media_ids`. OAuth 1.0a user
  context via `requests-oauthlib` (already locked as tweepy's dependency).
  **Verify the upload endpoint with a real account before enabling.**
- Never raises. Every attempt — including dry runs and refusals — is logged to
  `cache:x:log` (last 100) and appended to `x_posts.jsonl` on disk.
- Config: an `x` block in `notifications.json` — `enabled`, `dry_run`,
  credentials, `daily_cap` (default 15; the free tier is ~17/24 h — check the
  account's tier), `kinds.<kind>.enabled`, `hashtags.<kind>`, `max_tags`. The old
  `twitter` block is read as a credential fallback only. Dev is already muted by
  `allow_notifications` (the recursive `enabled` zeroing).
- The daily cap is a Redis counter keyed by CT day; a refusal is logged with its
  reason.

**Invariant:** every X post in the repo goes through `x_post.post`. Nothing else
imports tweepy or calls the X API.

## 2. Text and hashtags

- `fit_text(body, link, tags)` — X weighted length: a URL counts 23, the result
  ≤ 280. Tags go last and are dropped from the end before the body is truncated.
- Defaults — reports `#stocks #StockMarket #trading` + `$SPY $QQQ`; trade ideas
  `#options #optionstrading #trading` + `$<SYMBOL>` (+ `#0DTE` for DTE ≤ 1);
  marketing `#options #trading`. `max_tags` default 4, counting cashtags.
- Tags are de-duplicated case-insensitively and normalised to a leading `#`/`$`.

## 3. Market reports (`market_svc`)

- The scheduler already watches `report_summary.report_stamp`. A new stamp also
  triggers a post: `report_card.py` draws the card (verdict `h1`, slot chip, up to
  4 section `h2`s) with `card_kit`-style Pillow drawing, then `x_post.post`.
- Dedup: the posted stamp is remembered in Redis (`cache:x:report_posted`), so a
  restart cannot repost. A report whose `as_of` is older than
  `report_max_age_min` (45) when first seen is skipped and logged.

## 4. Hourly trade ideas (`options_svc`)

- `run_trade_idea` already renders the PNG. After the Discord/Telegram sends it
  calls `x_post.post(trade_idea.x_caption(idea), png, kind="trade_idea")`,
  wrapped so an X failure can never affect the other sends. The outcome is added
  to the `cache:options:trade_idea` result as `x`.

## 5. Ad-hoc page `/x` (private app only)

- Text box with live weighted count, editable tag chips pre-filled from
  `hashtags.marketing`, an optional image (upload, or pick from the gallery/live
  shots, converted to PNG), a preview, and a confirm-gated **Post**.
- Tier 1 never calls X: Post enqueues `{"type": "x_post", ...}` on `cmd:options`
  (image as base64, size-capped) and the page reads the outcome from
  `cache:x:log`. `x_post` is replay-guarded like the other side-effectful
  commands (`_is_stale_side_effect`).
- The page lists recent posts from all three sources with status, link and
  reason. It is **not** a public live screen.

## 6. Testing

Text fitting, hashtag assembly/cap, the daily cap, report dedup and staleness,
the report card over a fixture `latest.html`, the trade-idea path with an X
client that errors and one that raises. First prod run is `dry_run: true`; read
the log for a day, then enable.

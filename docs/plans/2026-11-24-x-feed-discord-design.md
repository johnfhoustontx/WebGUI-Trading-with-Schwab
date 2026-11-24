# The X Feed to Discord — design

**Date:** 2026-11-24 · **Status:** built on `claude/x-feed-discord`, not promoted

## Ask

"Add the X newsfeed to Discord." The operator then pointed at the feed's own
source, FxEmbed (github.com/FxEmbed/FxEmbed): instead of building our own
message from a post, post the post's link on an FxEmbed host and let Discord
draw it.

## Decision

Each new post goes to the channel as ONE plain message holding its link,
`https://fixupx.com/<author>/status/<id>`. FxEmbed answers Discord's link
preview with the whole post: text, photos, video, the quoted post. So:

- nothing of the post's text is sent, so there is nothing to escape or trim;
- `allowed_mentions` is empty, so no link can ping anyone;
- if FxEmbed is down, Discord shows a bare link, which still opens the post.

Rejected: a Discord embed built from the stored post. It needs media handling,
length limits and escaping, and still looks worse than FxEmbed's own card.

## What goes

- **Live posts only** (`origin == "live"`: page one of a recent poll). Never an
  account's baseline when it is added, never a catch-up after downtime: a burst
  of hour-old links is noise, and a restart must not replay the morning.
- **A group rule** with the `[public]` rule's shape (`groups`, `ungrouped`),
  shipped `["News"]`. `groups.py` now holds the rule for both.
- **Types** `post` and `reply` (a self-thread reply), not `repost` as shipped.
- **At most `max_per_poll` (10) from one account's poll**, oldest first; the
  rest are recorded as skipped.

## Once only

`discord_sent` in `xfeed.db` (a new table, kept apart from the Node schema):
one row per status, keyed on the status id ALONE, so two watched accounts
reposting the same post send it once. The row is written before the post is
queued, so a status is offered to Discord at most once whatever happens after.
A post still queued at shutdown is not sent later: a late link is worse than
none. Rows older than `keep_days` (30) are pruned, at most once an hour.

## Sending

One daemon thread, `send_gap_s` (2 s) apart: a webhook may post about 30
messages a minute, and the feed averages about 95 posts an hour across every
account. A 429 waits Discord's `retry_after` (JSON, then the `Retry-After`
header; capped at 60 s), a 5xx or no answer waits 1, 2, 4 s, each up to
`max_retries` (3). Another 4xx (a deleted webhook is a 404) fails at once. A
failure is counted (`_degrade`, area `xfeed.discord`).

⚠ The webhook URL holds its token, and a `requests` exception's text holds the
URL. Only an exception's TYPE is logged or stored
(`test_the_webhook_never_reaches_the_record_or_the_log`).

## The webhook

`routes.x_feed.discord` in `shared/notifications.json`, resolved by the new
`channels.discord_route_only`. Unlike `discord_target` it never falls back to a
legacy key or the global webhook: an install with only the signal webhook set
would otherwise pour a hundred links an hour into the signal channel. It is
also empty when the master `enabled` is off (always so outside prod and under
pytest) and when the `x_feed` Discord switch in `config/notify.toml` is off.

Three gates, then: the webhook set, `[discord] enabled` (Settings →
Configuration → X feed → Discord, shipped off), and the per-category switch
(Settings → General). `x_feed` joins `ROUTE_CATEGORIES`; its notify row has
Discord only, so the General grid draws no Telegram box for it.

## Not done

- Telegram. The ask was Discord, and FxEmbed's link preview is Discord's.
- A per-account choice; groups are the unit, as for the public site.
- Not verified that Discord unfurls a link posted by a webhook: it should, as
  for any message, but the first live post is the proof.

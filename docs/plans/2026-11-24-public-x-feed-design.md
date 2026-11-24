# The X Feed on the public site — design

**Date:** 2026-11-24 · **Status:** built on `claude/add-x-feed-website-ca6ced`, not promoted

## Ask

"Add the X feed to the website." The 2026-11-13 X Feed build listed "X posts on
the public `live.` screens" as a non-goal; the operator now wants them there.

## Decision (operator's choice)

Publish **chosen account groups**, all of them as shipped, narrowed later in
Settings → Configuration → X feed → Public site with no code change. Rejected:
reading the private views (no way to keep an account off the site), and shipping
with nothing published.

## Shape

- **Service.** `xfeed_svc/handlers.publish_public` writes a second pair of views,
  `cache:xfeed:latest_public` and `cache:xfeed:accounts_public`, on every
  `publish_all`. Only the accounts `config/xfeed.toml [public]` names
  (`enabled`, `groups` with `"*"` for every account, `ungrouped`); an empty set
  is no rows, never the whole feed. The accounts payload drops `last_error`,
  the backoff fields and the poll schedule. Same as the news feed: the service
  decides what may be published, the page reads only the result.
- **Page.** `xfeed.render()` hands off to `pages/xfeed_live.py` when
  `shell.is_public()` — the process, never a screen kwarg. That builds the
  private `XFeedPage` with `_may_enqueue` False and `public=True`: the two
  public views, no filters, no Load older, no account controls, no Settings or
  Symbol links. Same painters, so the two origins cannot drift.
- **Navigation.** `pages/news_strip.py` draws Headlines | X Feed on both public
  news pages (the public origin has no shell strip), as `strategy_strip` does
  for the Strategy Tools. The Live Desk gets its own X Feed tool card.

## Route

`/x-feed`, not `/news/x`: the live screens share the private app's global
route table, so a public route equal to a private one is a hole in the auth
sweep (`test_auth_covers_every_route.py` holds that set closed at `/desk`,
`/sentiment`, `/news`). The strip still names the private route and
`shell.route_for` maps it.

## Guards

- The `live` Redis user reads `~cache:*`, so the private pair is readable there;
  the code is the only guard. `webgui/tests/test_xfeed_live.py` renders the
  public page against a bus holding a different post under each key and asserts
  only the public pair is read, watched and drawn.
- The page's one command site, `xfeed._enqueue`, opens with
  `if not _may_enqueue: return`, the form `test_live_commands.py` requires of a
  published page.

## Not done

- Client-side filters on the public page (the private filters are service
  queries, which the public origin cannot send).
- A per-account public flag; groups are the unit.

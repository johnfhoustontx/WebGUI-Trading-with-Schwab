# The "new" tag on the site's Blog menu item

**Date:** 2026-11-24 · **Status:** built

## The request

When a Blog entry is published, show a small glowing "new" at the top right of
**Blog** in the site's menu for two days, sloping downwards. The operator chose
the look from three mockups: the app's own Option Signals "new" badge (blue
`#1565c0`, white lowercase "new", 9px, a box with rounded corners), kept as a box,
tilted and with a glow around it.

## Where the menu is drawn

| Surface | Drawn by | Can it run a script? |
|---|---|---|
| The six tracked site pages | the files themselves | yes, each already loads the market glow |
| A trade idea's page (`/t/<id>/`) | `options_svc/trade_pages.py` | yes, the same scripts |
| A report's page (`/r/<id>/`) | `report_pages.py`, from `report.html` | yes, the template's own scripts |
| A Blog entry's page | `blog_svc/sitewriter.entry_page` | no, by rule |
| A framed document (entry, report) | `site_nav.inject_into_document`, once | no |
| The live screens | `site_nav.site_nav_html` in `ui.html` | no |

## Decisions

1. **The window is decided once, by the writer.** `blog.json` gains `new_until`:
   the newest FIRST publication plus `[site] new_badge_hours` (48), in UTC. Both
   readers compare a clock with that string and hold no number of their own, so
   the browser and the server cannot disagree about the window, and the setting
   lives in one config key (Settings → Configuration → Site blog). A revision
   does not count, the same rule that keeps a revised entry in its place in the
   list.
2. **The site's pages decide in the browser** (`assets/blog-new.js`), because
   they are static and must lose the tag at the 48-hour mark with no rebuild. The
   script follows the market glow's rules: one same-origin request, data never
   parsed as markup, any failure leaves the menu as it was. A page left open
   loses the tag at `new_until`.
3. **The live screens decide at page build** (`site_nav_html(mark_new=True)` asks `site_nav.blog_is_new`, reading the
   same `blog.json` from disk; the live process is on the same box). Their menu is
   a `ui.html` string with no script. A screen left open keeps the tag until it is
   reloaded: accepted, a wall screen is not where a reader finds the Blog.
4. **No tag on an entry's page or in a framed document.** The entry page runs no
   script by rule, and a framed document gets its bar once, when written, so a
   tag baked in would outlive its window. On an entry's page the reader is already
   in the Blog.

## Not done

- Browser check on the real site: the site's script and stylesheet were checked
  in a local page; the first real check is the next published entry.

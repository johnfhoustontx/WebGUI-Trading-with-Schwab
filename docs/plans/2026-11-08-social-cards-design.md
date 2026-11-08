# Social cards — design

2026-11-08. One 1200×630 card for every blog post and every posted trade idea,
gathered on one private page from which the operator posts.

## Intent

The operator is starting to post to Instagram (and, later, Facebook). Instagram
will one day post for them automatically; until then they post by hand. They want
**one place and one method** for sourcing every post, not "save this one from
Telegram, long-press that one on the site".

So every post that leaves NeuralStrike — a blog entry, an hourly trade idea — gets
one card, and the SAME image is what X, Discord, Telegram, the site's link
previews, Instagram and Facebook all show. A private page, **Social Cards**, lists
them all with the buttons to post them, and absorbs the existing Post to X page.

**What success looks like:** a new blog post or posted trade idea appears on
Social Cards within a minute; the operator downloads its JPEG, copies its caption,
and posts it to Instagram from that one page; and a link to a blog post pasted
anywhere previews as its card.

## Goals

1. **A blog card** for every published blog entry, drawn when it is published
   (and for existing entries on the next site rebuild).
2. **A new trade idea card** at 1200×630 that **replaces** today's 1200×675 card
   everywhere it goes now: X, Discord, Telegram, and the site's home strip and
   `ideas.html`.
3. **Both cards are Instagram-grid-safe**: everything essential sits inside the
   middle 3:4 strip (x 364–836 of 1200, full height), so the profile grid's crop
   reads on its own. The side wings carry only what the feed may show and the
   grid may lose.
4. **The blog card is the post page's preview image**, and is shown on the page.
5. **Social Cards** (`/social`), a tab in Account → More: every card, newest
   first, blog and trade idea together, each with **Download** (the JPEG),
   **Copy caption**, and **Post to X**.
6. **Post to X is absorbed**: its composer and its Recent posts table move onto
   Social Cards; `/x` forwards to `/social`; the More strip becomes
   **EOD Report · Blog · Social Cards · User Manuals**.
7. **The past gets cards too, for the site only** (the operator, 2026-11-08: "the
   social cards from the past will not be posted, but for the look and feel of
   the site generate the cards"). Every blog entry already published, and every
   trade idea still on the site, is redrawn in the new card, so the site shows one
   card shape throughout. These backfilled cards are marked as such and **Social
   Cards does not list them**; it lists only cards drawn for a post made after
   launch.

## Non-goals

- Posting to Instagram or Facebook automatically. (The page should not make that
  harder: a card has a stable identity that a future "posted to Instagram" mark can
  hang on.)
- A separate portrait image for Instagram. The operator chose one image.
- Posting anything from before launch. Past blog entries and past trade ideas get
  cards for the site's look (goal 7) but are never offered for posting.
- Cards for shadow ideas (never posted) or the daily condor (never posted).
- A card for the market reports' X posts.

## Anti-goals (would be a failure even if it works)

- The grid tile showing an anonymous slice: a cut-off headline, a chart fragment
  with no ticker.
- Two sourcing methods for posts.
- A trade idea's card on X differing from the one on Social Cards.
- The new card dropping any figure today's trade idea card shows.

## The cards (approved from mockups, 2026-11-08)

The approved mockups are the HTML in this design's session; their geometry is
restated here so the builder needs nothing else.

**Blog card — Nocturne (the public site's palette).** Ground `#161826`, text
`#e9e9ed`, accent `#9184d9`, muted `#9a9bb0`, Inter.

- *Middle strip, centred:* the lockup (THE FLIP mark + `NEURALSTRIKE`, tracked
  `.2em`); `BLOG · NOV 8, 2026` in the accent, small caps, tracked; the **title**,
  ~50px, centred, its second half in the accent; the **summary**, ~20px, muted,
  centred.
- *Wings:* the mark drawn very large and faint (~7% opacity), cut by each edge;
  `neuralstrike.co/blog` bottom-left; the entry's **tags** as outlined chips
  bottom-right.

**Trade idea card — the app's blue (today's card's palette and figures).**
`card_kit`'s ground, tiles and tones; brand blue `#6b86ff`.

- *Header:* lockup top-left, `TRADE IDEA` centred in blue, date and time
  top-right.
- *Middle strip:* the **ticker** very large (~96px), `Strategy · $price` beneath,
  the bias chip, the **payoff at expiration** chart, the **grade** tile
  (`Good … 70 / 100`).
- *Left wing:* the **legs** (side pill, quantity, strike, PUT/CALL), the expiry
  and DTE, the **breakeven** tile.
- *Right wing:* **max risk**, **max profit**, **probability of profit**, stacked.
- *Foot:* "Per contract, at expiration, after commissions." and
  `neuralstrike.co`.

Both are drawn with Pillow at 2× (as `trade_idea_card` is today), so the files are
2400×1260 — the same 1.91:1 shape. The `og:image:width`/`height` tags state the
file's real size.

## Where each card goes

- **Blog:** `blog_svc` draws the card when it writes an entry into the site, and
  writes it beside the entry (`deploy/site/blog/<slug>/`, generated, gitignored
  like the rest of the blog). The entry page (`sitewriter.entry_page`) gains
  `og:image` (absolute), `og:image:width`, `og:image:height`, `og:image:alt`
  and `twitter:card = summary_large_image`, and shows the card at the top of the
  page. Unpublishing removes the card with the entry. An entry whose card cannot
  be drawn is still published — a card must never cost a post.
- **Trade idea:** `trade_idea_card` draws the new layout; `push_notify` sends it to
  X, Discord and Telegram exactly where it sends today's card;
  `site_ideas.publish` stores it as it stores today's. The PNG still goes to
  X/Discord/Telegram; the JPEG is for Instagram and Facebook.
- **Both** also save a JPEG (Instagram's posting interface accepts only JPEG).

## Social Cards (`/social`)

A Tier-1 page in the More group, replacing Post to X.

- **Cards**: one row per card, newest first, blog and trade idea mixed — the card
  image, its kind and time, its title (blog) or ticker and strategy (idea), and:
  - **Download** — the JPEG, as a file.
  - **Copy caption** — the trade idea's caption is `trade_idea.caption` (what X
    gets today); a blog entry's is its title, summary, and its address written out
    (Instagram does not link captions), plus its tags as hashtags.
  - **Post to X** — fills the absorbed composer with the card, the caption and the
    link, scrolls to it, and posts through the existing confirm dialog and the
    existing `x_post` command. Nothing posts without that confirm.
- **Composer** and **Recent posts** — Post to X's, unchanged in behaviour.

## Constraints (from the project's rules)

- Tier 1 reads Redis and serves no engine. The card list reaches the page as a
  cache view; image bytes reach the browser through a private route that reads
  only files the card list names (the blog preview routes are the pattern).
- `blog_svc` and `options_svc` are separate processes and must not import each
  other; drawing shared by both (the lockup, Inter, the 2× canvas) moves to a
  `shared/` module, and the Tier-1 allow-list is unaffected because Tier 1 draws
  nothing.
- Configurable by default: JPEG quality, how many days of cards the page lists, the
  title's size range, the caption's hashtag count — in `config/*.toml` with
  `config_schema.py` entries.
- Never raise on the post path: a card that fails falls back to today's behaviour
  (the text post; the entry without a card).
- Docs in the same change: `webgui-routes.md` (`/social`, `/x` gone),
  `reference/blog.md`, `page_help.py`, the User Guide and Reference Guide, the
  CHANGELOG.

## Calls made by Claude, not the operator

- The title's accent falls on its second half automatically.
- A title that will not fit the strip shrinks, then is cut with "…".
- **How a past trade idea is redrawn.** The site's manifest row keeps the legs,
  type, expiry, entry cash, max loss and profit, the spot and the post time; its
  `alt` is the posted caption, which holds the probability of profit; the Paper
  Ledger's row for that post (`idea_posted`) holds the grade score and the
  breakevens. A one-off tool (`tools/`) redraws each idea from those, writes the
  PNG, JPEG and WebP over the old ones, and repoints nothing (the file names stay).
  An idea for which any printed figure cannot be found **keeps its old image**:
  the card never shows a dash where the posted card showed a number. From launch
  on, the manifest row also stores everything the card prints, so a later redraw
  needs no other source.
- Existing blog entries get their cards on the first rebuild after launch.
- The card is shown at the top of the blog post's page.
- 2× files (2400×1260) rather than 1200×630 files.

- **How far back the page reaches** is what the site already keeps: trade ideas
  for `switches.site_settings()["keep_days"]` (6 as shipped) — the JPEG lives and
  is pruned beside the PNG under `deploy/site/ideas/<date>/` — and every
  published blog entry. No new store.
- **A card's identity is a key convention only**, nothing persisted now:
  `blog:<slug>` and `idea:<YYYY-MM-DD>-<HHMM>-<SYMBOL>` (the idea's existing file
  stem). The future Instagram mark hangs on it.
- **Two cache views merged on the page**: `blog_svc` publishes its entries' cards,
  `options_svc` the posted ideas' cards (derived from what `site_ideas` keeps);
  each row carries the key, kind, time, title, caption, address and the two
  site-relative file paths. Tier 1 merges and sorts.
- **The services write both files at publish time** (PNG and JPEG); Tier 1
  converts nothing and draws nothing.
- **Post to X from Social Cards attaches the PNG** — the same bytes the automatic
  trade idea post sends, so a card on X is identical whichever way it got there.
  Today's 2× trade idea PNG is ~125 KB, far under X's 5 MB; the page refuses, as
  the composer does today, anything over it. **Download gives the JPEG.**
- **A blog card is drawn when missing or when the entry's title, summary, tags or
  date changed** (a digest of those beside the card), so a rebuild is idempotent
  and a failed card is retried on the next one. An entry with no summary or no
  tags draws without that element; the title is required to publish already.
- **`/x` becomes a redirect to `/social`** in `main.py`; its More tab, its
  `page_help` entry and its route-list rows go, `/social` takes them.

## Left to the builder

- The private image route's name and shape.
- How the composer is prefilled (state, not a new command).

## Checks the builder must make

- **The site's ideas strip and `ideas.html` must lay out the 1.91:1 card** (an
  `aspect-ratio` that assumes 16:9 would letterbox or crop it), and still lay out
  a 16:9 one for any past idea the redraw had to leave alone. Likewise the blog
  post page's new top image.
- **Run the past-idea redraw against a copy of prod's site tree first** (the E:
  drive backup) and look at every redrawn card beside its original: each figure
  must match what was posted.
- **Look at the new trade idea card as each channel shows it** — X's timeline,
  Discord's embed, Telegram's photo — on the first live post, and record the
  result in the CHANGELOG. They re-encode; the grid-safe claim is about
  Instagram, but a channel cropping the wings would lose figures.
- Inter is vendored at `deploy/site/assets/inter-latin.woff2` and is what
  `trade_idea_card.font()` already loads (it rendered in this design's mockup
  session); the shared drawing module keeps its fallback to `card_kit`'s fonts.

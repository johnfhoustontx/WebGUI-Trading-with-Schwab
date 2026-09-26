# Market news feed — free public sources, one service, three private readers and one public screen — design

**Date:** 2026-09-25 · **Status:** approved, not built

## Ask

Show market news inside the app: the headlines, SEC filings and insider buys a
trader watches during the day, tagged by ticker, newest first — on its own page,
on the Desk, on the Symbol dossier, and as a public screen reached from the
website's Tools menu. Source it from **free public feeds only**: no paid news
API, no X reads (parked; X's API is pay-per-read and browser automation is a
suspension offence), no scraping of anyone else's product.

## What the reference site taught us

`stocktradernetwork.com/portal/newsfeed` — the operator's reference — is a
React app (`news.stocktradernetwork.com`) over an open JSON endpoint. Traced
on 2026-09-25, 200 items: every source it shows except one is a **free public
feed** — Google News RSS per publisher (its WSJ, NYT, Seeking Alpha rows are
all `news.google.com/rss/articles/…` redirects), Yahoo Finance RSS,
MarketWatch's `mw_rss_topstories`, CNBC RSS, Benzinga RSS, EDGAR (Form 4 code
P purchases with the `ownership.xml` parsed; S-1/S-3/424B shelf offerings),
and the `trumpstruth.org` archive. The five press wires it locks behind
membership all publish RSS. Its one untraceable source, *Analyst Actions*
(28 items, no URL), is presumably a paid ratings API. Its `topics` tags are a
layer it adds; 75% of items are "General".

**Decision: replicate the sources, never the endpoint.** The endpoint is an
undocumented internal API of a beta product with no terms; it can change or
close any day and the app would go dark with no fallback. Reading the same
public feeds directly costs one small collector and depends on nobody.

## Decisions

| Question | Answer | Why |
|---|---|---|
| Where does the collector run? | **A sixth Tier-2 service, `news_svc`** (`:8216`, dev `:9216`) | Fifteen-plus HTTP fetches a poll, some slow, some flaky by nature — they must not sit on `market_svc`'s 3 s quote loop, and a broken feed parser must not take the Market Dashboard or the Desk ticker down. Cost: a port, a unit, a Status card. |
| Claude summaries? | **Phase 2.** v1 is headline + teaser + source + tickers | See the raw feed quality first; the summary step is designed below and built after. |
| Which tickers drive per-ticker feeds and the filter? | **The GEX collection list** (`config/symbols.toml` via `shared.symbols`) **plus a small `[tickers] extras`** | Zero new list to keep in step; a name added to the scan gets news automatically. `extras` is for names followed but not traded. |
| Where does it show? | `/news` · a Symbol-page band · a Desk strip · **and `live.neuralstrike.co/news`** under the site's Tools menu | The three private readers are one view apiece on polls that already run; the public screen is the reference site's whole product. |
| What may the public see? | Headline, the feed's own teaser, source, time, link — never a body or a summary; **per-feed `public` flag** (`[feed_flags]`, re-checked at every publish), enforced at the service | The shape RSS exists to syndicate and the one Google News / STN use. A source with restrictive terms — or one kept as the operator's edge — is dropped at the producer, not hidden by the page. |
| New dependencies | **`feedparser`** only (pure Python; `requirements.txt` AND `requirements.lock`) | RSS/Atom variants are messy; Form 4 XML parses with the stdlib. |
| Paid calls | **None.** Zero Schwab, zero Claude, zero vendor keys in v1 | Every source is public; EDGAR asks only for a contact User-Agent. |

## 1. Shape

```
config/news.toml ──> news_svc (:8216)  scheduler: poll feeds → news.db → publish
                          │
                          ├─ cache:news:feed         newest ~300 items, every feed   (private)
                          ├─ cache:news:feed_public  the same, public feeds only     (live origin)
                          └─ cache:news:status       per-feed last poll / error / count today
                                    │
     webgui  /news · Desk headlines strip · Symbol "In the news" band   (private, read feed)
     live_main  /news                                                    (public, reads feed_public)
```

`services/news_svc/app.py` is `make_app("news", scheduler=scheduler.loop,
command_handler=handlers.handle_command)`, importable without side effects like
`market_svc`. One command type, `news_refresh` (the private page's Refresh:
runs a poll now). The scheduler is gated by the `schedulers` env flag — dev
fetches nothing at rest — and ticks `_heartbeat` like every other loop.

## 2. Sources — `config/news.toml`

Loaded through `shared.config_toml.toml_loader` (defaults are the real values,
the file overrides, `config/local/news.toml` overrides that, mtime-cached, never
raises). Every value below is an operator choice and gets a
`webgui/config_schema.py` entry so Settings → Configuration shows it.

```toml
[collector]
rth_poll_min      = 5      # 08:30–15:00 CT, via shared.market_calendar (floor 3 in Settings)
offhours_poll_min = 15
weekend_poll_min  = 60     # holidays follow the calendar too
keep_days         = 7      # news.db retention
view_items        = 300    # rows in the published view (~150 KB)
request_timeout_s = 20
sec_user_agent    = "NeuralStrike news_svc <contact@…>"   # SEC requires a contact; ≤10 req/s

[tickers]
extras = []                # followed but not scanned; the base set is the GEX collection list

[trending]
window_h = 6               # the Trending chips count mentions in this window

[[feeds]]
name = "MarketWatch"
kind = "rss"
url  = "https://feeds.content.dowjones.io/public/rss/mw_topstories"

[[feeds]]
name = "Yahoo Finance"
kind = "yahoo_ticker"                      # {symbol} expands over the ticker set
url  = "https://feeds.finance.yahoo.com/rss/2.0/headline?s={symbol}"

[[feeds]]
name  = "WSJ"
kind  = "google_news"                      # a Google News RSS search
query = "site:wsj.com markets when:1d"

[[feeds]]
name = "SEC Insider Buys"
kind = "edgar_form4"
min_value_usd = 1000000                    # every code-P buy on a tracked ticker, plus any buy ≥ this

[[feeds]]
name  = "SEC Offerings"
kind  = "edgar_filings"
forms = ["S-1", "S-3", "424B5"]

# also shipped: CNBC, ZeroHedge, Benzinga, Federal Reserve press releases,
# PR Newswire, GlobeNewswire, Business Wire, the Truth Social archive

# The per-feed switches, one TABLE per feed, keyed by the feed's name.
[feed_flags."MarketWatch"]
enabled = true
public  = true

[feed_flags."SEC Insider Buys"]
enabled = true
public  = true
# ... one entry for EVERY shipped feed
```

**The switches live in `[feed_flags."<feed name>"]`, not in `[[feeds]]`
(2026-09-26).** A list in `config/local/news.toml` replaces the whole shipped
list, while a table deep-merges key by key — so with the flags inside
`[[feeds]]`, switching one feed off from Settings would have written a copy of
the entire feed list into the override and frozen it there, and every later
change to the shipped list would have been silently shadowed. As tables, an
override of one feed's `public` is one line and touches nothing else. The rules
(`shared/news_config.py`):

- a flag absent from `[feed_flags]` defaults **true**; a present but non-bool
  one **fails closed** (`false`, with a WARNING);
- a legacy `enabled` / `public` still inside a `[[feeds]]` entry is WARNed as
  moved and used only where `[feed_flags]` is silent, so a legacy
  `enabled = false` still keeps the feed off;
- a `[feed_flags]` entry naming no feed (a typo) is WARNed; one that is not a
  table is ignored with a WARNING;
- a duplicate feed name keeps the FIRST entry, and the WARNING says when that
  kept entry is disabled — the name keys the switch, so "enabling the
  duplicate" does nothing;
- `news_config.flags(name)` returns one feed's current `{enabled, public}` by
  the same rules, `false`/`false` for a name that is not a usable feed.

**Every shipped feed is public**, ZeroHedge and Truth Social included; only
GlobeNewswire ships disabled (its host drops non-browser clients). There is
**no per-feed `poll_min`**: every feed polls on the collector's cadence.

**Settings → Configuration** edits the switches ("Feed switches", one row per
feed, named by the feed) and `[collector]` / `[tickers]` / `[trending]`, but
shows the **feed list read-only** — displayed, never editable, never written to
the override — because the only way to override part of a list is to replace
all of it. A feed is added or changed in `config/news.toml`. During market hours
the poll interval cannot go below **3 minutes** (`collector.rth_poll_min`).

**Five `kind`s, one adapter each** (`services/news_svc/adapters/`), every one a
pure function `parse(bytes, feed_cfg, now) -> list[Item]` over the fetched
body, so each is tested on a saved fixture with no network:

| kind | fetches | notes |
|---|---|---|
| `rss` | one URL | `feedparser`; honours `ETag` / `Last-Modified`, so an unchanged feed costs one cheap 304 |
| `yahoo_ticker` | the template once per symbol in the ticker set | symbols spread across the poll window so ~90 fetches never burst; each symbol at most once per poll |
| `google_news` | one search URL | links are Google redirects — kept as-is in v1 (resolving is one HTTP hop per item); the publisher name comes from the item's `<source>` |
| `edgar_form4` | the EDGAR "current filings" Atom for type 4, then each new filing's `ownership.xml` | code **P** (open-market purchase) only, non-derivative; parsed into `detail` (insider, relationship, shares, value, date) exactly the reference feed's shape; CIK → ticker via the SEC's `company_tickers.json`, cached daily |
| `edgar_filings` | the same Atom per form type | headline "`<company> files <form>`"; no amount parsing in v1 |

## 3. Items, dedupe, ticker attribution

```
Item: id · source · original_source · title · teaser · url · published_at ·
      first_seen · tickers · kind · topics · detail · public
```

- **`id` = hash of the canonical URL** (tracking params stripped — `utm_*`,
  `?mod=`). A second key, normalized title + source within 24 h, catches one
  story arriving through two feeds; it shows **once with every source badge**.
- **Tickers.** Per-ticker feeds tag themselves. EDGAR items resolve through the
  CIK map. A general headline gets a ticker **only from an explicit `$NVDA` or
  `(NVDA)`** matched against the ticker set — never from a company name (a
  story about Apple does not tag AAPL in v1: precision over recall). Every
  symbol passes `shared.symbols.clean_symbol` before it names a key or a link.
- **`topics`** in v1 is what the adapter knows structurally (`SEC Filing`,
  `Insider Transaction`, `Offering`, `Macro` for the Fed feed); no keyword
  classifier. The reference site's 75%-"General" tag is the argument for not
  building one yet.
- **`public`** is copied from the feed's flag at ingest **and re-checked at
  every publish**: the public view is built from the store's `public` column
  AND the feed's CURRENT `news_config.flags(source)["public"]`, so switching a
  feed's `public` off hides the items it already published on the next poll,
  not only the ones ingested afterwards (built in the poll-cycle task). Either
  way the decision is the producer's.

**Store.** `services/news_svc/data/news.db` (gitignored, added to the root
`conftest.py` live-DB guard list and to `backup_local.DATA_TREES`): `items`
keyed on `id`, an index on `published_at`, and `feed_state` (per feed: etag,
last-modified, last poll, last error, last item time). Retention prunes past
`keep_days` on each poll.

**Publish.** After each poll, `feed` = the newest `view_items` rows,
`feed_public` = the same query with `public = 1`, further filtered to feeds
whose CURRENT flag is public (`news_config.flags`), both `cache_set(...,
skip_unchanged=True)` with an event; `status` alongside. ⚠ `feed_public` is
built from the store's flag, never by filtering `feed` page-side — a
producer-side test feeds a `public = false` feed through the real publish and
asserts nothing of it reaches `feed_public`.

## 4. Failure policy

- A feed that errors (timeout, 5xx, parse failure) calls
  `_degrade.degraded("news.feed.<name>")`, records the error in `feed_state`
  and `status`, and **keeps its last items** — the view is never republished
  smaller because one source broke. The Status page shows `healthy – N
  degraded`; the private page shows the failing feed's name in its status line.
- The SEC adapter rate-limits itself to well under 10 req/s and always sends
  `sec_user_agent`; an EDGAR outage degrades that feed alone.
- The page reads "Nothing published yet" only when the view is absent (the
  service has never run), never for a quiet tape — the same "never print a zero
  you did not read" rule every feed in the app follows.
- A ticker set that fails to load (no `Top 20.xlsx` on a fresh clone) degrades
  to the base symbols, as the scanner does; per-ticker feeds shrink, nothing
  refuses.

## 5. Display

**`/news` — private page** (`webgui/pages/news.py`, built with `ui_kit`; a new
MARKETS rail item after Flow Alerts; added to `test_shell.py`, the nav-partition
test, the inline-style guard and `page_help.py`). Layout follows the reference
site: header with the Updated stamp and a Refresh button (enqueues
`news_refresh`); a **source chip row** (All + each enabled feed); **Trending
6h** ticker chips (a pure count of tickers named in the general feeds'
headlines — Yahoo per-ticker items are skipped, since they carry the ticker they
were fetched for); a **watchlist toggle** (ticker set only) and a **ticker
search**; rows of *time CT (the app's convention) · ticker chips → `/symbol`
· headline (opens the original in a new tab) · source badge(s)*, Form 4 rows
showing the parsed detail; 60 rows then "Show more". Repaint is
`view_watch.watch_view("news:feed", …)`. The rail badge counts items newer
than the last visit (an `app_settings` stamp), the same build/update pair the
other badges use.

**Symbol page** gains an **"In the news"** band: the newest 8 items tagged with
that ticker, `news:feed` joining its batched `read_versions`.

**Desk** gains a **headlines strip**: the newest 5 items, one line each,
`news:feed` as the Desk poll's eleventh view. No Highcharts anywhere.

All three are private. The Desk and Symbol pages are not public screens, so
nothing changes there.

## 6. The public screen — `live.neuralstrike.co/news`

One line in `webgui/live_screens.py`:

```python
Screen("news", "/news", "Market News", "news", "/news", kwargs={"public": True}, tile=False)
```

— the fifth Tools-menu screen: no gallery tile, no capture, reached from the
menu on `index / live / gallery / report.html` ("Market News — headlines,
filings and insider buys, live") and pinned by `deploy/tests/test_site.py::TOOLS`,
which already fails when a published tool is missing from any of the four menus.
Add `/news` to `sitemap.txt`.

`pages/news.py::render(public=True)` hands off to **`pages/news_live.py`**
before anything private builds — the Finder / Rescue / Calculator pattern. The
public page is **a pure reader, the only public screen that writes nothing**:

- reads `cache:news:feed_public`, never `feed`;
- draws no Refresh (the private enqueue site opens with a `may_enqueue`-shaped
  gate, as `test_live_commands.py` requires of every published module) and no
  rail badge;
- no watchlist toggle — that reads the owner's `app_settings`, frozen on this
  origin — but the same source chips, Trending 6h and a ticker filter via
  **`?symbol=`**, allow-listed through `clean_symbol` so `/news?symbol=NVDA`
  is a shareable link;
- the same `watch_view` repaint. The live ACL user reads it under its `@read`
  grant; the runbook step confirms the key pattern covers `cache:news:*` or
  adds one `%R~cache:news:*` selector.

Per-visitor cost is one ~150 KB read per feed change; no Schwab, no Claude, no
stream write. The Phase-2 Claude summaries and any fetched article body stay
private: the public page shows only what the feeds themselves syndicate.

## 7. Housekeeping the repo demands

`config/ports.toml` (`news = 8216`; the dev offset applies automatically) ·
`repo_paths` (`NEWS_DB`) · `deploy/systemd/generate_units.py` (units 9 → 10;
`promote.sh` runs `--install`) · the Status page's service label · the health
fan-out (automatic via `SERVICE_URLS`) · `config_schema.py` · the root
`conftest.py` guard list · `backup_local.DATA_TREES` · `test_shell.py` ·
`test_live_main.py`'s route-set pin · `test_site.TOOLS` · `sitemap.txt` · the
runbook's ACL step · `page_help.py` · the User Guide and Reference Guide ·
CHANGELOG · CLAUDE.md's invariants (ports, unit count, folder map, the live
route count) · `requirements.txt` + `requirements.lock` (`feedparser`).

## 8. Testing

- Every adapter over a saved fixture (`tests/fixtures/*.xml`, including one
  real `ownership.xml`), asserting the normalized items — no network; the root
  conftest blocks HTTP anyway.
- Dedupe (URL canonicalisation; the title+source second key), ticker
  attribution (explicit cashtag yes, company name no, `clean_symbol` on every
  path), Trending counts, cadence by calendar, retention.
- `feed_public` from the **producer** side: a `public = false` feed through the
  real poll-and-publish, then read both views.
- A failing feed keeps its last items and registers a degrade; the view is not
  republished smaller.
- `make_app` smoke; `live_main` publishes `/news` and nothing else new; the
  public module passes `test_live_commands`; the four site menus carry the
  entry.
- Page facts are pure (`news_view.py`: row shaping, chip counts, badge count),
  tested without a browser, like the sentiment screens.

## 9. Phase 2 — designed, not built

- **Claude summaries** for items on the ticker set or carrying a macro topic:
  2–3 sentences, tickers, lean (bullish / bearish / neutral), through the
  subscription CLI path (`claude_cli`), a daily cap in `[summaries]`, and the
  "code states the facts" rule — the model joins what the adapter extracted.
  Private only.
- **Body extraction** (`trafilatura`) for free sources, with a "headline only"
  tag where a page is paywalled or blocked; never dropped silently.
- **Keyword / ticker alerts** through the notify chokepoint (`shared/notify`).
- **Analyst Actions** — the one reference-site source with no public feed —
  from Finnhub's free tier, if wanted.
- **Telegram channels** (`t.me/s/<channel>` preview or Telethon) as a sixth
  adapter, for the channels first asked about.

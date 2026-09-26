# Market News v2 — impact rank, SEC panel, economic calendar — design

**Date:** 2026-09-26 · **Status:** approved (owner decisions final), not built ·
**Builds on:** [the v1 design](2026-09-25-news-feed-design.md) (shipped) ·
**Sources probed in:** [the calendar research](2026-09-26-news-calendar-research.md) ·
**Plan:** [2026-09-26-news-v2-plan.md](2026-09-26-news-v2-plan.md)

## Ask

Four changes to `/news`, all decided by the owner:

1. a rules-based **High / Med / Low impact** on every item, with the reasons that
   earned it;
2. a **Source** column, and **single-line** rows (the teaser line goes);
3. the **SEC / EDGAR** items move out of the headline list into a panel of their own;
4. an **economic calendar** as tiles in three groups — *Economic news/Calendar*,
   *Dividend / IPO*, *Economic data (CPI, PPI etc)*.

Laid out as: headline list LEFT; SEC panel RIGHT-TOP; calendar tiles RIGHT-BOTTOM;
stacked on a phone. The public `/news` gets the same layout from public views only.

## Decisions

The owner's decisions are final; the rows marked **(design)** are choices this
document makes inside them, each with its reason.

| Question | Answer | Why |
|---|---|---|
| Where is impact computed? | `news_svc`, at ingest, stored on the item (`impact_score`, `impact_band`, `impact_reasons`, `impact_ver`); re-scored when the row merges or the `[impact]` config / ticker set changes | Owner decision 1. One computation, visible in the store, testable by eye through its reasons. |
| Staleness cap | Applied **at publish**, not stored **(design)** | "Older than 24 h caps at Med" is a function of *now*; a stored band would be wrong a day later. The stored band is the uncapped one; the view's band is capped. |
| Impact on the PUBLIC view | **Re-scored at publish from the public row** **(design)** | A merged row shows the public view fewer `sources` and fewer `tickers` than it stores. Its stored score counts the private ones (the multi-source boost, the watchlist boost), so reusing it would let a private feed raise a public band — and a reason like `sources:3` would count a feed the public page never names. The scorer is pure and cheap (≈400 rows a poll). |
| Where do SEC items go? | **Split at the producer**: `cache:news:feed` / `feed_public` become headlines only (every kind but `edgar_form4` / `edgar_filings`), and new `cache:news:sec` / `sec_public` carry the EDGAR kinds with their own window `[collector] sec_view_items` (100) **(design)** | Filtering page-side would let a busy 424B5 day eat the 300-row headline window (the offerings feed alone can post dozens a day). The Desk strip and Trending want headlines anyway; the Symbol band reads both views, because an insider buy on the symbol is dossier material. |
| Calendar config home | `config/news.toml` (`[calendar…]`, `[impact…]`), through `shared/news_config.py`'s one loader | Owner decision 11. One file for the feature; the loader already has the override + mtime-cache contract. |
| Calendar views | `cache:news:calendar` (private), `cache:news:calendar_public`, `cache:news:calendar_status` (private detail) | Owner decision 10; see §5 for what differs. |
| Dividends on the public view | **Only for symbols in the GEX collection list; `[tickers] extras` stay private** **(design, answering decision 10)** | The collection list is already public: `options_svc.publish_gamma_symbols` writes it (less `$VIX`) to `cache:options:gamma_pub_symbols` for the public Gamma dropdown, and the public Opportunity Board lists the scan watchlist. `extras` — "followed but not scanned" — is published nowhere, so a dividend tile for an extra would be the first public statement that the owner follows that name. |
| The scheduled release list in *Economic news/Calendar* | **Not the whole list** — Fed events plus a short configurable `extra_releases` allow-list (ships: JOLTS, Employment Cost Index) **(design, answering decision 6)** | Every release a tile covers already shows there with its own date and time; listing CPI in both groups shows one event twice in adjacent tiles from two sources that can disagree on the minute. The rest of the BLS/BEA calendar (313 BLS events a year: *Real Earnings*, *County Employment and Wages*…) is noise at this size. JOLTS and ECI move markets but have no value tile, so they are the two worth a line; the list is config. |
| Dividend fetch | `trade_svc` (it already calls the proxy), nightly, into a shared store `shared/dividends.py` over `repo_paths.DIVIDENDS_DB`; `news_svc` only reads it | Owner decision 8. `news_svc` keeps its invariant: **no proxy, no Claude** — pinned by a source-level test. |
| `trade_svc` gains a scheduler | Yes — one branch, the daily dividend pull | It had none ("on-demand only"). The `schedulers` flag gates it like every other loop, so dev stays quiet. |
| FRED | Official API with `FRED_API_KEY` from the stack `.env`; key-free `fredgraph.csv` when the key is absent | Owner decision 7. The key is never in git, config, `.env.live`, a log line, an error string or a published view. |
| Module name for the calendar cycle | `services/news_svc/econ_calendar.py`, **not** `calendar.py` **(design)** | `calendar` is a stdlib module; a service module of that name shadows it when `app.py` runs as a script (the CLAUDE.md `secrets.py` incident). |
| ISM | Dropped | Owner decision 6; licensed data, no free source. |
| Consensus figures | None | Owner decision 6. |

## 1. Shape

```
config/news.toml ─┬─> news_svc (:8216)
                  │     feeds branch    (v1 cadence)   → news.db items (+ impact) → feed / feed_public
                  │                                                                → sec  / sec_public
                  │                                                                → status
                  │     calendar branch (refresh_min)  → Fed · BLS · BEA · FRED · Nasdaq · dividends store
                  │     watch branch    (every tick)   → FRED series whose release just passed
                  │                                    → news.db cal_sources / econ_obs
                  │                                    → calendar / calendar_public / calendar_status
                  │
                  └─> trade_svc (:8213) scheduler: once a trading day → proxy /passthrough /quotes
                                         → shared/dividends.py store (services/trade_svc/data/dividends.db)

webgui /news  (private)  feed · sec · calendar            [news list | SEC panel / calendar tiles]
live   /news  (public)   feed_public · sec_public · calendar_public   (same layout, no controls)
```

The three `news_svc` branches run as **independent tasks with a still-running
skip** (the `options_svc.scheduler.launch_branches` shape): a feed poll takes
minutes (Yahoo per-ticker + paced SEC requests), and the release watch must not
queue behind it. Each branch has its own lock, so a `news_refresh` command and
the scheduler never run the same branch twice at once. The loop still beats
`_heartbeat` once per pass inside its `while`.

## 2. Impact (owner decision 1)

`services/news_svc/impact.py` — pure: `score(row, cfg, universe) -> (score, reasons)`,
`band(score, cfg)`, `cap_stale(band, published_at, now, cfg) -> (band, capped)`,
`fingerprint(cfg, universe)`. No I/O.

```toml
[impact]
high_at          = 6       # score >= high_at -> high
med_at           = 3       # score >= med_at  -> med, else low
stale_after_h    = 24      # a HIGH item older than this shows as MED
multi_source     = 1       # +points when two or more feeds carry the story
watchlist        = 2       # +points when the item is tagged with a followed ticker
match_teaser     = false   # keywords match the headline only

[impact.keywords.tier1]    # a TABLE per tier (a local override of one tier's words merges key by key)
points = 5
words  = ["FOMC", "rate decision", "rate cut", "rate hike", "Fed chair", "CPI",
          "inflation report", "jobs report", "nonfarm payrolls", "payrolls",
          "bankruptcy", "chapter 11", "trading halt", "halted", "SEC charges", "indicted",
          "acquire", "acquisition", "merger", "takeover", "tender offer",
          "guidance cut", "cuts guidance", "raises guidance", "profit warning", "delist"]
[impact.keywords.tier2]
points = 3
words  = ["downgrade", "upgrade", "beats", "misses", "earnings", "PPI", "PCE", "GDP",
          "retail sales", "jobless claims", "tariff", "sanctions", "buyback",
          "dividend cut", "recall", "investigation", "lawsuit", "layoffs",
          "price target", "offering", "stake"]
[impact.keywords.tier3]
points = 1
words  = ["outlook", "forecast", "analyst", "sector", "rally", "selloff"]

[impact.source_points]     # by feed name; a feed absent here scores 0
"Federal Reserve" = 3
"Truth Social"    = 2
"WSJ"             = 1
"ZeroHedge"       = -1

[impact.form4]             # by detail.total_value (the sum of code-P buys), dollars
small_usd  = 250000
small      = 1
large_usd  = 1000000
large      = 3
huge_usd   = 10000000
huge       = 6
officer    = 1             # + when the relationship names an officer or director

[impact.filings]           # by detail.form
"424B5"  = 3               # a takedown: shares actually being sold now
"S-3"    = 2
"S-1"    = 1
"S-3ASR" = 1               # a large issuer's automatic shelf: routine
untracked = -1             # a filing on no followed ticker (likely a micro-cap)
```

Rules, each a test:

- **Word-boundary, case-insensitive** match: `(?<!\w)<phrase>(?!\w)` with the phrase
  `re.escape`d and internal whitespace matching `\s+`. "CPI" never hits "CPIX";
  "beats" never hits "heartbeats"; "rate cut" matches "Rate  Cut". Compiled once per
  config fingerprint.
- **A tier counts once**, whatever matches inside it; several tiers add up. A headline
  repeating "Fed" cannot stack.
- **Source points take the MAX over the row's `sources`**, not the sum — the
  multi-source boost is the one place two outlets add.
- **Form 4**: the highest band whose threshold `total_value` reaches. A missing,
  NaN, infinite, bool or non-positive `total_value` scores **0**, never a band (the
  NaN-pins-the-bound trap in CLAUDE.md).
- **Filings**: `detail.form` looked up exactly (`S-3` never takes `S-3ASR`'s points);
  `untracked` applies when the row carries no ticker.
- **Thresholds from config**; `med_at > high_at` or a non-number falls back to the
  built-in pair, with one WARNING from the loader, never a raise.
- **Reasons** name the rule, not private data: `kw:tier1:FOMC`, `source:Federal Reserve`,
  `sources:2`, `watchlist`, `form4:$1.2M`, `officer`, `filing:424B5`, `untracked`,
  `stale`. `watchlist` names no ticker. Every tagged ticker is in the ticker set by
  construction (extraction runs against it), so the boost reveals nothing a ticker
  chip does not already show.
- **Stored uncapped; capped at publish.** A HIGH older than `stale_after_h` publishes
  as MED with `stale` appended to its reasons.
- **Re-scored** when `impact_ver` differs from `fingerprint(cfg, universe)` (a config or
  ticker-set change) or is NULL (a new row, or one a merge touched — `insert_many`'s
  merge path sets it NULL). Bounded by `keep_days`: a full re-score is a few thousand
  pure calls.

Payload shape (every row in every view): `"impact": {"band": "high"|"med"|"low",
"score": 7, "reasons": ["kw:tier1:FOMC", "source:Federal Reserve"]}`.

## 3. Headline list and SEC panel (owner decisions 2–5)

**Headline list** — one row per item, **single line**, columns:

| Time | Impact | Tickers | Headline | Source |
|---|---|---|---|---|
| `10:43 AM` / `Sep 25 10:43 AM` (CT) | a pill `H` / `M` / `L`, reasons in its tooltip | chips → `/symbol` (private) or filter (public) | the link, truncated with an ellipsis (`truncate min-w-0`) | the source badge(s) |

The teaser line, `news.second_line`, `_echoes_title` and `_SECOND` are deleted with
their tests (the teaser stays in the payload — the Desk and a future summary may use
it). A band filter joins the control bar (*All · High · High + Med*), pure in
`news_view.filter_rows(min_band=…)`. Band colours are a finite map to fixed Tailwind
classes (the Tailwind-first rule): high `bg-rose-500/20 text-rose-300`, med
`bg-amber-500/15 text-amber-300`, low muted.

**SEC panel** — reads `news:sec` (public: `news:sec_public`), columns **Date/Time ·
Symbol · Headline/Details**, where the third cell is the headline link then
`news_view.detail_line(row)` muted after a `·`, one line, truncated. The row's impact
pill leads that cell — the Form 4 and filing rules exist to rank these rows, and an
extra column would not fit the half-width panel. Newest first, its own "Show more".

**Layout** — `grid grid-cols-1 lg:grid-cols-5 gap-3`: headline list `lg:col-span-3`;
a right column `lg:col-span-2 flex flex-col gap-3` whose two panels are each
`lg:h-[calc(50vh-5rem)] overflow-y-auto` (the SEC panel top, the calendar bottom).
Below `lg` everything stacks in DOM order — headlines, SEC, calendar — at natural
height. The control bar and Trending sit inside the left column: they filter the
headline list only.

## 4. The economic calendar (owner decisions 6, 7, 9)

### Sources

Each source is a TABLE `[calendar.sources.<name>]` with `enabled`, `url`,
`user_agent` (`""` = `[collector] feed_user_agent`), `refresh_min` (absent = the
`[calendar] refresh_min`), and for Nasdaq `accept`. **The User-Agent is per source**
because the hosts disagree in opposite directions (research §0): the repo UA gets 200
from every government host and an HTTP/2 refusal from Nasdaq; a Chrome UA gets 200
from Nasdaq and a 403 from BLS and a stream reset from FRED.

| name | url | feeds | time zone of what it says |
|---|---|---|---|
| `fed` | `federalreserve.gov/json/calendar.json` | FOMC meeting / press conference / minutes, Beige Book, Board speeches and testimony | **Eastern** (the Board's convention) |
| `bls` | `bls.gov/schedule/news_release/bls.ics` | CPI, PPI, Employment Situation, `extra_releases` | `TZID=US-Eastern` |
| `bea` | `bea.gov/news/schedule/ics/online-calendar-subscription.ics` | GDP, Personal Income and Outlays (PCE) | **UTC** (`…Z`) |
| `fred_calendar` | `fred.stlouisfed.org/releases/calendar?rid={rid}&y={year}&view=year&vs={start}&ve={end}` | retail sales (rid 9), jobless claims (rid 180) | **Central** (unstated; inferred against BLS) |
| `fred_api` | `api.stlouisfed.org/fred` | observations (with key); release dates fallback | dates only |
| `fredgraph` | `fred.stlouisfed.org/graph/fredgraph.csv?id={series}&cosd={start}` | observations (no key) | dates only |
| `nasdaq_ipo` | `api.nasdaq.com/api/ipo/calendar?date={yyyy_mm}` (this month + next) | IPOs | dates only |
| (store) | `shared/dividends.py` | watchlist ex-div / pay | dates only |

Parsing traps, each pinned on a saved fixture:

- **`calendar.json` starts with a UTF-8 BOM** — decode `utf-8-sig`; `days` is a comma
  list ("3, 10, 17") expanded to one event per day; `time` may be `""` (a date-only
  event); `title` / `location` carry HTML entities (`&#8217;`, `&amp;` → `html.unescape`)
  and trailing spaces; times read "2:00 p.m." / "9:15 a.m.".
- **ICS**: unfold continuation lines (RFC 5545: a line starting with a space or tab
  continues the previous one) before splitting; unescape `\,` `\;` `\n` `\\`;
  `DTSTART;TZID=US-Eastern:…` → America/New_York, `DTSTART:…Z` → UTC, a `VALUE=DATE`
  start → date-only.
- **FRED calendar HTML**: a day header then rows; **a blank time cell means "same as
  the row above"**; names carry entities. Filter by `rid` so the page is one page.
- **Nasdaq**: every value a STRING — money `"$2,530,000,000"` / `"$240,000,000.00"` →
  int, dates `M/D/YYYY` → ISO, price `"40.00-44.00"` kept as display text,
  `proposedTickerSymbol` may be `null` (withdrawn rows); the envelope's
  `status.rCode` ≠ 200 is a failure even under HTTP 200. `?date=` of a month with no
  deals returns empty tables — normal, not an error.
- **BLS ics is one calendar year**: from mid-December until BLS posts the next year's
  file, January's releases are absent. An indicator with no future event renders
  **"Next date not yet published"**, never a blank and never a guessed date.

Every stored and published time is an **aware ISO instant**; a date-only event
carries `date` with `at: null`. Tier 1 renders CT (`"Wed Oct 14 · 7:30 AM CT"`) —
the app's convention.

### Indicators — `[calendar.indicators.<key>]`, one table each (tile order = the built-in order)

Order is the built-in `DEFAULTS` order: the file is deep-merged onto it, so a new
indicator appends, and removing a table from the file does not remove it — set
`enabled = false`.

| key | label | series | transform | schedule | match / rid | tile |
|---|---|---|---|---|---|---|
| `cpi` | CPI | CPIAUCSL | `pct_mom` | bls | "Consumer Price Index" | CPI |
| `core_cpi` | Core CPI | CPILFESL | `pct_mom` | bls | "Consumer Price Index" | CPI |
| `ppi` | PPI | PPIFIS | `pct_mom` | bls | "Producer Price Index" | PPI |
| `nfp` | Nonfarm payrolls | PAYEMS | `change_k` | bls | "Employment Situation" | Jobs |
| `unrate` | Unemployment | UNRATE | `level_pct` | bls | "Employment Situation" | Jobs |
| `pce` | PCE prices | PCEPI | `pct_mom` | bea | "Personal Income and Outlays" | PCE |
| `core_pce` | Core PCE | PCEPILFE | `pct_mom` | bea | "Personal Income and Outlays" | PCE |
| `gdp` | GDP | A191RL1Q225SBEA | `pct_saar` | bea | "GDP (" | GDP |
| `retail` | Retail sales | RSAFS | `pct_mom` | fred | rid 9 | Retail sales |
| `claims` | Jobless claims | ICSA | `level_k` | fred | rid 180 | Jobless claims |

Each also carries `enabled` and an optional `time_ct` (used only when the only date
source available gives no time — the FRED API fallback). The **headline GDP is
`A191RL1Q225SBEA`, not `GDP`** (a nominal level). Most series are levels or indexes,
so `econ.derive(obs, transform)` computes the headline figure: `pct_mom` (and the
figure one period back as its prior), `change_k` (PAYEMS m/m in thousands, "+162K"),
`level_pct` / `level_k` / `pct_saar` as-is (the unit only changes the label). A NaN, a `"."` (FRED's missing value) or a
non-positive base yields `None` — the tile prints `—`, never `0.0%`.

### Actual vs prior

`news.db` keeps `econ_obs(series, obs_date, value, first_seen, bootstrap)`. The tile
for an indicator shows the **next** release (date/time CT), then:

- before the release: **Actual —**, **Prior** = the latest observation;
- after it, once an observation first seen at or after the release time exists:
  **Actual** = that observation, **Prior** = the one before, and the tile marks
  *Released* while within `actual_fresh_h`;
- after it but before the value lands: *Awaiting* (the release-watch window).

**Bootstrap trap**: the first fill of a series stamps every row `bootstrap = 1` with
`first_seen = now`, which would attribute an OLD value to a release that happened a
minute ago. A bootstrap observation is attributed to the last release only once
`now > release_at + release_watch_min` (by then FRED has surely posted); inside the
window it reads *Awaiting*. The page makes this decision (it is a function of now),
from facts the payload carries: `last_release_at`, `latest {obs_date, value,
first_seen, bootstrap}`, `prior {…}`.

### Release watch

From an indicator's scheduled release time until a new observation lands — at most
`release_watch_min` (60) — the watch branch fetches that series every
`release_poll_min` (2). `econ.watch_due(indicator_state, now, cfg)` is pure. On
the key path that is well inside FRED's documented 120 requests/minute (release day
touches two or three series); on the key-free path it is ~30 CSV requests per release.

### FRED key

`os.environ.get("FRED_API_KEY")`, read at call time (so adding it to `.env` takes a
service restart, not a code change). **Present** → `fred/series/observations`
(JSON, `sort_order=desc&limit=15` — enough for the prior of a y/y) and, only when
the calendar HTML fails, `fred/release/dates` + the indicator's `time_ct`. **Absent**
(dev, tests, not yet set) → `fredgraph.csv` (one series per call — a multi-series
request ignores `cosd`). ⚠ **The key is a query parameter, so it is in every URL**:
`http_fetch` puts the URL into its errors and `requests` puts it into its exception
text. Every FRED API call goes through one wrapper that **replaces the key with `***`
in any exception it re-raises**; the source's `error` string, the degrade detail, the
log line and `calendar_status` are all built from the redacted text. A test drives a
fetch that raises with the key in its message and asserts the key appears in no
log record, no store row and no published view. The key lives only in the stack
`.env` (which every service unit loads — `generate_units._env_file`), never in
`.env.live`, git, `config/`, or Settings.

### Dividends (owner decision 8)

**Writer — `trade_svc`.** `services/trade_svc/dividends.py` asks the proxy, one
symbol per call (the proxy's `/passthrough` splits `params` on commas, so a
multi-symbol value is mangled): `endpoint=/quotes`, `params=symbols=<SYM>` (probe
whether `,fields=fundamental` — a second pair, which the split handles — trims the
reply). Symbols = `shared.news_config.ticker_set()` less `$`-prefixed indices,
≈80 calls once a trading day at `[calendar.dividends] refresh_at` (06:40 CT —
outside the session and off the quarter hours the options autoscan owns), skipped
when today's run is already recorded (a restart does not re-fetch).

⚠ **The field names are unverified.** Schwab's quote `fundamental` block is
documented as `divAmount`, `divYield`, `divExDate`, `divPayDate`, `divFreq`,
`divPayAmount`, `nextDivExDate`, `nextDivPayDate`, `declarationDate`; the
instruments endpoint spells them `dividendAmount`, `dividendDate`,
`dividendPayDate`, `nextDividendDate`, `nextDividendPayDate`. The parser accepts
**both spellings**, dates as `YYYY-MM-DD`, `YYYY-MM-DDTHH:MM:SSZ` or
`YYYY-MM-DD HH:MM:SS.f`, amounts as numbers or numeric strings, and prefers the
per-payment amount (`divPayAmount`) over the annual (`divAmount / divFreq`). One live
call on prod is a task step; its sanitized reply becomes the parser's fixture.

**Store — `shared/dividends.py`** (the `shared/earnings.py` shape: `init_db(db_path=None)`
resolved at call time, row factory, schema here, read helpers here, writer in the
service). `dividends(symbol, ex_date, pay_date, amount, frequency, declared_date,
recorded_at, PK(symbol, ex_date))` and `coverage(symbol, checked_on, status)` with
status `ok | none | error` — three-valued like earnings: *a non-payer* ≠ *not
fetched*. `repo_paths.DIVIDENDS_DB = TRADE_SVC_DATA / "dividends.db"`; the root
conftest guard and `backup_local.DATA_TREES` already cover that directory.

**Reader — `news_svc`** calls `shared.dividends.upcoming(symbols, start, end)` and
`coverage_summary(symbols)`, never `trade_svc`. A missing store (trade_svc never
ran) is the source state `never`, not an empty "no dividends".

### IPOs

`[calendar.ipo] min_offer_usd` (100,000,000 — roughly half of Nasdaq's rows are SPACs
and tiny deals), `lookback_days` (7: priced deals shown for a week), this month and
next fetched (a month's table fills only 1–2 weeks out). Upcoming rows show the
expected price date and range; priced rows the price.

### Fed events

`[calendar.fed] types = ["FOMC", "Beige", "Speeches", "Testimony"]`,
`horizon_days` (45 — an FOMC meeting can be six weeks out), `speech_horizon_days`
(14 — Board speeches are frequent). FOMC titles map to short labels: *FOMC statement*
(the "FOMC Meeting" row at 2:00 p.m.), *Press conference*, *FOMC minutes*.

## 5. Views and failure policy (owner decision 10)

```
cache:news:calendar         {"events": [...], "data": [...], "dividends": [...],
                             "ipos": [...], "sources": {"fed": "ok", "bls": "stale", ...}}
cache:news:calendar_public  the same, dividends cut to the collection list
cache:news:calendar_status  {"sources": [{"name","last_ok","last_poll","error"}], "ts"}   (private)
```

- **No timestamp in `calendar` / `calendar_public`** (`skip_unchanged`, CLAUDE.md's
  perf note): "updated" comes from the `{key}:ts` side key. `sources` holds a state
  word only — `ok`, `stale` (the last fetch failed; the last good result is shown),
  `never` (no good result yet), `off` (disabled) — so it changes only when a state
  flips. Error TEXT lives only in `calendar_status`.
- **Each source fails alone.** A fetch or parse failure is `_degrade.degraded(
  "news.cal.<source>")`, records the redacted error, and keeps the source's **last
  good parsed result** (`news.db` `cal_sources.payload`), so one blocked host greys one
  group's tiles, not the page.
- **The public copy is built by the producer** with `public_symbols =
  shared.symbols.collection_base()`, never by filtering the private view in Tier 1.
  A producer-side test puts an `extras` symbol's dividend into the store and asserts
  it reaches `calendar` and not `calendar_public`.
- ⚠ **The ACL is not a layer here either** (the v1 note): the live Redis user's
  `~cache:*` read covers every `news:*` key. The public page reads only the three
  `_public` views, pinned at source level by `test_news_live.py`.

## 6. Display of the calendar (Tier 1)

`news_view.calendar_groups(payload, now)` → three groups, in this order, with these
exact headers: **"Economic news/Calendar"**, **"Dividend / IPO"**, **"Economic data
(CPI, PPI etc)"**. Each tile: a title, a CT when-line, and for data tiles
*Actual* / *Prior* lines per indicator (`"+0.4% m/m"`, `"+162K"`, `"4.1%"`,
`"197K"`, `"1.5% SAAR"`, `"—"`). A group whose sources are all `stale` or `never`
shows a muted *"Source unavailable — showing the last good reading"* /
*"Not published yet"* line; a group with no rows in its horizon says so plainly
("No Fed events in the next 45 days"). Nothing prints a zero it did not read.

## 7. Housekeeping

`repo_paths` (`DIVIDENDS_DB`) · `config_schema.py` (every new key, a new `phrases`
kind for the keyword lists, the calendar indicators and sources; the dividend keys
restart `trade_svc` and `news_svc`) · `pages/config_editor.py` (render `phrases`) ·
`services/tests/test_scaffold.py` (add `trade_svc` to the heartbeat parametrize) ·
`page_help.py` · User Guide, Reference Guide, Technical Reference (impact rules,
calendar sources and cadences) · the runbook (`FRED_API_KEY` in `.env`, not
`.env.live`) · CHANGELOG · CLAUDE.md (`news_svc` "no Schwab or Claude call" gains
"one optional credential"; `trade_svc` now has a scheduler; the two new public views;
the SEC split of `feed`).

## 8. Testing

- Pure: impact rules (every bullet in §2), each adapter over a saved fixture (BOM,
  entities, folded ICS lines, blank-time rows, string-valued Nasdaq, null ticker),
  transforms (NaN / `"."` → `None`), actual/prior attribution including bootstrap,
  `watch_due`, the dividend parser over both spellings.
- Producer-side: an `extras` dividend never reaches `calendar_public`; a public view's
  impact is re-scored from the public row; EDGAR kinds reach `sec` and never `feed`;
  one failing source keeps its last good result and flips only its state; the FRED key
  appears nowhere after a failing call.
- Invariants by source: `news_svc` imports no proxy client and names no proxy URL;
  `news_live.py` spells no private key; `econ_calendar` is not named `calendar`.
- Page facts pure in `news_view` (single-line row shape, band filter, SEC rows,
  calendar groups, CT formatting); the kit guard and inline-style guard over the
  changed pages.

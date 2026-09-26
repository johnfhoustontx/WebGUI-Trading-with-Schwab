# /news additions — feasibility research (impact rank, SEC panel, economic calendar)

Date: 2026-09-26. Research only — nothing here is built. Every "probe" below is a real
request made from the sandbox on 2026-09-26 (through the agent HTTPS proxy, a
datacenter egress IP — prod's VPS is also a datacenter IP, so bot-defence results
should transfer, but re-check from prod before shipping).

Scope asked for:

- **A** a rules-based High / Med / Low **impact** rank on every news item;
- **B** a separate **SEC / EDGAR** panel;
- **C** an **economic calendar** as tiles in three groups — *Economic news / Calendar*
  (FOMC, minutes, Chair speeches, scheduled releases), *Economic data* (CPI, PPI,
  NFP + unemployment, PCE, GDP, retail sales, jobless claims, ISM — date/time, prior,
  actual; **no consensus**), and *Dividend / IPO* (watchlist ex/pay dates + amount via
  the Schwab proxy; market-wide IPO calendar from Nasdaq).

---

## 0. The headline finding — User-Agent is PER SOURCE, not per service

The existing collector has one `feed_user_agent` (`Mozilla/5.0 (compatible; NeuralStrike
news_svc; +https://neuralstrike.co)`) and one `sec_user_agent`. The calendar sources
disagree about what they accept, in opposite directions:

| host | repo `feed_user_agent` | full Chrome UA | `curl/8` |
|---|---|---|---|
| `fred.stlouisfed.org` (CSV + calendar HTML) | **200** | **timeout / HTTP-2 stream reset** | 200 |
| `www.bls.gov` (ics + schedule pages) | **200** | **403** | **403** |
| `api.nasdaq.com` | **HTTP-2 INTERNAL_ERROR (refused)** | **200** (~2.3 s) | refused |
| `www.federalreserve.gov`, `www.bea.gov`, `www.census.gov` | 200 | (not needed) | — |

So the repo UA works for every government source, and **Nasdaq alone needs a browser
UA**. Any calendar source table needs an optional per-source `user_agent` key (in the
TOML, catalogued in `config_schema.py`), defaulting to `feed_user_agent`. BLS evidently
wants a contact-bearing UA (its published policy); FRED's bot defence rejects a bare
Chrome UA from a datacenter IP.

---

## 1. FRED (api.stlouisfed.org) — needs a key; two key-free FRED paths work

### 1a. The official API requires a key (confirmed)

```
GET https://api.stlouisfed.org/fred/releases/dates?file_type=json
HTTP 400 {"error_code":400,"error_message":"Bad Request.  Variable api_key is not set. ..."}
GET https://api.stlouisfed.org/fred/series/observations?series_id=CPIAUCSL&file_type=json
HTTP 400 (same)
```

With a free key (register at fred.stlouisfed.org → My Account → API Keys; documented
limit 120 requests/minute) the relevant endpoints are:

- `fred/releases/dates?realtime_start=<today>&realtime_end=<+90d>&include_release_dates_with_no_data=true`
  — every release's dates, future included.
- `fred/release/dates?release_id=<rid>&include_release_dates_with_no_data=true` — one
  release (rid 10 CPI, 46 PPI, 50 Employment Situation, 53 GDP, 54 Personal Income and
  Outlays [PCE], 9 Advance Retail Sales, 180 UI Weekly Claims).
- `fred/series/observations?series_id=<ID>&sort_order=desc&limit=2` — latest + prior.

⚠ **The API's release dates are DATES ONLY — no time of day.** The key buys a
documented contract for values, not the times the tiles need.

### 1b. Key-free: `fredgraph.csv` (values) — works

```
GET https://fred.stlouisfed.org/graph/fredgraph.csv?id=UNRATE&cosd=2026-06-01
HTTP 200  content-type: application/csv  cache-control: public, max-age=340
observation_date,UNRATE
2026-06-01,4.2
2026-07-01,4.1
2026-08-01,4.1
```

All nine asked-for series answered 200 in 0.3–0.9 s (last two rows shown):

| series | meaning | last two observations |
|---|---|---|
| CPIAUCSL | CPI-U, SA index | 2026-07 332.813 · 2026-08 334.131 |
| CPILFESL | core CPI index | 336.789 · 337.765 |
| PPIFIS | PPI final demand, SA index | 156.784 · 157.411 |
| PAYEMS | nonfarm payrolls, thousands (level) | 158913 · 159075 |
| UNRATE | unemployment rate % | 4.1 · 4.1 |
| PCEPI | PCE price index | 2026-06 131.454 · 2026-07 131.659 |
| GDP | nominal GDP, $bn SAAR (level) | 2026-Q1 31865.721 · 2026-Q2 32486.066 |
| RSAFS | retail + food services sales, $m | 764462 · 773947 |
| ICSA | initial claims, weekly | 2026-09-12 198000 · 2026-09-19 197000 |

Also verified: `A191RL1Q225SBEA` (real GDP % change SAAR — **the headline GDP number;
use this, not `GDP`**, last 2026-Q2 = 1.5), `PCEPILFE` (core PCE index). Multi-series
`?id=A,B,C` works but ignores `cosd` (returns from 1939) — fetch one series per call.
It is an **undocumented** endpoint (the site's "Download CSV" link), so treat as
best-effort; the key path is the documented fallback.

⚠ Most series are **levels/indexes**, not the headline figure. The tile has to derive:
CPI/core CPI/PPI/PCE/core PCE → **% m/m** (and y/y from the 13th row back);
PAYEMS → **m/m change in thousands** ("+162K"); RSAFS → % m/m; UNRATE, ICSA,
A191RL1Q225SBEA → as-is. "Prior" = the derived figure one period earlier (note
FRED holds the *revised* prior, which is what a release reports anyway).

### 1c. Key-free: FRED's release-calendar HTML carries TIMES (in Central)

```
GET https://fred.stlouisfed.org/releases/calendar?rid=10&y=2026&view=year&vs=2026-09-20&ve=2026-12-31
```

Parsed rows (`<span style="font-weight: bold;">DAY</span>` headers, then
`<td nowrap>TIME</td><td><a href="/release?rid=N">NAME</a>`; a blank time cell means
"same as the row above"):

```
rid 10  CPI            Wed Oct 14 7:30 am · Tue Nov 10 7:30 am · Thu Dec 10 7:30 am
rid 50  Empl. Situation Fri Oct 02 7:30 am · Fri Nov 06 · Fri Dec 04
rid 46  PPI            Thu Oct 15 · Fri Nov 13 · Tue Dec 15
rid 54  Personal Inc.  Wed Sep 30 · Thu Oct 29 · Wed Nov 25 · Wed Dec 23
rid 53  GDP            Wed Sep 30 · Thu Oct 29 · Wed Nov 25 · Wed Dec 23
rid 9   Retail sales   Thu Oct 15 · Tue Nov 17 · Wed Dec 16
rid 180 Claims         every Thursday 7:30 am (15 rows Sep 24 – Dec 31)
rid 101 FOMC Press Rel. — no timed rows (use the Fed's own calendar instead)
```

**7:30 am here is 8:30 ET — FRED renders in Central time** (the page never says so;
inferred by agreement with BLS's ics, which states `TZID:US-Eastern` 083000 for the same
releases). The unfiltered month view is paginated (17 pages for Oct-2026), so filter by
`rid`. Scraped HTML — fragile, but it is one source for every release's date + time.

---

## 2. Key-free agency sources (official, stable formats) — all work with the repo UA

### BLS release schedule — ICS, **works**

```
GET https://www.bls.gov/schedule/news_release/bls.ics      (repo UA → 200, 80 KB)
X-WR-TIMEZONE:US-Eastern ; 313 VEVENTs covering 2025-01-03 … 2026-12-30
DTSTART;TZID=US-Eastern:20261002T083000 | Employment Situation
DTSTART;TZID=US-Eastern:20261014T083000 | Consumer Price Index
DTSTART;TZID=US-Eastern:20261015T083000 | Producer Price Index
DTSTART;TZID=US-Eastern:20261030T083000 | Employment Cost Index
DTSTART;TZID=US-Eastern:20260929T100000 | Job Openings and Labor Turnover Survey
```

Times **Eastern, explicit**. Chrome UA and curl both get **403**. The file ends with the
current year, so from mid-December the next year's file must be published before
January's events appear (expect a thin tile early in January).

### BLS public data API

```
GET https://api.bls.gov/publicAPI/v1/timeseries/data/CUUR0000SA0
{"status":"REQUEST_NOT_PROCESSED","message":["... daily threshold for total number of
 requests ... has been reached."]}      ← shared sandbox IP already exhausted it
GET https://api.bls.gov/publicAPI/v2/timeseries/data/CUSR0000SA0   (no key)
{"status":"REQUEST_SUCCEEDED", ... {"year":"2026","period":"M08","latest":"true","value":"334.131"} ...}
```

Unregistered limit is per IP and tiny (25 queries/day, 10 years, 25 series/query); a free
v2 registration key gives 500/day. **Not needed** — FRED carries the same numbers.

### BEA release schedule — ICS, **works**

```
GET https://www.bea.gov/news/schedule/ics/online-calendar-subscription.ics  (200, 31 KB, 119 events)
DTSTART:20260930T123000Z | GDP (Third Estimate)\, Industries\, Corporate Profits ... 2nd Quarter 2026
DTSTART:20260930T123000Z | Personal Income and Outlays\, August 2026
DTSTART:20261029T123000Z | GDP (Advance Estimate)\, 3rd Quarter 2026
DTSTART:20261125T133000Z | GDP (Second Estimate) and Corporate Profits\, 3rd Quarter 2026
```

Times in **UTC** (`Z`); 12:30Z = 8:30 EDT, 13:30Z = 8:30 EST. The SUMMARY also names the
reference period ("August 2026", "3rd Quarter 2026") — useful on the tile. Commas are
ICS-escaped (`\,`).

### Census economic-indicator calendar — HTML, **works**

```
GET https://www.census.gov/economic-indicators/calendar-listview.html  (200, 91 KB)
... Advance Monthly Sales for Retail and Food Services | October 15, 2026 | 8:30 AM | September 2026 | A202610150830 ...
```

Times Eastern (and an `AYYYYMMDDHHMM` id per row). Retail sales only need this if FRED's
calendar is not used.

### Federal Reserve — `calendar.json`, **the best find**

```
GET https://www.federalreserve.gov/json/calendar.json   (200, application/json, 543 KB, UTF-8 BOM)
{"events":[ {"month":"2026-10","days":"1","time":"3:30 p.m.","type":"Speeches",
             "title":"Discussion - Governor Lisa D. Cook ","location":"At the Central Banking Seminar (Virtual)",
             "description":"Global Central Banking"}, ...], "announcement":[...]}
```

2,596 events, 2017-01 … 2026-12. `type` counts: Stat 1059, Speeches 581, events 569,
FOMC 135, Other 105, Testimony 74, Beige 56, Conferences 10, Board 6. Upcoming FOMC rows:

```
2026-10 7   2:00 p.m. FOMC  FOMC Minutes
2026-10 28  2:00 p.m. FOMC  FOMC Meeting        (statement)
2026-10 28  2:30 p.m. FOMC  FOMC Press Conference
2026-11 18  2:00 p.m. FOMC  FOMC Minutes
2026-12 9   2:00 p.m. / 2:30 p.m.  FOMC Meeting / Press Conference
2026-12 30  2:00 p.m. FOMC  FOMC Minutes
2026-10 14  2:00 p.m. Beige Beige Book
```

Chair appearances are titled `Speech - Chairman Kevin Warsh` / `Testimony - Chairman …`
(e.g. 2026-08-28 Jackson Hole, 2026-07-14/15 testimony); none is posted for Oct–Dec yet.
Times are **Eastern** (the Board's convention; FOMC statement 2:00 p.m. matches).
Parsing notes: strip the BOM (`utf-8-sig`); `days` is a comma list ("3, 10, 17, 24") —
expand to one event per day; `time` may be `""`; titles/locations carry HTML entities
(`&#8217;`, `&amp;`); `title` has trailing spaces. Covers **Board** members only — regional
Fed presidents are not in it. It also covers G.17 industrial production (9:15 a.m.).

The FOMC calendars page (`/monetarypolicy/fomccalendars.htm`, 200, 165 KB) is parseable
(`fomc-meeting__month` / `fomc-meeting__date` divs, `*` = SEP meeting) but carries no
times and lists a minutes date only after release — `calendar.json` supersedes it. The
speeches RSS (`/feeds/speeches.xml`) lists past speeches only; the existing "Federal
Reserve" press feed already covers statements.

### DOL weekly claims

`https://www.dol.gov/ui/data.pdf` (200, PDF) and `oui.doleta.gov/unemploy/csv/ar539.csv`
(200, 13 MB) both answer, but neither is needed: the schedule is every Thursday 8:30 ET
(FRED rid 180 lists it) and ICSA on FRED carries the value.

### ISM — **not feasible**

`ismworld.org/.../ism-report-on-business/` redirects a non-browser to an SSO login page;
the NAPM series left FRED in 2016 (`fredgraph.csv?id=NAPM` → 404). ISM data is licensed.
Recommend dropping ISM (or showing the date only, hard-coded as "1st business day 10:00
ET", which violates the no-literal rule and would still have no value).

---

## 3. Nasdaq — IPO calendar works (browser UA required); dividend calendar is reference only

```
GET https://api.nasdaq.com/api/ipo/calendar?date=2026-09
  UA: Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36
  Accept: application/json, text/plain, */*
HTTP 200, 11.6 KB, ~2.3 s
{"data":{"priced":{"asOf","headers","rows"},"upcoming":{"upcomingTable":{"headers","rows"},"lastUpdatedTime"},
         "filed":{...},"withdrawn":{...},"month":9,"year":2026,"totalResults":8},
 "status":{"rCode":200,...}}
```

Row shapes (all values STRINGS, dates `M/D/YYYY`, money `"$2,530,000,000"` with or
without `.00`, price may be a range `"40.00-44.00"`):

```
priced:   {dealID, proposedTickerSymbol:"OIG", companyName, proposedExchange:"NASDAQ Global Select",
           proposedSharePrice:"12.00", sharesOffered:"20,000,000", pricedDate:"9/18/2026",
           dollarValueOfSharesOffered:"$240,000,000", dealStatus:"Priced"}          (7 rows)
upcoming: {..., proposedSharePrice:"40.00-44.00", expectedPriceDate:"9/30/2026", ...}  (4 rows; e.g. OURA $2.53B)
filed:    {dealID, proposedTickerSymbol, companyName, filedDate, dollarValueOfSharesOffered} (32 rows)
withdrawn:{..., proposedTickerSymbol: null possible, withdrawDate}                    (8 rows)
lastUpdatedTime: "LAST UPDATED: 09/24/2026* - Source: EDGAR® Online"
```

`?date=2026-10` returned all-empty (upcoming deals appear only ~1–2 weeks out), so fetch
the current AND next month. Undocumented, unauthenticated, no published rate limit — poll
it a few times a day at most. Expect ~50% SPAC/tiny deals; a `min_offer_usd` filter
belongs in config.

Dividend calendar (reference): `GET api.nasdaq.com/api/calendar/dividends?date=2026-09-28`
→ `{"data":{"calendar":{"headers":{...},"rows":[{"symbol":"GSBC","dividend_Ex_Date":"9/28/2026",
"payment_Date":"10/14/2026","record_Date":"9/28/2026","dividend_Rate":0.43,
"indicated_Annual_Dividend":1.72,"announcement_Date":"9/16/2026"}, …]}}}` — only 8 rows
for that date (incomplete market-wide). The per-symbol history
`/api/quote/JPM/dividends?assetclass=stocks` answers *"Dividend History for Non-Nasdaq
symbols is not available"* — useless for NYSE names. **Use Schwab for the watchlist.**

---

## 4. Schwab dividends via this repo's proxy (code read only — no live call)

- `schwab_proxy.py` `/quote` and `/quotes` **hard-code `fields=quote`**, so the fundamental
  block is dropped. `proxy_client.SchwabClient.get_quotes` returns a flattened
  `{last, change, change_pct, high, low, volume}` — no dividend fields.
- `/instruments?symbol=X&projection=fundamental` exists; `proxy_client.get_fundamentals`
  unwraps `instruments[0].fundamental`. Per Schwab's Market Data API that object carries
  `dividendAmount`, `dividendYield`, `dividendDate` (ex-date), `dividendPayDate`,
  `dividendPayAmount`, `dividendFreq`, `nextDividendDate`, `nextDividendPayDate`,
  `declarationDate` (field names from Schwab's schema — **unverified here**; confirm with
  one live call on prod before building).
- `/passthrough?endpoint=/quotes&params=symbols=JPM` **with no `fields`** returns every
  root node incl. `fundamental` (`divAmount`, `divYield`, `divExDate`, `divPayDate`,
  `divFreq`, `divPayAmount`, `nextDivExDate`, `nextDivPayDate`, `declarationDate`,
  `lastEarningsDate`). `services/trade_svc/deepdive/engine.py` already does exactly this
  and reads `divAmount` / `divYield`. ⚠ `/passthrough` splits `params` on commas, so it
  is **one symbol per call** (the deep-dive code raises on a comma). ~80 watchlist names ×
  once a day is negligible against the 68–76k/day budget; a cleaner fix is an optional
  `fields` query param on the proxy's `/quotes` (then one batched call).
- Nothing in the repo stores a dividend date today (`rescue.py` says so explicitly).

**Where it should live.** CLAUDE.md records that `news_svc` "calls neither the proxy nor
Claude" — making it a Schwab caller changes that invariant. `trade_svc` already calls the
proxy and owns the once-a-day `earnings_calendar.refresh` (Alpha Vantage, gated to one pull
per CT day in `compute._refresh_earnings_calendar`). The `shared/earnings.py` pattern fits
directly: a `dividends` table (`symbol, ex_date, pay_date, record_date, amount, frequency,
declared_date, recorded_at`, PK `(symbol, ex_date)`) written nightly by `trade_svc`, with a
`shared/dividends.py` read path (`init_db(db_path=None)` resolved at call time, cursor
row_factory — both lessons from `shared/earnings.py`), so `news_svc` builds the tile from a
shared store without importing `trade_svc`. It can share `earnings_calendar.db` or take a
new `repo_paths.DIVIDENDS_DB`; a new file is cleaner (the earnings DB's coverage semantics
are specific). The repo-root `conftest.py` connect guard covers `services/trade_svc/data`
already. Same three-valued honesty as earnings: "no dividend" (non-payer) ≠ "not fetched".

---

## 5. Reusable pieces and how a calendar view slots in

- `services/news_svc/fetch.http_fetch(url, *, etag, last_modified, user_agent, timeout, …)`
  — conditional GET with a total-deadline watchdog and body cap. Reuse as-is; pass the
  per-source UA. `max_body_bytes` 5 MB covers the 543 KB Fed JSON.
- `shared/market_calendar` — `is_trading_day`, `next_trading_day`, `is_regular_hours`,
  holidays; use it to cadence the calendar job and to skip holidays when projecting
  "every Thursday" claims rows.
- `news_svc` layout: `adapters/*` are pure parsers over bytes (add `adapters/econ.py`:
  `parse_bls_ics`, `parse_bea_ics`, `parse_fed_calendar`, `parse_fred_csv`,
  `parse_nasdaq_ipo`); `compute.poll_now` + `_POLL_LOCK`; `handlers.publish_*` with
  `skip_unchanged=True` and no timestamp in the payload; `scheduler.loop` (30 s tick,
  per-key cadence from TOML).
- **Proposed**: a second branch in `scheduler.loop` with its own last-run clock, e.g.
  `[calendar] refresh_min = 60` (schedule sources, IPO) plus a **release-watch** mode:
  from a release's scheduled time until its value appears, poll that series' FRED CSV
  every `release_poll_min = 2` for up to `release_watch_min = 30`. Publishes
  `cache:news:calendar` / `events:news:calendar` (and `cache:news:calendar_public` if
  the Tools screen shows it — the public copy rule in CLAUDE.md applies: build it
  separately, never filter the private view). Payload sketch:

```json
{"events":   [{"id":"fed:2026-10-28:fomc-meeting","group":"events","kind":"fomc",
               "title":"FOMC statement","at":"2026-10-28T13:00:00-05:00","tz_src":"ET",
               "source":"Federal Reserve","url":"https://www.federalreserve.gov/..."}],
 "data":     [{"id":"cpi","title":"CPI","period":"Sep 2026","at":"2026-10-14T07:30:00-05:00",
               "prior":"+0.4% m/m","actual":null,"unit":"% m/m","series":"CPIAUCSL",
               "source":"BLS"}],
 "dividends":[{"symbol":"JPM","ex_date":"2026-10-06","pay_date":"2026-10-31","amount":1.40}],
 "ipos":     [{"symbol":"OURA","company":"Oura Inc.","status":"upcoming","date":"2026-09-30",
               "price":"40.00-44.00","offer_usd":2530000000,"exchange":"NASDAQ Global Select"}],
 "sources":  {"bls":"ok","bea":"ok","fed":"ok","fred":"ok","nasdaq_ipo":"ok","dividends":"stale"}}
```

  Store every time as an aware ISO instant and render in CT (the page already renders
  CT). Keep the last good per-source result when a fetch fails (per-source `status`,
  like `cache:news:status`), so one blocked host blanks one tile group, not the page.
- Webgui: `news_view.py` stays the pure transform layer (add `calendar_tiles(payload,
  now)`); `news.py` gets a tile strip above the feed built with `ui_kit` (the guard test
  forbids raw `ui.*` buttons/tables). `draw_rows` is headline-row shaped; tiles need
  their own drawer. `watch_view("news:calendar", …)` for repaints.
- **SEC panel (B)** needs no new fetching: `kind in ("edgar_form4","edgar_filings")` rows
  are already in `cache:news:feed`; filter them out of the main list and into a second
  region (or a second view published by `store.newest(kinds=…)` so the 300-row window
  is not eaten by filings). `news_view.detail_line` already formats both. Optionally add
  `8-K` to `SEC Offerings`' `forms` (item-level 8-K parsing is a separate job).
- Config: every new key goes in `config/news.toml` (or a new `config/econ_calendar.toml`)
  **and** `webgui/config_schema.py`, or `test_config_schema.py` fails.

---

## 6. Impact rank (A) — inputs available today and a proposed rules scheme

Per item today: `source` (+ merged `sources`), `original_source` (Google News publisher),
`kind` (`rss | yahoo_ticker | google_news | edgar_form4 | edgar_filings`), `tickers`
(explicit-only extraction against the watchlist universe), `topics` (only EDGAR sets any:
`["SEC Filing","Insider Transaction"]` / `["SEC Filing","Offering"]`), `detail` (Form 4:
`total_value`, `groups`, `relationship`, `insider(s)`, `transaction_date`; filings:
`form`), `title`, `teaser`, `published_at`, `first_seen`. There is **no market cap** on an
item, so "small cap" can only be proxied (not in the watchlist universe).

Proposal — an additive integer score computed **service-side at ingest** (so the public
view carries it too and the rank is one computation), stored in `detail` or a new
`impact` column, mapped to a band:

```toml
[impact]
high_at = 6          # score >= high_at  -> High
med_at  = 3          # score >= med_at   -> Med, else Low

[impact.keywords]    # case-insensitive whole-word/phrase match on title (+teaser at half weight?)
# tier -> points; first match per tier counts once
tier1 = { points = 5, words = ["FOMC", "rate decision", "rate cut", "rate hike", "Fed chair",
          "CPI", "inflation report", "jobs report", "nonfarm payrolls", "payrolls",
          "bankruptcy", "chapter 11", "trading halt", "halted", "SEC charges", "indicted",
          "acquire", "acquisition", "merger", "to buy", "takeover", "tender offer",
          "guidance cut", "cuts guidance", "raises guidance", "profit warning", "delist"] }
tier2 = { points = 3, words = ["downgrade", "upgrade", "beats", "misses", "earnings",
          "PPI", "PCE", "GDP", "retail sales", "jobless claims", "tariff", "sanctions",
          "buyback", "dividend cut", "recall", "investigation", "lawsuit", "layoffs",
          "price target", "offering", "stake"] }
tier3 = { points = 1, words = ["outlook", "forecast", "analyst", "sector", "rally", "selloff"] }

[impact.source_points]   # by feed name; absent = 0
"Federal Reserve" = 3
"WSJ"             = 1
"Truth Social"    = 2      # market-moving policy posts; keywords decide the rest
"ZeroHedge"       = -1
"Benzinga"        = 0

[impact.watchlist]
per_ticker = 2           # +2 if any ticker is in the followed universe
max        = 2

[impact.form4]           # by detail.total_value (sum of code-P buys)
bands = [ [250000, 1], [1000000, 3], [10000000, 6] ]   # >= usd -> points
officer_bonus = 1        # relationship contains CEO / CFO / Director

[impact.filings]         # by detail.form; dilution weighs more on a tracked ticker
"424B5"  = 3             # takedown = shares actually being sold now
"S-1"    = 1
"S-3"    = 2
"S-3ASR" = 1             # WKSI automatic shelf: large issuers, routine
untracked_penalty = -1   # not in universe (likely micro-cap; noisy)

[impact.recency]
stale_after_h = 24       # older than this caps at Med
```

Rules worth pinning in tests: a keyword counts once per tier (a headline repeating "Fed"
cannot stack); `title_key`-style casefolded matching with word boundaries so "CPI" does
not hit "CPIX" and "beats" does not hit "heartbeats"; multi-source merge (`sources` > 1)
adds +1 (two outlets reporting it is itself a signal); a missing/NaN `total_value` scores
0, never a band (the NaN-pins-the-bound trap); the band thresholds are config and the
Settings catalogue shows them. Offer a "High only" filter chip on the page; the
Desk/Symbol headline strips could sort by band. Output one of `"high" | "med" | "low"`
plus the score and the matched reasons (e.g. `["tier1:FOMC","source:Federal Reserve"]`)
so a user can see *why* — a rank without reasons is untestable by eye.

---

## 7. Recommendation summary

| need | use | credential |
|---|---|---|
| Release dates + times (CPI, PPI, NFP/UNRATE, ECI, JOLTS) | BLS `bls.ics` (Eastern, explicit) | none (repo UA) |
| GDP, PCE / Personal Income dates + times + period | BEA ICS (UTC) | none |
| Retail sales, claims dates | FRED calendar HTML by rid (Central) — or Census list + "every Thursday" | none |
| FOMC meeting/statement/presser/minutes, Chair speeches/testimony, Beige Book | Fed `calendar.json` (Eastern) | none |
| Actual + prior values | `fredgraph.csv` per series (key-free) — official FRED API as fallback | none; **optional free FRED key** |
| IPO calendar | Nasdaq `api/ipo/calendar?date=YYYY-MM` (this + next month) | none, **browser UA required** |
| Watchlist dividends | Schwab via proxy `/passthrough` `/quotes` (no `fields`) or `/instruments` fundamental — written by `trade_svc` into a shared store | existing Schwab session |
| ISM | not available free — drop | licensed |
| Consensus | not requested; no free source anyway | — |

Blockers / decisions for the owner: (1) per-source UA config; (2) whether `news_svc` may
call the proxy (recommend no — `trade_svc` writes dividends, `news_svc` reads);
(3) optional FRED API key (`FRED_API_KEY` env, gitignored like `ALPHAVANTAGE_API_KEY`)
only if the undocumented CSV proves flaky; (4) BLS ics is one calendar year — January
gap until BLS posts the next file; (5) confirm Schwab's dividend field names with one
live call on prod; (6) the Nasdaq and fredgraph endpoints are undocumented — degrade
per source and show which group is stale.

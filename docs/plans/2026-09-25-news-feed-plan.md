# Market News Feed Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** A sixth Tier-2 service (`news_svc`) that polls free public feeds (RSS, Google News, Yahoo per-ticker, EDGAR Form 4 buys and shelf offerings), stores them, and publishes two Redis views read by a private `/news` page, a Desk strip, a Symbol-page band, and a read-only public screen under the website's Tools menu.

**Architecture:** `services/news_svc` = five pure adapters (`bytes → [Item]`) + an injectable fetcher + a SQLite store + one poll cycle that publishes `cache:news:feed` (all items), `cache:news:feed_public` (only items from feeds flagged `public`, decided at ingest) and `cache:news:status`. Tier 1 reads the views; the public page reads only `feed_public` and enqueues nothing. Design: [2026-09-25-news-feed-design.md](2026-09-25-news-feed-design.md).

**Tech Stack:** Python 3.11, `feedparser` (new), stdlib `xml.etree` for Form 4, `requests`, SQLite, the repo's `shared.bus` / `shared.config_toml` / `services._scaffold`, NiceGUI via `pages.ui_kit`.

---

## Read before starting

- **CLAUDE.md**, at least: "3-tier architecture" (the Tier-1 import allow-list), "STANDING RULE — configurable by default", "Observability", "The public live screens", "Tests" (run each suite from its folder; compare the failing SET).
- Every command below is written for the Windows checkout: the venv is `"D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe"`. Set `PY` to it once per shell:
  ```bash
  PY="D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe"
  ```
  Service suites run **from the repo root** (`$PY -m pytest services/news_svc -q`); webgui tests run **from `webgui/`**.
- **No test may touch the network or a live SQLite store.** The root `conftest.py` blocks both. Every adapter takes `bytes`; every store test uses `tmp_path`.
- Commit after every task. Commit messages end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- Never `.style()` in a page (the Tailwind-first rule); never a raw `ui.button` / `ui.dialog` / `ui.notify` / `ui.table` — use `pages.ui_kit` (`tests/test_ui_kit_guard.py` fails otherwise).

---

### Task 1: The dependency and the port

**Files:**
- Modify: `requirements.txt` (the third-party block, alphabetical near `fastapi`)
- Modify: `requirements.lock`
- Modify: `config/ports.toml:24-30` (`[services]`)
- Modify: `repo_paths.py:142-172` (beside `TRADE_SVC_DATA`)
- Modify: `conftest.py:48-54` (`_LIVE_DIRS`)
- Modify: `tools/backup_local.py:116-128` (`DATA_TREES`)
- Test: `tests/test_news_wiring.py` (new)

**Step 1: Install and pin feedparser**

```bash
$PY -m pip install feedparser==6.0.11 && $PY -c "import feedparser, sgmllib3k" 2>/dev/null; $PY -m pip show feedparser | grep -i "^Requires"
```
Expected: `Requires: sgmllib3k`. Add **both** to `requirements.txt` (`feedparser==6.0.11`, `sgmllib3k==1.0.0`) and to `requirements.lock` in alphabetical position. ⚠ CLAUDE.md: a dep missing from the lock ships to prod as a feature that silently does nothing.

**Step 2: Write the failing wiring test**

`tests/test_news_wiring.py`:
```python
"""news_svc is wired everywhere a service must be: port, data dir, guards."""
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_news_has_a_service_port():
    import repo_paths
    assert repo_paths.SERVICE_PORTS["news"] == 8216
    assert repo_paths.SERVICE_URLS["news"] == "http://127.0.0.1:8216"


def test_news_db_lives_under_the_service_and_is_guarded():
    import repo_paths
    import conftest
    assert repo_paths.NEWS_DB == ROOT / "services" / "news_svc" / "data" / "news.db"
    assert repo_paths.NEWS_SVC_DATA in conftest._LIVE_DIRS


def test_news_data_is_backed_up():
    from tools import backup_local
    assert "services/news_svc/data" in backup_local.DATA_TREES


def test_feedparser_is_locked():
    txt = (ROOT / "requirements.txt").read_text()
    lock = (ROOT / "requirements.lock").read_text()
    for pkg in ("feedparser", "sgmllib3k"):
        assert pkg in txt, pkg
        assert pkg in lock, f"{pkg} missing from requirements.lock — prod would not install it"
```

**Step 3: Run to verify it fails**

```bash
$PY -m pytest tests/test_news_wiring.py -q
```
Expected: FAIL (`KeyError: 'news'`, `AttributeError: NEWS_DB`).

**Step 4: Wire it**

`config/ports.toml` `[services]`: add `news      = 8216   # public-feed news collector (news_svc)`.

`repo_paths.py`, after `EARNINGS_CALENDAR_DB`:
```python
# news_svc: the public-feed collector's own store (gitignored data/).
NEWS_SVC_DATA = REPO_ROOT / "services" / "news_svc" / "data"
NEWS_DB = NEWS_SVC_DATA / "news.db"
```

`conftest.py` `_LIVE_DIRS`: add `_ROOT / "services" / "news_svc" / "data",`.

`tools/backup_local.py` `DATA_TREES`: add `"services/news_svc/data",` after the trade_svc line.

Check the data dir is gitignored: `git check-ignore -v services/news_svc/data/news.db` must print a rule. If it prints nothing, add `services/news_svc/data/` to `.gitignore`.

**Step 5: Run all affected suites**

```bash
$PY -m pytest tests -q
```
Expected: all pass (the env-profile tests build their own dicts, so the new port needs no fixture edit).

**Step 6: Commit**

```bash
git add requirements.txt requirements.lock config/ports.toml repo_paths.py conftest.py tools/backup_local.py tests/test_news_wiring.py .gitignore
git commit -m "feat(news): reserve :8216 for news_svc, its data dir, and pin feedparser"
```

---

### Task 2: `config/news.toml` and its loader

**Files:**
- Create: `config/news.toml`
- Create: `shared/news_config.py`
- Test: `shared/tests/test_news_config.py`

**Step 1: Write the failing test**

```python
import pathlib

from shared import news_config as nc

ROOT = pathlib.Path(__file__).resolve().parents[2]


def test_defaults_are_the_real_values():
    cfg = nc.load()
    assert cfg["collector"]["rth_poll_min"] == 5
    assert cfg["collector"]["view_items"] == 300
    assert cfg["trending"]["window_h"] == 6


def test_every_shipped_feed_has_a_name_kind_and_flags():
    for feed in nc.feeds():
        assert feed["name"] and feed["kind"] in nc.KINDS, feed
        assert isinstance(feed["enabled"], bool)
        assert isinstance(feed["public"], bool)


def test_disabled_feeds_are_not_returned(monkeypatch):
    monkeypatch.setattr(nc, "load", lambda: {
        "feeds": [{"name": "A", "kind": "rss", "url": "x", "enabled": False},
                  {"name": "B", "kind": "rss", "url": "y"}]})
    assert [f["name"] for f in nc.feeds()] == ["B"]


def test_an_unknown_kind_is_dropped_not_raised(monkeypatch):
    monkeypatch.setattr(nc, "load", lambda: {
        "feeds": [{"name": "A", "kind": "carrier_pigeon", "url": "x"}]})
    assert nc.feeds() == []


def test_ticker_set_is_the_collection_list_plus_extras(monkeypatch):
    monkeypatch.setattr(nc, "load", lambda: {"tickers": {"extras": ["zzz", "SPY", "bad symbol"]}})
    monkeypatch.setattr(nc._symbols, "collection_base", lambda: ["SPY", "QQQ"])
    assert nc.ticker_set() == ["SPY", "QQQ", "ZZZ"]
```

**Step 2: Run to verify it fails**

```bash
$PY -m pytest shared/tests/test_news_config.py -q
```
Expected: FAIL (`ModuleNotFoundError: shared.news_config`).

**Step 3: Write the config file**

`config/news.toml`:
```toml
# Market news feed — every source news_svc polls, and how often.
# Read through shared/news_config.py (built-in defaults ← this file ← config/local/news.toml).
# Every key is editable in Settings -> Configuration.

[collector]
rth_poll_min      = 5      # 08:30–15:00 CT (shared.market_calendar)
offhours_poll_min = 15
weekend_poll_min  = 60     # Saturday, Sunday and NYSE holidays
keep_days         = 7      # rows older than this are pruned from news.db
view_items        = 300    # newest rows in the published views
request_timeout_s = 20
# The SEC requires a contact in the User-Agent and allows at most 10 requests/s.
sec_user_agent    = "NeuralStrike news_svc contact@neuralstrike.co"

[tickers]
# Followed but not scanned. The base set is the GEX collection list (config/symbols.toml).
extras = []

[trending]
window_h = 6               # the Trending chips count mentions in this window

# ── feeds ──────────────────────────────────────────────────────────────────────
# kind: rss | yahoo_ticker | google_news | edgar_form4 | edgar_filings
# enabled (default true) · public (default true: shown on live.neuralstrike.co/news)

[[feeds]]
name = "MarketWatch"
kind = "rss"
url  = "https://feeds.content.dowjones.io/public/rss/mw_topstories"

[[feeds]]
name = "CNBC"
kind = "rss"
url  = "https://www.cnbc.com/id/100003114/device/rss/rss.html"

[[feeds]]
name = "ZeroHedge"
kind = "rss"
url  = "https://feeds.feedburner.com/zerohedge/feed"

[[feeds]]
name = "Benzinga"
kind = "rss"
url  = "https://www.benzinga.com/feed"

[[feeds]]
name = "Federal Reserve"
kind = "rss"
url  = "https://www.federalreserve.gov/feeds/press_all.xml"

[[feeds]]
name = "PR Newswire"
kind = "rss"
url  = "https://www.prnewswire.com/rss/financial-services-latest-news/financial-services-latest-news-list.rss"

[[feeds]]
name = "GlobeNewswire"
kind = "rss"
url  = "https://www.globenewswire.com/RssFeed/subjectcode/13-Earnings%20Releases%20and%20Operating%20Results/feedTitle/GlobeNewswire%20-%20Earnings%20Releases"

[[feeds]]
name = "Business Wire"
kind = "rss"
url  = "https://feed.businesswire.com/rss/home/?rss=G1QFDERJXkJeEFpRVQ=="

[[feeds]]
name = "Truth Social"
kind = "rss"
url  = "https://trumpstruth.org/feed"

[[feeds]]
name = "Yahoo Finance"
kind = "yahoo_ticker"
url  = "https://feeds.finance.yahoo.com/rss/2.0/headline?s={symbol}&region=US&lang=en-US"

[[feeds]]
name  = "WSJ"
kind  = "google_news"
query = "site:wsj.com markets when:1d"

[[feeds]]
name  = "Seeking Alpha"
kind  = "google_news"
query = "site:seekingalpha.com when:1d"

[[feeds]]
name = "SEC Insider Buys"
kind = "edgar_form4"
min_value_usd = 1000000   # every code-P buy on a tracked ticker, plus any buy at or above this

[[feeds]]
name  = "SEC Offerings"
kind  = "edgar_filings"
forms = ["S-1", "S-3", "424B5"]
```

⚠ Feed URLs are the ones probed on 2026-09-25 except PR Newswire / GlobeNewswire / Business Wire / Fed / Benzinga, which come from those sites' published feed pages — **verify each with `curl -sI -A "Mozilla/5.0" <url> | head -1` before committing** and replace any that is not `200`.

**Step 4: Write the loader**

`shared/news_config.py`:
```python
"""config/news.toml — the news collector's feeds, cadence and ticker extras.

Tier-2 (news_svc) reads it; Tier 1 reads only ``trending.window_h``. Stdlib +
shared.config_toml + shared.symbols, nothing else."""
import copy
import logging

from repo_paths import REPO_ROOT
from shared import symbols as _symbols
from shared.config_toml import toml_loader
from shared.symbols import clean_symbol

log = logging.getLogger(__name__)

NEWS_TOML = REPO_ROOT / "config" / "news.toml"

KINDS = ("rss", "yahoo_ticker", "google_news", "edgar_form4", "edgar_filings")

DEFAULTS = {
    "collector": {
        "rth_poll_min": 5, "offhours_poll_min": 15, "weekend_poll_min": 60,
        "keep_days": 7, "view_items": 300, "request_timeout_s": 20,
        "sec_user_agent": "NeuralStrike news_svc contact@neuralstrike.co",
    },
    "tickers": {"extras": []},
    "trending": {"window_h": 6},
    "feeds": [],
}

load, reset_cache = toml_loader(NEWS_TOML, DEFAULTS, label="news.toml")


def feeds() -> list:
    """Enabled feeds with ``enabled`` / ``public`` filled in; unknown kinds dropped."""
    out = []
    for raw in load().get("feeds") or []:
        if not isinstance(raw, dict):
            continue
        feed = copy.deepcopy(raw)
        feed.setdefault("enabled", True)
        feed.setdefault("public", True)
        if feed.get("kind") not in KINDS:
            log.warning("news.toml: feed %r has unknown kind %r - skipped",
                        feed.get("name"), feed.get("kind"))
            continue
        if not feed.get("name") or not feed["enabled"]:
            continue
        out.append(feed)
    return out


def ticker_set() -> list:
    """The GEX collection list plus ``[tickers] extras``, cleaned, order kept."""
    out, seen = [], set()
    for raw in list(_symbols.collection_base()) + list(load()["tickers"].get("extras") or []):
        sym = clean_symbol(raw)
        if sym and sym not in seen:
            seen.add(sym)
            out.append(sym)
    return out
```

**Step 5: Run to verify it passes**

```bash
$PY -m pytest shared/tests/test_news_config.py -q
```
Expected: 5 passed.

**Step 6: Commit**

```bash
git add config/news.toml shared/news_config.py shared/tests/test_news_config.py
git commit -m "feat(news): config/news.toml and its loader"
```

---

### Task 3: The Settings → Configuration catalogue entry

**Files:**
- Modify: `webgui/config_schema.py` (add `NEWS = "news_svc"`, a `RESTART_LABELS` entry, `_NEWS`, and put it in `FILES`)
- Test: existing `webgui/tests/test_config_schema.py` (parametrised over `EDITABLE`)

**Step 1: Run the existing coverage test to see it fail**

```bash
cd webgui && $PY -m pytest tests/test_config_schema.py -q -k "catalogued_or_explained"
```
Expected: FAIL `uncatalogued config files: {'news.toml'}`.

**Step 2: Add the catalogue**

In `config_schema.py` beside `MARKET = "market_svc"` add `NEWS = "news_svc"` and `NEWS: "News service"` in `RESTART_LABELS`. Then, before `FILES`:

```python
# ─────────────────────────────────────────────────────────────────────────────
# Market news — config/news.toml
# ─────────────────────────────────────────────────────────────────────────────
_NEWS = ConfigFile(
    name="news.toml", title="Market news", icon="newspaper",
    summary="Which public feeds the news collector reads, how often, and which "
            "of them the public site may show.",
    restart=(NEWS,),
    caution="Every feed is a public RSS, Google News or SEC feed. A feed marked "
            "not public stays in the app and never reaches live.neuralstrike.co.",
    sections=(
        Section("Polling", "How often every feed is read. Faster costs nothing "
                "in API budget but is discourteous to the publishers.", (
            Field("collector.rth_poll_min", "During market hours",
                  "Minutes between polls, 08:30–15:00 CT.", kind="int", unit="min", min=1, max=60, step=1),
            Field("collector.offhours_poll_min", "Outside market hours", "",
                  kind="int", unit="min", min=1, max=240, step=1),
            Field("collector.weekend_poll_min", "Weekends and holidays", "",
                  kind="int", unit="min", min=5, max=720, step=5),
            Field("collector.keep_days", "Keep items for",
                  "Older rows are pruned from the store.", kind="int", unit="days", min=1, max=90, step=1),
            Field("collector.view_items", "Rows published",
                  "The newest rows the page, the Desk and the Symbol page read.",
                  kind="int", min=50, max=1000, step=50),
            Field("collector.request_timeout_s", "Request timeout", "",
                  kind="int", unit="s", min=5, max=120, step=5),
            Field("collector.sec_user_agent", "SEC User-Agent",
                  "The SEC requires a contact address in every request.", kind="text"),
        )),
        Section("Tickers", "The per-ticker feeds and the watchlist filter use the "
                "gamma collection list (Symbols & watchlists) plus these.", (
            Field("tickers.extras", "Extra tickers", "", kind="symbols"),
        )),
        Section("Trending", "", (
            Field("trending.window_h", "Trending window",
                  "The Trending chips count ticker mentions in this window.",
                  kind="int", unit="h", min=1, max=48, step=1),
        )),
        Section("Feeds", "One entry per source, in display order.", (
            Field("feeds.*.name", "Name", "", kind="text"),
            Field("feeds.*.kind", "Kind", "rss · yahoo_ticker · google_news · "
                  "edgar_form4 · edgar_filings", kind="choice",
                  choices=("rss", "yahoo_ticker", "google_news", "edgar_form4", "edgar_filings")),
            Field("feeds.*.url", "Feed URL", "For rss and yahoo_ticker ({symbol} "
                  "expands over the ticker set).", kind="text", optional=True),
            Field("feeds.*.query", "Google News search", "", kind="text", optional=True),
            Field("feeds.*.forms", "SEC form types", "", kind="symbols", optional=True),
            Field("feeds.*.min_value_usd", "Insider buy floor",
                  "A buy on an untracked ticker is shown only at or above this.",
                  kind="money", min=0, optional=True),
            Field("feeds.*.enabled", "Enabled", "", kind="bool", optional=True),
            Field("feeds.*.public", "Show on the public site", "", kind="bool", optional=True),
        )),
    ),
)
```
Add `_NEWS` to the `FILES` tuple (after `_SYMBOLS`).

**Step 3: Run the whole schema suite**

```bash
cd webgui && $PY -m pytest tests/test_config_schema.py -q
```
Expected: all pass. If `test_every_shipped_value_round_trips_through_its_own_field` fails on `feeds.*.forms`, change that field's kind to `"text"` and store forms as a comma list — check `config_store.flatten` first; `symbols` kind expects tickers matching `SYMBOL_RE`, and `424B5` matches it.

**Step 4: Commit**

```bash
git add webgui/config_schema.py
git commit -m "feat(news): catalogue news.toml in Settings -> Configuration"
```

---

### Task 4: Items — canonical URL, id, ticker attribution

**Files:**
- Create: `services/news_svc/__init__.py` (empty)
- Create: `services/news_svc/items.py`
- Create: `services/news_svc/tests/__init__.py` (empty), `services/news_svc/tests/conftest.py` (copy of `services/market_svc/tests/conftest.py`)
- Test: `services/news_svc/tests/test_items.py`

**Step 1: Write the failing tests**

```python
from services.news_svc import items


def test_canonical_url_strips_tracking_and_fragment():
    u = "https://www.marketwatch.com/story/x-2e7c5c10?mod=mw_rss_topstories&utm_source=a#frag"
    assert items.canonical_url(u) == "https://www.marketwatch.com/story/x-2e7c5c10"


def test_canonical_url_keeps_meaningful_query():
    u = "https://finance.yahoo.com/m/abc/x.html?p=1"
    assert items.canonical_url(u) == u


def test_item_id_is_stable_and_url_based():
    a = items.item_id("https://a.com/x?utm_source=1")
    b = items.item_id("https://a.com/x")
    assert a == b and len(a) == 16


def test_title_key_normalises_case_and_punctuation():
    assert items.title_key("Apple (AAPL) Eyes Apple Pay!") == items.title_key("apple aapl eyes apple pay")


def test_tickers_only_from_explicit_cashtags_or_parentheses():
    universe = ["AAPL", "NVDA", "MSFT"]
    assert items.extract_tickers("Apple (AAPL) and $NVDA rally; Microsoft too", universe) == ["AAPL", "NVDA"]


def test_a_company_name_never_tags():
    assert items.extract_tickers("Apple rallies", ["AAPL"]) == []


def test_tickers_outside_the_universe_are_dropped():
    assert items.extract_tickers("(ZZZZ) $SPY", ["SPY"]) == ["SPY"]


def test_make_item_fills_every_field_and_cleans_symbols():
    it = items.make_item(source="MarketWatch", title="T", url="https://a.com/x?utm_x=1",
                         published_at="2026-09-25T21:57:00+00:00", public=True,
                         tickers=["nvda", "bad symbol"], now="2026-09-25T22:00:00+00:00")
    assert it["id"] == items.item_id("https://a.com/x")
    assert it["tickers"] == ["NVDA"]
    assert it["teaser"] == "" and it["topics"] == [] and it["detail"] == {}
    assert it["first_seen"] == "2026-09-25T22:00:00+00:00"
```

**Step 2: Run to verify it fails**

```bash
$PY -m pytest services/news_svc/tests/test_items.py -q
```
Expected: FAIL (`ModuleNotFoundError`).

**Step 3: Implement**

`services/news_svc/items.py`:
```python
"""The normalized news item and the pure rules over it (no I/O).

An item is a plain dict so it round-trips through SQLite and Redis unchanged:
id · source · original_source · title · teaser · url · published_at ·
first_seen · tickers · kind · topics · detail · public
"""
import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from shared.symbols import clean_symbol

FIELDS = ("id", "source", "original_source", "title", "teaser", "url",
          "published_at", "first_seen", "tickers", "kind", "topics", "detail",
          "public")

# Query keys that identify a click, not a page.
_TRACKING = re.compile(r"^(utm_|mod$|ref$|src$|cmpid$|ncid$|guccounter$|oc$)")

_CASHTAG = re.compile(r"\$([A-Z]{1,6})\b")
_PAREN = re.compile(r"\(([A-Z]{1,6}(?::[A-Z]+)?)\)")
_NOISE = re.compile(r"[^a-z0-9 ]+")


def canonical_url(url: str) -> str:
    parts = urlsplit((url or "").strip())
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not _TRACKING.match(k)]
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path,
                       urlencode(query), ""))


def item_id(url: str) -> str:
    return hashlib.sha1(canonical_url(url).encode()).hexdigest()[:16]


def title_key(title: str) -> str:
    return " ".join(_NOISE.sub(" ", (title or "").lower()).split())


def extract_tickers(text: str, universe) -> list:
    """Tickers named EXPLICITLY (``$NVDA`` or ``(NVDA)`` / ``(META:NASDAQ)``)
    and in ``universe``. A company name never tags — precision over recall."""
    allowed = set(universe)
    found = []
    for m in list(_CASHTAG.finditer(text or "")) + list(_PAREN.finditer(text or "")):
        sym = clean_symbol(m.group(1).split(":")[0])
        if sym and sym in allowed and sym not in found:
            found.append(sym)
    return found


def make_item(*, source, title, url, published_at, public, now, kind="rss",
              original_source="", teaser="", tickers=(), topics=(), detail=None):
    clean = []
    for raw in tickers:
        sym = clean_symbol(raw)
        if sym and sym not in clean:
            clean.append(sym)
    return {
        "id": item_id(url), "source": source, "original_source": original_source or "",
        "title": (title or "").strip(), "teaser": (teaser or "").strip(),
        "url": canonical_url(url), "published_at": published_at, "first_seen": now,
        "tickers": clean, "kind": kind, "topics": list(topics),
        "detail": dict(detail or {}), "public": bool(public),
    }
```

**Step 4: Run to verify it passes**

```bash
$PY -m pytest services/news_svc/tests/test_items.py -q
```
Expected: 8 passed.

**Step 5: Commit**

```bash
git add services/news_svc
git commit -m "feat(news): the normalized item, canonical URLs and explicit-only ticker tagging"
```

---

### Task 5: The `rss` adapter (feedparser) with fixtures

**Files:**
- Create: `services/news_svc/adapters/__init__.py`, `services/news_svc/adapters/rss.py`
- Create: `services/news_svc/tests/fixtures/marketwatch.xml` (save the real feed: `curl -s -A "Mozilla/5.0" https://feeds.content.dowjones.io/public/rss/mw_topstories -o services/news_svc/tests/fixtures/marketwatch.xml`)
- Test: `services/news_svc/tests/test_adapter_rss.py`

**Step 1: Write the failing test**

```python
import pathlib

from services.news_svc.adapters import rss

FIX = pathlib.Path(__file__).parent / "fixtures"
NOW = "2026-09-26T00:00:00+00:00"
FEED = {"name": "MarketWatch", "kind": "rss", "public": True, "url": "https://x"}


def test_parses_every_entry_with_the_feeds_name_as_source():
    out = rss.parse((FIX / "marketwatch.xml").read_bytes(), FEED, NOW, universe=["AAPL"])
    assert len(out) >= 5
    it = out[0]
    assert it["source"] == "MarketWatch" and it["kind"] == "rss" and it["public"] is True
    assert it["url"].startswith("https://www.marketwatch.com/") and "mod=" not in it["url"]
    assert it["published_at"].endswith("+00:00")
    assert it["title"]


def test_entries_without_a_link_are_skipped():
    body = b"""<rss><channel><item><title>no link</title></item>
    <item><title>ok</title><link>https://a.com/x</link></item></channel></rss>"""
    out = rss.parse(body, FEED, NOW, universe=[])
    assert [i["title"] for i in out] == ["ok"]


def test_missing_date_falls_back_to_now():
    body = b"""<rss><channel><item><title>t</title><link>https://a.com/x</link></item></channel></rss>"""
    assert rss.parse(body, FEED, NOW, universe=[])[0]["published_at"] == NOW


def test_garbage_is_an_empty_list_not_an_exception():
    assert rss.parse(b"\x00\x01 not xml", FEED, NOW, universe=[]) == []
```

**Step 2: Run to verify it fails** — `$PY -m pytest services/news_svc/tests/test_adapter_rss.py -q` → FAIL.

**Step 3: Implement**

`services/news_svc/adapters/rss.py`:
```python
"""Plain RSS / Atom → items. Pure over the fetched bytes."""
import calendar
import datetime as dt
import html
import re

import feedparser

from services.news_svc import items

_TAGS = re.compile(r"<[^>]+>")


def _iso(entry, now):
    st = entry.get("published_parsed") or entry.get("updated_parsed")
    if not st:
        return now
    return dt.datetime.fromtimestamp(calendar.timegm(st), dt.timezone.utc).isoformat()


def _teaser(entry):
    raw = entry.get("summary") or ""
    return html.unescape(_TAGS.sub("", raw)).strip()[:400]


def parse(body: bytes, feed: dict, now: str, *, universe) -> list:
    try:
        parsed = feedparser.parse(body)
    except Exception:  # noqa: BLE001 - a broken feed is an empty poll, logged by the caller
        return []
    out = []
    for entry in parsed.get("entries") or []:
        url = entry.get("link")
        if not url:
            continue
        title = entry.get("title") or ""
        out.append(items.make_item(
            source=feed["name"], kind=feed["kind"], public=feed.get("public", True),
            title=title, teaser=_teaser(entry), url=url,
            published_at=_iso(entry, now), now=now,
            original_source=(entry.get("source") or {}).get("title", ""),
            tickers=items.extract_tickers(f"{title} {_teaser(entry)}", universe)))
    return out
```

**Step 4: Run to verify it passes** → 4 passed.

**Step 5: Commit** — `git add services/news_svc/adapters services/news_svc/tests && git commit -m "feat(news): rss adapter over feedparser"`.

---

### Task 6: `yahoo_ticker` and `google_news` adapters

**Files:**
- Create: `services/news_svc/adapters/yahoo_ticker.py`, `services/news_svc/adapters/google_news.py`
- Create fixtures: `fixtures/yahoo_nvda.xml` (`curl -s "https://feeds.finance.yahoo.com/rss/2.0/headline?s=NVDA&region=US&lang=en-US"`), `fixtures/google_wsj.xml` (`curl -s -A "Mozilla/5.0" "https://news.google.com/rss/search?q=site:wsj.com+markets+when:1d&hl=en-US&gl=US&ceid=US:en"`)
- Test: `services/news_svc/tests/test_adapter_yahoo_google.py`

**Step 1: Write the failing tests**

```python
import pathlib

from services.news_svc.adapters import google_news, yahoo_ticker

FIX = pathlib.Path(__file__).parent / "fixtures"
NOW = "2026-09-26T00:00:00+00:00"


def test_yahoo_urls_expand_over_the_ticker_set():
    feed = {"name": "Yahoo Finance", "kind": "yahoo_ticker",
            "url": "https://f/rss?s={symbol}&x=1"}
    assert yahoo_ticker.urls(feed, ["SPY", "$SPX"]) == [("SPY", "https://f/rss?s=SPY&x=1")]


def test_yahoo_items_carry_the_symbol_they_were_fetched_for():
    feed = {"name": "Yahoo Finance", "kind": "yahoo_ticker", "public": True, "url": "x"}
    out = yahoo_ticker.parse((FIX / "yahoo_nvda.xml").read_bytes(), feed, NOW,
                             universe=["NVDA"], symbol="NVDA")
    assert out and all(i["tickers"][0] == "NVDA" for i in out)


def test_google_news_search_url_is_encoded():
    feed = {"name": "WSJ", "kind": "google_news", "query": "site:wsj.com markets when:1d"}
    assert google_news.url(feed) == ("https://news.google.com/rss/search?q=site%3Awsj.com+markets+when%3A1d"
                                     "&hl=en-US&gl=US&ceid=US%3Aen")


def test_google_news_uses_the_publisher_as_original_source_and_strips_the_suffix():
    feed = {"name": "WSJ", "kind": "google_news", "public": True, "query": "q"}
    out = google_news.parse((FIX / "google_wsj.xml").read_bytes(), feed, NOW, universe=[])
    assert out
    assert out[0]["original_source"]            # the <source> element, e.g. "WSJ"
    assert not out[0]["title"].endswith(" - WSJ")  # Google appends " - <publisher>"
```

**Step 2: Run to verify it fails.**

**Step 3: Implement**

`yahoo_ticker.py`:
```python
"""Yahoo Finance per-ticker RSS: one URL per symbol; every item is tagged with
the symbol it was fetched for (the feed IS the attribution)."""
from services.news_svc.adapters import rss


def urls(feed: dict, universe) -> list:
    """``[(symbol, url)]`` — index symbols ($SPX) have no Yahoo news feed."""
    return [(sym, feed["url"].replace("{symbol}", sym))
            for sym in universe if not sym.startswith("$")]


def parse(body: bytes, feed: dict, now: str, *, universe, symbol) -> list:
    out = rss.parse(body, feed, now, universe=universe)
    for it in out:
        it["kind"] = "yahoo_ticker"
        it["tickers"] = [symbol] + [t for t in it["tickers"] if t != symbol]
    return out
```

`google_news.py`:
```python
"""Google News RSS search → items. Links are Google redirects (kept as-is in v1);
the publisher comes from each entry's <source> and is stripped off the title."""
from urllib.parse import urlencode

from services.news_svc.adapters import rss

_BASE = "https://news.google.com/rss/search?"


def url(feed: dict) -> str:
    return _BASE + urlencode({"q": feed["query"], "hl": "en-US", "gl": "US", "ceid": "US:en"})


def parse(body: bytes, feed: dict, now: str, *, universe) -> list:
    out = rss.parse(body, feed, now, universe=universe)
    for it in out:
        it["kind"] = "google_news"
        pub = it["original_source"]
        if pub and it["title"].endswith(f" - {pub}"):
            it["title"] = it["title"][: -len(pub) - 3].rstrip()
    return out
```

**Step 4: Run to verify it passes.** **Step 5: Commit** `feat(news): yahoo per-ticker and google news adapters`.

---

### Task 7: EDGAR adapters — Form 4 buys and shelf offerings

**Files:**
- Create: `services/news_svc/adapters/edgar.py`
- Create fixtures: `fixtures/edgar_current_4.xml` (`curl -s -A "NeuralStrike test contact@neuralstrike.co" "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=4&owner=include&count=40&output=atom"`), `fixtures/form4_buy.xml` (a real `ownership.xml` with a code-P purchase; the LEN/Berkshire one: `https://www.sec.gov/Archives/edgar/data/0001067983/000119312526403089/xslF345X06/ownership.xml` → fetch the RAW xml sibling without `xslF345X06/`), `fixtures/edgar_current_s3.xml`, `fixtures/company_tickers.json` (trim the real file to ~20 entries incl. AAPL 320193)
- Test: `services/news_svc/tests/test_adapter_edgar.py`

**Step 1: Write the failing tests**

```python
import json
import pathlib

from services.news_svc.adapters import edgar

FIX = pathlib.Path(__file__).parent / "fixtures"
NOW = "2026-09-26T00:00:00+00:00"


def test_current_filings_atom_yields_one_entry_per_accession():
    entries = edgar.parse_current((FIX / "edgar_current_4.xml").read_bytes())
    accessions = [e["accession"] for e in entries]
    assert accessions and len(accessions) == len(set(accessions))   # Reporting+Issuer rows collapse
    e = entries[0]
    assert e["index_url"].startswith("https://www.sec.gov/Archives/edgar/data/")
    assert e["form"] and e["company"] and e["cik"].isdigit()


def test_form4_purchase_is_parsed_into_detail():
    d = edgar.parse_form4((FIX / "form4_buy.xml").read_bytes())
    assert d["symbol"] == "LEN"
    assert d["insider"] and d["relationship"]
    assert d["total_value"] > 1_000_000
    assert all(g["code"] == "P" for g in d["groups"])


def test_form4_without_a_purchase_is_none():
    body = (FIX / "form4_buy.xml").read_bytes().replace(b"<transactionCode>P</transactionCode>",
                                                       b"<transactionCode>S</transactionCode>")
    assert edgar.parse_form4(body) is None


def test_form4_item_is_kept_for_a_tracked_ticker_or_a_big_buy():
    d = {"symbol": "ZZZ", "insider": "A", "relationship": "Director", "total_value": 5_000,
         "groups": [], "transaction_date": "2026-09-23", "company": "Z Corp"}
    feed = {"name": "SEC Insider Buys", "kind": "edgar_form4", "public": True, "min_value_usd": 1_000_000}
    assert edgar.form4_item(d, feed, "https://sec/x", NOW, universe=["ZZZ"]) is not None
    assert edgar.form4_item(d, feed, "https://sec/x", NOW, universe=[]) is None
    d["total_value"] = 2_000_000
    assert edgar.form4_item(d, feed, "https://sec/x", NOW, universe=[])["tickers"] == ["ZZZ"]


def test_form4_headline_reads_like_the_reference_feed():
    d = {"symbol": "LEN", "insider": "BERKSHIRE HATHAWAY INC", "relationship": "10% Owner",
         "total_value": 136_382_789.45, "groups": [{"shares": 1_659_025}], "company": "Lennar",
         "transaction_date": "2026-09-23"}
    feed = {"name": "SEC Insider Buys", "kind": "edgar_form4", "public": True, "min_value_usd": 0}
    it = edgar.form4_item(d, feed, "https://sec/x", NOW, universe=["LEN"])
    assert it["title"] == "LEN — BERKSHIRE HATHAWAY INC (10% Owner) bought $136.4M"
    assert it["topics"] == ["SEC Filing", "Insider Transaction"]


def test_cik_map_resolves_tickers():
    m = edgar.cik_map(json.loads((FIX / "company_tickers.json").read_text()))
    assert m["320193"] == "AAPL"


def test_filing_item_names_company_and_form():
    e = {"form": "S-3", "company": "CytoDyn Inc.", "cik": "1175680", "accession": "a",
         "index_url": "https://sec/i", "updated": "2026-09-25T21:08:19-04:00"}
    feed = {"name": "SEC Offerings", "kind": "edgar_filings", "public": True, "forms": ["S-3"]}
    it = edgar.filing_item(e, feed, NOW, cik_to_ticker={"1175680": "CYDY"})
    assert it["title"] == "CytoDyn Inc. files S-3 (shelf registration)"
    assert it["tickers"] == ["CYDY"] and it["topics"] == ["SEC Filing", "Offering"]
```

**Step 2: Run to verify it fails.**

**Step 3: Implement**

`services/news_svc/adapters/edgar.py`:
```python
"""EDGAR: the "current filings" Atom feed, Form 4 purchases, shelf offerings.

Pure parsers over bytes. The FETCH plan (compute.py): one Atom fetch per form
type per poll; for each NEW Form 4 accession, one fetch of the filing folder's
``index.json`` to find the XML, one fetch of the XML. ≤ 10 req/s, always with
the configured User-Agent — the SEC blocks anything else.
"""
import re
import xml.etree.ElementTree as ET

from services.news_svc import items

ATOM = "{http://www.w3.org/2005/Atom}"
CURRENT = ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent"
           "&type={form}&owner=include&count=100&output=atom")
TICKERS_JSON = "https://www.sec.gov/files/company_tickers.json"

_TITLE = re.compile(r"^(?P<form>[\w/-]+) - (?P<company>.+?) \((?P<cik>\d+)\) \((?P<role>[^)]+)\)")
_ACCESSION = re.compile(r"/(\d{10}-\d{2}-\d{6})-index\.htm")

FORM_LABEL = {"S-1": "IPO / new registration", "S-3": "shelf registration",
              "424B5": "prospectus supplement (offering)"}


def parse_current(body: bytes) -> list:
    """Entries of a current-filings Atom, one per accession (a Form 4 lists the
    filing twice, as Reporting and as Issuer — the Issuer row wins)."""
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return []
    by_acc = {}
    for entry in root.iter(f"{ATOM}entry"):
        title = (entry.findtext(f"{ATOM}title") or "").strip()
        link = entry.find(f"{ATOM}link")
        href = link.get("href") if link is not None else ""
        m, a = _TITLE.match(title), _ACCESSION.search(href or "")
        if not m or not a:
            continue
        acc = a.group(1)
        rec = {"form": m["form"], "company": m["company"], "cik": m["cik"],
               "role": m["role"], "accession": acc, "index_url": href,
               "updated": (entry.findtext(f"{ATOM}updated") or "").strip()}
        if acc not in by_acc or m["role"] == "Issuer":
            by_acc[acc] = rec
    return list(by_acc.values())


def xml_url(index_url: str, index_json: dict):
    """The primary Form 4 XML inside a filing folder, from its ``index.json``."""
    folder = index_url.rsplit("/", 1)[0]
    for f in (index_json.get("directory") or {}).get("item") or []:
        name = f.get("name", "")
        if name.endswith(".xml") and "FilingSummary" not in name:
            return f"{folder}/{name}"
    return None


def _num(el, path):
    try:
        return float(el.findtext(path) or 0)
    except ValueError:
        return 0.0


def parse_form4(body: bytes):
    """A Form 4's open-market purchases (code P), or None when it has none."""
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return None
    groups = []
    for tx in root.iter("nonDerivativeTransaction"):
        if (tx.findtext("transactionCoding/transactionCode") or "").strip() != "P":
            continue
        shares = _num(tx, "transactionAmounts/transactionShares/value")
        price = _num(tx, "transactionAmounts/transactionPricePerShare/value")
        groups.append({"code": "P", "security": tx.findtext("securityTitle/value") or "",
                       "shares": shares, "price": price, "value": shares * price,
                       "date": tx.findtext("transactionDate/value") or ""})
    if not groups:
        return None
    rel = root.find("reportingOwner/reportingOwnerRelationship")
    labels = []
    if rel is not None:
        if (rel.findtext("isDirector") or "").strip() in ("1", "true"):
            labels.append("Director")
        if (rel.findtext("isOfficer") or "").strip() in ("1", "true"):
            labels.append(rel.findtext("officerTitle") or "Officer")
        if (rel.findtext("isTenPercentOwner") or "").strip() in ("1", "true"):
            labels.append("10% Owner")
    return {"symbol": (root.findtext("issuer/issuerTradingSymbol") or "").strip().upper(),
            "company": root.findtext("issuer/issuerName") or "",
            "insider": root.findtext("reportingOwner/reportingOwnerId/rptOwnerName") or "",
            "relationship": ", ".join(labels) or "Insider",
            "groups": groups, "total_value": sum(g["value"] for g in groups),
            "transaction_date": groups[0]["date"]}


def _money(v):
    if v >= 1e9:
        return f"${v/1e9:.1f}B"
    if v >= 1e6:
        return f"${v/1e6:.1f}M"
    if v >= 1e3:
        return f"${v/1e3:.0f}K"
    return f"${v:.0f}"


def form4_item(detail: dict, feed: dict, index_url: str, now: str, *, universe):
    sym = detail["symbol"]
    floor = float(feed.get("min_value_usd") or 0)
    if sym not in set(universe) and detail["total_value"] < floor:
        return None
    title = (f"{sym} — {detail['insider']} ({detail['relationship']}) bought "
             f"{_money(detail['total_value'])}")
    return items.make_item(
        source=feed["name"], kind="edgar_form4", public=feed.get("public", True),
        title=title, teaser="Form 4 insider purchase filed with the SEC",
        url=index_url, published_at=now, now=now, tickers=[sym],
        topics=["SEC Filing", "Insider Transaction"], detail=detail)


def cik_map(company_tickers: dict) -> dict:
    """``{cik (no leading zeros): TICKER}`` from the SEC's company_tickers.json."""
    return {str(v["cik_str"]): str(v["ticker"]).upper()
            for v in company_tickers.values() if v.get("ticker")}


def filing_item(entry: dict, feed: dict, now: str, *, cik_to_ticker):
    form = entry["form"]
    sym = cik_to_ticker.get(entry["cik"])
    title = f"{entry['company']} files {form} ({FORM_LABEL.get(form, 'registration')})"
    return items.make_item(
        source=feed["name"], kind="edgar_filings", public=feed.get("public", True),
        title=title, teaser="", url=entry["index_url"],
        published_at=entry.get("updated") or now, now=now,
        tickers=[sym] if sym else [], topics=["SEC Filing", "Offering"],
        detail={"form": form, "cik": entry["cik"], "accession": entry["accession"]})
```

**Step 4: Run to verify it passes.** Expected 7 passed. If `parse_form4` finds no `nonDerivativeTransaction`, open the fixture — some filers namespace the document; strip namespaces with `ET.iterparse` or a regex `re.sub(rb' xmlns="[^"]+"', b"", body)` before parsing.

**Step 5: Commit** `feat(news): EDGAR adapters - Form 4 purchases and shelf offerings`.

---

### Task 8: The store

**Files:**
- Create: `services/news_svc/store.py`
- Test: `services/news_svc/tests/test_store.py`

**Step 1: Write the failing tests**

```python
from services.news_svc import items, store

NOW = "2026-09-26T00:00:00+00:00"


def _item(url, title="t", public=True, published="2026-09-25T20:00:00+00:00", tickers=()):
    return items.make_item(source="S", title=title, url=url, published_at=published,
                           public=public, now=NOW, tickers=tickers)


def test_insert_is_idempotent_on_id(tmp_path):
    db = store.Store(tmp_path / "n.db")
    assert db.insert_many([_item("https://a/1"), _item("https://a/1?utm_x=2")]) == 1
    assert db.insert_many([_item("https://a/1")]) == 0
    assert len(db.newest(10)) == 1


def test_same_story_from_a_second_feed_merges_sources(tmp_path):
    db = store.Store(tmp_path / "n.db")
    a = _item("https://a/1", title="Apple rallies"); a["source"] = "Yahoo Finance"
    b = _item("https://b/2", title="Apple Rallies!"); b["source"] = "Google"
    db.insert_many([a]); db.insert_many([b])
    rows = db.newest(10)
    assert len(rows) == 1 and rows[0]["sources"] == ["Yahoo Finance", "Google"]


def test_newest_is_ordered_and_public_filter_is_by_flag(tmp_path):
    db = store.Store(tmp_path / "n.db")
    db.insert_many([_item("https://a/old", published="2026-09-25T10:00:00+00:00", public=False),
                    _item("https://a/new", published="2026-09-25T20:00:00+00:00")])
    assert [r["url"] for r in db.newest(10)] == ["https://a/new", "https://a/old"]
    assert [r["url"] for r in db.newest(10, public_only=True)] == ["https://a/new"]


def test_tickers_round_trip_and_prune(tmp_path):
    db = store.Store(tmp_path / "n.db")
    db.insert_many([_item("https://a/1", tickers=["NVDA"], published="2026-09-01T00:00:00+00:00")])
    assert db.newest(10)[0]["tickers"] == ["NVDA"]
    assert db.prune(keep_days=7, now=NOW) == 1
    assert db.newest(10) == []


def test_feed_state_round_trips(tmp_path):
    db = store.Store(tmp_path / "n.db")
    db.set_feed_state("MarketWatch", etag="abc", last_ok=NOW, error=None)
    assert db.feed_state("MarketWatch")["etag"] == "abc"
    db.set_feed_state("MarketWatch", error="boom")
    st = db.feed_state("MarketWatch")
    assert st["etag"] == "abc" and st["error"] == "boom"


def test_seen_accessions(tmp_path):
    db = store.Store(tmp_path / "n.db")
    assert db.unseen_accessions(["a", "b"]) == ["a", "b"]
    db.mark_accessions(["a"])
    assert db.unseen_accessions(["a", "b"]) == ["b"]
```

**Step 2: Run to verify it fails.**

**Step 3: Implement**

`services/news_svc/store.py`:
```python
"""news.db — items, per-feed HTTP state, and the EDGAR accessions already seen.

``db_path=None`` resolves ``repo_paths.NEWS_DB`` at CALL time (the shape CLAUDE.md
prefers, so the pytest connect guard can redirect it)."""
import datetime as dt
import json
import sqlite3

from services.news_svc import items

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id TEXT PRIMARY KEY, source TEXT NOT NULL, sources TEXT NOT NULL,
    original_source TEXT, title TEXT NOT NULL, title_key TEXT NOT NULL,
    teaser TEXT, url TEXT NOT NULL, published_at TEXT NOT NULL, first_seen TEXT NOT NULL,
    tickers TEXT NOT NULL, kind TEXT NOT NULL, topics TEXT NOT NULL,
    detail TEXT NOT NULL, public INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_items_published ON items(published_at);
CREATE INDEX IF NOT EXISTS idx_items_title_key ON items(title_key);
CREATE TABLE IF NOT EXISTS feed_state (
    name TEXT PRIMARY KEY, etag TEXT, last_modified TEXT, last_ok TEXT,
    last_poll TEXT, error TEXT
);
CREATE TABLE IF NOT EXISTS seen_accessions (accession TEXT PRIMARY KEY, seen TEXT NOT NULL);
"""

_JSON_COLS = ("sources", "tickers", "topics", "detail")


class Store:
    def __init__(self, db_path=None):
        if db_path is None:
            from repo_paths import NEWS_DB
            db_path = NEWS_DB
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._c = sqlite3.connect(str(db_path), check_same_thread=False)
        self._c.row_factory = sqlite3.Row
        self._c.executescript(SCHEMA)

    # ── items ──────────────────────────────────────────────────────────────
    def insert_many(self, rows) -> int:
        """Insert new items; a duplicate id is skipped, a same-story title within
        24 h merges its source into the existing row. Returns the count inserted."""
        n = 0
        for it in rows:
            key = items.title_key(it["title"])
            dup = self._c.execute(
                "SELECT id, sources FROM items WHERE title_key=? AND "
                "abs(julianday(published_at)-julianday(?)) < 1", (key, it["published_at"])).fetchone()
            if dup is not None:
                sources = json.loads(dup["sources"])
                if it["source"] not in sources:
                    sources.append(it["source"])
                    self._c.execute("UPDATE items SET sources=? WHERE id=?",
                                    (json.dumps(sources), dup["id"]))
                continue
            cur = self._c.execute(
                "INSERT OR IGNORE INTO items VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (it["id"], it["source"], json.dumps([it["source"]]), it["original_source"],
                 it["title"], key, it["teaser"], it["url"], it["published_at"], it["first_seen"],
                 json.dumps(it["tickers"]), it["kind"], json.dumps(it["topics"]),
                 json.dumps(it["detail"]), int(bool(it["public"]))))
            n += cur.rowcount
        self._c.commit()
        return n

    def newest(self, limit, *, public_only=False) -> list:
        where = "WHERE public=1" if public_only else ""
        rows = self._c.execute(
            f"SELECT * FROM items {where} ORDER BY published_at DESC, first_seen DESC LIMIT ?",
            (limit,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            for col in _JSON_COLS:
                d[col] = json.loads(d[col])
            d["public"] = bool(d["public"])
            d.pop("title_key", None)
            out.append(d)
        return out

    def prune(self, *, keep_days, now) -> int:
        cutoff = (dt.datetime.fromisoformat(now) - dt.timedelta(days=keep_days)).isoformat()
        cur = self._c.execute("DELETE FROM items WHERE published_at < ?", (cutoff,))
        self._c.execute("DELETE FROM seen_accessions WHERE seen < ?", (cutoff,))
        self._c.commit()
        return cur.rowcount

    # ── feed state ────────────────────────────────────────────────────────
    def feed_state(self, name) -> dict:
        r = self._c.execute("SELECT * FROM feed_state WHERE name=?", (name,)).fetchone()
        return dict(r) if r else {"name": name, "etag": None, "last_modified": None,
                                  "last_ok": None, "last_poll": None, "error": None}

    def set_feed_state(self, name, **fields):
        st = self.feed_state(name)
        st.update(fields)
        self._c.execute(
            "INSERT OR REPLACE INTO feed_state VALUES (?,?,?,?,?,?)",
            (name, st["etag"], st["last_modified"], st["last_ok"], st["last_poll"], st["error"]))
        self._c.commit()

    def all_feed_states(self) -> list:
        return [dict(r) for r in self._c.execute("SELECT * FROM feed_state").fetchall()]

    # ── EDGAR ─────────────────────────────────────────────────────────────
    def unseen_accessions(self, accessions) -> list:
        seen = {r[0] for r in self._c.execute("SELECT accession FROM seen_accessions").fetchall()}
        return [a for a in accessions if a not in seen]

    def mark_accessions(self, accessions, now=None):
        now = now or dt.datetime.now(dt.timezone.utc).isoformat()
        self._c.executemany("INSERT OR IGNORE INTO seen_accessions VALUES (?,?)",
                            [(a, now) for a in accessions])
        self._c.commit()
```

**Step 4: Run to verify it passes** → 6 passed. **Step 5: Commit** `feat(news): the news.db store`.

---

### Task 9: The fetcher and the poll cycle

**Files:**
- Create: `services/news_svc/fetch.py`
- Create: `services/news_svc/compute.py`
- Test: `services/news_svc/tests/test_compute.py`

**Step 1: Write the failing tests** (a fake fetcher; no network)

```python
import pathlib

from services.news_svc import compute, store

FIX = pathlib.Path(__file__).parent / "fixtures"
NOW = "2026-09-26T00:00:00+00:00"


class FakeFetch:
    def __init__(self, bodies):
        self.bodies, self.calls = bodies, []

    def __call__(self, url, *, etag=None, last_modified=None, user_agent=None, timeout=20):
        self.calls.append(url)
        body = self.bodies.get(url)
        if body is None:
            raise compute.FetchError(f"404 {url}")
        if body == "NOT_MODIFIED":
            return compute.Fetched(status=304, body=b"", etag=etag, last_modified=last_modified)
        return compute.Fetched(status=200, body=body, etag="e1", last_modified=None)


def _cfg(feeds):
    return {"collector": {"keep_days": 7, "view_items": 300, "request_timeout_s": 5,
                          "sec_user_agent": "t"}, "feeds": feeds}


def test_rss_feed_poll_stores_items_and_records_state(tmp_path):
    db = store.Store(tmp_path / "n.db")
    feed = {"name": "MarketWatch", "kind": "rss", "public": True, "url": "https://mw", "enabled": True}
    fetch = FakeFetch({"https://mw": (FIX / "marketwatch.xml").read_bytes()})
    res = compute.poll_feed(feed, db, fetch, universe=["AAPL"], now=NOW, cfg=_cfg([feed]))
    assert res["inserted"] > 0 and res["error"] is None
    assert db.feed_state("MarketWatch")["etag"] == "e1"


def test_not_modified_costs_nothing_and_is_not_an_error(tmp_path):
    db = store.Store(tmp_path / "n.db")
    db.set_feed_state("MarketWatch", etag="e0")
    feed = {"name": "MarketWatch", "kind": "rss", "public": True, "url": "https://mw"}
    res = compute.poll_feed(feed, db, FakeFetch({"https://mw": "NOT_MODIFIED"}),
                            universe=[], now=NOW, cfg=_cfg([feed]))
    assert res == {"feed": "MarketWatch", "inserted": 0, "error": None, "status": 304}


def test_a_failing_feed_keeps_its_items_and_reports_the_error(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(compute._degrade, "degraded", lambda area, **kw: calls.append(area))
    db = store.Store(tmp_path / "n.db")
    feed = {"name": "MarketWatch", "kind": "rss", "public": True, "url": "https://mw"}
    compute.poll_feed(feed, db, FakeFetch({"https://mw": (FIX / "marketwatch.xml").read_bytes()}),
                      universe=[], now=NOW, cfg=_cfg([feed]))
    before = len(db.newest(500))
    res = compute.poll_feed(feed, db, FakeFetch({}), universe=[], now=NOW, cfg=_cfg([feed]))
    assert res["error"] and len(db.newest(500)) == before
    assert calls == ["news.feed.MarketWatch"]


def test_yahoo_polls_one_url_per_symbol(tmp_path):
    db = store.Store(tmp_path / "n.db")
    feed = {"name": "Yahoo Finance", "kind": "yahoo_ticker", "public": True, "url": "https://y?s={symbol}"}
    body = (FIX / "yahoo_nvda.xml").read_bytes()
    fetch = FakeFetch({"https://y?s=NVDA": body, "https://y?s=SPY": body})
    compute.poll_feed(feed, db, fetch, universe=["NVDA", "SPY", "$SPX"], now=NOW, cfg=_cfg([feed]))
    assert sorted(fetch.calls) == ["https://y?s=NVDA", "https://y?s=SPY"]


def test_form4_poll_fetches_only_unseen_accessions(tmp_path):
    db = store.Store(tmp_path / "n.db")
    feed = {"name": "SEC Insider Buys", "kind": "edgar_form4", "public": True, "min_value_usd": 0}
    atom = (FIX / "edgar_current_4.xml").read_bytes()
    from services.news_svc.adapters import edgar
    entries = edgar.parse_current(atom)
    first = entries[0]
    folder = first["index_url"].rsplit("/", 1)[0]
    bodies = {edgar.CURRENT.format(form="4"): atom,
              f"{folder}/index.json": b'{"directory":{"item":[{"name":"ownership.xml"}]}}',
              f"{folder}/ownership.xml": (FIX / "form4_buy.xml").read_bytes()}
    fetch = FakeFetch(bodies)
    db.mark_accessions([e["accession"] for e in entries[1:]])   # everything but the first is old
    res = compute.poll_feed(feed, db, fetch, universe=["LEN"], now=NOW, cfg=_cfg([feed]))
    assert res["inserted"] == 1
    assert fetch.calls.count(f"{folder}/ownership.xml") == 1
    assert db.unseen_accessions([first["accession"]]) == []


def test_run_poll_publishes_both_views_and_status(tmp_path):
    from shared.bus import Bus
    bus = Bus()
    db = store.Store(tmp_path / "n.db")
    feeds = [{"name": "Pub", "kind": "rss", "public": True, "url": "https://p", "enabled": True},
             {"name": "Priv", "kind": "rss", "public": False, "url": "https://q", "enabled": True}]
    body = (FIX / "marketwatch.xml").read_bytes()
    body_q = body.replace(b"marketwatch.com/story", b"private.example/story")
    compute.run_poll(bus, db, FakeFetch({"https://p": body, "https://q": body_q}),
                     feeds=feeds, universe=[], now=NOW, cfg=_cfg(feeds))
    feed = bus.cache_get("cache:news:feed").payload
    pub = bus.cache_get("cache:news:feed_public").payload
    status = bus.cache_get("cache:news:status").payload
    assert {i["source"] for i in feed["items"]} == {"Pub", "Priv"}
    assert {i["source"] for i in pub["items"]} == {"Pub"}
    assert {s["name"] for s in status["feeds"]} == {"Pub", "Priv"}
    assert status["ts"] == NOW
```

**Step 2: Run to verify it fails.**

**Step 3: Implement**

`services/news_svc/fetch.py`:
```python
"""One conditional HTTP GET. The only network call in the service; injectable."""
import requests


class FetchError(Exception):
    pass


class Fetched:
    __slots__ = ("status", "body", "etag", "last_modified")

    def __init__(self, status, body, etag, last_modified):
        self.status, self.body, self.etag, self.last_modified = status, body, etag, last_modified


def http_fetch(url, *, etag=None, last_modified=None, user_agent=None, timeout=20) -> Fetched:
    headers = {"User-Agent": user_agent or "NeuralStrike news_svc"}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    try:
        r = requests.get(url, headers=headers, timeout=timeout)
    except requests.RequestException as exc:
        raise FetchError(str(exc)) from exc
    if r.status_code == 304:
        return Fetched(304, b"", etag, last_modified)
    if r.status_code != 200:
        raise FetchError(f"HTTP {r.status_code} {url}")
    return Fetched(200, r.content, r.headers.get("ETag"), r.headers.get("Last-Modified"))
```

`services/news_svc/compute.py`:
```python
"""The poll cycle: feeds → adapters → store → the three views. Pure over an
injected ``fetch`` so the whole thing runs in tests with no network."""
import datetime as dt
import json
import logging
import time

from services import _degrade
from services.news_svc import handlers, store as _store
from services.news_svc.adapters import edgar, google_news, rss, yahoo_ticker
from services.news_svc.fetch import FetchError, Fetched, http_fetch  # noqa: F401 (re-exported for tests)

log = logging.getLogger("news_svc.compute")

_SEC_MIN_GAP_S = 0.15          # ≤ 10 req/s is the SEC's rule; this is ~6.7/s
_cik_cache = {"ts": 0.0, "map": {}}


def _now_iso():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _conditional(feed_name, db, fetch, url, *, user_agent, timeout):
    st = db.feed_state(feed_name)
    return fetch(url, etag=st["etag"], last_modified=st["last_modified"],
                 user_agent=user_agent, timeout=timeout)


def _cik_to_ticker(fetch, user_agent, timeout):
    if time.time() - _cik_cache["ts"] < 86400 and _cik_cache["map"]:
        return _cik_cache["map"]
    got = fetch(edgar.TICKERS_JSON, user_agent=user_agent, timeout=timeout)
    _cik_cache.update(ts=time.time(), map=edgar.cik_map(json.loads(got.body)))
    return _cik_cache["map"]


def _poll_edgar(feed, db, fetch, *, universe, now, ua, timeout):
    forms = ["4"] if feed["kind"] == "edgar_form4" else list(feed.get("forms") or [])
    new_items, seen = [], []
    cik_map = None
    for form in forms:
        got = fetch(edgar.CURRENT.format(form=form), user_agent=ua, timeout=timeout)
        entries = edgar.parse_current(got.body)
        fresh = set(db.unseen_accessions([e["accession"] for e in entries]))
        for e in entries:
            if e["accession"] not in fresh:
                continue
            seen.append(e["accession"])
            if feed["kind"] == "edgar_form4":
                time.sleep(_SEC_MIN_GAP_S)
                folder = e["index_url"].rsplit("/", 1)[0]
                idx = json.loads(fetch(f"{folder}/index.json", user_agent=ua, timeout=timeout).body)
                xml = edgar.xml_url(e["index_url"], idx)
                if not xml:
                    continue
                time.sleep(_SEC_MIN_GAP_S)
                detail = edgar.parse_form4(fetch(xml, user_agent=ua, timeout=timeout).body)
                if detail:
                    it = edgar.form4_item(detail, feed, e["index_url"], now, universe=universe)
                    if it:
                        new_items.append(it)
            else:
                if e["form"] not in forms:
                    continue
                cik_map = cik_map or _cik_to_ticker(fetch, ua, timeout)
                new_items.append(edgar.filing_item(e, feed, now, cik_to_ticker=cik_map))
    db.mark_accessions(seen, now)
    return new_items, 200


def poll_feed(feed, db, fetch, *, universe, now, cfg) -> dict:
    """Poll one feed into the store. Never raises: a failure is recorded on the
    feed's state, counted as a degrade, and the feed's last items stay."""
    ua = cfg["collector"]["sec_user_agent"]
    timeout = cfg["collector"]["request_timeout_s"]
    name, kind = feed["name"], feed["kind"]
    try:
        if kind == "rss":
            got = _conditional(name, db, fetch, feed["url"], user_agent=ua, timeout=timeout)
            new = [] if got.status == 304 else rss.parse(got.body, feed, now, universe=universe)
            status = got.status
            db.set_feed_state(name, etag=got.etag, last_modified=got.last_modified)
        elif kind == "google_news":
            got = _conditional(name, db, fetch, google_news.url(feed), user_agent=ua, timeout=timeout)
            new = [] if got.status == 304 else google_news.parse(got.body, feed, now, universe=universe)
            status = got.status
            db.set_feed_state(name, etag=got.etag, last_modified=got.last_modified)
        elif kind == "yahoo_ticker":
            new, status = [], 200
            for sym, url in yahoo_ticker.urls(feed, universe):
                got = fetch(url, user_agent=ua, timeout=timeout)
                new.extend(yahoo_ticker.parse(got.body, feed, now, universe=universe, symbol=sym))
        else:
            new, status = _poll_edgar(feed, db, fetch, universe=universe, now=now, ua=ua, timeout=timeout)
        inserted = db.insert_many(new)
        db.set_feed_state(name, last_ok=now, last_poll=now, error=None)
        return {"feed": name, "inserted": inserted, "error": None, "status": status}
    except Exception as exc:  # noqa: BLE001 — one feed must not stop the cycle
        _degrade.degraded(f"news.feed.{name}")
        db.set_feed_state(name, last_poll=now, error=f"{type(exc).__name__}: {exc}"[:200])
        return {"feed": name, "inserted": 0, "error": str(exc), "status": None}


def run_poll(bus, db, fetch, *, feeds, universe, now, cfg) -> dict:
    results = [poll_feed(f, db, fetch, universe=universe, now=now, cfg=cfg) for f in feeds]
    db.prune(keep_days=cfg["collector"]["keep_days"], now=now)
    n = cfg["collector"]["view_items"]
    handlers.publish_feed(bus, db.newest(n), now)
    handlers.publish_feed_public(bus, db.newest(n, public_only=True), now)
    handlers.publish_status(bus, db.all_feed_states(), results, now)
    return {"results": results}


def poll_now(bus, db=None, fetch=http_fetch):
    """What the scheduler and the ``news_refresh`` command both run."""
    from shared import news_config as nc
    db = db or _store.Store()
    return run_poll(bus, db, fetch, feeds=nc.feeds(), universe=nc.ticker_set(),
                    now=_now_iso(), cfg=nc.load())
```

`services/news_svc/handlers.py` (written in this task; tested via `run_poll`):
```python
"""Publish the three news views; dispatch cmd:news."""
import logging

log = logging.getLogger("news_svc.handlers")

CACHE_FEED = "cache:news:feed"
EVENT_FEED = "events:news:feed"
CACHE_PUBLIC = "cache:news:feed_public"
EVENT_PUBLIC = "events:news:feed_public"
CACHE_STATUS = "cache:news:status"
EVENT_STATUS = "events:news:status"


def publish_feed(bus, rows, now) -> int:
    return bus.cache_set(CACHE_FEED, {"items": rows, "ts": now}, event=EVENT_FEED,
                         skip_unchanged=True)


def publish_feed_public(bus, rows, now) -> int:
    # ⚠ ``rows`` MUST come from ``store.newest(public_only=True)`` — the flag is
    # decided at ingest. Never filter the private view here.
    assert all(r.get("public") for r in rows), "a non-public row reached feed_public"
    return bus.cache_set(CACHE_PUBLIC, {"items": rows, "ts": now}, event=EVENT_PUBLIC,
                         skip_unchanged=True)


def publish_status(bus, states, results, now) -> int:
    by_name = {r["feed"]: r for r in results}
    feeds = [{"name": s["name"], "last_ok": s["last_ok"], "last_poll": s["last_poll"],
              "error": s["error"], "inserted": by_name.get(s["name"], {}).get("inserted", 0)}
             for s in states]
    return bus.cache_set(CACHE_STATUS, {"feeds": feeds, "ts": now}, event=EVENT_STATUS)


def handle_command(bus, command) -> None:
    kind = getattr(command, "type", None)
    if kind == "news_refresh":
        from services.news_svc import compute
        compute.poll_now(bus)
        return
    log.debug("ignoring cmd:news %s", kind)
```

**Step 4: Run to verify it passes** → 6 passed. Also `$PY -m pytest services/tests/test_no_silent_degrades.py -q` (the ≥15-line guard in `poll_feed` speaks, so it passes).

**Step 5: Commit** `feat(news): fetcher, poll cycle and the three published views`.

---

### Task 10: Scheduler and app

**Files:**
- Create: `services/news_svc/scheduler.py`, `services/news_svc/app.py`
- Test: `services/news_svc/tests/test_scheduler.py`, `services/news_svc/tests/test_app.py`

**Step 1: Write the failing tests**

```python
# test_scheduler.py
import datetime as dt
from zoneinfo import ZoneInfo

from services.news_svc import scheduler

CT = ZoneInfo("America/Chicago")
CFG = {"collector": {"rth_poll_min": 5, "offhours_poll_min": 15, "weekend_poll_min": 60}}


def test_cadence_by_calendar():
    assert scheduler.poll_interval_s(dt.datetime(2026, 9, 24, 10, 0, tzinfo=CT), CFG) == 300
    assert scheduler.poll_interval_s(dt.datetime(2026, 9, 24, 18, 0, tzinfo=CT), CFG) == 900
    assert scheduler.poll_interval_s(dt.datetime(2026, 9, 26, 10, 0, tzinfo=CT), CFG) == 3600
    assert scheduler.poll_interval_s(dt.datetime(2026, 7, 3, 10, 0, tzinfo=CT), CFG) == 3600  # holiday


def test_loop_calls_heartbeat_every_pass():
    import ast, inspect
    src = inspect.getsource(scheduler.loop)
    assert "_heartbeat.tick()" in src


# test_app.py
from fastapi.testclient import TestClient


def test_app_health():
    from services.news_svc.app import app
    with TestClient(app) as client:
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["domain"] == "news"
```

**Step 2: Run to verify it fails.**

**Step 3: Implement**

`scheduler.py`:
```python
"""Poll the feeds on a calendar-aware cadence. No Schwab, no Claude."""
import asyncio
import datetime as dt
import logging
from zoneinfo import ZoneInfo

from services import _heartbeat
from services.news_svc import compute
from shared import market_calendar as mc
from shared import news_config as nc

_log = logging.getLogger("news_svc.scheduler")
_CT = ZoneInfo("America/Chicago")


def poll_interval_s(now, cfg) -> int:
    c = cfg["collector"]
    local = now.astimezone(_CT)
    if not mc.is_trading_day(local.date()):
        return int(c["weekend_poll_min"]) * 60
    if mc.is_regular_hours(now):
        return int(c["rth_poll_min"]) * 60
    return int(c["offhours_poll_min"]) * 60


async def loop(bus) -> None:
    loop_ = asyncio.get_running_loop()
    while True:
        _heartbeat.tick()
        try:
            await loop_.run_in_executor(None, compute.poll_now, bus)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — never let the scheduler die
            _log.exception("news poll cycle failed")
        await asyncio.sleep(poll_interval_s(dt.datetime.now(dt.timezone.utc), nc.load()))
```

`app.py` — copy `services/market_svc/app.py`, replace `market` with `news` and the docstring:
```python
"""Runnable news service (port 8216).

Polls the public feeds in config/news.toml and publishes cache:news:feed,
cache:news:feed_public and cache:news:status. One command, news_refresh.
Importable without side effects; starts uvicorn only under __main__.
"""
import pathlib
import sys

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from services._scaffold import make_app  # noqa: E402
from services.news_svc import handlers, scheduler  # noqa: E402

app = make_app("news", scheduler=scheduler.loop, command_handler=handlers.handle_command)

if __name__ == "__main__":
    import uvicorn
    from repo_paths import SERVICE_PORTS
    uvicorn.run(app, host="127.0.0.1", port=SERVICE_PORTS["news"])
```

**Step 4: Run** `$PY -m pytest services/news_svc -q` → all pass. Also `$PY -m pytest services/tests -q` (the heartbeat-in-loop guard and the degrade guard scan every service).

**Step 5: Commit** `feat(news): scheduler and app for news_svc`.

---

### Task 11: Units, Status page, health fan-out

**Files:**
- Modify: `webgui/pages/status.py:119-125` (`svc_labels`: add `"news": "news_svc (public news feeds)"`)
- Modify: `tests/test_systemd_units.py:576` docstring "nine-unit" → "ten-unit"
- Test: `webgui/tests/test_status.py` (add one test mirroring `test_restart_spec_market_service` for `news`)

**Step 1: Add the test, run it, watch it fail, add the label.**

```python
def test_restart_spec_news_service():
    spec = status.restart_spec(_target("news", "service"))
    assert spec == {"kind": "unit", "title": "news_svc :8216", "name": "news_svc"}
```

**Step 2: Verify the generator emits the unit with no code change**

```bash
$PY -c "from deploy.systemd import generate_units as g; print([c for c in g.components() if c[0]=='news_svc'])"
```
Expected: `[('news_svc', 8216, 'services/news_svc/app.py')]`.

**Step 3: Run** `$PY -m pytest tests -q` and `cd webgui && $PY -m pytest tests/test_status.py -q`.

**Step 4: Commit** `feat(news): status card and unit for news_svc`.

---

### Task 12: Pure page facts — `webgui/pages/news_view.py`

**Files:**
- Create: `webgui/pages/news_view.py`
- Test: `webgui/tests/test_news_view.py`

**Step 1: Write the failing tests**

```python
import datetime as dt

from pages import news_view as nv

NOW = dt.datetime(2026, 9, 26, 1, 0, tzinfo=dt.timezone.utc)


def _it(i, *, tickers=(), source="MarketWatch", sources=None, hours_ago=0.5, public=True):
    ts = (NOW - dt.timedelta(hours=hours_ago)).isoformat()
    return {"id": f"i{i}", "source": source, "sources": sources or [source], "title": f"T{i}",
            "teaser": "", "url": f"https://x/{i}", "published_at": ts, "first_seen": ts,
            "tickers": list(tickers), "kind": "rss", "topics": [], "detail": {}, "public": public}


def test_rows_are_shaped_for_display_in_et():
    r = nv.rows({"items": [_it(1, tickers=["NVDA"])]}, now=NOW)[0]
    assert r["time"] == "8:30 PM"          # 01:00 UTC → 21:00 ET
    assert r["tickers"] == ["NVDA"] and r["sources"] == ["MarketWatch"]


def test_trending_counts_mentions_in_the_window():
    items = [_it(1, tickers=["NVDA"]), _it(2, tickers=["NVDA", "AAPL"]), _it(3, tickers=["AAPL"], hours_ago=9)]
    assert nv.trending({"items": items}, now=NOW, window_h=6) == [("NVDA", 2), ("AAPL", 1)]


def test_filters_by_source_and_symbol():
    items = [_it(1, tickers=["NVDA"]), _it(2, source="CNBC"), _it(3, tickers=["AAPL"])]
    rows = nv.rows({"items": items}, now=NOW)
    assert [r["id"] for r in nv.filter_rows(rows, sources={"CNBC"}, symbol=None)] == ["i2"]
    assert [r["id"] for r in nv.filter_rows(rows, sources=None, symbol="nvda")] == ["i1"]
    assert [r["id"] for r in nv.filter_rows(rows, sources=None, symbol=None, watchlist={"AAPL"})] == ["i3"]


def test_source_chips_come_from_the_rows_not_the_config():
    rows = nv.rows({"items": [_it(1), _it(2, source="CNBC")]}, now=NOW)
    assert nv.sources_present(rows) == ["CNBC", "MarketWatch"]


def test_unseen_count_and_cold_feed():
    assert nv.unseen({"items": [_it(1), _it(2, hours_ago=3)]}, since=(NOW - dt.timedelta(hours=1)).isoformat()) == 1
    assert nv.rows(None, now=NOW) == []
```

**Step 2: Run to verify it fails.** **Step 3: Implement**

```python
"""PURE facts for the news page, the Desk strip and the Symbol band.
Imports nothing from nicegui or bus_client; ``shared.symbols`` only."""
import datetime as dt
from collections import Counter
from zoneinfo import ZoneInfo

from shared.symbols import clean_symbol

_ET = ZoneInfo("America/New_York")
VIEW = "news:feed"
VIEW_PUBLIC = "news:feed_public"
VIEW_STATUS = "news:status"


def _dt(s):
    try:
        return dt.datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None


def rows(payload, *, now) -> list:
    if not isinstance(payload, dict):
        return []
    out = []
    for it in payload.get("items") or []:
        when = _dt(it.get("published_at"))
        et = when.astimezone(_ET) if when else None
        out.append({
            "id": it.get("id"), "title": it.get("title", ""), "teaser": it.get("teaser", ""),
            "url": it.get("url", ""), "tickers": [t for t in it.get("tickers", []) if clean_symbol(t)],
            "sources": it.get("sources") or [it.get("source", "")], "kind": it.get("kind", ""),
            "topics": it.get("topics", []), "detail": it.get("detail") or {},
            "time": et.strftime("%-I:%M %p") if et else "", "day": et.strftime("%b %d") if et else "",
            "published_at": it.get("published_at"), "first_seen": it.get("first_seen"),
            "age_min": ((now - when).total_seconds() / 60) if when else None,
        })
    return out


def trending(payload, *, now, window_h) -> list:
    counts = Counter()
    for r in rows(payload, now=now):
        if r["age_min"] is not None and r["age_min"] <= window_h * 60:
            counts.update(r["tickers"])
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))


def sources_present(rows_) -> list:
    return sorted({s for r in rows_ for s in r["sources"]})


def filter_rows(rows_, *, sources, symbol, watchlist=None) -> list:
    sym = clean_symbol(symbol) if symbol else None
    out = []
    for r in rows_:
        if sources and not (set(r["sources"]) & set(sources)):
            continue
        if sym and sym not in r["tickers"]:
            continue
        if watchlist is not None and not (set(r["tickers"]) & set(watchlist)):
            continue
        out.append(r)
    return out


def unseen(payload, *, since) -> int:
    if not isinstance(payload, dict) or not since:
        return 0
    return sum(1 for it in payload.get("items") or [] if (it.get("first_seen") or "") > since)


def for_symbol(payload, symbol, *, now, limit=8) -> list:
    return filter_rows(rows(payload, now=now), sources=None, symbol=symbol)[:limit]
```
⚠ `%-I` is POSIX-only; on Windows use `et.strftime("%I:%M %p").lstrip("0")`. Use the `lstrip` form — the tests must pass on both.

**Step 4: Run** `cd webgui && $PY -m pytest tests/test_news_view.py -q` → 5 passed. **Step 5: Commit** `feat(news): pure page facts for the news views`.

---

### Task 13: The private `/news` page

**Files:**
- Create: `webgui/pages/news.py`
- Modify: `webgui/main.py` — `OPTIONS_RAIL` (add `("/news", "Market News", "newspaper")` after Flow Alerts), `NAV_SECTIONS` MARKETS list (add `_sec_page("/news")` after `/options/flow`), the route-colour map near line 897 (add `/news`), and a `@_page("/news")` beside `/options/flow`
- Modify: `webgui/page_help.py` (a `"/news"` entry, same voice as `/options/flow`)
- Modify: `webgui/tests/test_shell.py:15-27` (add `"/news"` to `expected`), `webgui/tests/test_no_inline_style.py` (add `news.py`, `news_live.py`, `news_view.py` to the guarded set)
- Test: `webgui/tests/test_news_page.py`

**Step 1: Write the failing tests**

```python
"""The /news page: route, rail, help, and the enqueue gate."""
import ast
import pathlib

PAGES = pathlib.Path(__file__).resolve().parents[1] / "pages"


def test_route_rail_and_breadcrumb():
    import main
    assert main.breadcrumb_trail("/news") == ["Markets", "Market News"]
    assert ("/news", "Market News", "newspaper") in main.OPTIONS_RAIL


def test_help_exists():
    import page_help
    assert "/news" in page_help.HELP_MD


def test_the_only_enqueue_is_gated_by_may_enqueue():
    src = (PAGES / "news.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute) and n.func.attr == "request"]
    assert len(calls) == 1
    assert "_may_enqueue = _shell.may_enqueue()" in src
    assert "if _may_enqueue" in src


def test_public_render_hands_off_before_building():
    src = (PAGES / "news.py").read_text(encoding="utf-8")
    assert "if public:\n        from . import news_live" in src.replace("from pages import news_live", "from . import news_live")
```

**Step 2: Run to verify it fails.**

**Step 3: Implement the page** (`webgui/pages/news.py`; keep `render()` thin — every fact comes from `news_view`)

```python
"""Market News — the newest headlines, filings and insider buys, tagged by ticker.

Tier-1: nicegui + bus_client + shell + pages.* only. Reads ``cache:news:feed``
(``news_view.VIEW``); the ONE write is Refresh → ``news_refresh`` on cmd:news,
built only when ``shell.may_enqueue()`` says this process may.
``render(public=True)`` hands off to ``news_live`` before anything here builds.
"""
from __future__ import annotations

import datetime as _dt

from pages import copy as _copy
from pages import news_view as nv

PAGE_SIZE = 60
SYMBOL_ROUTE = "/symbol"
WAITING = "No news yet — the news service hasn't published this session."


def render(public=False):
    if public:
        from pages import news_live
        return news_live.render()
    import app_settings
    import bus_client
    import shell as _shell
    from nicegui import run, ui

    from pages import ui_kit as kit
    from pages.ui_guard import guard, guard_async
    from pages.view_watch import watch_view
    from shared import news_config as nc

    _may_enqueue = _shell.may_enqueue()
    state = {"payload": None, "rows": [], "sources": None, "symbol": None,
             "watchlist_only": False, "shown": PAGE_SIZE}
    watchlist = set(nc.ticker_set())
    seen_since = app_settings.load().get("news_seen_ts")
    linked = _shell.can_navigate(SYMBOL_ROUTE)

    with kit.page():
        head = kit.header("Market News", view=nv.VIEW)
        with head.actions:
            if _may_enqueue:
                def _refresh():
                    bus_client.request("news", {"type": "news_refresh"})
                    kit.toast("info", "Refreshing every feed — new items land in a moment.")
                kit.button("Refresh", kind="secondary", icon="refresh", on_click=guard(_refresh))
        with kit.control_bar():
            src_sel = kit.select_field("Sources", [], value=[], multiple=True, width="w-96").props("use-chips")
            sym_in = kit.symbol_field("Ticker", on_load=lambda: _apply())
            wl = ui.switch("Watchlist only").classes("ml-2")
        trending_row = ui.row().classes("gap-2 flex-wrap")
        status = kit.status_line()
        region = kit.region(WAITING)
        more = kit.button("Show more", kind="secondary", on_click=guard(lambda: _more()))

    def _paint():
        ...  # clear region.content; for each row in filtered[:shown] draw one ui.row:
             # time label · ticker links (ui.link to f"{SYMBOL_ROUTE}?symbol={t}" when linked,
             # else a label that sets state["symbol"]) · ui.link(title, url, new_tab=True)
             # · one small badge per source · a second muted line with teaser / Form 4 detail.
             # Empty filtered → kit.empty("Nothing matches those filters.")
             # Update trending_row from nv.trending(payload, now, nc.load()["trending"]["window_h"]).
             # more.set_visibility(len(filtered) > state["shown"])

    async def _load():
        payload = await run.io_bound(bus_client.read, nv.VIEW)
        state["payload"] = payload
        state["rows"] = nv.rows(payload, now=_dt.datetime.now(_dt.timezone.utc))
        src_sel.options = nv.sources_present(state["rows"]); src_sel.update()
        _paint()
        app_settings.set("news_seen_ts", _dt.datetime.now(_dt.timezone.utc).isoformat())

    ui.timer(0.1, guard_async(_load), once=True)
    watch_view(nv.VIEW, guard_async(_load))
```
Write `_paint`, `_apply`, `_more` in full (the ellipsis above is the plan's shorthand, not code). Follow `pages/options/flow.py` for the exact `kit.*` idioms and `guard`/`guard_async` wrapping. A ticker chip when `linked` is `ui.link(t, f"{SYMBOL_ROUTE}?symbol={t}")`; a headline is `ui.link(r["title"], r["url"], new_tab=True)`. No `.style()`; colours through `theme.py` tokens.

Register in `main.py`:
```python
@_page("/news")
def news_page() -> None:
    with _layout("/news", "Market News"):
        from pages import news
        news.render()
```

`page_help.py` entry (write it in the `/options/flow` voice): what the page shows, where items come from (public feeds, EDGAR), that a ticker is tagged only when the headline names it, what Trending counts, that Refresh polls every feed now.

**Step 4: Run**

```bash
cd webgui && $PY -m pytest tests/test_news_page.py tests/test_shell.py tests/test_no_inline_style.py tests/test_ui_kit_guard.py tests/test_shared_copy.py -q
```
Expected: all pass. `test_nav_sections_partition…` and `test_drawer_icons_are_present_and_distinct` cover the rail (the icon count in that test's docstring moves 17 → 18; update the assertion if it is a literal).

**Step 5: Verify in the browser** — the harness in memory (`local-page-harness-replaces-missing-dev`) renders a page with a fake bus: seed `cache:news:feed` from `services/news_svc/tests/fixtures` through `compute.run_poll` with the `FakeFetch`, then screenshot `/news`. Fix anything that renders wrong.

**Step 6: Commit** `feat(news): the /news page under MARKETS`.

---

### Task 14: The Symbol band and the Desk strip

**Files:**
- Modify: `webgui/pages/symbol.py:189-232` (`VIEWS` += `"news:feed"`; `REGION_VIEWS["news"] = ("news:feed",)`), add `news_band(symbol, env, now)` (pure, beside `flow_band`), a `_paint_news` painter and its `_band("In the news")` mount after the flow band
- Modify: `webgui/pages/desk.py:1558-1581` (`VIEWS` += `"news:feed"`; `_REGION_VIEWS["news"] = ("news:feed",)`), `PANEL_HEADS["news"]`, `_paint_news` drawing 5 rows, mounted after the flow panel; `EMPTY_NEWS = "No headlines published yet today."`
- Test: `webgui/tests/test_symbol.py`, `webgui/tests/test_desk.py` (one pure test each)

**Step 1: Tests**

```python
# test_symbol.py
def test_news_band_is_the_symbols_items_or_the_cold_line():
    from pages import symbol
    assert symbol.news_band("NVDA", None, now=NOW)["message"] == symbol.WAITING_NEWS
    env = {"items": [{"id": "1", "title": "x", "url": "https://a", "tickers": ["NVDA"],
                      "published_at": NOW.isoformat(), "source": "S", "sources": ["S"]}]}
    band = symbol.news_band("NVDA", env, now=NOW)
    assert band["message"] == "" and band["rows"][0]["title"] == "x"
    assert symbol.news_band("AAPL", env, now=NOW)["message"] == "No headlines for AAPL in the feed."

# test_desk.py
def test_news_view_is_polled_in_the_batch_and_owns_its_region():
    from pages import desk
    assert "news:feed" in desk.VIEWS
    assert desk._REGION_VIEWS["news"] == ("news:feed",)
```

**Step 2–4:** implement with `news_view.for_symbol` / `news_view.rows(...)[:5]`; painters follow `_paint_flow` in each file (clear body → `kit.empty` for the two absence cases → one line per row: time · source · headline link). Run `cd webgui && $PY -m pytest tests/test_symbol.py tests/test_desk.py -q`.

**Step 5: Commit** `feat(news): In-the-news band on the Symbol page and a headlines strip on the Desk`.

---

### Task 15: The public screen and the website's Tools menu

**Files:**
- Create: `webgui/pages/news_live.py`
- Modify: `webgui/live_screens.py` (add the `Screen` after `simulator`)
- Modify: `webgui/tests/test_live_screens.py:17-23` (`16` → `17`, with a dated comment)
- Modify: `deploy/site/index.html`, `live.html`, `gallery.html`, `report.html` (the Tools menu: add the item after Simulator)
- Modify: `deploy/tests/test_site.py:1217` (`TOOLS` += `("news", "Market News")`)
- Modify: `docs/dev-prod-environments.md` §2 step 4b (a note that the `live` user's read grant must cover `cache:news:*`; add a `%R~cache:news:*` selector line if the grant is a key pattern)
- Test: `webgui/tests/test_news_live.py`

**Step 1: Write the failing tests**

```python
import pathlib

PAGES = pathlib.Path(__file__).resolve().parents[1] / "pages"


def test_news_is_a_published_tools_screen():
    import live_screens
    s = {x.slug: x for x in live_screens.SCREENS}["news"]
    assert (s.route, s.module, s.private_route, s.tile, s.kwargs) == \
        ("/news", "news", "/news", False, {"public": True})


def test_the_public_page_reads_only_the_public_view_and_never_writes():
    src = (PAGES / "news_live.py").read_text(encoding="utf-8")
    assert "news_view.VIEW_PUBLIC" in src or "VIEW_PUBLIC" in src
    assert '"news:feed"' not in src
    assert ".request(" not in src and "app_settings" not in src
```
Plus the existing `test_live_main.py::test_it_registers_every_published_screen_and_nothing_else`, `test_live_commands.py` and `deploy/tests/test_site.py::test_a_tool_is_a_link_exactly_when_it_is_published` — all three fail until the pieces land.

**Step 2: Run to verify it fails.**

**Step 3: Implement**

`webgui/pages/news_live.py` — the private page minus every owner control: reads `nv.VIEW_PUBLIC`, no Refresh, no watchlist switch, no `app_settings`; `?symbol=` from `ui.context.client.request.query_params` through `clean_symbol` seeds the ticker filter; ticker chips set the in-page filter (no `/symbol` on this origin — `shell.can_navigate` is False there anyway). Reuse the same `_paint` logic: move the row-drawing into a shared helper `_draw_rows(container, rows, *, on_ticker)` in `news.py` that both entrypoints import, so the two origins cannot drift.

`live_screens.py`:
```python
    # A pure READER — the only public screen that writes nothing. Reads
    # cache:news:feed_public (items from feeds flagged public in config/news.toml,
    # decided at ingest). ``public=True`` hands off to pages/news_live.py.
    # Design: docs/plans/2026-09-25-news-feed-design.md.
    Screen("news", "/news", "Market News", "news", "/news",
           kwargs={"public": True}, tile=False),
```

Site menus — after the Simulator item in each of the four files:
```html
        <a class="ns-menu-item" href="https://live.neuralstrike.co/news">
          <span class="ns-menu-title">Market News</span>
          <span class="ns-menu-desc">Headlines, filings and insider buys, live</span>
        </a>
```

**Step 4: Run**

```bash
cd webgui && $PY -m pytest tests/test_news_live.py tests/test_live_screens.py tests/test_live_main.py tests/test_live_commands.py tests/test_shell_seam.py -q
cd .. && $PY -m pytest deploy/tests/test_site.py -q
```
Expected: all pass.

**Step 5: Commit** `feat(news): the public Market News screen under the site's Tools menu`.

---

### Task 16: Docs, manuals, CLAUDE.md

**Files:**
- Modify: `docs/CHANGELOG.md` (a dated entry: what shipped, the pieces, the feeds)
- Modify: `docs/webgui-routes.md` (`/news`, the Symbol band, the Desk strip, the public screen)
- Modify: `CLAUDE.md` — the ports table (`news = 8216`), the routes table (a `/news` row), the folder map (`services/news_svc`), "The public live screens" (sixteen → seventeen; the sixth published screen writes nothing), the unit count (nine → ten), the Tier-1 allow-list (`shared.news_config` — stdlib + config_toml + symbols; Tier 1 reads only `trending.window_h` and the ticker set)
- Modify: `docs/manuals/` User Guide (a "Market News" section) and Reference Guide (the per-tab entry); rebuild with `build_docs.py`
- Modify: `docs/dev-prod-environments.md` (the ACL note from Task 15, if not done there)

**Step 1:** write each; **Step 2:** `cd webgui && $PY -m pytest tests/test_manuals*.py tests/test_shell_seam.py -q` and `$PY -m pytest shared/tests/test_cross_tier_mirrors.py -q`; **Step 3:** commit `docs(news): routes, changelog, manuals and the CLAUDE.md invariants`.

---

### Task 17: Full-suite run and live verification

**Step 1: Every suite the change touches, compared by failing SET**

```bash
$PY -m pytest services/news_svc services/tests shared/tests tests deploy/tests tools/tests -q
(cd webgui && $PY -m pytest . -q)
```
Expected: no failures beyond the documented baseline (CLAUDE.md "Tests").

**Step 2: Run the service for real, once, from the checkout**

```bash
$PY services/news_svc/app.py
```
In a second shell after ~30 s:
```bash
$PY -c "from shared.bus import Bus; e=Bus().cache_get('cache:news:feed'); print(len(e.payload['items']), e.payload['items'][0]['title'])"
$PY -c "from shared.bus import Bus; print(Bus().cache_get('cache:news:status').payload)"
```
Expected: a few hundred items; every feed in `status` with `error: None`. A feed with an error → fix its URL in `news.toml` (Task 2's curl check) or its adapter, then re-run. ⚠ A live Redis runs on the Windows box (memory `local-redis-on-the-windows-box`): this writes prod-shaped keys there, which is fine, but do not mistake them for prod's.

**Step 3: Promote per the standing rule** (memory `worktree-work-must-land-in-dev`): merge to `main`, push, then the verbatim promote command; afterwards on the box confirm `systemctl --user status trading-prod-news_svc`, that the Status page shows the card healthy, that `https://app.neuralstrike.co/news` renders, and that `https://live.neuralstrike.co/news` renders with no Refresh button and only public-flagged sources. If the public page shows nothing while the private one does, the `live` Redis user cannot read `cache:news:*` — apply the ACL line from Task 15.

**Step 4: Commit any fix-ups**, then done. Phase 2 (Claude summaries, body extraction, alerts, Telegram) is a new plan.

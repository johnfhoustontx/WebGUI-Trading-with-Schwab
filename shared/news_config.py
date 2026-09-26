"""config/news.toml — the news collector's feeds, cadence and ticker extras.

Tier-2 (news_svc) reads it; Tier 1 reads only ``trending.window_h`` and the
ticker set. Stdlib + shared.config_toml + shared.symbols, nothing else —
``shared/tests/test_news_config.py`` pins that import set, because the webgui
imports this module and it therefore sits on the Tier-1 allow-list.

What it guards, and what it does not:

* a missing or unparseable file -> the built-in defaults (``toml_loader``);
* ``[[feeds]]`` or ``[tickers] extras`` that is not a list -> ``[]``, with a
  WARNING (a bare string must never be iterated into single letters);
* a feed with an unknown ``kind``, no ``name``, or a name already seen -> that
  feed is skipped, with a WARNING (the first of two duplicates wins);
* ``enabled`` / ``public`` present but not a real bool -> FAIL CLOSED: the feed
  is disabled / not public, with a WARNING;
* ``feeds()`` coming back empty -> a WARNING, so a service collecting nothing
  leaves a trace.

It does NOT validate values (URLs, poll minutes, counts): a wrong number of the
right type is read as written. Nothing here raises. Treat anything ``load()``
returns as read-only: it is the cached mapping.
"""
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


def _as_list(value, where):
    """``value`` if it is a list, else ``[]`` with a WARNING (a TOML table or
    a bare string in a list's place must not be iterated as one)."""
    if isinstance(value, list):
        return value
    if value is not None:
        log.warning("news.toml: %s is not a list (%s) - ignored",
                    where, type(value).__name__)
    return []


def _flag(feed, key, default):
    """A real bool, the default when absent, else ``False`` with a WARNING -
    a malformed ``enabled`` or ``public`` must fail CLOSED."""
    if key not in feed:
        return default
    value = feed[key]
    if isinstance(value, bool):
        return value
    log.warning("news.toml: feed %r has %s = %r, not true/false - treated as false",
                feed.get("name"), key, value)
    return False


def feeds() -> list:
    """Enabled feeds with ``enabled`` / ``public`` filled in as real bools.

    Each feed is a deep COPY - ``load()`` returns the cached mapping, so filling
    the flags in place would write into every later reader's config. Unknown
    kinds, nameless feeds and repeated names are skipped with a WARNING (the
    name keys the per-feed state, so the first of two duplicates wins)."""
    out, seen = [], set()
    for raw in _as_list(load().get("feeds"), "[[feeds]]"):
        if not isinstance(raw, dict):
            log.warning("news.toml: a [[feeds]] entry is not a table (%s) - skipped",
                        type(raw).__name__)
            continue
        feed = copy.deepcopy(raw)
        feed["enabled"] = _flag(raw, "enabled", True)
        feed["public"] = _flag(raw, "public", True)
        if feed.get("kind") not in KINDS:
            log.warning("news.toml: feed %r has unknown kind %r - skipped",
                        feed.get("name"), feed.get("kind"))
            continue
        name = feed.get("name")
        if not name:
            log.warning("news.toml: a %r feed has no name - skipped", feed.get("kind"))
            continue
        if name in seen:
            log.warning("news.toml: duplicate feed name %r - the later one is skipped",
                        name)
            continue
        seen.add(name)
        if feed["enabled"]:
            out.append(feed)
    if not out:
        log.warning("news.toml: no enabled feeds - news_svc will collect nothing")
    return out


def ticker_set() -> list:
    """The GEX collection list plus ``[tickers] extras``, cleaned, order kept."""
    tickers = load().get("tickers") or {}
    if not isinstance(tickers, dict):
        log.warning("news.toml: [tickers] is not a table (%s) - ignored",
                    type(tickers).__name__)
        tickers = {}
    extras = _as_list(tickers.get("extras"), "[tickers] extras")
    out, seen = [], set()
    for raw in list(_symbols.collection_base()) + list(extras):
        sym = clean_symbol(raw)
        if sym and sym not in seen:
            seen.add(sym)
            out.append(sym)
    return out

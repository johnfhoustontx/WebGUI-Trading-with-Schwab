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
* the switches live in ``[feed_flags."<feed name>"]`` (a table, so a local
  override of one feed's flag merges key by key rather than replacing the
  whole ``[[feeds]]`` list); a flag absent there defaults True;
* ``enabled`` / ``public`` present but not a real bool -> FAIL CLOSED: the feed
  is disabled / not public, with a WARNING;
* a legacy ``enabled`` / ``public`` still inside a ``[[feeds]]`` entry -> a
  WARNING that it moved, and used only where ``[feed_flags]`` is silent;
* a ``[feed_flags]`` entry naming no feed, or one that is not a table -> a
  WARNING (the second is ignored);
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
    "feed_flags": {},
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


FLAG_KEYS = ("enabled", "public")
_CLOSED = {"enabled": False, "public": False}


def _flag_table():
    """``[feed_flags]`` as ``{name: table}``. A non-table ``[feed_flags]`` or a
    non-table entry in it is ignored with a WARNING (its feed's flags then
    default, which is True - the same as the entry being absent)."""
    table = load().get("feed_flags")
    if table is None:
        return {}
    if not isinstance(table, dict):
        log.warning("news.toml: [feed_flags] is not a table (%s) - ignored",
                    type(table).__name__)
        return {}
    out = {}
    for name, entry in table.items():
        if isinstance(entry, dict):
            out[name] = entry
        else:
            log.warning("news.toml: [feed_flags] entry %r is not a table (%s) - ignored",
                        name, type(entry).__name__)
    return out


def _bool(name, key, value):
    """A real bool, else ``False`` with a WARNING - a malformed ``enabled`` or
    ``public`` must fail CLOSED."""
    if isinstance(value, bool):
        return value
    log.warning("news.toml: feed %r has %s = %r, not true/false - treated as false",
                name, key, value)
    return False


def _resolve(raw, table):
    """``{"enabled", "public"}`` for one ``[[feeds]]`` entry.

    ``[feed_flags."<name>"]`` decides; a key it does not set falls back to a
    LEGACY copy inside the ``[[feeds]]`` entry (with a WARNING that the switch
    moved, so a legacy ``enabled = false`` still fails closed), else True."""
    name = raw.get("name")
    entry = table.get(name, {})
    out = {}
    for key in FLAG_KEYS:
        if key in raw:
            log.warning("news.toml: feed %r sets %s inside [[feeds]] - that switch "
                        "moved to [feed_flags.%r] and is used only where "
                        "[feed_flags] does not set it", name, key, name)
        if key in entry:
            out[key] = _bool(name, key, entry[key])
        elif key in raw:
            out[key] = _bool(name, key, raw[key])
        else:
            out[key] = True
    return out


def _entries():
    """The ``[[feeds]]`` entries that are tables, in file order."""
    out = []
    for raw in _as_list(load().get("feeds"), "[[feeds]]"):
        if not isinstance(raw, dict):
            log.warning("news.toml: a [[feeds]] entry is not a table (%s) - skipped",
                        type(raw).__name__)
            continue
        out.append(raw)
    return out


def feeds() -> list:
    """Enabled feeds with ``enabled`` / ``public`` filled in as real bools.

    The switches live in ``[feed_flags."<name>"]`` (see ``_resolve``). Each feed
    is a deep COPY - ``load()`` returns the cached mapping, so filling the flags
    in place would write into every later reader's config. Unknown kinds,
    nameless feeds and repeated names are skipped with a WARNING (the name keys
    the per-feed state and the switch, so the first of two duplicates wins), and
    a ``[feed_flags]`` entry that names no feed is reported - a typo there would
    otherwise switch nothing, silently."""
    table = _flag_table()
    out, kept, named = [], {}, set()
    for raw in _entries():
        name = raw.get("name")
        if name:
            named.add(name)
        feed = copy.deepcopy(raw)
        feed.update(_resolve(raw, table))
        if feed.get("kind") not in KINDS:
            log.warning("news.toml: feed %r has unknown kind %r - skipped",
                        name, feed.get("kind"))
            continue
        if not name:
            log.warning("news.toml: a %r feed has no name - skipped", feed.get("kind"))
            continue
        if name in kept:
            if kept[name]["enabled"]:
                log.warning("news.toml: duplicate feed name %r - the later one is "
                            "skipped", name)
            else:
                log.warning("news.toml: duplicate feed name %r - the later one is "
                            "skipped, and the kept (first) entry is disabled, so no "
                            "%r feed is polled", name, name)
            continue
        kept[name] = feed
        if feed["enabled"]:
            out.append(feed)
    for name in table:
        if name not in named:
            log.warning("news.toml: [feed_flags] entry %r names no feed - ignored "
                        "(a typo?)", name)
    if not out:
        log.warning("news.toml: no enabled feeds - news_svc will collect nothing")
    return out


def flags(name) -> dict:
    """The CURRENT ``{"enabled", "public"}`` for one feed, as real bools.

    The same rules as ``feeds()`` (the first entry of a name wins), for the poll
    cycle to re-check at every publish. A name that is not a usable feed - absent,
    or one ``feeds()`` would skip for its kind - is ``False`` for both: fail
    closed."""
    for raw in _entries():
        if raw.get("name") != name:
            continue
        if not name or raw.get("kind") not in KINDS:
            return dict(_CLOSED)
        return _resolve(raw, _flag_table())
    return dict(_CLOSED)


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

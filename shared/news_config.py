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
  feed is skipped, with a WARNING (the first of two duplicates wins); a name
  that is not a non-empty str (a list, table, int, bool) counts as no name;
* the switches live in ``[feed_flags."<feed name>"]`` (a table, so a local
  override of one feed's flag merges key by key rather than replacing the
  whole ``[[feeds]]`` list); a flag absent there defaults True;
* ``enabled`` / ``public`` present but not a real bool -> FAIL CLOSED: the feed
  is disabled / not public, with a WARNING;
* a legacy ``enabled`` / ``public`` still inside a ``[[feeds]]`` entry -> a
  WARNING that it moved, and used only where ``[feed_flags]`` is silent;
* a ``[feed_flags]`` entry naming no feed, one that is not a table, or one
  whose key is not a str -> a WARNING (the last two are ignored);
* ``feeds()`` coming back empty -> a WARNING, so a service collecting nothing
  leaves a trace.

Every WARNING above comes from ``feeds()``. ``flags(name)``, ``all_feeds()``
and ``public_feed_names()`` apply the same rules through the same resolver
(``_resolved``) but SILENTLY, because the poll cycle calls them at every publish
and a warning there would repeat per call.

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
        # Every non-SEC feed. Browser-style because Yahoo answers a 404 page to
        # a client with no Mozilla token (measured 2026-09-26).
        "feed_user_agent": "Mozilla/5.0 (compatible; NeuralStrike news_svc; +https://neuralstrike.co)",
        "max_body_bytes": 5_000_000,
    },
    "tickers": {"extras": []},
    "trending": {"window_h": 6},
    "feeds": [],
    "feed_flags": {},
}

load, reset_cache = toml_loader(NEWS_TOML, DEFAULTS, label="news.toml")


def _as_list(value, where, warn=True):
    """``value`` if it is a list, else ``[]`` with a WARNING (a TOML table or
    a bare string in a list's place must not be iterated as one)."""
    if isinstance(value, list):
        return value
    if value is not None and warn:
        log.warning("news.toml: %s is not a list (%s) - ignored",
                    where, type(value).__name__)
    return []


FLAG_KEYS = ("enabled", "public")
_CLOSED = {"enabled": False, "public": False}


def _name(raw):
    """A feed's name if it is a non-empty str, else ``None``. Anything else -
    a list or table (unhashable: keying the switch by it would raise), an int,
    a bool - is no name at all, so the feed is skipped as nameless."""
    name = raw.get("name")
    return name if isinstance(name, str) and name else None


def _flag_table(warn=True):
    """``[feed_flags]`` as ``{name: table}``. A non-table ``[feed_flags]`` or a
    non-table entry in it is ignored with a WARNING (its feed's flags then
    default, which is True - the same as the entry being absent)."""
    table = load().get("feed_flags")
    if table is None:
        return {}
    if not isinstance(table, dict):
        if warn:
            log.warning("news.toml: [feed_flags] is not a table (%s) - ignored",
                        type(table).__name__)
        return {}
    out = {}
    for name, entry in table.items():
        if not isinstance(name, str):
            if warn:
                log.warning("news.toml: [feed_flags] key %r is not a string - ignored",
                            name)
            continue
        if isinstance(entry, dict):
            out[name] = entry
        elif warn:
            log.warning("news.toml: [feed_flags] entry %r is not a table (%s) - ignored",
                        name, type(entry).__name__)
    return out


def _bool(name, key, value, warn=True):
    """A real bool, else ``False`` with a WARNING - a malformed ``enabled`` or
    ``public`` must fail CLOSED."""
    if isinstance(value, bool):
        return value
    if warn:
        log.warning("news.toml: feed %r has %s = %r, not true/false - treated as false",
                    name, key, value)
    return False


def _resolve(raw, table, warn=True):
    """``{"enabled", "public"}`` for one ``[[feeds]]`` entry.

    ``[feed_flags."<name>"]`` decides; a key it does not set falls back to a
    LEGACY copy inside the ``[[feeds]]`` entry (with a WARNING that the switch
    moved, so a legacy ``enabled = false`` still fails closed), else True."""
    name = _name(raw)
    entry = table.get(name, {}) if name else {}
    out = {}
    for key in FLAG_KEYS:
        if key in raw and warn:
            log.warning("news.toml: feed %r sets %s inside [[feeds]] - that switch "
                        "moved to [feed_flags.%r] and is used only where "
                        "[feed_flags] does not set it", name, key, name)
        if key in entry:
            out[key] = _bool(name, key, entry[key], warn)
        elif key in raw:
            out[key] = _bool(name, key, raw[key], warn)
        else:
            out[key] = True
    return out


def _resolved(warn):
    """Every usable feed, keyed by name in file order, with ``enabled`` /
    ``public`` filled in as real bools - disabled feeds INCLUDED - plus the
    ``[feed_flags]`` table it was resolved against.

    The ONE place the rules live, so ``feeds()`` and ``flags()`` cannot
    disagree: unknown kinds and nameless feeds are skipped before a name is
    recorded, so of two entries sharing a name the first USABLE one wins.
    ``warn=False`` resolves the same values without logging a word. Each feed
    is a deep COPY - ``load()`` returns the cached mapping, so filling the
    flags in place would write into every later reader's config."""
    table = _flag_table(warn)
    kept = {}
    for raw in _as_list(load().get("feeds"), "[[feeds]]", warn):
        if not isinstance(raw, dict):
            if warn:
                log.warning("news.toml: a [[feeds]] entry is not a table (%s) - skipped",
                            type(raw).__name__)
            continue
        name = _name(raw)
        feed = copy.deepcopy(raw)
        feed.update(_resolve(raw, table, warn))
        if feed.get("kind") not in KINDS:
            if warn:
                log.warning("news.toml: feed %r has unknown kind %r - skipped",
                            name, feed.get("kind"))
            continue
        if not name:
            if warn:
                log.warning("news.toml: a %r feed has no name - skipped",
                            feed.get("kind"))
            continue
        if name in kept:
            if not warn:
                continue
            if kept[name]["enabled"]:
                log.warning("news.toml: duplicate feed name %r - the later one is "
                            "skipped", name)
            else:
                log.warning("news.toml: duplicate feed name %r - the later one is "
                            "skipped, and the kept (first) entry is disabled, so no "
                            "%r feed is polled", name, name)
            continue
        kept[name] = feed
    return kept, table


def feeds() -> list:
    """Enabled feeds with ``enabled`` / ``public`` filled in as real bools.

    The switches live in ``[feed_flags."<name>"]`` (see ``_resolve``). Unknown
    kinds, nameless feeds and repeated names are skipped with a WARNING (the
    name keys the per-feed state and the switch, so the first usable entry of a
    name wins), and a ``[feed_flags]`` entry that names no feed is reported - a
    typo there would otherwise switch nothing, silently. This is the one reader
    that reports a malformed config."""
    kept, table = _resolved(warn=True)
    named = set()
    for raw in _as_list(load().get("feeds"), "[[feeds]]", warn=False):
        if isinstance(raw, dict) and _name(raw):
            named.add(_name(raw))
    for name in table:
        if name not in named:
            log.warning("news.toml: [feed_flags] entry %r names no feed - ignored "
                        "(a typo?)", name)
    out = [feed for feed in kept.values() if feed["enabled"]]
    if not out:
        log.warning("news.toml: no enabled feeds - news_svc will collect nothing")
    return out


def flags(name) -> dict:
    """The CURRENT ``{"enabled", "public"}`` for one feed, as real bools.

    Exactly the feed ``feeds()`` resolves for that name (``_resolved``), for the
    poll cycle to re-check at every publish - so it is SILENT: a malformed
    config is reported by ``feeds()``, and a warning here would repeat once per
    publish. A disabled feed reports its real ``public`` with ``enabled``
    False; a name that is not a usable feed - absent, or one ``feeds()`` skips -
    is ``False`` for both: fail closed."""
    ok = isinstance(name, str) and name
    feed = _resolved(warn=False)[0].get(name) if ok else None
    if feed is None:
        return dict(_CLOSED)
    return {key: feed[key] for key in FLAG_KEYS}


def all_feeds() -> list:
    """EVERY usable feed - enabled or not - in file order, with ``enabled`` /
    ``public`` filled in, resolved exactly as ``feeds()`` resolves them but
    SILENTLY (the poll cycle calls it at every publish, for the status view)."""
    return list(_resolved(warn=False)[0].values())


def public_feed_names() -> list:
    """The names of every feed whose CURRENT ``public`` flag is True, enabled
    or not, in file order - the public view's ``public_sources``. Silent, and
    by the same rules as ``flags()``: a malformed flag fails closed."""
    return [feed["name"] for feed in all_feeds() if feed["public"]]


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

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
right type is read as written - except ``[dedupe] same_feed_merge_h``, which
``same_feed_merge_h()`` reads as the default when it is not a finite number >= 0
(a bool included) and clamps to 24 above it, because it decides whether two
rows become one - and the v2 accessors: ``impact_config()`` (thresholds must be
numbers with ``high_at > med_at``, else the built-in pair with one WARNING;
``stale_after_h`` must be > 0 and the two boosts >= 0, else the default with
one WARNING; malformed keyword tiers and non-numeric points are dropped),
``calendar_config()`` / ``calendar_source()`` (a non-bool ``enabled`` fails
closed; a ``*_min`` / ``*_h`` that is not a finite number > 0, or is past its
cap of a week of minutes / a year of hours, is the default)
and ``indicators()`` (unknown transform/schedule or no series -> skipped with a
WARNING). Keyword tiers, sources and indicators are TABLES, never lists: a list
in ``config/local`` replaces the whole list. Nothing here raises. Treat anything ``load()``
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
        "sec_view_items": 100,
    },
    "tickers": {"extras": []},
    "trending": {"window_h": 6},
    # One feed repeating one title within this many hours is ONE story (the
    # same WSJ piece under two Google redirect URLs). 0 = off. See store.py.
    "dedupe": {"same_feed_merge_h": 6},
    "feeds": [],
    "feed_flags": {},
    # The rules-based High / Med / Low on every item (services/news_svc/impact.py).
    "impact": {
        "high_at": 6, "med_at": 3, "stale_after_h": 24,
        "multi_source": 1, "watchlist": 2, "match_teaser": False,
        "keywords": {
            "tier1": {"points": 5, "words": [
                "FOMC", "rate decision", "rate cut", "rate hike", "Fed chair", "CPI",
                "inflation report", "jobs report", "nonfarm payrolls", "payrolls",
                "bankruptcy", "chapter 11", "trading halt", "halted", "SEC charges",
                "indicted", "acquire", "acquisition", "merger", "takeover",
                "tender offer", "guidance cut", "cuts guidance", "raises guidance",
                "profit warning", "delist"]},
            "tier2": {"points": 3, "words": [
                "downgrade", "upgrade", "beats", "misses", "earnings", "PPI", "PCE",
                "GDP", "retail sales", "jobless claims", "tariff", "sanctions",
                "buyback", "dividend cut", "recall", "investigation", "lawsuit",
                "layoffs", "price target", "offering", "stake"]},
            "tier3": {"points": 1, "words": [
                "outlook", "forecast", "analyst", "sector", "rally", "selloff"]},
        },
        "source_points": {"Federal Reserve": 3, "Truth Social": 2, "WSJ": 1,
                          "ZeroHedge": -1},
        "form4": {"small_usd": 250_000, "small": 1, "large_usd": 1_000_000,
                  "large": 3, "huge_usd": 10_000_000, "huge": 6, "officer": 1},
        "filings": {"424B5": 3, "S-3": 2, "S-1": 1, "S-3ASR": 1, "untracked": -1},
    },
    # The economic calendar: Fed events, BLS/BEA/FRED release schedules, FRED
    # observations, Nasdaq IPOs, watchlist dividends (trade_svc writes those).
    "calendar": {
        "enabled": True,
        "refresh_min": 60,
        "values_refresh_min": 240,
        "release_poll_min": 2,
        "release_watch_min": 60,
        "actual_fresh_h": 24,
        "sources": {
            "fed": {"enabled": True,
                    "url": "https://www.federalreserve.gov/json/calendar.json",
                    "user_agent": ""},
            "bls": {"enabled": True,
                    "url": "https://www.bls.gov/schedule/news_release/bls.ics",
                    "user_agent": "", "refresh_min": 720},
            "bea": {"enabled": True,
                    "url": "https://www.bea.gov/news/schedule/ics/"
                           "online-calendar-subscription.ics",
                    "user_agent": "", "refresh_min": 720},
            "fred_calendar": {"enabled": True,
                              "url": "https://fred.stlouisfed.org/releases/calendar"
                                     "?rid={rid}&y={year}&view=year&vs={start}&ve={end}",
                              "user_agent": "", "refresh_min": 720},
            "fred_api": {"enabled": True, "url": "https://api.stlouisfed.org/fred",
                         "user_agent": ""},
            "fredgraph": {"enabled": True,
                          "url": "https://fred.stlouisfed.org/graph/fredgraph.csv"
                                 "?id={series}&cosd={start}",
                          "user_agent": ""},
            "nasdaq_ipo": {"enabled": True,
                           "url": "https://api.nasdaq.com/api/ipo/calendar?date={month}",
                           "user_agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
                                         " (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
                           "accept": "application/json, text/plain, */*",
                           "refresh_min": 240},
        },
        "fed": {"types": ["FOMC", "Beige", "Speeches", "Testimony"],
                "horizon_days": 45, "speech_horizon_days": 14},
        "events": {"extra_releases": ["Job Openings and Labor Turnover Survey",
                                      "Employment Cost Index"]},
        "ipo": {"min_offer_usd": 100_000_000, "lookback_days": 7},
        "dividends": {"enabled": True, "refresh_at": "06:40", "horizon_days": 30,
                      "lookback_days": 3},
        # Tile order is THIS built-in order: the loader deep-merges the file
        # onto these defaults, so an indicator the file adds appends after
        # them, and removing a table from the file does NOT remove it (the
        # default survives the merge) - set enabled = false instead.
        "indicators": {
            "cpi": {"enabled": True, "label": "CPI", "series": "CPIAUCSL",
                    "transform": "pct_mom", "schedule": "bls",
                    "match": "Consumer Price Index", "tile": "CPI"},
            "core_cpi": {"enabled": True, "label": "Core CPI", "series": "CPILFESL",
                         "transform": "pct_mom", "schedule": "bls",
                         "match": "Consumer Price Index", "tile": "CPI"},
            "ppi": {"enabled": True, "label": "PPI", "series": "PPIFIS",
                    "transform": "pct_mom", "schedule": "bls",
                    "match": "Producer Price Index", "tile": "PPI"},
            "nfp": {"enabled": True, "label": "Nonfarm payrolls", "series": "PAYEMS",
                    "transform": "change_k", "schedule": "bls",
                    "match": "Employment Situation", "tile": "Jobs"},
            "unrate": {"enabled": True, "label": "Unemployment", "series": "UNRATE",
                       "transform": "level_pct", "schedule": "bls",
                       "match": "Employment Situation", "tile": "Jobs"},
            "pce": {"enabled": True, "label": "PCE prices", "series": "PCEPI",
                    "transform": "pct_mom", "schedule": "bea",
                    "match": "Personal Income and Outlays", "tile": "PCE"},
            "core_pce": {"enabled": True, "label": "Core PCE", "series": "PCEPILFE",
                         "transform": "pct_mom", "schedule": "bea",
                         "match": "Personal Income and Outlays", "tile": "PCE"},
            # The headline (real, SAAR), not "GDP" - that series is a nominal level.
            "gdp": {"enabled": True, "label": "GDP", "series": "A191RL1Q225SBEA",
                    "transform": "pct_saar", "schedule": "bea", "match": "GDP (",
                    "tile": "GDP"},
            "retail": {"enabled": True, "label": "Retail sales", "series": "RSAFS",
                       "transform": "pct_mom", "schedule": "fred", "release_id": 9,
                       "time_ct": "07:30", "tile": "Retail sales"},
            "claims": {"enabled": True, "label": "Jobless claims", "series": "ICSA",
                       "transform": "level_k", "schedule": "fred", "release_id": 180,
                       "time_ct": "07:30", "tile": "Jobless claims"},
        },
    },
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


SAME_FEED_MERGE_MAX_H = 24


def same_feed_merge_h(cfg=None) -> float:
    """``[dedupe] same_feed_merge_h``: the hours within which ONE feed's two
    items with one title key merge into one row (0 = never). ``cfg`` is a
    loaded mapping (``load()`` when omitted). Anything that is not a finite
    real number >= 0 - a bool, a string, NaN, a negative, a missing table - is
    the built-in default, silently: the poll cycle reads it every feed.

    A usable value above ``SAME_FEED_MERGE_MAX_H`` (24) is clamped to it: the
    Settings field is 0-24, and a title match already needs the two items
    within a day, so 48 would act as 24 anyway - the reader returns what the
    store will actually do. A decimal within range is kept as written."""
    default = DEFAULTS["dedupe"]["same_feed_merge_h"]
    cfg = load() if cfg is None else cfg
    table = cfg.get("dedupe") if isinstance(cfg, dict) else None
    value = table.get("same_feed_merge_h", default) if isinstance(table, dict) else default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    if not 0 <= value < float("inf"):      # NaN fails every comparison
        return default
    return min(value, SAME_FEED_MERGE_MAX_H)


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


# ── v2: impact rank and economic calendar ─────────────────────────────────────

TRANSFORMS = ("pct_mom", "change_k", "level_pct", "level_k", "pct_saar")
SCHEDULES = ("bls", "bea", "fred")

_warned = set()


def _warn_once(key, msg, *args):
    """One WARNING per distinct bad value: these accessors run every poll."""
    if key in _warned:
        return
    _warned.add(key)
    log.warning(msg, *args)


def _is_num(value) -> bool:
    """A finite real number, never a bool (``float(True)`` is 1.0). No
    ``math`` here - the module's import set is pinned for Tier 1."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return value == value and value not in (float("inf"), float("-inf"))


def _table(cfg, key):
    value = cfg.get(key) if isinstance(cfg, dict) else None
    return value if isinstance(value, dict) else None


def _num_table(value, default):
    """``{str: number}`` - a non-table falls back to the default table, a
    non-numeric entry is dropped (silently: the scorer adds only numbers)."""
    if not isinstance(value, dict):
        value = default
    return {k: v for k, v in value.items() if isinstance(k, str) and _is_num(v)}


def _tiers(value):
    """Keep a tier only when it is a table with an int ``points`` and a list
    of words; the words keep only non-empty strings."""
    if not isinstance(value, dict):
        value = DEFAULTS["impact"]["keywords"]
    out = {}
    for name, tier in value.items():
        if not isinstance(tier, dict):
            continue
        points, words = tier.get("points"), tier.get("words")
        if isinstance(points, bool) or not isinstance(points, int):
            continue
        if not isinstance(words, list):
            continue
        out[name] = {"points": points,
                     "words": [w for w in words if isinstance(w, str) and w.strip()]}
    return out


def impact_config() -> dict:
    """``[impact]`` as a validated COPY (the scorer may keep it).

    ``high_at`` / ``med_at`` must both be finite numbers with
    ``high_at > med_at``; anything else is the built-in pair, with one WARNING
    per distinct bad value. Other scalars fall back silently to their default;
    keyword tiers, source points, Form 4 bands and filing points keep only
    well-formed entries (see ``_tiers`` / ``_num_table``). Never raises."""
    default = DEFAULTS["impact"]
    raw = _table(load(), "impact") or {}
    high, med = raw.get("high_at", default["high_at"]), raw.get("med_at", default["med_at"])
    if not (_is_num(high) and _is_num(med) and high > med):
        _warn_once(("impact", repr(high), repr(med)),
                   "news.toml: [impact] high_at = %r, med_at = %r is not a pair of "
                   "numbers with high_at > med_at - using %r / %r",
                   high, med, default["high_at"], default["med_at"])
        high, med = default["high_at"], default["med_at"]
    out = {"high_at": high, "med_at": med}
    # stale_after_h must be > 0 (0 would cap every HIGH on sight); the two
    # boosts may be 0 (off) but never negative - a negative "boost" would
    # quietly turn a followed ticker into a penalty.
    for key, floor_ok in (("stale_after_h", lambda v: v > 0),
                          ("multi_source", lambda v: v >= 0),
                          ("watchlist", lambda v: v >= 0)):
        value = raw.get(key, default[key])
        if _is_num(value) and floor_ok(value):
            out[key] = value
            continue
        _warn_once(("impact", key, repr(value)),
                   "news.toml: [impact] %s = %r is not a usable number - using %r",
                   key, value, default[key])
        out[key] = default[key]
    teaser = raw.get("match_teaser", default["match_teaser"])
    out["match_teaser"] = teaser if isinstance(teaser, bool) else default["match_teaser"]
    out["keywords"] = _tiers(raw.get("keywords"))
    out["source_points"] = _num_table(raw.get("source_points"), default["source_points"])
    out["form4"] = _num_table(raw.get("form4"), default["form4"])
    out["filings"] = _num_table(raw.get("filings"), default["filings"])
    return copy.deepcopy(out)


def _calendar():
    return _table(load(), "calendar") or DEFAULTS["calendar"]


def _cadence_key(key) -> bool:
    return isinstance(key, str) and key.endswith(("_min", "_h"))


# The largest cadence a timedelta is ever built from: a week of minutes, a year
# of hours - the same caps ``webgui/pages/news_view.py`` (_MAX_WATCH_MIN /
# _MAX_FRESH_H) reads the published settings under. Past them a value is junk
# (``10**400`` minutes would overflow every timedelta built from it).
CADENCE_MAX_MIN = 10080
CADENCE_MAX_H = 8760


def _cadence_max(key):
    return CADENCE_MAX_MIN if key.endswith("_min") else CADENCE_MAX_H


def _cadence_ok(key, value) -> bool:
    return _is_num(value) and 0 < value <= _cadence_max(key)


def _cadence(where, key, value, default):
    """A ``*_min`` / ``*_h`` value when it is a finite number > 0 and at most
    its cap (a week of minutes, a year of hours; never a bool), else
    ``default`` with one WARNING per distinct bad value. A 0 or negative
    cadence would poll in a tight loop or never; NaN fails ``> 0``."""
    if _cadence_ok(key, value):
        return value
    _warn_once((where, key, repr(value)),
               "news.toml: %s %s = %r is not a number > 0 and <= %r - using %r",
               where, key, value, _cadence_max(key), default)
    return default


def calendar_config() -> dict:
    """The ``[calendar]`` scalars (sub-tables left out), each defaulted when
    absent. A copy.

    ``enabled`` that is not a real bool FAILS CLOSED (False), as a source's or
    an indicator's does. Every ``*_min`` / ``*_h`` scalar that is not a finite
    number > 0 (a bool, a string, NaN, 0, a negative) or is past its cap
    (10080 minutes, 8760 hours) is its built-in default;
    one WARNING per distinct bad value."""
    cal = _calendar()
    base = DEFAULTS["calendar"]
    out = {k: v for k, v in base.items() if not isinstance(v, dict)}
    out.update({k: v for k, v in cal.items() if not isinstance(v, dict)})
    if not isinstance(out.get("enabled"), bool):
        _warn_once(("calendar", "enabled", repr(out.get("enabled"))),
                   "news.toml: [calendar] enabled = %r, not true/false - treated as "
                   "false", out.get("enabled"))
        out["enabled"] = False
    for key in list(out):
        if _cadence_key(key) and key in base:
            out[key] = _cadence("[calendar]", key, out[key], base[key])
        elif _cadence_key(key) and not _cadence_ok(key, out[key]):
            out.pop(key)                 # an unknown key with no default: drop it
    return copy.deepcopy(out)


def _hhmm(value):
    """``"HH:MM"`` (1-2 digit hour 0-23, 2-digit minute 0-59) normalised to two
    digits each, else None. No ``re``: the import set is pinned for Tier 1."""
    if not isinstance(value, str):
        return None
    parts = value.strip().split(":")
    if len(parts) != 2:
        return None
    hh, mm = parts
    if not (hh.isascii() and hh.isdigit() and 1 <= len(hh) <= 2
            and mm.isascii() and mm.isdigit() and len(mm) == 2):
        return None
    h, m = int(hh), int(mm)
    return f"{h:02d}:{m:02d}" if h < 24 and m < 60 else None


def _int_at_least(value, floor):
    """A real int (never a bool or a float) ``>= floor``, else None."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value >= floor else None


def dividends_config() -> dict:
    """``[calendar.dividends]`` validated, as a copy - the table
    ``calendar_config()`` leaves out (it returns ``[calendar]`` scalars only).

    * ``enabled`` not a real bool -> False (FAIL CLOSED);
    * ``refresh_at`` not ``"HH:MM"`` -> the default (a valid one comes back
      zero-padded: ``"7:05"`` -> ``"07:05"``);
    * ``horizon_days`` not an int >= 1 -> the default;
    * ``lookback_days`` not an int >= 0 -> the default (0 is real: keep no
      past rows).

    A bool is never an int here. A table that is not a table is the defaults.
    One WARNING per distinct bad value; never raises. Keys the table does not
    ship with are dropped."""
    default = DEFAULTS["calendar"]["dividends"]
    raw = _table(_calendar(), "dividends") or {}
    out = {}
    enabled = raw.get("enabled", default["enabled"])
    if not isinstance(enabled, bool):
        _warn_once(("calendar.dividends", "enabled", repr(enabled)),
                   "news.toml: [calendar.dividends] enabled = %r, not true/false - "
                   "treated as false", enabled)
        enabled = False
    out["enabled"] = enabled
    checks = (("refresh_at", _hhmm, "HH:MM"),
              ("horizon_days", lambda v: _int_at_least(v, 1), "an int >= 1"),
              ("lookback_days", lambda v: _int_at_least(v, 0), "an int >= 0"))
    for key, check, want in checks:
        value = raw.get(key, default[key])
        good = check(value)
        if good is None:
            _warn_once(("calendar.dividends", key, repr(value)),
                       "news.toml: [calendar.dividends] %s = %r is not %s - using %r",
                       key, value, want, default[key])
            good = default[key]
        out[key] = good
    return copy.deepcopy(out)


def calendar_source(name) -> dict:
    """``[calendar.sources.<name>]`` as a copy, with ``user_agent`` filled from
    ``[collector] feed_user_agent`` when ``""`` or absent, and ``refresh_min``
    from ``[calendar] refresh_min`` when absent OR not a finite number > 0 (the
    inherited value is itself validated by ``calendar_config``). A name that is
    not a source table is ``enabled = False`` (fail closed)."""
    sources = _table(_calendar(), "sources") or {}
    table = sources.get(name) if isinstance(name, str) else None
    out = copy.deepcopy(table) if isinstance(table, dict) else {"enabled": False}
    if not isinstance(out.get("enabled", True), bool):
        out["enabled"] = False
    out.setdefault("enabled", True)
    if not (isinstance(out.get("user_agent"), str) and out["user_agent"]):
        collector = _table(load(), "collector") or {}
        ua = collector.get("feed_user_agent")
        out["user_agent"] = (ua if isinstance(ua, str) and ua
                             else DEFAULTS["collector"]["feed_user_agent"])
    inherited = calendar_config()["refresh_min"]          # already validated
    if "refresh_min" in out:
        out["refresh_min"] = _cadence(f"[calendar.sources.{name}]", "refresh_min",
                                      out["refresh_min"], inherited)
    else:
        out["refresh_min"] = inherited
    return out


def indicators() -> list:
    """Every ENABLED ``[calendar.indicators.<key>]`` table as a dict with
    ``key`` added, in the built-in order with any file-only indicator
    appended (the file is deep-merged onto DEFAULTS). An unknown ``transform`` / ``schedule``, a
    missing ``series`` or a non-table entry is skipped with a WARNING; an
    ``enabled`` that is not a real bool fails closed."""
    tables = _table(_calendar(), "indicators")
    if tables is None:
        tables = DEFAULTS["calendar"]["indicators"]
    out = []
    for key, raw in tables.items():
        if not isinstance(raw, dict):
            _warn_once(("ind", key, "table"),
                       "news.toml: [calendar.indicators.%s] is not a table - skipped", key)
            continue
        enabled = raw.get("enabled", True)
        if not isinstance(enabled, bool):
            _warn_once(("ind", key, "enabled", repr(enabled)),
                       "news.toml: indicator %r has enabled = %r, not true/false - "
                       "treated as false", key, enabled)
            continue
        if not enabled:
            continue
        series = raw.get("series")
        if not (isinstance(series, str) and series):
            _warn_once(("ind", key, "series"),
                       "news.toml: indicator %r has no series - skipped", key)
            continue
        if raw.get("transform") not in TRANSFORMS:
            _warn_once(("ind", key, "transform", repr(raw.get("transform"))),
                       "news.toml: indicator %r has unknown transform %r - skipped",
                       key, raw.get("transform"))
            continue
        if raw.get("schedule") not in SCHEDULES:
            _warn_once(("ind", key, "schedule", repr(raw.get("schedule"))),
                       "news.toml: indicator %r has unknown schedule %r - skipped",
                       key, raw.get("schedule"))
            continue
        item = copy.deepcopy(raw)
        item["key"] = key
        out.append(item)
    return out

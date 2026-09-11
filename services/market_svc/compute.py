"""Market dashboard compute — fetch raw quotes, build the display payload.

I/O seams (``fetch_raw_quotes`` / ``read_sector_pcr``) are thin + defensive;
``build_dashboard`` is PURE over an already-fetched raw dict + pcr so it carries
the coverage. All defensive — a fetch/parse failure degrades to no-data tiles.
"""
import logging
import math
import re
from datetime import datetime

import requests

from repo_paths import ENV_FLAGS, PROXY_URL
from services.market_svc import classify, symbols
from shared import market_calendar as mc

log = logging.getLogger("market_svc.compute")

CACHE_SENTIMENT = "cache:sentiment:composite"
CACHE_MATRIX = "cache:options:matrix"

# Baseline for the cap-weighted put/call tile: pcr>1 = more puts = risk-off.
_PCR_BASELINE = 1.0

# Pooled HTTP session (keep-alive) — matches the house perf pattern
# (schwab_proxy.trader_request reuses a pooled session).
_SESSION = requests.Session()


def fetch_raw_quotes(syms, *, timeout=8.0):
    """GET the proxy's raw /quotes for ``syms``; returns the raw Schwab dict.

    Uses the raw endpoint (not SchwabProxyClient.get_quotes) so assetMainType +
    futurePercentChange survive. Never raises — returns {} on any failure.
    """
    if not syms:
        return {}
    try:
        resp = _SESSION.get(f"{PROXY_URL}/quotes",
                            params={"symbols": ",".join(syms)}, timeout=timeout)
        if resp.status_code != 200:
            return {}
        return resp.json() or {}
    except Exception:  # noqa: BLE001
        log.warning("market /quotes fetch failed", exc_info=True)
        return {}


# Version-gated memo: read_sector_pcr runs every ~2s poll but the composite it
# reads changes only every ~120s. Deserialize the full payload only when the
# composite's version moves; otherwise serve the cached float off a cheap :ver
# probe (avoids a full JSON deserialize of the composite ~every poll).
_PCR_CACHE = {"ver": None, "pcr": None}


def reset_pcr_cache():
    """Drop the version-gated sector-pcr memo (test helper)."""
    _PCR_CACHE.update(ver=None, pcr=None)


def read_sector_pcr(bus):
    """Cap-weighted sector put/call ratio from cache:sentiment:composite, or None."""
    try:
        ver = bus.cache_version(CACHE_SENTIMENT)
        if ver is not None and ver == _PCR_CACHE["ver"]:
            return _PCR_CACHE["pcr"]
        env = bus.cache_get(CACHE_SENTIMENT)
        if not env:
            _PCR_CACHE.update(ver=ver, pcr=None)
            return None
        live = (env.payload or {}).get("live") or {}
        pcr = live.get("sector_pcr")
        val = float(pcr) if pcr not in (None, "") else None
        _PCR_CACHE.update(ver=ver, pcr=val)
        return val
    except Exception:  # noqa: BLE001
        return None


# Version-gated memo for the net-premium aggregate (mirrors _PCR_CACHE): the
# matrix bumps ~every minute but we only need to re-read the `premium` block when
# its version moves. Off a cheap :ver probe otherwise.
_NETPREM_CACHE = {"ver": None, "prem": None}


def reset_net_prem_cache():
    """Drop the version-gated net-premium memo (test helper)."""
    _NETPREM_CACHE.update(ver=None, prem=None)


def read_net_prem(bus):
    """Dollar-weighted call/put premium skew aggregate from cache:options:matrix
    (``premium`` block, built by options_svc.matrix.market_premium_aggregate), or
    None. Defensive — a missing/cold options service degrades to None."""
    try:
        ver = bus.cache_version(CACHE_MATRIX)
        if ver is not None and ver == _NETPREM_CACHE["ver"]:
            return _NETPREM_CACHE["prem"]
        env = bus.cache_get(CACHE_MATRIX)
        if not env:
            _NETPREM_CACHE.update(ver=ver, prem=None)
            return None
        prem = (env.payload or {}).get("premium")
        val = prem if isinstance(prem, dict) else None
        _NETPREM_CACHE.update(ver=ver, prem=val)
        return val
    except Exception:  # noqa: BLE001
        return None


def symbol_premium_skew(call, put):
    """Signed call/put PREMIUM skew (%) = (call−put)/(call+put)·100, or None when
    no premium has accrued (both ~0). Positive = more money through calls."""
    try:
        c = float(call or 0.0)
        p = float(put or 0.0)
    except (TypeError, ValueError):
        return None
    total = c + p
    if total <= 0:
        return None
    return round((c - p) / total * 100.0, 1)


# Version-gated memo for the per-symbol raw premium map (mirrors _NETPREM_CACHE).
_SYMPREM_CACHE = {"ver": None, "map": None}


def reset_symbol_premiums_cache():
    """Drop the version-gated per-symbol premium memo (test helper)."""
    _SYMPREM_CACHE.update(ver=None, map=None)


def read_symbol_premiums(bus):
    """{symbol: (call_prem, put_prem)} from cache:options:matrix rows, or {}.

    Feeds the per-symbol premium sublines + the BIG10 basket aggregate. Defensive —
    a missing/cold options service degrades to {}."""
    try:
        ver = bus.cache_version(CACHE_MATRIX)
        if ver is not None and ver == _SYMPREM_CACHE["ver"]:
            return _SYMPREM_CACHE["map"] or {}
        env = bus.cache_get(CACHE_MATRIX)
        if not env:
            _SYMPREM_CACHE.update(ver=ver, map={})
            return {}
        out = {}
        for r in (env.payload or {}).get("rows") or []:
            sym = r.get("symbol")
            if sym is not None:
                out[sym] = (r.get("call_prem"), r.get("put_prem"))
        _SYMPREM_CACHE.update(ver=ver, map=out)
        return out
    except Exception:  # noqa: BLE001
        return {}


def _leg(raw, sym):
    q = raw.get(sym)
    if not q:
        return None
    return classify.normalize_quote(q)


def _tile_base(entry):
    return {"display": entry["display"], "description": entry["description"],
            "category": entry["category"], "polarity": entry["polarity"],
            "value_only": entry["value_only"]}


def _attach_prem(t, e, symbol_prem):
    """Attach a per-symbol call/put premium skew (``prem_skew_pct``) to a tile
    flagged ``prem`` in the symbol map. A quote tile looks itself up by
    ``quote_symbol``; a basket (BIG10) dollar-weight-aggregates its members
    (Σcall/Σput over whichever are present). ``None`` when no premium is
    available (e.g. a name not in the collected universe)."""
    symbol_prem = symbol_prem or {}
    if e["kind"] == "basket":
        c_sum = p_sum = 0.0
        for m in e.get("basket", ()):
            c, p = symbol_prem.get(m, (None, None))
            c_sum += float(c or 0.0)
            p_sum += float(p or 0.0)
        t["prem_skew_pct"] = symbol_premium_skew(c_sum, p_sum)
    else:
        c, p = symbol_prem.get(e.get("quote_symbol"), (None, None))
        t["prem_skew_pct"] = symbol_premium_skew(c, p)


def rank_tiles(tiles):
    """PURE: order one frame's tiles as a leaderboard — best day %-move first.

    Three bands, in order: composite BASKET tiles (BIG10) pinned leftmost, then
    quoted tiles descending by ``change_pct``, then tiles with no percentage to
    rank on (no-data, or a value-only internal). ``sorted`` is stable, so within
    a band the curated symbol-map order survives — equal movers don't jitter
    between polls.

    The basket is PINNED rather than sorted because it carries a ``change_pct``
    of its own (its members' average), which would otherwise drop it into the
    middle of the very constituents it summarizes.
    """
    def key(t):
        if t.get("basket"):
            return (0, 0.0)
        pct = t.get("change_pct")
        try:
            return (1, -float(pct))
        except (TypeError, ValueError):
            return (2, 0.0)

    return sorted(tiles, key=key)


def build_dashboard(raw, *, sector_pcr, proxy_up, net_prem=None, symbol_prem=None):
    """Assemble the ordered categories→tiles payload (pure).

    ``net_prem`` = the dollar-weighted call/put premium skew aggregate
    (``matrix.market_premium_aggregate`` output, or None) feeding the Net Prem
    external tile. ``symbol_prem`` = ``{symbol: (call, put)}`` (from the matrix
    rows) feeding the per-symbol premium sublines on ``prem``-flagged tiles."""
    tiles_by_cat = {c: [] for c in symbols.CATEGORY_ORDER}
    for e in symbols.SYMBOL_MAP:
        t = _tile_base(e)
        if e["kind"] == "quote":
            n = _leg(raw, e["quote_symbol"])
            if n is None:
                t.update(last=None, change=None, change_pct=None, color_state="no_data")
            else:
                last, chg, pct = n
                drive = last if e["value_only"] else pct
                t.update(last=last, change=None if e["value_only"] else chg,
                         change_pct=None if e["value_only"] else pct,
                         color_state=classify.color_state(
                             drive, polarity=e["polarity"], value_only=e["value_only"]))
        elif e["kind"] == "spread":
            a, b, mode = e["spread"]
            la, lb = _leg(raw, a), _leg(raw, b)
            if la is None or lb is None:
                t.update(last=None, change=None, change_pct=None, color_state="no_data")
            else:
                last, chg, pct = classify.spread_value(mode, la, lb)
                if mode == "diff_last":
                    # A signed COUNT spread ($ADVN-$DECN): the count isn't a %, so
                    # color by SIGN (single intensity, value_only path) and display
                    # the level only — NOT the pct thresholds (else it's always
                    # "strong"). Mark value_only so tile_text shows the level, no %.
                    t["value_only"] = True
                    t.update(last=last, change=None, change_pct=None,
                             color_state=classify.color_state(
                                 chg, polarity=e["polarity"], value_only=True))
                else:  # diff_pct (HYG-LQD): a real percentage-point spread
                    t.update(last=last, change=None, change_pct=pct,
                             color_state=classify.color_state(pct, polarity=e["polarity"]))
        elif e["kind"] == "external":
            if e["source"] == "options_net_prem":  # dollar-weighted premium skew
                skew_pct = (net_prem or {}).get("skew_pct") if isinstance(net_prem, dict) else None
                if skew_pct is None:
                    t.update(last=None, change=None, change_pct=None, color_state="no_data")
                else:
                    # Carry the raw aggregate on the tile; the page's tile_text
                    # renders "Call 49%"/"Put 22%" + the net-$ subline (Tier-1
                    # formatting). Color by sign (single mild intensity), like
                    # the sibling Put/Call tile.
                    t.update(net_prem=True, skew_pct=skew_pct,
                             net_m=(net_prem or {}).get("net_m"),
                             symbols=(net_prem or {}).get("symbols"),
                             last=None, change=None, change_pct=None,
                             color_state=classify.color_state(
                                 skew_pct, polarity=e["polarity"], value_only=True))
            else:  # sentiment put/call
                if sector_pcr is None:
                    t.update(last=None, change=None, change_pct=None, color_state="no_data")
                else:
                    dev = sector_pcr - _PCR_BASELINE
                    t.update(last=sector_pcr, change=None, change_pct=None,
                             color_state=classify.color_state(
                                 dev, polarity=e["polarity"], value_only=True))
        elif e["kind"] == "basket":  # equal-weighted composite (BIG10): avg %chg + breadth
            pcts = [n[2] for n in (_leg(raw, m) for m in e.get("basket", ())) if n is not None]
            if not pcts:
                t.update(last=None, change=None, change_pct=None, color_state="no_data")
            else:
                avg = sum(pcts) / len(pcts)
                up = sum(1 for p in pcts if p > 0)
                t.update(last=None, change=None, change_pct=avg,
                         basket=True, avg_pct=avg, breadth_text=f"{up}/{len(pcts)} up",
                         color_state=classify.color_state(avg, polarity=e["polarity"]))
        if e.get("prem"):
            _attach_prem(t, e, symbol_prem)
        tiles_by_cat[e["category"]].append(t)

    for c in symbols.SORTED_CATEGORIES:
        if tiles_by_cat.get(c):
            tiles_by_cat[c] = rank_tiles(tiles_by_cat[c])
    categories = [{"category": c, "tiles": tiles_by_cat[c]}
                  for c in symbols.CATEGORY_ORDER if tiles_by_cat[c]]
    return {"categories": categories, "proxy_up": proxy_up, "errors": []}


def collect(bus):
    """Fetch + build the full dashboard payload (the scheduler's per-tick call)."""
    from services import _proxy
    raw = fetch_raw_quotes(symbols.quote_symbols())
    pcr = read_sector_pcr(bus)
    net_prem = read_net_prem(bus)
    symbol_prem = read_symbol_premiums(bus)
    proxy_up = bool(raw) or bool(_proxy.health().get("up"))
    return build_dashboard(raw, sector_pcr=pcr, proxy_up=proxy_up,
                           net_prem=net_prem, symbol_prem=symbol_prem)


# ---------------------------------------------------------------------------
# Market summary — a periodic Claude-written verdict (cache:market:summary).
# ---------------------------------------------------------------------------
_SUMMARY_MODEL = "claude-sonnet-5"
# A cap, not a spend (billing is on generated tokens), and the ONLY ceiling on
# the reply: a finished reply is shown whole, however long. A reply that hits
# this cap keeps only its complete sentences (``_complete_sentences``) — nothing
# is ever cut mid-word. The 400-character slice that once published "... rather
# than chas" is gone (2026-09-10). Pinned by a test.
_SUMMARY_MAX_TOKENS = 300
# The code states the facts; the model only joins them and adds the posture.
# Every time the model paraphrased a reading on 2026-09-10 it bent one: "Gliding"
# (lower, but nobody pushing) became "real weight behind the slide" and later
# "absent buyers"; a put credit spread became a "bearish trade"; "2 rising and
# beating" became "only 2 of 11 outperforming" when 6 were. So each reading is
# written here as one plain-English statement, the model sees ONLY these
# statements, and ``generate_summary`` withholds any reply that drops or rewords
# one (``_missing_fact``). Plain paraphrases of the webgui's hovers
# (sentiment.BAND_WORD_PICTURE / TREND_PICTURE, regime_mix.REGIME_PICTURE); the
# key sets are pinned by shared/tests/test_cross_tier_mirrors.py, so a word
# cannot reach the screen without its statement here. No apostrophes or quotes:
# a curly one in the reply must not fail the word-for-word check. No semicolons
# either: the model turned Circling's into a comma in 3 of 3 live replies on
# 2026-09-11, withholding every summary while the trend read Circling.
#
# Keyed by the SIGNAL word: bias and signal are two bands of the one sentiment
# composite, so one statement covers both. The composite scores calm,
# supportive conditions HIGH and stress LOW (a VIX spike and heavy put buying
# both score low - sentiment-dashboard/tests/test_scale_direction.py). Until
# 2026-09-11 these read it as contrarian and told the Desk investors were
# fearful on a calm, rising morning.
_SENTIMENT_FACTS = {
    "Strong Bull": "Market conditions are very calm and supportive, which this "
                   "model reads as a strong reason to lean long.",
    "Bullish": "Market conditions are calm and supportive, which this model "
               "reads as a reason to lean long.",
    "Neutral": "Market conditions are mixed, so this model sees no edge either "
               "way.",
    "Bearish": "Market conditions are under some stress, which this model reads "
               "as a warning.",
    "Strong Bear": "Market conditions are under heavy stress, which this model "
                   "reads as a strong warning.",
}
_TREND_FACTS = {
    "Climbing": "Prices are rising, with buyers pushing them up.",
    "Stalling": "Prices are still high, but the buying has run out.",
    "Circling": "Prices have no clear direction, with buyers and sellers "
                "balanced.",
    "Gliding": "Prices are drifting lower, but sellers are not pushing them.",
    "Diving": "Prices are falling under heavy selling.",
}
# "Unclear" is the regime's word for having no reading, so it states nothing.
_REGIME_FACTS = {
    "Balanced": "The market is quiet and two-sided, sitting near its average.",
    "Trending": "Prices are moving with persistence, but which way is not yet "
                "confirmed.",
    "Rallying": "The market is in a steep, steady move higher.",
    "Firming": "The market is climbing steadily and gently.",
    "Retreating": "The market is in a steep, steady move lower.",
    "Softening": "The market is declining steadily and gently.",
    "Breakout": "The market is breaking out of its range into new ground.",
    "Breakdown": "The market is breaking down out of its range to the "
                 "downside.",
    "Whipsaw": "The market is choppy: lots of movement but no progress.",
    "Stressed": "Fear is driving the market: volatility is high and price gaps "
                "are not filling.",
    "Unclear": "",
}
_HORIZON_LEAD = {"today": "Today", "quarter": "Over the quarter"}


def _size_fact(size):
    """The position-size multiplier ("0.85x") as a plain statement, without
    quoting it; "" when there is no usable multiplier."""
    m = _finite(str(size).strip().rstrip("xX")) if size is not None else None
    if m is None:
        return ""
    if abs(m - 1.0) < 1e-9:
        return "The model suggests trading at normal size."
    return ("The model suggests trading "
            f"{'smaller' if m < 1.0 else 'larger'} than usual.")


def _sector_fact(horizon, totals):
    """The Bull/Bear counts as one statement, straight from the ready-made
    totals — the model never counts. "" when there is nothing counted."""
    t = totals or {}
    lead = _HORIZON_LEAD.get(horizon)
    n = t.get("sectors")
    if not lead or not n:
        return ""

    def verb(k):
        return "is" if k == 1 else "are"

    rising, falling, beating = t.get("rising", 0), t.get("falling", 0), \
        t.get("beating_sp500", 0)
    return (f"{lead}, {rising} of the {n} {'sector' if n == 1 else 'sectors'} "
            f"{verb(rising)} rising and {falling} {verb(falling)} falling, and "
            f"{beating} {verb(beating)} beating the S&P 500.")


def summary_facts(packet):
    """The packet's readings as plain-English statements, in reading order:
    sentiment, position size, trend, regime, sectors. An absent reading states
    nothing — never a neutral stand-in. ``[]`` when there is nothing to say."""
    p = packet if isinstance(packet, dict) else {}
    bb = p.get("bullbear") if isinstance(p.get("bullbear"), dict) else {}
    facts = [
        _SENTIMENT_FACTS.get(p.get("signal"), ""),
        _size_fact(p.get("size")),
        _TREND_FACTS.get((p.get("trend") or {}).get("word"), ""),
        _REGIME_FACTS.get((p.get("regime") or {}).get("word"), ""),
        _sector_fact(bb.get("horizon"), bb.get("totals")),
    ]
    return [f for f in facts if f]


def _missing_fact(text, facts):
    """The first statement the reply does not carry word for word, or None.

    Case-insensitive and blind to the statement's own closing full stop, so a
    fact the model joined mid-sentence ("..., and the model suggests ...")
    still counts; anything else — a changed word, a dropped clause — does not."""
    t = " ".join(str(text or "").lower().split())
    for fact in facts:
        want = " ".join(fact.lower().split()).rstrip(".")
        if want not in t:
            return fact
    return None


_SUMMARY_SYSTEM = (
    "You write the market summary on a trading desk, in plain everyday "
    "English. You get a list of facts as JSON. Each fact was written by the "
    "app from its own readings and is exactly right. Include every fact "
    "exactly as written, in the order given. Do not reword, shorten, merge, "
    "reorder or drop any fact, and add no other facts or claims about the "
    "market. You may join neighbouring facts with a comma or 'and' and lower "
    "a fact's first letter when you do. Then add one closing sentence with a "
    "practical trading posture that follows from the facts; standard options "
    "terms such as 'put credit spreads' are fine when the advice needs them. "
    "ACCURACY IS PARAMOUNT: any strategy you name must match its real "
    "direction. A put credit spread profits if prices hold up or rise (bullish "
    "to neutral). A call credit spread profits if prices stay down (bearish to "
    "neutral). Buying puts or a put debit spread is bearish; buying calls or a "
    "call debit spread is bullish; an iron condor profits if prices stay in a "
    "range (neutral). Never describe a put credit spread as bearish. Never "
    "describe a call credit spread as bullish. If the facts point different "
    "ways, say so plainly in the posture rather than blending them; if you "
    "are not sure a strategy fits, give the posture without naming one. No "
    "prices, no numbers beyond those in the facts, no preamble, no "
    "disclaimers, no bullet points, no markdown."
)


def _anthropic_api_key():
    import os
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key.strip()
    try:
        from repo_paths import SHARED_DIR
        p = SHARED_DIR / "anthropic_key.txt"
        if p.exists():
            return p.read_text(encoding="utf-8").strip()
    except Exception:  # noqa: BLE001
        log.debug("reading anthropic_key.txt failed", exc_info=True)
    return None


def _make_summary_client():
    """A real ``anthropic.Anthropic`` client, or ``None`` (never raises).

    ``None`` in an environment whose profile clears ``allow_claude`` (dev) —
    checked FIRST, so a suppressed environment never even reads the key. Not a
    new code path: ``generate_summary`` already returns the empty narrative when
    no key is configured, so the dev ticker simply shows its live data items."""
    if not ENV_FLAGS.get("allow_claude", True):
        return None
    key = _anthropic_api_key()
    if not key:
        return None
    try:
        import anthropic
        # Bounded: this runs inline in the poll loop, so a hung connection must not
        # stall the dashboard cadence (the SDK default is ~600s).
        return anthropic.Anthropic(api_key=key, timeout=30.0, max_retries=1)
    except Exception:  # noqa: BLE001
        return None


# The five Market Trend flight words. MIRRORS the five-state entries of
# webgui/pages/sentiment._TREND_SHORT (this tier cannot import Tier 1); pinned by
# shared/tests/test_cross_tier_mirrors.py. The sentence must use the words the
# screen shows, or it names the trend one way while the pill beside it says another.
_TREND_WORDS = {"bullish": "Climbing", "lack_of_bullishness": "Stalling",
                "neutral": "Circling", "lack_of_bearishness": "Gliding",
                "bearish": "Diving"}

_QUADRANTS = ("rising_leading", "rising_lagging", "falling_leading",
              "falling_lagging", "unknown")


def _finite(v):
    """A real number or None — a NaN, a bool or a non-number is no reading,
    never 0 (``float(True)`` is 1.0, and a bool is not a reading)."""
    if isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _quadrant(trend, excess):
    """MIRRORS webgui/pages/bullbear.quadrant: ties go to the cautious side (a
    flat trend is not rising, a zero excess is not leading), and a missing axis
    is ``unknown`` rather than a default bucket."""
    if trend is None or excess is None:
        return "unknown"
    if trend > 0:
        return "rising_leading" if excess > 0 else "rising_lagging"
    return "falling_leading" if excess > 0 else "falling_lagging"


def bullbear_counts(bullbear, now=None):
    """``(horizon, {quadrant: n})`` over the sector rows.

    Today's axes once the regular session has opened AND a benchmark move
    exists; the quarter's otherwise — the Desk strip's own rule
    (``desk.strip_is_live``), so the sentence counts on the horizon the chips
    are drawn on. ``(None, {})`` when there are no rows to count."""
    view = bullbear if isinstance(bullbear, dict) else {}
    levels = view.get("levels") if isinstance(view.get("levels"), dict) else {}
    rows = levels.get("sector") if isinstance(levels.get("sector"), list) else []
    rows = [r for r in rows if isinstance(r, dict)]
    if not rows:
        return None, {}
    live = (_finite(view.get("benchmark_day_pct")) is not None
            and mc.regular_session_has_opened(now or datetime.now().astimezone()))
    counts = {q: 0 for q in _QUADRANTS}
    for row in rows:
        raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
        trend, excess = ((row.get("day_pct"), row.get("day_excess")) if live
                         else (raw.get("trend"), raw.get("excess")))
        counts[_quadrant(_finite(trend), _finite(excess))] += 1
    return ("today" if live else "quarter"), counts


def _bullbear_totals(counts):
    """The combined sector counts, ready-made — ``_sector_fact`` states them and
    ``_count_claim_error`` checks the reply against them.

    "Beating the S&P 500" is TWO buckets (rising_leading + falling_leading) and
    "rising" another two; left to combine them itself, the model twice reported
    the rising-and-beating bucket alone as the whole (2026-09-10: "only 2 of the
    11 sectors are beating the S&P 500" when 6 were). ``{}`` when nothing was
    counted."""
    c = counts or {}
    if not c:
        return {}
    return {
        "sectors": sum(c.values()),
        "rising": c.get("rising_leading", 0) + c.get("rising_lagging", 0),
        "falling": c.get("falling_leading", 0) + c.get("falling_lagging", 0),
        "beating_sp500": c.get("rising_leading", 0) + c.get("falling_leading", 0),
        "trailing_sp500": c.get("rising_lagging", 0) + c.get("falling_lagging", 0),
    }


def build_summary_packet(sentiment, regime, bullbear, now=None):
    """The six readings the Desk shows, in the screen's own words — the ONLY
    facts the summary is written from. Prices are deliberately absent: a
    sentence quoting them goes stale between refreshes."""
    s = sentiment if isinstance(sentiment, dict) else {}
    live = s.get("live") if isinstance(s.get("live"), dict) else {}
    comp = live.get("composite") if isinstance(live.get("composite"), dict) else {}
    der = s.get("derived") if isinstance(s.get("derived"), dict) else {}
    trend = der.get("trend") if isinstance(der.get("trend"), dict) else {}
    r = regime if isinstance(regime, dict) else {}
    horizon, counts = bullbear_counts(bullbear, now)
    return {
        "sentiment": {"composite": _finite(comp.get("total_score"))},
        "trend": {"word": _TREND_WORDS.get(trend.get("state")),
                  "score": _finite(trend.get("smoothed_score",
                                              trend.get("score")))},
        "bias": der.get("bias") or None,
        "signal": der.get("signal") or None,
        "size": der.get("size") or None,
        # The Desk prints console_regime.regime_name: the service label, else
        # the committed key's display word, else "Unclear". sentiment_svc always
        # publishes the label, so only the first applies in practice; a
        # label-less payload from an older writer reads "Unclear" here, and the
        # Desk's moved-since line then says the two differ. A withheld
        # confidence stays withheld, as the console does.
        "regime": {"word": r.get("label") or "Unclear",
                   "confidence": (None if r.get("unclear")
                                  else _finite(r.get("confidence")))},
        # ``totals`` is derived from ``counts`` (so the fingerprint, which reads
        # the counts, is unchanged); the sector fact is written from it, so no
        # count in the summary is ever added up by the model.
        "bullbear": {"horizon": horizon, "counts": counts,
                     "totals": _bullbear_totals(counts)},
    }


# Display resolution: a move smaller than these never reaches the reader's eye,
# so it must not buy a new sentence.
FINGERPRINT_COMPOSITE_STEP = 0.5
FINGERPRINT_TREND_STEP = 5.0
FINGERPRINT_CONFIDENCE_STEP = 0.1
# The sector counts are compared with a TOLERANCE, not in bands: a band still
# flips whenever a count sits on its edge, and in session one of eleven sectors
# crosses flat or crosses the S&P 500 inside every ten-minute gap. Compared
# exactly, that bought a new paid sentence every gap from 09:14 to 16:40 CT on
# 2026-09-11 (43 of them). A sentence's rising and beating counts may now be
# this many sectors off the live ones before it is rewritten; the Desk's
# moved-since line still says when they differ at all.
FINGERPRINT_SECTOR_TOLERANCE = 1


def _bucket(v, step):
    return None if v is None else round(round(v / step) * step, 6)


def _sector_reading(counts):
    """``(sectors counted, rising, beating the S&P 500)`` — the three numbers
    the sector fact states — or None when nothing was counted."""
    t = _bullbear_totals(counts)
    if not t:
        return None
    return t["sectors"], t["rising"], t["beating_sp500"]


def summary_fingerprint(packet):
    """What the sentence is written from, at display resolution — or None when
    there is nothing to summarize (no composite and no trend word).

    Words compare exactly and numbers to their step; the LAST element is the
    Bull/Bear ``_sector_reading``, which ``same_summary_readings`` compares
    with a tolerance. Compare two fingerprints with that, never with ``==``."""
    p = packet if isinstance(packet, dict) else {}
    sent = p.get("sentiment") or {}
    trend = p.get("trend") or {}
    reg = p.get("regime") or {}
    bb = p.get("bullbear") or {}
    if sent.get("composite") is None and trend.get("word") is None:
        return None
    return (
        _bucket(sent.get("composite"), FINGERPRINT_COMPOSITE_STEP),
        trend.get("word"),
        _bucket(trend.get("score"), FINGERPRINT_TREND_STEP),
        p.get("bias"), p.get("signal"),
        reg.get("word"),
        _bucket(reg.get("confidence"), FINGERPRINT_CONFIDENCE_STEP),
        bb.get("horizon"),
        _sector_reading(bb.get("counts")),
    )


def same_summary_readings(a, b):
    """Would one sentence describe both fingerprints' readings?

    Every element must match exactly except the sector reading, where the
    rising and beating counts may each differ by ``FINGERPRINT_SECTOR_TOLERANCE``
    (the number of sectors counted must still match). The gate compares against
    the fingerprint the CURRENT sentence was written from, so a slow drift is
    still caught once it has moved past the tolerance."""
    if a is None or b is None:
        return a == b
    if a[:-1] != b[:-1]:
        return False
    sa, sb = a[-1], b[-1]
    if sa is None or sb is None:
        return sa == sb
    return sa[0] == sb[0] and all(
        abs(x - y) <= FINGERPRINT_SECTOR_TOLERANCE for x, y in zip(sa[1:], sb[1:]))


def _count_anthropic_call():
    """Best-effort per-day Claude-call counter (Settings -> API usage).

    Recorded immediately before each ``messages.create`` so every real attempt
    counts and no-key/stand-down paths (which never reach the API) do not.
    Never raises — counting must not break a Claude call."""
    try:
        from shared import anthropic_counter
        anthropic_counter.record()
    except Exception:  # noqa: BLE001
        pass


_SUMMARY_KEYS = ("cache:sentiment:composite", "cache:sentiment:regime",
                 "cache:sentiment:bullbear")
_PACKET_MEMO = {"key": None, "packet": None}


def reset_packet_memo():
    """Drop the version-gated packet memo (test helper)."""
    _PACKET_MEMO.update(key=None, packet=None)


def read_summary_packet(bus, now=None):
    """The summary packet off its three views, rebuilt only when a version moved
    or the session-open horizon flipped.

    The Bull/Bear payload is ~190 KB and this runs on every 3 s poll: ungated it
    would be deserialized ~7,800 times a trading day for a packet that changes a
    handful of times. ``None`` on a read failure (the gate then skips)."""
    now = now or datetime.now().astimezone()
    try:
        vers = bus.cache_versions(_SUMMARY_KEYS)
        key = (tuple(vers.get(k) for k in _SUMMARY_KEYS),
               mc.regular_session_has_opened(now))
        if key == _PACKET_MEMO["key"] and _PACKET_MEMO["packet"] is not None:
            return _PACKET_MEMO["packet"]
        envs = [bus.cache_get(k) for k in _SUMMARY_KEYS]
        packet = build_summary_packet(*[(e.payload if e else {}) for e in envs],
                                      now=now)
        _PACKET_MEMO.update(key=key, packet=packet)
        return packet
    except Exception:  # noqa: BLE001
        log.warning("summary packet read failed", exc_info=True)
        return None


# A sentence end: . ! or ? (optionally followed by a closing quote or bracket)
# that is followed by whitespace or the end of the text — so the point in
# "0.85x" is not one.
_SENTENCE_END = re.compile(r"[.!?][\"'”’)\]]*(?=\s|$)")


def _complete_sentences(text):
    """``text`` up to the end of its last complete sentence, or "" when it has
    none. Used only on a reply that ran out of room: what the reader sees is
    always whole sentences, never a word cut in half."""
    text = str(text or "")
    ends = list(_SENTENCE_END.finditer(text))
    return text[:ends[-1].end()].strip() if ends else ""


# (wrong direction word, spread side): a PUT credit spread is bullish-to-neutral
# (it profits when prices hold up), a CALL credit spread bearish-to-neutral.
# Accuracy is paramount — the 20:42 CT sentence on 2026-09-10 called put credit
# spreads "bearish trades".
_WRONG_SPREAD_DIRECTION = (("bearish", "put"), ("bullish", "call"))


def _spread_direction_error(text):
    """The phrase that ties a credit spread to the WRONG direction, or None.

    Deliberately narrow: it looks only where a direction word is attached to the
    spread itself ("bearish trades like put credit spreads", "put credit spreads
    are a bearish play"), not anywhere in the same sentence — so a correct
    "despite the bearish read, put credit spreads still fit" passes. A hit
    withholds the sentence: the last good one stays up until the readings
    change."""
    t = " ".join(str(text or "").lower().split())
    for adj, side in _WRONG_SPREAD_DIRECTION:
        spread = rf"{side} credit spreads?"
        for pattern in (
                rf"\b{adj}\b[^.;:!?]{{0,25}}?\b(?:like|such as|via|using|with|through)\s+{spread}\b",
                rf"\b{spread}\s*(?:,|—|-|are|is)?\s*(?:an?\s+|the\s+)?{adj}\b"):
            found = re.search(pattern, t)
            if found:
                return found.group(0)
    return None


# "N of M sectors are <verb>" — every verb maps to the ready-made total it
# claims. Only unambiguous verbs: "holding up" or "up" could mean rising or
# merely resilient, so a claim phrased that way is left unchecked rather than
# forced onto a total it may not mean.
_COUNT_VERBS = {
    "beating": "beating_sp500", "outperforming": "beating_sp500",
    "outpacing": "beating_sp500", "leading": "beating_sp500",
    "trailing": "trailing_sp500", "lagging": "trailing_sp500",
    "underperforming": "trailing_sp500",
    "rising": "rising", "gaining": "rising", "advancing": "rising",
    "climbing": "rising",
    "falling": "falling", "declining": "falling", "dropping": "falling",
    "sliding": "falling",
}
_NUMBER_WORDS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4,
                 "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
                 "ten": 10, "eleven": 11, "twelve": 12}
_COUNT_NUMBER = r"(\d+|" + "|".join(_NUMBER_WORDS) + r")"
# The window after "sectors" stops at a comma or sentence mark, so "2 of the 11
# sectors, while most are falling" is not read as "2 falling".
_COUNT_CLAIM = re.compile(
    rf"\b{_COUNT_NUMBER} of (?:the |all )?{_COUNT_NUMBER}(?:\s+[a-z&-]+){{0,2}}?"
    rf"\s+sectors?\b[^.;:,!?]{{0,30}}?\b({'|'.join(_COUNT_VERBS)})\b")


def _as_count(word):
    return int(word) if word.isdigit() else _NUMBER_WORDS[word]


def _count_claim_error(text, totals):
    """The first "N of M sectors are <verb>" claim that disagrees with the
    packet's ready-made ``totals``, or None (also None when there are no
    totals to check against). Accuracy is paramount: a sentence stating a
    wrong count is withheld, not shown."""
    if not totals:
        return None
    t = " ".join(str(text or "").lower().split())
    for claim in _COUNT_CLAIM.finditer(t):
        n, of = _as_count(claim.group(1)), _as_count(claim.group(2))
        key = _COUNT_VERBS[claim.group(3)]
        if of != totals.get("sectors") or n != totals.get(key):
            return claim.group(0)
    return None


class _Withheld:
    """``generate_summary``'s answer when the readings were answered and the
    answer REFUSED, or there was nothing to state. Kept apart from ``None`` (the
    call itself failed) because the loop treats them differently: a failure is
    retried on the same readings once the gap has passed, a refusal is not. The
    same readings are refused the same way — the Circling fact's semicolon came
    back as a comma in 3 of 3 live replies, and retrying it bought 30 paid calls
    and no sentence between 02:30 and 09:06 CT on 2026-09-11."""

    def __repr__(self):
        return "WITHHELD"


WITHHELD = _Withheld()


def generate_summary(packet, client=None):
    """Call Claude for the 1-2 sentence consolidated read + posture.

    Returns ``{"narrative", "inputs", "as_of"}`` on the no-client path (dev / no
    key) with an empty narrative — never a fabricated one — and on a successful
    call. A finished reply is shown whole, never shortened; one that ran out of
    room keeps only its complete sentences.

    The model is sent ONLY ``summary_facts(packet)`` — statements the code
    wrote from the readings — and may only join them and add the posture.

    Returns ``None`` when the attempt FAILED (API error, timeout): the caller
    keeps the last good sentence and retries the same readings after the gap.

    Returns ``WITHHELD`` — the caller keeps the last good sentence and waits
    for the readings to change — when there are no facts to state (no call is
    made), when a reply that ran out of room has no complete sentence, when the
    reply ties a credit spread to the wrong direction, when it states a sector
    count that disagrees with the packet's ready-made totals, or when it drops
    or rewords any fact (accuracy first: a wrong sentence is worse than a
    slightly older right one)."""
    import json
    from datetime import timezone
    out = {"narrative": "", "inputs": packet or {},
           "as_of": datetime.now(timezone.utc).isoformat()}
    c = client if client is not None else _make_summary_client()
    if c is None:
        return out
    facts = summary_facts(packet)
    if not facts:
        return WITHHELD
    try:
        _count_anthropic_call()
        resp = c.messages.create(
            model=_SUMMARY_MODEL, max_tokens=_SUMMARY_MAX_TOKENS,
            thinking={"type": "disabled"},
            system=_SUMMARY_SYSTEM,
            messages=[{"role": "user",
                       "content": json.dumps({"facts": facts})}])
        text = "".join(getattr(b, "text", "") for b in getattr(resp, "content", []) or [])
        cut_off = getattr(resp, "stop_reason", None) == "max_tokens"
    except Exception:  # noqa: BLE001 — never raise out of a summary attempt.
        log.warning("market summary generation failed", exc_info=True)
        return None
    text = " ".join(text.split()).strip()
    if cut_off:
        log.warning("market summary hit max_tokens (%d) - keeping only its "
                    "complete sentences", _SUMMARY_MAX_TOKENS)
        text = _complete_sentences(text)
        if not text:
            log.warning("market summary withheld - it ran out of room before "
                        "finishing a sentence")
            return WITHHELD
    wrong = _spread_direction_error(text)
    if wrong:
        log.warning("market summary withheld - it ties a credit spread to the "
                    "wrong direction: %r", wrong)
        return WITHHELD
    totals = ((packet or {}).get("bullbear") or {}).get("totals") or {}
    miscount = _count_claim_error(text, totals)
    if miscount:
        log.warning("market summary withheld - a sector count disagrees with "
                    "the totals %s: %r", totals, miscount)
        return WITHHELD
    dropped = _missing_fact(text, facts)
    if dropped:
        log.warning("market summary withheld - it dropped or reworded the "
                    "fact %r", dropped)
        return WITHHELD
    out["narrative"] = text
    return out

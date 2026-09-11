"""Market dashboard compute — fetch raw quotes, build the display payload.

I/O seams (``fetch_raw_quotes`` / ``read_sector_pcr``) are thin + defensive;
``build_dashboard`` is PURE over an already-fetched raw dict + pcr so it carries
the coverage. All defensive — a fetch/parse failure degrades to no-data tiles.
"""
import logging
import math
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
# A cap, not a spend (billing is on generated tokens). Headroom so a sentence is
# never cut mid-word and still rendered as if complete; pinned by a test.
_SUMMARY_MAX_TOKENS = 300
_SUMMARY_MAX_CHARS = 400
# Plain everyday English (2026-09-10, by request). The six chips under the
# sentence already show the labels and the numbers, so the sentence explains
# what they MEAN instead of repeating them; the first draft asked for "the given
# words verbatim" and produced "Composite reads Cautious/Bearish at 3.16 ...".
_SUMMARY_SYSTEM = (
    "You write the one-line market summary on a trading desk, for a trader who "
    "wants it in plain everyday English. You get six readings as JSON, using "
    "the app's own labels: sentiment (a 0-10 composite that is CONTRARIAN - a "
    "high score means investors are fearful, which this model reads as an "
    "opportunity; a low score means they are relaxed or complacent, which it "
    "reads as a warning); trend (a label and a 0-100 score for which way prices "
    "are heading and how much force is behind the move); bias and signal (two "
    "bands of that same sentiment composite, with a position size); regime "
    "(what kind of market it is, with a confidence); and bull/bear (how many of "
    "the 11 S&P sectors are rising or falling and beating or trailing the S&P "
    "500, counted today or over the quarter). Write at most TWO short sentences "
    "(<=350 characters) in plain everyday English that say what these readings "
    "mean. Do not repeat the app's labels or jargon (composite, contrarian, "
    "regime, breadth, tape, bias, signal, or the trend and regime labels "
    "themselves) and quote no scores, decimals or position-size multipliers; "
    "simple counts such as '2 of the 11 sectors' are fine. Treat sentiment, bias "
    "and signal as ONE reading. Say where the readings agree or conflict. Close "
    "with a practical trading posture in plain words; standard options terms "
    "such as 'put credit spreads' are fine when the advice needs them. No "
    "prices, no percent moves, no preamble, no disclaimers, no bullet points, no "
    "markdown."
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
        "bullbear": {"horizon": horizon, "counts": counts},
    }


# Display resolution: a move smaller than these never reaches the reader's eye,
# so it must not buy a new sentence.
FINGERPRINT_COMPOSITE_STEP = 0.5
FINGERPRINT_TREND_STEP = 5.0
FINGERPRINT_CONFIDENCE_STEP = 0.1


def _bucket(v, step):
    return None if v is None else round(round(v / step) * step, 6)


def summary_fingerprint(packet):
    """What the sentence is written from, at display resolution — or None when
    there is nothing to summarize (no composite and no trend word).

    Words compare exactly; numbers to their step; Bull/Bear counts exactly with
    their horizon. Two packets with one fingerprint would get the same sentence."""
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
        tuple(sorted((bb.get("counts") or {}).items())),
    )


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


def generate_summary(packet, client=None):
    """Call Claude for the 1-2 sentence consolidated read + posture.

    Returns ``{"narrative", "inputs", "as_of"}`` on the no-client path (dev / no
    key) with an empty narrative — never a fabricated one — and on a successful
    call. Returns ``None`` when the attempt FAILED (API error, timeout), so the
    caller keeps the last good sentence rather than blanking it."""
    import json
    from datetime import timezone
    out = {"narrative": "", "inputs": packet or {},
           "as_of": datetime.now(timezone.utc).isoformat()}
    c = client if client is not None else _make_summary_client()
    if c is None:
        return out
    try:
        _count_anthropic_call()
        resp = c.messages.create(
            model=_SUMMARY_MODEL, max_tokens=_SUMMARY_MAX_TOKENS,
            thinking={"type": "disabled"},
            system=_SUMMARY_SYSTEM,
            messages=[{"role": "user", "content": json.dumps(packet)}])
        if getattr(resp, "stop_reason", None) == "max_tokens":
            log.warning("market summary hit max_tokens (%d) - the sentence may "
                        "be cut", _SUMMARY_MAX_TOKENS)
        text = "".join(getattr(b, "text", "") for b in getattr(resp, "content", []) or [])
        out["narrative"] = " ".join(text.split()).strip()[:_SUMMARY_MAX_CHARS]
    except Exception:  # noqa: BLE001 — never raise out of a summary attempt.
        log.warning("market summary generation failed", exc_info=True)
        return None
    return out

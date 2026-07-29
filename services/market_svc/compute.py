"""Market dashboard compute — fetch raw quotes, build the display payload.

I/O seams (``fetch_raw_quotes`` / ``read_sector_pcr``) are thin + defensive;
``build_dashboard`` is PURE over an already-fetched raw dict + pcr so it carries
the coverage. All defensive — a fetch/parse failure degrades to no-data tiles.
"""
import logging

import requests

from repo_paths import PROXY_URL
from services.market_svc import classify, symbols

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
_SUMMARY_MAX_TOKENS = 220
_SUMMARY_MAX_CHARS = 400
_SUMMARY_SYSTEM = (
    "You are a terse markets desk analyst. Given a compact JSON snapshot of the "
    "current tape (sentiment, trend, breadth, volatility, index moves, sector/theme "
    "leaders), write ONE or TWO plain sentences (<=350 chars) summarizing the market "
    "read and posture. No preamble, no disclaimers, no bullet points, no markdown — "
    "just the sentences. Lead with the overall condition."
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


def _tiles_by_cat(dashboard):
    out = {}
    for c in (dashboard or {}).get("categories", []):
        out[c.get("category")] = c.get("tiles", [])
    return out


def build_summary_packet(dashboard, sentiment):
    """PURE: compact facts for the summary prompt from the two cache payloads."""
    byc = _tiles_by_cat(dashboard)
    live = (sentiment or {}).get("live") or {}
    der = (sentiment or {}).get("derived") or {}
    comp = live.get("composite") or {}
    trend = der.get("trend") or {}

    def _movers(cats, n=3):
        tiles = [t for c in cats for t in byc.get(c, []) if t.get("change_pct") is not None]
        tiles.sort(key=lambda t: abs(t.get("change_pct") or 0), reverse=True)
        return [{"display": t.get("display"), "change_pct": t.get("change_pct")} for t in tiles[:n]]

    return {
        "sentiment": {"score": comp.get("total_score"), "bias": comp.get("bias")},
        "trend": {"label": trend.get("label"), "score": trend.get("score")},
        "breadth": (live.get("breadth") or {}).get("interpretation"),
        "put_call": live.get("sector_pcr"),
        "vol": [{"display": t.get("display"), "last": t.get("last"), "change_pct": t.get("change_pct")}
                for t in byc.get("Volatility", [])],
        "index": [{"display": t.get("display"), "change_pct": t.get("change_pct")}
                  for t in byc.get("Cash Index", [])],
        "movers": _movers(["Sector SPDR", "Thematic / Industry ETF"], 4),
    }



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

def generate_summary(dashboard, sentiment, client=None):
    """Build the packet, call Claude for a 1-2 sentence verdict. Defensive → {'narrative': ''}."""
    import json
    packet = build_summary_packet(dashboard, sentiment)
    c = client if client is not None else _make_summary_client()
    if c is None:
        return {"narrative": ""}
    try:
        _count_anthropic_call()
        resp = c.messages.create(
            model=_SUMMARY_MODEL, max_tokens=_SUMMARY_MAX_TOKENS,
            thinking={"type": "disabled"},
            system=_SUMMARY_SYSTEM,
            messages=[{"role": "user", "content": json.dumps(packet)}])
        text = ""
        for block in getattr(resp, "content", []) or []:
            if getattr(block, "type", None) == "text" or hasattr(block, "text"):
                text += getattr(block, "text", "")
        text = " ".join(text.split()).strip()[:_SUMMARY_MAX_CHARS]
        return {"narrative": text}
    except Exception:  # noqa: BLE001 — never raise out of a summary attempt.
        log.warning("market summary generation failed", exc_info=True)
        return {"narrative": ""}

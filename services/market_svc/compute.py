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


def read_sector_pcr(bus):
    """Cap-weighted sector put/call ratio from cache:sentiment:composite, or None."""
    try:
        env = bus.cache_get(CACHE_SENTIMENT)
        if not env:
            return None
        live = (env.payload or {}).get("live") or {}
        pcr = live.get("sector_pcr")
        return float(pcr) if pcr not in (None, "") else None
    except Exception:  # noqa: BLE001
        return None


def _leg(raw, sym):
    q = raw.get(sym)
    if not q:
        return None
    return classify.normalize_quote(q)


def _tile_base(entry):
    return {"display": entry["display"], "description": entry["description"],
            "category": entry["category"], "polarity": entry["polarity"],
            "value_only": entry["value_only"]}


def build_dashboard(raw, *, sector_pcr, proxy_up):
    """Assemble the ordered categories→tiles payload (pure)."""
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
        elif e["kind"] == "external":  # sentiment put/call
            if sector_pcr is None:
                t.update(last=None, change=None, change_pct=None, color_state="no_data")
            else:
                dev = sector_pcr - _PCR_BASELINE
                t.update(last=sector_pcr, change=None, change_pct=None,
                         color_state=classify.color_state(
                             dev, polarity=e["polarity"], value_only=True))
        tiles_by_cat[e["category"]].append(t)

    categories = [{"category": c, "tiles": tiles_by_cat[c]}
                  for c in symbols.CATEGORY_ORDER if tiles_by_cat[c]]
    return {"categories": categories, "proxy_up": proxy_up, "errors": []}


def collect(bus):
    """Fetch + build the full dashboard payload (the scheduler's per-tick call)."""
    from services import _proxy
    raw = fetch_raw_quotes(symbols.quote_symbols())
    pcr = read_sector_pcr(bus)
    proxy_up = bool(raw) or bool(_proxy.health().get("up"))
    return build_dashboard(raw, sector_pcr=pcr, proxy_up=proxy_up)

"""Each fund's top holdings, quoted, for the Macro Board's ETF hover card.

Publishes ``cache:market:holdings``. The lists come from
``config/etf_holdings.toml`` (``shared/etf_holdings_config``); the prices are one
proxy ``/quotes`` request for every holding at once, on a cadence of its own
(``rth_refresh_sec`` in the session, ``offhours_refresh_sec`` outside it) so the
board's three-second poll neither grows nor waits on it.

A holding's move is the quote's own percent change from the previous close,
read through ``classify.normalize_quote`` exactly as a tile's is. A holding with
no quote is published with ``None`` price and move, never 0. A holding whose last
trade is older than ``stale_after_h`` keeps its price but has NO move
(``stale: True`` and the trade date): a thin OTC line's percent change is taken
against a close that can be months old, and read +10% for a Samsung line that
had not traded since April. One with no trade on record at all is ``stale``
with no price, no move and no date.
"""
import datetime as _dt
import time
from zoneinfo import ZoneInfo

from services import _degrade
from services.market_svc import classify, compute
from shared import etf_holdings_config as cfg_mod
from shared import market_calendar as mc
from shared.contracts.market import MarketHoldings

CACHE = "cache:market:holdings"
EVENT = "events:market:holdings"

_CT = ZoneInfo("America/Chicago")


def quote_symbols(cfg) -> list[str]:
    """Every holding a card shows, deduplicated in first-seen order."""
    out: list[str] = []
    for syms in cfg["funds"].values():
        for s in syms[:cfg["top_n"]]:
            if s not in out:
                out.append(s)
    return out


def last_trade(q):
    """The quote's last trade time in unix seconds, or None."""
    inner = q.get("quote", q) if isinstance(q, dict) else {}
    try:
        ms = float(inner.get("tradeTime") or 0)
    except (TypeError, ValueError):
        return None
    return ms / 1000.0 if ms > 0 else None


def build(raw, cfg, ts) -> dict:
    """PURE: the published view from a raw ``/quotes`` answer and the settings."""
    funds = {}
    stale_sec = cfg["stale_after_h"] * 3600
    for fund, syms in cfg["funds"].items():
        rows = []
        for s in syms[:cfg["top_n"]]:
            q = raw.get(s) if isinstance(raw, dict) else None
            n = classify.normalize_quote(q) if q else None
            last, pct = (n[0], n[2]) if n else (None, None)
            traded = last_trade(q) if n else None
            # No trade on record at all (an OTC line Schwab quotes but nobody
            # trades): its price and close are both placeholders, so neither
            # is shown. Measured 2026-11-12: FUISF read -73%, RLNIY +9%.
            if n and traded is None:
                last = pct = None
            stale = bool(n) and (traded is None or ts - traded > stale_sec)
            rows.append({"symbol": s, "name": cfg["names"].get(s, ""),
                         "last": last, "change_pct": None if stale else pct,
                         "stale": stale,
                         "traded": (_dt.datetime.fromtimestamp(traded, _CT)
                                    .strftime("%Y-%m-%d") if stale and traded else None)})
        funds[fund] = {"rows": rows, "note": cfg["notes"].get(fund, "")}
    return {"enabled": True, "ts": int(ts), "as_of": cfg["as_of"], "funds": funds}


def interval(cfg, now) -> int:
    return cfg["rth_refresh_sec"] if mc.is_regular_hours(now) else cfg["offhours_refresh_sec"]


def publish(bus, payload) -> int:
    """Validate against MarketHoldings and cache+publish. Returns the version.

    No ``skip_unchanged``: the payload carries its own ``ts``."""
    return bus.cache_set(CACHE, MarketHoldings(**payload).model_dump(), event=EVENT)


def refresh(bus, state, now=None, wall=None):
    """Fetch and publish when due; return True when it published. Never raises.

    ``state`` is the caller's dict and survives between calls. Switched off, the
    view is published once as ``enabled: False`` with no funds, so the page drops
    its cards instead of showing the last prices forever. A fetch that returned
    nothing publishes nothing: the last view stays and its ``ts`` ages."""
    now = now or _dt.datetime.now(_CT)
    wall = time.time() if wall is None else wall
    try:
        cfg = cfg_mod.load()
        if not cfg["enabled"]:
            if state.get("enabled", True):
                publish(bus, {"enabled": False, "ts": int(wall), "as_of": "", "funds": {}})
                state["enabled"] = False
            return False
        if state.get("enabled", True) and wall < state.get("next_at", 0):
            return False
        state["next_at"] = wall + interval(cfg, now)
        syms = quote_symbols(cfg)
        raw = compute.fetch_raw_quotes(syms) if syms else {}
        if syms and not raw:
            return False
        publish(bus, build(raw, cfg, wall))
        state["enabled"] = True
        return True
    except Exception:
        _degrade.degraded("market.holdings")
        return False

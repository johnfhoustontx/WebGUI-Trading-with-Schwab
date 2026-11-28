"""New highs / new lows across US-listed stocks, for the Macro Board's top bar.

Publishes ``cache:market:new_highs``: how many stocks MADE a new 52-week,
13-week and 4-week high and low TODAY, across the common stocks priced at or
above ``min_price`` with their main listing on the exchanges in
``config/new_highs.toml`` (3,426 at $5 and up on 2026-11-27).

**Made today** (the operator's definition, 2026-11-27): the stock traded today
and today's high is ABOVE the window's highest high as of the day before (a new
low: today's low BELOW the window's lowest low as of the day before). Measured on
the live screener that day, the window fields themselves (``High.1M`` ...)
include today: no stock's ``high`` is above its own ``High.1M``. "At or above"
them therefore also counted a stock that only TIED its old high (14 of the 104
52-week ones, mostly SPACs pinned at a price), and a stock that did not trade
today at all, whose ``high`` is still its last session's (28 more; ``time``, the
daily bar's open, said Wednesday). The rule compares with the ``[1]`` fields
(the window as of the previous bar), strictly, and requires ``time`` to be on
or after today's date.

The counts come from TradingView's screener, not Schwab: Schwab's API has no
scanner, and counting 5,000 stocks ourselves would cost the proxy a pull per
stock. The screener COUNTS on its own side: each request carries a field-to-
field filter (``high`` above ``price_52_week_high[1]``) and a two-row range,
and only its ``totalCount`` is read. One refresh is seven small requests.
⚠ The address is unofficial (``config/new_highs.toml`` says why that is
accepted); a refresh that loses any one count publishes nothing, so the board
keeps the last full set and greys it by age, never a half-fresh one.

⚠ TradingView has no week fields: 13 wk is its 3-month window and 4 wk its
1-month window (``WINDOWS``). The windows live here, not in config, because
changing one means changing the labels and the help that explain it.

**The lists behind the counts** (``cache:market:new_highs_detail``, 2026-11-27):
each window request also asks for its matching stocks (``LIST_COLUMNS``, up to
``list_max`` rows), so the board can show the sectors behind a count on hover and
a sector's stocks on a click without a request of its own. The lists are a view
of their own because they run to hundreds of KB on a broad day and the board's
2-second poll reads only the counts; they are written BEFORE the counts, so a
page that sees new counts finds their lists already there. ``list_max = 0``
asks for counts alone and publishes the lists switched off.

⚠ **The sectors are GICS, mapped here.** TradingView's own ``sector`` field is
its 21 industry groups (Finance, Technology Services, ...), not the 11 GICS
sectors, and it has no GICS field. So the lists ask for each stock's
``industry`` (127 of them) and map it through ``config/new_highs.toml [gics]``;
an industry the map lacks is ``NO_SECTOR``.

This module imports nothing from ``compute``: it calls neither the proxy nor
Schwab.

Cadence (``due``): every ``rth_refresh_sec`` from the regular open to the close
(the EARLY close on a half day, through ``session_close_on``: ``is_regular_hours``
still says 15:00 then), one settling refresh ``after_close_min`` after the close,
and nothing before the open or on a closed day. The requests go one after another,
never in parallel: an unofficial address must not see bursts.
"""
import datetime as _dt
import threading
from zoneinfo import ZoneInfo

import requests

from services import _degrade
from shared import http_watchdog as _wd
from shared import market_calendar as mc
from shared import new_highs_config as cfg_mod
from shared.numeric import finite

CACHE = "cache:market:new_highs"
EVENT = "events:market:new_highs"
DETAIL = "cache:market:new_highs_detail"
DETAIL_EVENT = "events:market:new_highs_detail"

SOURCE = "TradingView"
_CT = ZoneInfo("America/Chicago")

# (key, label, the window's high field, the window's low field), in board order.
# ``[1]`` is the field's value one daily bar back: the window as it stood before
# today, which today's high must rise above to be a new one.
WINDOWS = (
    ("52w", "52 wk", "price_52_week_high[1]", "price_52_week_low[1]"),
    ("13w", "13 wk", "High.3M[1]", "Low.3M[1]"),
    ("4w", "4 wk", "High.1M[1]", "Low.1M[1]"),
)
SIDES = ("highs", "lows")
UNIVERSE = "universe"
# What each window request lists per stock, in this order (``parse_rows``).
LIST_COLUMNS = ("name", "description", "industry", "close", "change", "high", "low")
# A stock whose industry the GICS map does not name (or that has none).
NO_SECTOR = "Unclassified"


def _universe(cfg) -> list[dict]:
    """The stocks counted: common, main listing, the configured exchanges, and
    priced at or above ``min_price`` (0: every price)."""
    out = [
        {"left": "type", "operation": "equal", "right": "stock"},
        {"left": "is_primary", "operation": "equal", "right": True},
        {"left": "exchange", "operation": "in_range", "right": list(cfg["exchanges"])},
    ]
    if cfg["min_price"] > 0:
        out.append({"left": "close", "operation": "egreater", "right": cfg["min_price"]})
    return out


def _traded_on(day) -> dict:
    """The stock's latest daily bar is ``day``'s: ``time`` (the bar's open, unix
    seconds) at or after that date's midnight Central. A stock that has not
    traded today still carries its last session's high and low."""
    start = _dt.datetime.combine(day, _dt.time(0), tzinfo=_CT)
    return {"left": "time", "operation": "egreater", "right": int(start.timestamp())}


def _body(filters, list_max=0) -> dict:
    """A request for the count alone, or (``list_max`` > 0) the count and up to
    that many rows, sorted by name so an unchanged list reads the same."""
    body = {"markets": ["america"], "symbols": {"query": {"types": []}},
            "filter": filters, "columns": ["name"], "range": [0, 1]}
    if list_max > 0:
        body.update(columns=list(LIST_COLUMNS), range=[0, list_max],
                    sort={"sortBy": "name", "sortOrder": "asc"})
    return body


def _side_filter(side, high_field, low_field) -> dict:
    # Strictly: a high that only ties the old one is not a new high.
    if side == "highs":
        return {"left": "high", "operation": "greater", "right": high_field}
    return {"left": "low", "operation": "less", "right": low_field}


def requests_for(cfg, day) -> list[tuple[str, dict]]:
    """PURE: every request one refresh on trading date ``day`` makes, as
    ``(key, body)``, in the order they are sent. The universe count first, then
    ``window.side``."""
    base = _universe(cfg)
    today = _traded_on(day)
    out = [(UNIVERSE, _body(list(base)))]
    for key, _label, high_field, low_field in WINDOWS:
        for side in SIDES:
            out.append((f"{key}.{side}",
                        _body(base + [today, _side_filter(side, high_field, low_field)],
                              cfg["list_max"])))
    return out


def build(counts, cfg, session) -> dict | None:
    """PURE: the published view, or None unless EVERY count is present.

    ``counts`` maps each ``requests_for`` key to its ``totalCount`` (None for a
    request that failed). All or nothing: a window must never pair a fresh high
    with a stale low."""
    keys = [UNIVERSE] + [f"{key}.{side}" for key, *_ in WINDOWS for side in SIDES]
    if any(counts.get(k) is None for k in keys):
        return None
    by_window = {key: {side: counts[f"{key}.{side}"] for side in SIDES}
                 for key, *_ in WINDOWS}
    return {"enabled": True, "source": SOURCE, "universe": counts[UNIVERSE],
            "session": session, "min_price": cfg["min_price"],
            "exchanges": list(cfg["exchanges"]), "public": cfg["public_show"],
            "stale_after_sec": stale_after_sec(cfg), "counts": by_window}


def parse_rows(data, gics) -> list[dict]:
    """PURE: the screener's ``data`` rows as stocks. A row without a symbol or
    with too few values is skipped; a number that is not finite is None. The
    ``sector`` is the GICS sector ``gics`` (industry -> sector) names for the
    stock's ``industry``, ``NO_SECTOR`` for an industry it lacks or none."""
    out = []
    for row in data if isinstance(data, list) else []:
        d = row.get("d") if isinstance(row, dict) else None
        if not isinstance(d, list) or len(d) < len(LIST_COLUMNS):
            continue
        sym, name, industry, close, change, high, low = d[:len(LIST_COLUMNS)]
        if not isinstance(sym, str) or not sym:
            continue
        industry = industry if isinstance(industry, str) else ""
        out.append({"symbol": sym, "name": name if isinstance(name, str) else "",
                    "sector": gics.get(industry) or NO_SECTOR, "industry": industry,
                    "price": finite(close), "change_pct": finite(change),
                    "high": finite(high), "low": finite(low)})
    return out


def _sector_groups(stocks, side) -> list[dict]:
    """The stocks by sector, the most stocks first (ties by name). Within a
    sector, highs show the biggest gain first and lows the biggest fall; a stock
    with no change goes last. ``level`` is the day's high or low reached."""
    groups: dict[str, list[dict]] = {}
    for st in stocks:
        groups.setdefault(st["sector"], []).append(
            {"symbol": st["symbol"], "name": st["name"], "industry": st["industry"],
             "price": st["price"], "change_pct": st["change_pct"],
             "level": st["high"] if side == "highs" else st["low"]})
    sign = -1 if side == "highs" else 1
    for rows in groups.values():
        rows.sort(key=lambda r: (r["change_pct"] is None,
                                 sign * (r["change_pct"] or 0), r["symbol"]))
    return [{"sector": name, "count": len(rows), "stocks": rows}
            for name, rows in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))]


def build_detail(lists, counts, cfg, session) -> dict:
    """PURE: the lists view - per window and side the ``total`` (the count the
    board shows), how many stocks were ``listed`` (fewer than the total only
    past ``list_max``), and their ``sectors``. ``lists`` maps a ``window.side``
    key to its parsed stocks; a key it lacks lists none."""
    out: dict = {}
    for key, *_ in WINDOWS:
        for side in SIDES:
            stocks = lists.get(f"{key}.{side}") or []
            out.setdefault(key, {})[side] = {
                "total": counts[f"{key}.{side}"], "listed": len(stocks),
                "sectors": _sector_groups(stocks, side)}
    return {"enabled": True, "session": session, "public": cfg["public_show"],
            "lists": out}


def publish_detail(bus, payload) -> int:
    """Validate against MarketNewHighsDetail and cache+publish (``skip_unchanged``:
    no timestamp in it, so an unchanged list bumps no version)."""
    from shared.contracts.market import MarketNewHighsDetail
    return bus.cache_set(DETAIL, MarketNewHighsDetail(**payload).model_dump(),
                         event=DETAIL_EVENT, skip_unchanged=True)


def stale_after_sec(cfg) -> int:
    """How old the counts may get in the session before the board greys them:
    one refresh interval plus a failed refresh and its retry. Carried in the view
    because Tier 1 does not read this service's config."""
    return cfg["rth_refresh_sec"] + 2 * cfg["retry_sec"]


def publish(bus, payload) -> int:
    """Validate against MarketNewHighs and cache+publish. Returns the version.

    ``skip_unchanged``: the view carries no timestamp, so a refresh that finds the
    same counts only refreshes ``:ts`` and bumps no version."""
    from shared.contracts.market import MarketNewHighs
    return bus.cache_set(CACHE, MarketNewHighs(**payload).model_dump(), event=EVENT,
                         skip_unchanged=True)


# ── fetching ─────────────────────────────────────────────────────────────────
# A plain browser's name: nothing about the operator goes to TradingView.
_USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64)"


def _post(url, **kwargs):
    """``requests.post`` through the watched adapter - the seam tests replace."""
    return _wd.post(url, **kwargs)


def fetch(body, cfg) -> tuple[int | None, str]:
    """One screener request: ``(totalCount, "")``, or ``(None, why)``: see
    ``_ask``."""
    data, why = _ask(body, cfg)
    return (data["totalCount"], "") if data is not None else (None, why)


def fetch_rows(body, cfg) -> tuple[int | None, list | None, str]:
    """One screener request with its rows: ``(totalCount, rows, "")``, or
    ``(None, None, why)``. A count above 0 without a list of rows is a failure
    (``no rows``): the board must never show a count whose list is missing."""
    data, why = _ask(body, cfg)
    if data is None:
        return None, None, why
    rows = data.get("data")
    if rows is None and data["totalCount"] == 0:
        rows = []
    if not isinstance(rows, list):
        return None, None, "no rows"
    return data["totalCount"], rows, ""


def _ask(body, cfg) -> tuple[dict | None, str]:
    """One screener request: ``(answer, "")`` with a valid ``totalCount``, or
    ``(None, why)`` for ANY
    failure - an exception, the deadline, a status that is not 200, a body that
    is not JSON, an ``error`` in the answer (even beside a count), or a count
    that is not a whole number of 0 or more. Never raises.

    ``why`` is short and carries nothing secret: it goes into the degrade's
    detail, so the Status page says why a refresh failed, not only which
    request did."""
    timeout = cfg["timeout_sec"]
    watch = _wd.Watch()
    timer = threading.Timer(timeout * _wd.DEADLINE_FACTOR, watch.fire)
    timer.name = "new-highs-deadline"
    timer.daemon = True
    _wd.ACTIVE.watch = watch
    timer.start()
    try:
        try:
            r = _post(cfg["url"], json=body, timeout=timeout,
                      headers={"User-Agent": _USER_AGENT})
        finally:
            _wd.ACTIVE.watch = None
        if watch.expired:
            return None, f"deadline ({timeout * _wd.DEADLINE_FACTOR:g} s)"
        if r.status_code != 200:
            return None, f"status {r.status_code}"
        data = r.json()
    except requests.RequestException as exc:
        return None, f"{type(exc).__name__}: {str(exc)[:120]}"
    except ValueError:
        return None, "not JSON"
    finally:
        timer.cancel()
        watch.close()           # the duplicated descriptors go with the request
    if not isinstance(data, dict):
        return None, "not an object"
    if data.get("error"):
        return None, f"error: {str(data['error'])[:120]}"
    n = data.get("totalCount")
    if isinstance(n, bool) or not isinstance(n, int) or n < 0:
        return None, "no count"
    return data, ""


def fetch_count(body, cfg) -> int | None:
    """``fetch`` without its reason: the count, or None."""
    return fetch(body, cfg)[0]


# ── when ─────────────────────────────────────────────────────────────────────
def due(now, state, cfg) -> bool:
    """PURE: whether a refresh is due at ``now`` (Central).

    ``state["next_at"]`` (unix seconds) holds the next refresh back, after a
    refresh and after a failed one; ``state["after_close_done"]`` is the date
    whose settling refresh has published."""
    d = now.date()
    if not mc.is_trading_day(d):
        return False
    if now.timestamp() < state.get("next_at", 0):
        return False
    close = mc.session_close_on(d)
    if mc.regular_open_on(d) <= now < close:
        return True
    settle = close + _dt.timedelta(minutes=cfg["after_close_min"])
    return now >= settle and state.get("after_close_done") != d.isoformat()


# ── the refresh ──────────────────────────────────────────────────────────────
def refresh(bus, state, now=None) -> bool:
    """Fetch and publish when due; return True when it published. Never raises.

    ``state`` is the caller's dict and survives between calls. Switched off, the
    view is published once as ``enabled: False`` so the board drops the counts;
    switched back on, the next call refreshes when one is due and, until then,
    puts the last counts back. A switch changed while no refresh is due reaches
    the view at once (``_sync``): ``[public] show`` must not wait for Monday's
    open. A refresh that lost any count publishes nothing and is tried again
    after ``retry_sec``."""
    now = now or _dt.datetime.now(_CT)
    try:
        cfg = cfg_mod.load()
        if not cfg["enabled"]:
            if state.get("enabled", True):
                publish_detail(bus, {"enabled": False})
                _publish(bus, state, {"enabled": False})
                state["enabled"] = False
            return False
        if state.get("enabled") is False:
            state.pop("next_at", None)             # back on: refresh when due
        state["enabled"] = True
        if not due(now, state, cfg):
            return _sync(bus, state, cfg)
        counts, lists, why = {}, {}, ""
        listing = cfg["list_max"] > 0
        for key, body in requests_for(cfg, now.date()):
            if listing and key != UNIVERSE:
                counts[key], rows, why = fetch_rows(body, cfg)
                lists[key] = parse_rows(rows, cfg["gics"])
            else:
                counts[key], why = fetch(body, cfg)
            if counts[key] is None:
                break                              # all or nothing: stop asking
        view = build(counts, cfg, now.date().isoformat())
        if view is None:
            state["next_at"] = now.timestamp() + cfg["retry_sec"]
            _degrade.degraded("market.new_highs", detail=f"no answer for {key}: {why}",
                              exc_info=False)
            return False
        # The lists first: a page that sees new counts finds their lists there.
        publish_detail(bus, build_detail(lists, counts, cfg, view["session"])
                       if listing else {"enabled": False})
        _publish(bus, state, view)
        state["last_view"] = view
        state["next_at"] = now.timestamp() + cfg["rth_refresh_sec"]
        if now >= mc.session_close_on(now.date()):
            state["after_close_done"] = now.date().isoformat()
        return True
    except Exception:
        _degrade.degraded("market.new_highs")
        state["next_at"] = now.timestamp() + cfg_mod.DEFAULTS["retry_sec"]
        return False


def _publish(bus, state, payload) -> None:
    publish(bus, payload)
    state["published"] = payload


def _sync(bus, state, cfg) -> bool:
    """Between refreshes: put the last counts back with this config's public
    switch and stale age, when that differs from what is published. Never
    fetches: a fetch outside the session would date Friday's counts Saturday.
    The last counts are this process's, or after a restart the stored view - but
    only one in today's shape: a view from before the made-today rule (``rows``,
    no ``counts``) meant something else and waits for the next refresh."""
    last = state.get("last_view")
    if last is None:
        env = bus.cache_get(CACHE)
        stored = env.payload if env else None
        if not (isinstance(stored, dict) and stored.get("enabled") is not False
                and stored.get("counts")):
            return False
        last = stored
    want = {**last, "public": cfg["public_show"], "stale_after_sec": stale_after_sec(cfg)}
    if state.get("published") == want:
        return False
    _publish(bus, state, want)
    state["last_view"] = want
    return True

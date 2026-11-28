"""New highs / new lows across US-listed stocks, for the Macro Board's top bar.

Publishes ``cache:market:new_highs``: how many stocks are at a new 52-week,
13-week and 4-week high and low, across every common stock with its main listing
on the exchanges in ``config/new_highs.toml`` (4,984 on 2026-11-27), and again
for those priced at or above ``min_price``.

The counts come from TradingView's screener, not Schwab: Schwab's API has no
scanner, and counting 5,000 stocks ourselves would cost the proxy a pull per
stock. The screener COUNTS on its own side: each request carries a field-to-
field filter (``high`` at or above ``price_52_week_high``) and a two-row range,
and only its ``totalCount`` is read. One refresh is thirteen small requests.
⚠ The address is unofficial (``config/new_highs.toml`` says why that is
accepted); a refresh that loses any one count publishes nothing, so the board
keeps the last full set and greys it by age, never a half-fresh one.

⚠ TradingView has no week fields: 13 wk is its 3-month window and 4 wk its
1-month window (``WINDOWS``). The windows live here, not in config, because
changing one means changing the labels and the help that explain it.

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

CACHE = "cache:market:new_highs"
EVENT = "events:market:new_highs"

SOURCE = "TradingView"

# (key, label, the window's high field, the window's low field), in board order.
WINDOWS = (
    ("52w", "52 wk", "price_52_week_high", "price_52_week_low"),
    ("13w", "13 wk", "High.3M", "Low.3M"),
    ("4w", "4 wk", "High.1M", "Low.1M"),
)
SIDES = ("highs", "lows")
ALL, OVER = "all", "over5"
UNIVERSE = "universe"


def _universe(cfg) -> list[dict]:
    return [
        {"left": "type", "operation": "equal", "right": "stock"},
        {"left": "is_primary", "operation": "equal", "right": True},
        {"left": "exchange", "operation": "in_range", "right": list(cfg["exchanges"])},
    ]


def _body(filters) -> dict:
    return {"markets": ["america"], "symbols": {"query": {"types": []}},
            "filter": filters, "columns": ["name"], "range": [0, 1]}


def _rows(cfg) -> tuple[str, ...]:
    return (ALL, OVER) if cfg["min_price"] > 0 else (ALL,)


def _side_filter(side, high_field, low_field) -> dict:
    if side == "highs":
        return {"left": "high", "operation": "egreater", "right": high_field}
    return {"left": "low", "operation": "eless", "right": low_field}


def requests_for(cfg) -> list[tuple[str, dict]]:
    """PURE: every request one refresh makes, as ``(key, body)``, in the order
    they are sent. The universe count first, then ``row.window.side``."""
    base = _universe(cfg)
    out = [(UNIVERSE, _body(list(base)))]
    for row in _rows(cfg):
        price = ([{"left": "close", "operation": "egreater", "right": cfg["min_price"]}]
                 if row == OVER else [])
        for key, _label, high_field, low_field in WINDOWS:
            for side in SIDES:
                out.append((f"{row}.{key}.{side}",
                            _body(base + price + [_side_filter(side, high_field, low_field)])))
    return out


def build(counts, cfg, session) -> dict | None:
    """PURE: the published view, or None unless EVERY count is present.

    ``counts`` maps each ``requests_for`` key to its ``totalCount`` (None for a
    request that failed). All or nothing: a window must never pair a fresh high
    with a stale low."""
    keys = [k for k, _ in requests_for(cfg)]
    if any(counts.get(k) is None for k in keys):
        return None
    rows = {row: {key: {side: counts[f"{row}.{key}.{side}"] for side in SIDES}
                  for key, *_ in WINDOWS}
            for row in _rows(cfg)}
    return {"enabled": True, "source": SOURCE, "universe": counts[UNIVERSE],
            "session": session, "min_price": cfg["min_price"],
            "exchanges": list(cfg["exchanges"]), "public": cfg["public_show"],
            "stale_after_sec": stale_after_sec(cfg), "rows": rows}


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
_CT = ZoneInfo("America/Chicago")
# A plain browser's name: nothing about the operator goes to TradingView.
_USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64)"


def _post(url, **kwargs):
    """``requests.post`` through the watched adapter - the seam tests replace."""
    return _wd.post(url, **kwargs)


def fetch(body, cfg) -> tuple[int | None, str]:
    """One screener request: ``(totalCount, "")``, or ``(None, why)`` for ANY
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
    return n, ""


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
                _publish(bus, state, {"enabled": False})
                state["enabled"] = False
            return False
        if state.get("enabled") is False:
            state.pop("next_at", None)             # back on: refresh when due
        state["enabled"] = True
        if not due(now, state, cfg):
            return _sync(bus, state, cfg)
        counts, why = {}, ""
        for key, body in requests_for(cfg):
            counts[key], why = fetch(body, cfg)
            if counts[key] is None:
                break                              # all or nothing: stop asking
        view = build(counts, cfg, now.date().isoformat())
        if view is None:
            state["next_at"] = now.timestamp() + cfg["retry_sec"]
            _degrade.degraded("market.new_highs", detail=f"no answer for {key}: {why}",
                              exc_info=False)
            return False
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
    The last counts are this process's, or after a restart the stored view."""
    last = state.get("last_view")
    if last is None:
        env = bus.cache_get(CACHE)
        stored = env.payload if env else None
        if not (isinstance(stored, dict) and stored.get("enabled") is not False
                and stored.get("rows")):
            return False
        last = stored
    want = {**last, "public": cfg["public_show"], "stale_after_sec": stale_after_sec(cfg)}
    if state.get("published") == want:
        return False
    _publish(bus, state, want)
    state["last_view"] = want
    return True

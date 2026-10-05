"""The Desk's Market read: six readings, each a tailwind, a headwind or neutral
for stocks, decided by rule from views the services already publish. PURE.

No Schwab call, no Claude call, no I/O: plain dicts in, a plain dict out. Each
row restates a reading that is already on a page (the Market Dashboard, the
Desk's dealer panel, Flow Alerts); this module only says which way it points and
keeps the previous reading beside it.

The verdict is ABSOLUTE ("for stocks"), not relative to the day's move: a
reading that "supports the move" would be green on a day stocks fall.

Absence is not a reading. A missing tile, a non-finite number, a view from
another day: the row is ``none`` ("No reading"), never ``neutral``.
Design: docs/plans/2026-10-05-market-read-scorecard-design.md.
"""
import datetime as _dt

from shared import market_calendar as _mc
from shared.numeric import finite

from services import _degrade
from services.market_svc.symbols import BREADTH_CATEGORIES

TAILWIND, HEADWIND, NEUTRAL, NONE = "tailwind", "headwind", "neutral", "none"
VERDICTS = (TAILWIND, HEADWIND, NEUTRAL, NONE)

# The rows, in the order the panel draws them.
ROW_KEYS = ("direction", "breadth", "structure", "volatility", "flow", "cross_asset")

# The Market Dashboard tiles each row reads, by the tile's display name.
INDEX_TILES = ("SPX", "NDX")
VIX_TILES = ("VIX", "VIX1D", "VIX3M")
CROSS_ASSET_TILES = ("TLT", "$DXY", "HYG")

# The alert kinds that name one contract (options_svc.flow_sides_tick.CONTRACT_TYPES).
_CONTRACT_ALERTS = ("uoa", "big_delta")


def _row(key, verdict, facts, **extra) -> dict:
    return {"key": key, "verdict": verdict, "facts": facts, **extra}


def tiles_by_name(dashboard) -> dict:
    """``{display name: tile}`` over every frame of the dashboard payload.
    Total over a missing or malformed payload."""
    out = {}
    cats = dashboard.get("categories") if isinstance(dashboard, dict) else None
    for cat in cats if isinstance(cats, list) else ():
        tiles = cat.get("tiles") if isinstance(cat, dict) else None
        for t in tiles if isinstance(tiles, list) else ():
            if isinstance(t, dict) and t.get("display"):
                out.setdefault(t["display"], t)
    return out


def _pct(tiles, name):
    return finite((tiles.get(name) or {}).get("change_pct"))


def _last(tiles, name):
    return finite((tiles.get(name) or {}).get("last"))


# ── the six rows ─────────────────────────────────────────────────────────────
def direction(dashboard, cfg) -> dict:
    """$SPX and $NDX on the day: both up, both down, or neither."""
    tiles = tiles_by_name(dashboard)
    spx, ndx = _pct(tiles, "SPX"), _pct(tiles, "NDX")
    facts = {"spx_pct": spx, "ndx_pct": ndx}
    if spx is None or ndx is None:
        return _row("direction", NONE, facts)
    move = cfg["direction"]["move_pct"]
    if spx >= move and ndx >= move:
        return _row("direction", TAILWIND, facts)
    if spx <= -move and ndx <= -move:
        return _row("direction", HEADWIND, facts)
    return _row("direction", NEUTRAL, facts)


def breadth(dashboard, cfg) -> dict:
    """Advancers and decliners across the equity frames: the Macro Board's own
    count. Direction is the tile's polarity-aware ``color_state``, and a basket
    tile (the average of the names beside it) is skipped."""
    adv = dec = 0
    cats = dashboard.get("categories") if isinstance(dashboard, dict) else None
    for cat in cats if isinstance(cats, list) else ():
        if not isinstance(cat, dict) or cat.get("category") not in BREADTH_CATEGORIES:
            continue
        tiles = cat.get("tiles")
        for t in tiles if isinstance(tiles, list) else ():
            if not isinstance(t, dict) or t.get("basket"):
                continue
            state = t.get("color_state") or ""
            if state.startswith("risk_on"):
                adv += 1
            elif state.startswith("risk_off"):
                dec += 1
    if adv + dec == 0:
        return _row("breadth", NONE, {"advancing": 0, "declining": 0, "share": None})
    share = adv / (adv + dec)
    facts = {"advancing": adv, "declining": dec, "share": share}
    if share >= cfg["breadth"]["strong_share"]:
        return _row("breadth", TAILWIND, facts)
    if share <= cfg["breadth"]["weak_share"]:
        return _row("breadth", HEADWIND, facts)
    return _row("breadth", NEUTRAL, facts)


def _structure_of(row, cfg) -> dict:
    """One symbol's reading from its Opportunity Board row."""
    sym = row.get("symbol")
    spot, flip = finite(row.get("spot")), finite(row.get("flip"))
    ceiling, net_gex = finite(row.get("call_wall")), finite(row.get("net_gex"))
    regime = row.get("gex_regime")
    out = {"symbol": sym, "spot": spot, "flip": flip, "ceiling": ceiling,
           "room_pct": None, "mode": None, "state": NONE}
    if spot is None or spot <= 0 or flip is None or regime not in ("above", "below"):
        return out
    # A net gamma of exactly zero is the after-hours artefact (index open
    # interest reads 0): the walls picked out of an all-zero grid are noise. The
    # dealer panel hides them for the same reason (pages/structure.py).
    if net_gex is not None and net_gex == 0.0:
        return out
    if regime == "below":
        # Below the flip dealers are short gamma and feed a move.
        out.update(mode="short", state=HEADWIND)
        return out
    out["mode"] = "long"
    if ceiling is None:
        return out
    room = (ceiling - spot) / spot * 100.0
    out["room_pct"] = room
    # At or through the ceiling counts as at it: room is zero or negative.
    if room <= cfg["structure"]["near_pct"]:
        out["state"] = HEADWIND
    elif room >= cfg["structure"]["room_pct"]:
        out["state"] = TAILWIND
    else:
        out["state"] = NEUTRAL
    return out


def structure(matrix, cfg) -> dict:
    """Price against the dealer gamma flip and the ceiling, for each configured
    symbol. They must AGREE for the row to lean; one of them without a reading
    leaves the row without one."""
    rows = matrix.get("rows") if isinstance(matrix, dict) else None
    by_symbol = {}
    for r in rows if isinstance(rows, list) else ():
        if isinstance(r, dict) and r.get("symbol") not in by_symbol:
            by_symbol[r.get("symbol")] = r
    symbols = [_structure_of(by_symbol.get(s) or {"symbol": s}, cfg)
               for s in cfg["structure"]["symbols"]]
    for s, name in zip(symbols, cfg["structure"]["symbols"]):
        s["symbol"] = name
    states = {s["state"] for s in symbols}
    facts = {"symbols": symbols}
    if not symbols or NONE in states:
        return _row("structure", NONE, facts)
    if len(states) == 1:
        return _row("structure", states.pop(), facts)
    return _row("structure", NEUTRAL, facts)


def volatility(dashboard, cfg) -> dict:
    """The VIX against its own day, its one-day and its three-month versions."""
    tiles = tiles_by_name(dashboard)
    vix, vix_pct = _last(tiles, "VIX"), _pct(tiles, "VIX")
    vix1d, vix3m = _last(tiles, "VIX1D"), _last(tiles, "VIX3M")
    spx = _pct(tiles, "SPX")
    facts = {"vix": vix, "vix_pct": vix_pct, "vix1d": vix1d, "vix3m": vix3m,
             "spx_pct": spx}
    if vix is None or vix_pct is None:
        return _row("volatility", NONE, facts)
    move = cfg["volatility"]["vix_move_pct"]
    # Fear rising while stocks rise, or the nearest day priced above the month.
    if (vix_pct >= move and spx is not None and spx > 0) or (
            vix1d is not None and vix1d > vix):
        return _row("volatility", HEADWIND, facts)
    if vix_pct <= -move and vix3m is not None and vix < vix3m:
        return _row("volatility", TAILWIND, facts)
    return _row("volatility", NEUTRAL, facts)


def _lean(bought, sold, total):
    return None if total <= 0 else (bought - sold) / total * 100.0


def flow(sides, alerts, cfg) -> dict:
    """The bought / sold ESTIMATE pooled by volume over today's flagged
    contracts, calls and puts apart. ``sides`` is ``cache:options:flow_sides``;
    ``alerts`` is ``cache:options:flow_alerts``, which says which side each
    contract is on. A contract flagged by two alerts is counted once."""
    facts = {"call_lean": None, "put_lean": None, "calls": 0, "puts": 0,
             "contracts": 0}
    contracts = sides.get("contracts") if isinstance(sides, dict) else None
    listed = alerts.get("alerts") if isinstance(alerts, dict) else None
    if (not isinstance(contracts, dict) or not isinstance(listed, list)
            or sides.get("date") != alerts.get("date")):
        return _row("flow", NONE, facts, estimate=True)
    pooled = {"call": [0.0, 0.0, 0.0], "put": [0.0, 0.0, 0.0]}
    counted = {"call": 0, "put": 0}
    seen = set()
    for a in listed:
        if not isinstance(a, dict) or a.get("type") not in _CONTRACT_ALERTS:
            continue
        side, osi = a.get("side"), a.get("osi") or a.get("id")
        entry = contracts.get(a.get("id"))
        if side not in pooled or osi in seen or not isinstance(entry, dict):
            continue
        poll = entry.get("poll")
        if not isinstance(poll, dict):
            continue
        b, s, u = (finite(poll.get(k)) for k in ("bought", "sold", "unlabelled"))
        if b is None or s is None or u is None or min(b, s, u) < 0 or b + s + u <= 0:
            continue
        seen.add(osi)
        counted[side] += 1
        pooled[side][0] += b
        pooled[side][1] += s
        pooled[side][2] += b + s + u
    facts.update(calls=counted["call"], puts=counted["put"],
                 contracts=counted["call"] + counted["put"],
                 call_lean=_lean(*pooled["call"]), put_lean=_lean(*pooled["put"]))
    if facts["contracts"] < cfg["flow"]["min_contracts"]:
        return _row("flow", NONE, facts, estimate=True)
    need = cfg["flow"]["lean_pts"]
    calls_bought = facts["call_lean"] is not None and facts["call_lean"] >= need
    puts_bought = facts["put_lean"] is not None and facts["put_lean"] >= need
    if calls_bought and not puts_bought:
        return _row("flow", TAILWIND, facts, estimate=True)
    if puts_bought and not calls_bought:
        return _row("flow", HEADWIND, facts, estimate=True)
    return _row("flow", NEUTRAL, facts, estimate=True)


def cross_asset(dashboard, cfg) -> dict:
    """Long Treasuries, the dollar and high-yield credit, by the board's OWN
    risk-on / risk-off colour. Whether a falling Treasury fund helps or hurts
    stocks is decided once, in ``classify.color_state``; this counts colours."""
    tiles = tiles_by_name(dashboard)
    seen, on, off = [], 0, 0
    for name in CROSS_ASSET_TILES:
        t = tiles.get(name) or {}
        state = t.get("color_state") or ""
        word = ("on" if state.startswith("risk_on") else
                "off" if state.startswith("risk_off") else
                "flat" if state == "flat" else None)
        seen.append({"name": name, "pct": finite(t.get("change_pct")), "state": word})
        on += word == "on"
        off += word == "off"
    facts = {"tiles": seen, "risk_on": on, "risk_off": off}
    if sum(1 for t in seen if t["state"] is not None) < 2:
        return _row("cross_asset", NONE, facts)
    if on >= 2:
        return _row("cross_asset", TAILWIND, facts)
    if off >= 2:
        return _row("cross_asset", HEADWIND, facts)
    return _row("cross_asset", NEUTRAL, facts)


# ── slots ────────────────────────────────────────────────────────────────────
def _ct(now):
    return now.replace(tzinfo=_mc.CT) if now.tzinfo is None else now.astimezone(_mc.CT)


def _slot_time(now, interval_min):
    """The clock slot ``now`` falls in, as a CT datetime, or None outside the
    session. Slots are clock multiples of the interval from the first one
    strictly AFTER the open (nothing has traded at the open itself) through the
    close inclusive."""
    now = _ct(now)
    day = now.date()
    if not _mc.is_trading_day(day):
        return None
    floored = now.replace(minute=now.minute // interval_min * interval_min,
                          second=0, microsecond=0)
    if not (_mc.regular_open_on(day) < floored <= _mc.regular_close_on(day)):
        return None
    return floored


def slot_due(now, last_slot, interval_min):
    """The ``"HH:MM"`` slot due now, or None. A late tick still fires the slot
    it is in, once: ``last_slot`` is the slot already published today."""
    at = _slot_time(now, interval_min)
    if at is None:
        return None
    slot = at.strftime("%H:%M")
    return None if slot == last_slot else slot


def _on(date, slot):
    y, m, d = (int(x) for x in date.split("-"))
    hh, mm = (int(x) for x in slot.split(":"))
    return _dt.datetime(y, m, d, hh, mm, tzinfo=_mc.CT)


def is_final(date, slot) -> bool:
    """Whether ``slot`` is the session's last: the reading taken at the close."""
    at = _on(date, slot)
    return at >= _mc.regular_close_on(at.date())


def next_slot(date, slot, interval_min):
    """The slot after ``slot``, or None when ``slot`` is the last of the day."""
    nxt = _on(date, slot) + _dt.timedelta(minutes=interval_min)
    return nxt.strftime("%H:%M") if nxt <= _mc.regular_close_on(nxt.date()) else None


# ── a reading ────────────────────────────────────────────────────────────────
def tally(rows) -> dict:
    out = dict.fromkeys(VERDICTS, 0)
    for r in rows:
        out[r["verdict"] if r.get("verdict") in out else NONE] += 1
    return out


def _safely(key, fn, *args) -> dict:
    """One row. A failure costs that row its reading, not the other five."""
    try:
        return fn(*args)
    except Exception:
        _degrade.degraded(f"market.read.{key}")
        return _row(key, NONE, {})


def build(inputs, cfg, *, date, slot, ts, previous=None) -> dict:
    """One reading. ``inputs`` holds the four views, each already None when it
    is missing, stale or from another day: ``dashboard``, ``matrix``, ``sides``,
    ``alerts``. ``previous`` is the last published reading; the same date
    supplies each row's ``prev`` and the day's ``history``."""
    dashboard, matrix = inputs.get("dashboard"), inputs.get("matrix")
    rows = [
        _safely("direction", direction, dashboard, cfg),
        _safely("breadth", breadth, dashboard, cfg),
        _safely("structure", structure, matrix, cfg),
        _safely("volatility", volatility, dashboard, cfg),
        _safely("flow", flow, inputs.get("sides"), inputs.get("alerts"), cfg),
        _safely("cross_asset", cross_asset, dashboard, cfg),
    ]
    same_day = isinstance(previous, dict) and previous.get("date") == date
    before = {r.get("key"): r for r in previous.get("rows", [])
              if isinstance(r, dict)} if same_day else {}
    for r in rows:
        was = before.get(r["key"])
        r["prev"] = ({"verdict": was.get("verdict"), "facts": was.get("facts")}
                     if was else None)
    history = [h for h in previous.get("history", []) if isinstance(h, dict)
               and h.get("slot") != slot] if same_day else []
    history.append({"slot": slot, "verdicts": {r["key"]: r["verdict"] for r in rows}})
    interval = cfg["interval_min"]
    return {
        "date": date, "ts": ts, "slot": slot, "interval_min": interval,
        "next_slot": next_slot(date, slot, interval), "final": is_final(date, slot),
        "public": cfg["public"] is True, "tally": tally(rows), "rows": rows,
        "history": history,
    }

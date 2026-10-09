"""Traded premium, booked as it trades: per strike and side, a running total of
new volume priced ONCE, at the mark of the reading it first appeared in.

The stored ``prem`` view is the day's volume at the CURRENT mark, so it falls
when marks fall. This figure cannot: a sixth view string, ``tprem``, in
``gex_history.db``, with the same ``{call, put, net}`` cell.

``advance``     pure: books one chain's new volume into a symbol's state.
``on_chain``    every fetched chain, on the collector's thread. In memory only
                -- it never opens the database, so it cannot slow or break a
                poll.
``write_rows``  once a poll, after ``poll_once``, on its write connection:
                resumes a symbol's total from the store the first time it is
                written after a start, writes this poll's rows in one commit,
                and logs the day's check once after the regular close.

Off unless ``config/marketdata.toml [collection] traded_premium`` is true, and
nothing reads the view yet. An ESTIMATE: unsigned, priced at the mid of the
minute the volume was first seen. It imports nothing from ``compute``.
Design: docs/plans/2026-10-09-traded-premium-increment-design.md.
"""
import datetime as _dt
import logging
import statistics
import sys
import time
from zoneinfo import ZoneInfo

from repo_paths import OPTIONS_SCANNER
from shared import market_calendar as _mc
from shared import marketdata_config as _mdc
from shared.numeric import finite as _finite
from shared.numeric import parsed_finite as _parsed

from services import _degrade

# ``flow_skew`` lives under options-scanner; ``compute`` puts it on the path
# too, repeated here so this module imports on its own (a test, a tool).
if str(OPTIONS_SCANNER) not in sys.path:
    sys.path.insert(0, str(OPTIONS_SCANNER))

import flow_skew  # noqa: E402  (the ONE definition of a contract's mark)

log = logging.getLogger(__name__)

VIEW = "tprem"
CALL, PUT = 0, 1

_CT = ZoneInfo("America/Chicago")
_MULTIPLIER = 100           # one option contract = 100 shares
_SIDES = (("callExpDateMap", CALL), ("putExpDateMap", PUT))
# A strike is keyed at three decimals. The store keeps strikes as float32, so
# an odd strike (17.63) comes back as 17.6299991607666; unrounded, a total
# resumed after a restart and the same strike's new volume would sit in two
# cells that pack to ONE float32 key, and one of them would be lost.
_STRIKE_DECIMALS = 3


def new_state() -> dict:
    """One symbol's session state."""
    return {
        "seeded": False,        # one usable reading taken this session
        "since_ts": None,       # when the watch began: the first usable reading
        "last_ts": None,        # the latest usable reading
        "hw": {},               # {contract symbol: highest volume already booked}
        "total": {},            # {strike: [call dollars, put dollars]}
        # The day's check. The chain's own volume, first and latest, is summed
        # straight off the chain so it does not depend on the booking below.
        "first_vol": 0.0, "chain_vol": 0.0,
        "booked_vol": 0.0, "late_vol": 0.0, "booked": 0.0,
        # The same new volume priced at each contract's ``last``, where it has
        # one, beside the mark dollars for exactly that volume.
        "last_vol": 0.0, "at_last": 0.0, "at_mark_same": 0.0,
    }


def _walk(chain):
    """Yield ``(side, strike, contract)`` for every contract dict in a chain.

    The strike is the MAP KEY, as ``flow_skew._accumulate_by_strike`` reads it:
    the key is always present, while the contract's own field can be absent."""
    for mapkey, side in _SIDES:
        exp_map = chain.get(mapkey)
        if not isinstance(exp_map, dict):
            continue
        for strike_map in exp_map.values():
            if not isinstance(strike_map, dict):
                continue
            for raw_strike, contracts in strike_map.items():
                strike = _parsed(raw_strike)
                if strike is None or not isinstance(contracts, (list, tuple)):
                    continue
                strike = round(strike, _STRIKE_DECIMALS)
                for c in contracts:
                    if isinstance(c, dict):
                        yield side, strike, c


def advance(state, chain, now_ts, *, late_sec):
    """Book one fetched chain's new volume into ``state``, IN PLACE. Returns
    the dollars booked by this reading, or None for a chain with no contracts
    (an error body), which changes nothing.

    The symbol's FIRST usable reading only sets each contract's baseline: after
    a restart the day's volume must not land in one minute. On a later reading
    a contract with no baseline stood at zero volume before, so all of its
    volume is new.

    The baseline is a HIGH-WATER mark: volume never falls within a session, so
    a glitch read of 0 books nothing and cannot re-book the day. A contract
    with new volume and no usable mark books nothing and KEEPS its baseline, so
    that volume is booked when a mark returns."""
    if not isinstance(chain, dict):
        return None
    seeded = state["seeded"]
    last_ts = state["last_ts"]
    late = last_ts is not None and now_ts - last_ts > late_sec
    hw, total = state["hw"], state["total"]
    contracts, chain_vol, dollars_now = 0, 0.0, 0.0
    for side, strike, c in _walk(chain):
        osi = c.get("symbol")
        vol = _finite(c.get("totalVolume"))
        if not osi or vol is None or vol < 0:
            continue
        contracts += 1
        chain_vol += vol
        before = hw.get(osi)
        if before is None:
            if not seeded:
                if vol > 0:
                    hw[osi] = vol
                continue
            before = 0.0
        dv = vol - before
        if dv <= 0:
            continue
        mark = _finite(flow_skew._contract_mark(c))
        if mark is None:
            continue
        hw[osi] = vol
        dollars = dv * mark * _MULTIPLIER
        cell = total.get(strike)
        if cell is None:
            cell = total[strike] = [0.0, 0.0]
        cell[side] += dollars
        dollars_now += dollars
        state["booked_vol"] += dv
        if late:
            state["late_vol"] += dv
        last = _finite(c.get("last"))
        if last is not None and last > 0:
            state["last_vol"] += dv
            state["at_last"] += dv * last * _MULTIPLIER
            state["at_mark_same"] += dollars
    if not contracts:
        return None
    if not seeded:
        state["seeded"], state["since_ts"] = True, now_ts
        state["first_vol"] = chain_vol
    state["chain_vol"] = chain_vol
    state["booked"] += dollars_now
    state["last_ts"] = now_ts
    return dollars_now


def grid(state) -> dict:
    """The running totals as the store's cell shape, strikes ascending. Exactly
    ``{call, put, net}`` floats: what the columnar packer gates on."""
    return {strike: {"call": cell[CALL], "put": cell[PUT],
                     "net": cell[CALL] - cell[PUT]}
            for strike, cell in sorted(state["total"].items())}


def _resume(state, prior) -> None:
    """Add the session's last stored totals to ``state``: what a restart must
    not lose. ``prior`` is ``gex_history_db.latest_grid_row``'s answer.

    Everything is read BEFORE anything is added, so a bad cell cannot leave
    half a total behind for the retry to add a second time."""
    if not prior or not isinstance(prior[3], dict):
        return
    adds = []
    for raw_strike, cell in prior[3].items():
        strike = _parsed(raw_strike)
        if strike is None or not isinstance(cell, dict):
            continue
        adds.append((round(strike, _STRIKE_DECIMALS),
                     max(_finite(cell.get("call")) or 0.0, 0.0),
                     max(_finite(cell.get("put")) or 0.0, 0.0)))
    total = state["total"]
    for strike, call, put in adds:
        cell = total.get(strike)
        if cell is None:
            cell = total[strike] = [0.0, 0.0]
        cell[CALL] += call
        cell[PUT] += put


# ── the session state ────────────────────────────────────────────────────────
def _fresh(date=None) -> dict:
    return {
        "date": date,           # CT session date the state belongs to
        "symbols": {},          # {symbol: new_state()}
        "rows": {},             # {symbol: spot}: read this poll, not yet written
        "restored": set(),      # symbols whose stored total has been added back
        "now": None,            # the latest reading's clock
        "poll_sec": 0.0,        # this poll's time in on_chain, so far
        "passes": [],           # each poll's time in on_chain, seconds
        "writes": [],           # each poll's write, seconds
        "checked": False,       # the day's check was logged
    }


# ``on_chain`` and ``write_rows`` run on ONE thread (poll_once loops over its
# results on the calling thread, then the write follows), so no lock.
_S: dict = _fresh()


def reset() -> None:
    """Drop all state (a test helper; also what switching it off does)."""
    _S.clear()
    _S.update(_fresh())


def _roll(date) -> None:
    if _S["date"] != date:
        _S.clear()
        _S.update(_fresh(date))


def _now(now):
    """``now`` as an aware Central datetime. None = the scheduler's clock, the
    one ``compute`` reads; a naive value is Central."""
    if now is None:
        from services.options_svc import scheduler as _sched   # lazy: import cycle
        now = _sched._market_now()
    if now.tzinfo is None:
        return now.replace(tzinfo=_CT)
    return now.astimezone(_CT)


def on_chain(symbol, chain, now=None) -> None:
    """Book one fetched chain's new volume. In memory only; never raises."""
    try:
        cfg = _mdc.section("collection")
        if cfg.get("traded_premium") is not True:
            # Switched off: drop the state, so switching it back on resumes
            # from the store like a restart. Hours of unwatched volume must
            # not be booked at one minute's mark.
            if _S["date"] is not None:
                reset()
            return
        now = _now(now)
        # Nothing before the regular open, the rule the bought/sold tally and
        # the hedging-flow model take: until then a chain can still carry
        # yesterday's volume for a contract that has not traded yet, and its
        # marks are frozen. After the 15:00 close it keeps booking: ETF options
        # trade to 15:15.
        if not _mc.regular_session_has_opened(now):
            return
        started = time.perf_counter()
        _roll(now.date().isoformat())
        state = _S["symbols"].get(symbol)
        if state is None:
            state = _S["symbols"][symbol] = new_state()
        booked = advance(state, chain, int(now.timestamp()),
                         late_sec=cfg["traded_premium_late_sec"])
        if booked is not None:
            _S["rows"][symbol] = _finite(chain.get("underlyingPrice"))
            _S["now"] = now
        _S["poll_sec"] += time.perf_counter() - started
    except Exception:
        _degrade.degraded("options.traded_premium.on_chain", detail=symbol)


def write_rows(gh, conn, ts_min=None) -> None:
    """Write this poll's rows in ONE commit. Never raises.

    The stored row is the RUNNING TOTAL, so a row this fails to write loses
    nothing: the next one carries it. A symbol written for the first time
    since this process started (or since the switch came on) first gets the
    session's last stored total added back.

    ``ts_min`` is the poll's minute, the one the hedging-flow rows use. A
    symbol's first row may hold an empty grid: its time is when the watch
    began."""
    rows, _S["rows"] = _S["rows"], {}
    if not rows:
        return
    try:
        _S["passes"].append(_S["poll_sec"])
        _S["poll_sec"] = 0.0
        started = time.perf_counter()
        day = _dt.date.fromisoformat(_S["date"])
        ts = int(ts_min) if ts_min is not None else int(time.time()) // 60 * 60
        for symbol, spot in rows.items():
            state = _S["symbols"][symbol]
            if symbol not in _S["restored"]:
                _resume(state, gh.latest_grid_row(conn, symbol, VIEW, date=day))
                _S["restored"].add(symbol)
            cells = grid(state)
            calls = sum(c["call"] for c in cells.values())
            puts = sum(c["put"] for c in cells.values())
            gh.insert_snapshot(
                conn, symbol, VIEW,
                {"ts": ts, "spot": spot, "net_total": calls - puts,
                 "call_prem": calls, "put_prem": puts},
                cells, None)
        conn.commit()
        _S["writes"].append(time.perf_counter() - started)
    except Exception:
        _degrade.degraded("options.traded_premium.write")
        try:
            conn.rollback()
        except Exception:
            log.debug("traded premium rollback failed", exc_info=True)
        return
    _log_day_check_once()


# ── the day's check ──────────────────────────────────────────────────────────
_SUMS = ("booked_vol", "seen_vol", "late_vol", "booked", "last_vol", "at_last",
         "at_mark_same")


def _timing(samples):
    if not samples:
        return None
    return {"polls": len(samples),
            "median_ms": round(statistics.median(samples) * 1000.0, 1),
            "worst_ms": round(max(samples) * 1000.0, 1)}


def day_check() -> dict:
    """The figures Phase A is judged on, per symbol and for the timing.

    ``seen_vol`` is the chain's own volume now less at the first reading: what
    the booking should have found. ``booked_vol`` under it is volume with no
    usable mark, or a contract that left the chain."""
    symbols = {}
    for symbol, s in _S["symbols"].items():
        if not s["seeded"]:
            continue
        symbols[symbol] = {
            "since_ts": s["since_ts"], "booked_vol": s["booked_vol"],
            "seen_vol": s["chain_vol"] - s["first_vol"],
            "late_vol": s["late_vol"], "booked": s["booked"],
            "last_vol": s["last_vol"], "at_last": s["at_last"],
            "at_mark_same": s["at_mark_same"]}
    return {"date": _S["date"], "symbols": symbols,
            "pass": _timing(_S["passes"]), "write": _timing(_S["writes"])}


def _pct(part, whole) -> str:
    return f"{100.0 * part / whole:.1f}%" if whole > 0 else "n/a"


def _check_line(s) -> str:
    return (f"booked {s['booked_vol']:,.0f} of {s['seen_vol']:,.0f} contracts "
            f"seen ({_pct(s['booked_vol'], s['seen_vol'])}); "
            f"{_pct(s['late_vol'], s['booked_vol'])} of them priced late; "
            f"${s['booked']:,.0f} at the mark; at last ${s['at_last']:,.0f} "
            f"against ${s['at_mark_same']:,.0f} at the mark for the "
            f"{_pct(s['last_vol'], s['booked_vol'])} of volume with a last price")


def _log_day_check_once() -> None:
    """One line for everything and one a symbol, at the first poll after the
    regular close. Never raises."""
    try:
        now = _S["now"]
        if _S["checked"] or now is None or _mc.is_regular_hours(now):
            return
        _S["checked"] = True
        check = day_check()
        every = {k: sum(s[k] for s in check["symbols"].values()) for k in _SUMS}
        log.info("traded_premium day_check %s all: %d symbols; %s; pass %s; "
                 "write %s", check["date"], len(check["symbols"]),
                 _check_line(every), check["pass"], check["write"])
        for symbol, s in sorted(check["symbols"].items()):
            since = _dt.datetime.fromtimestamp(s["since_ts"], _CT).strftime("%H:%M")
            log.info("traded_premium day_check %s %s: since %s; %s",
                     check["date"], symbol, since, _check_line(s))
    except Exception:
        _degrade.degraded("options.traded_premium.day_check")

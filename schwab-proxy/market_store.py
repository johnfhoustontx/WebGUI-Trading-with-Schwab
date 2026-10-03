"""What the proxy has already fetched from Schwab, kept in memory.

Three stores (chains, quotes, daily bars) and the ``Gateway`` that decides, per
request, whether a stored copy is fresh and covers the request or whether to
call Schwab. No FastAPI and no repo imports: the handlers in ``schwab_proxy.py``
are thin adapters over ``Gateway``, and everything here is unit-testable.

Nothing here is written to disk or to Redis. A proxy restart starts empty.

Design: docs/plans/2026-10-03-market-data-store-design.md
"""
from __future__ import annotations

import json
import math
import threading
import zlib
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, time as _time, timedelta
from zoneinfo import ZoneInfo

CT = ZoneInfo("America/Chicago")


class UpstreamError(Exception):
    """Schwab answered with something other than 200. Carries what the handler
    needs to raise the same HTTP error it raised before the store existed."""

    def __init__(self, status_code, detail):
        super().__init__(f"{status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class Served:
    """One answer. ``body`` is ready-to-send JSON bytes; ``data`` is an object
    for the framework to serialize. Exactly one of them is set.

    ``kind``: ``hit`` / ``subset`` / ``coalesced`` / ``composed`` were answered
    locally; ``miss`` / ``partial`` / ``pass`` called Schwab."""
    kind: str
    age: float
    body: bytes | None = None
    data: object = None


def _real_number(v) -> bool:
    return (isinstance(v, (int, float)) and not isinstance(v, bool)
            and math.isfinite(v))


#############################################
# CHAINS
#############################################

@dataclass(frozen=True)
class ChainKey:
    """The full identity of a ``/chains`` request."""
    symbol: str
    contract_type: str = "ALL"
    strike_range: str = "ALL"
    strike_count: int | None = None
    from_date: str | None = None
    to_date: str | None = None

    @classmethod
    def from_params(cls, params) -> "ChainKey":
        count = params.get("strikeCount")
        return cls(
            symbol=str(params.get("symbol")),
            contract_type=str(params.get("contractType") or "ALL"),
            strike_range=str(params.get("range") or "ALL"),
            strike_count=int(count) if count is not None else None,
            from_date=str(params["fromDate"])[:10] if params.get("fromDate") else None,
            to_date=str(params["toDate"])[:10] if params.get("toDate") else None,
        )

    def params(self) -> dict:
        out = {"symbol": self.symbol, "contractType": self.contract_type,
               "range": self.strike_range}
        if self.from_date:
            out["fromDate"] = self.from_date
        if self.to_date:
            out["toDate"] = self.to_date
        if self.strike_count is not None:
            out["strikeCount"] = self.strike_count
        return out

    @property
    def plain(self) -> bool:
        """Every strike, both sides, a stated date window. Only a plain chain
        may be cut, and only a plain request may be answered by cutting: a
        strike-filtered consumer sums over exactly the strikes it asked for."""
        return (self.contract_type == "ALL" and self.strike_range == "ALL"
                and self.strike_count is None
                and bool(self.from_date) and bool(self.to_date))

    def covers(self, other: "ChainKey") -> bool:
        # ISO dates compare correctly as strings.
        return (self.plain and other.plain and self.symbol == other.symbol
                and self.from_date <= other.from_date
                and self.to_date >= other.to_date)


@dataclass(frozen=True)
class _ChainEntry:
    key: ChainKey
    fetched_at: float
    state: str        # the market session it was fetched in
    header: dict      # every top-level field except the two expiration maps
    calls: dict       # {expiration key: (zlib JSON of its strike map, contracts)}
    puts: dict


_SIDES = ("callExpDateMap", "putExpDateMap")


def _pack_side(exp_map) -> dict:
    out = {}
    for exp_key, strikes in exp_map.items():
        if not isinstance(strikes, dict):
            raise ValueError("unexpected chain shape")
        count = sum(len(v) for v in strikes.values() if isinstance(v, list))
        blob = zlib.compress(
            json.dumps(strikes, separators=(",", ":")).encode(), 1)
        out[str(exp_key)] = (blob, count)
    return out


def _render(entry: _ChainEntry, lo=None, hi=None) -> bytes:
    """The entry as response JSON, cut to expirations in ``[lo, hi]`` when
    given. Built by joining stored fragments; nothing is re-parsed."""
    def side(frags):
        parts, n = [], 0
        for exp_key, (blob, count) in frags.items():
            day = exp_key.split(":")[0]
            if lo is not None and not (lo <= day <= hi):
                continue
            parts.append(json.dumps(exp_key).encode() + b":" + zlib.decompress(blob))
            n += count
        return b"{" + b",".join(parts) + b"}", n

    calls, n_calls = side(entry.calls)
    puts, n_puts = side(entry.puts)
    header = dict(entry.header)
    if "numberOfContracts" in header:
        header["numberOfContracts"] = n_calls + n_puts
    head = json.dumps(header, separators=(",", ":")).encode()
    joiner = b"," if header else b""
    return (head[:-1] + joiner + b'"callExpDateMap":' + calls
            + b',"putExpDateMap":' + puts + b"}")


def chain_shape(payload) -> frozenset:
    """Which contracts a chain holds: ``(side, expiration date, strike)``.
    Shadow mode compares these, never values — values move every second."""
    out = set()
    if not isinstance(payload, dict):
        return frozenset()
    for side in _SIDES:
        exp_map = payload.get(side)
        if not isinstance(exp_map, dict):
            continue
        for exp_key, strikes in exp_map.items():
            if not isinstance(strikes, dict):
                continue                  # not a strike map: no contracts
            day = str(exp_key).split(":")[0]
            for strike in strikes:
                out.add((side, day, str(strike)))
    return frozenset(out)


class ChainStore:
    def __init__(self, max_entries: int = 400):
        self._lock = threading.Lock()
        self._entries: "OrderedDict[ChainKey, _ChainEntry]" = OrderedDict()
        self._max = max(1, int(max_entries))

    def put(self, key: ChainKey, payload, *, now: float, state: str,
            max_entries: int | None = None) -> bool:
        """Keep one fetched chain. Returns False (and keeps nothing) for a
        payload that is not chain-shaped. ``max_entries`` is the bound to
        enforce on THIS put (the gateway passes the configured value, so a
        saved setting applies without a restart)."""
        if not isinstance(payload, dict):
            return False
        calls, puts = payload.get("callExpDateMap"), payload.get("putExpDateMap")
        if not isinstance(calls, dict) or not isinstance(puts, dict):
            return False
        try:
            entry = _ChainEntry(
                key=key, fetched_at=now, state=state,
                header={k: v for k, v in payload.items() if k not in _SIDES},
                calls=_pack_side(calls), puts=_pack_side(puts))
        except (ValueError, TypeError):
            return False
        if max_entries is not None:
            try:
                self._max = max(1, int(max_entries))
            except (TypeError, ValueError):
                pass                      # keep the bound already in force
        with self._lock:
            self._entries[key] = entry
            self._entries.move_to_end(key)
            while len(self._entries) > self._max:
                self._entries.popitem(last=False)
        return True

    @staticmethod
    def _fresh(entry, max_age, now, state) -> bool:
        age = now - entry.fetched_at
        return entry.state == state and 0 <= age <= max_age and max_age > 0

    def lookup(self, key: ChainKey, *, max_age: float, now: float,
               state: str) -> Served | None:
        with self._lock:
            exact = self._entries.get(key)
            others = ([e for k, e in self._entries.items()
                       if k.symbol == key.symbol and k != key]
                      if key.plain else [])
        if exact is not None and self._fresh(exact, max_age, now, state):
            return Served("hit", now - exact.fetched_at, body=_render(exact))
        best = None
        for e in others:
            if e.key.covers(key) and self._fresh(e, max_age, now, state):
                if best is None or e.fetched_at > best.fetched_at:
                    best = e
        if best is None:
            return None
        return Served("subset", now - best.fetched_at,
                      body=_render(best, key.from_date, key.to_date))

    def wide_key(self, key: ChainKey, *, today) -> ChainKey | None:
        """The wider window to fetch INSTEAD of ``key``, or None to fetch
        ``key`` as asked.

        It is the narrowest plain window already held for this symbol, at ANY
        age, that starts today and covers ``key`` - in practice the collector's
        today -> +7. Derived from what is held rather than configured, so it
        cannot drift from the window the collector actually asks for. A window
        that started on an earlier day is never reused: refetching it would ask
        Schwab for expirations in the past."""
        if not key.plain:
            return None
        start = today.isoformat()
        with self._lock:
            held = [e for k, e in self._entries.items()
                    if k.symbol == key.symbol and k != key
                    and k.from_date == start and k.covers(key)]
        if not held:
            return None
        return min(held, key=lambda e: (e.key.to_date, -e.fetched_at)).key

    def cut(self, wide: ChainKey, key: ChainKey) -> bytes | None:
        """``key``'s window out of the entry stored under ``wide``, regardless
        of age — for the moment right after ``wide`` was fetched."""
        with self._lock:
            entry = self._entries.get(wide)
        if entry is None:
            return None
        return _render(entry, key.from_date, key.to_date)


#############################################
# QUOTES
#############################################

class QuoteStore:
    """Schwab's raw per-symbol quote blocks, each with its own fetch time.
    Bounded by the app's symbol universe (about 900), so it needs no eviction."""

    def __init__(self):
        self._lock = threading.Lock()
        self._quotes: dict = {}

    def put_many(self, payload, *, now: float) -> None:
        if not isinstance(payload, dict):
            return
        with self._lock:
            for symbol, block in payload.items():
                # "errors" is Schwab's invalid-symbols bucket, not a quote.
                if symbol != "errors" and isinstance(block, dict):
                    self._quotes[symbol] = (block, now)

    def split(self, symbols, *, max_age: float, now: float):
        """``(fresh {symbol: block}, missing [symbol], oldest fresh age)``."""
        fresh, missing, oldest = {}, [], 0.0
        with self._lock:
            for symbol in symbols:
                got = self._quotes.get(symbol)
                age = None if got is None else now - got[1]
                if age is not None and max_age > 0 and 0 <= age <= max_age:
                    fresh[symbol] = got[0]
                    oldest = max(oldest, age)
                else:
                    missing.append(symbol)
        return fresh, missing, oldest

    def get(self, symbol: str, *, max_age: float, now: float):
        return self.split([symbol], max_age=max_age, now=now)[0].get(symbol)


#############################################
# DAILY BARS
#############################################

def bar_key(params) -> tuple:
    """``(symbol, periodType, period, frequencyType, frequency)`` — the exact
    range asked for. A shorter range is never cut from a longer one."""
    return (str(params.get("symbol")), str(params.get("periodType")),
            int(params.get("period")), str(params.get("frequencyType")),
            int(params.get("frequency")))


def bar_epoch(now_ct, settle_min: float, cal) -> tuple:
    """``(session date, "pre" | "live" | "settled")``.

    ``cal`` is ``shared.market_calendar`` (or a stand-in with the same four
    calls). An entry is served only inside the period it was fetched in, so
    each boundary refetches once — which is also what picks up a split
    adjustment to the history."""
    d = now_ct.date()
    if cal.is_trading_day(d):
        settled_at = cal.regular_close_on(d) + timedelta(minutes=float(settle_min))
        if now_ct >= settled_at:
            return (d.isoformat(), "settled")
        if cal.regular_session_has_opened(now_ct):
            return (d.isoformat(), "live")
        return (d.isoformat(), "pre")
    return (cal.prev_trading_day(d).isoformat(), "settled")


def _candle_date(candle):
    try:
        return datetime.fromtimestamp(candle["datetime"] / 1000, CT).date()
    except Exception:  # noqa: BLE001 — a malformed candle has no date.
        return None


def compose_today(payload, quote, today):
    """``payload`` with the bar for ``today`` built from ``quote``, or None when
    the quote cannot supply one. Returns a new dict; ``payload`` is untouched."""
    block = quote.get("quote") if isinstance(quote, dict) else None
    if not isinstance(block, dict) or not isinstance(payload, dict):
        return None
    o, h, lo, last = (block.get(k) for k in
                      ("openPrice", "highPrice", "lowPrice", "lastPrice"))
    if not all(_real_number(v) and v > 0 for v in (o, h, lo, last)):
        return None
    volume = block.get("totalVolume")
    bar = {"open": o, "high": h, "low": lo, "close": last,
           "volume": volume if _real_number(volume) else 0,
           # Schwab stamps a daily candle at midnight Central.
           "datetime": int(datetime.combine(today, _time(0), tzinfo=CT)
                           .timestamp() * 1000)}
    candles = list(payload.get("candles") or [])
    if candles and _candle_date(candles[-1]) == today:
        candles[-1] = bar
    else:
        candles.append(bar)
    return {**payload, "candles": candles}


_BAR_TOLERANCE = 0.005   # the quote may be up to two minutes older than the bar


def compare_today_bar(upstream, quote, today) -> str:
    """Shadow verdict on whether the quote-built bar agrees with Schwab's:
    ``match`` / ``mismatch`` / ``no_today`` (Schwab sent no bar for today) /
    ``no_quote``."""
    composed = compose_today(upstream, quote, today)
    if composed is None:
        return "no_quote"
    candles = (upstream or {}).get("candles") or []
    if not candles or _candle_date(candles[-1]) != today:
        return "no_today"
    theirs, ours = candles[-1], composed["candles"][-1]
    for field in ("close", "high", "low"):
        a, b = theirs.get(field), ours[field]
        if not _real_number(a) or abs(a - b) > _BAR_TOLERANCE * abs(b):
            return "mismatch"
    return "match"


class BarStore:
    """Daily price series, one per (symbol, range), valid for one bar period."""

    def __init__(self):
        self._lock = threading.Lock()
        self._entries: dict = {}

    def put(self, key, payload, *, now: float, epoch) -> None:
        if not isinstance(payload, dict) or not payload.get("candles"):
            return          # an empty series is refetched, never re-served
        blob = zlib.compress(
            json.dumps(payload, separators=(",", ":")).encode(), 1)
        with self._lock:
            self._entries[key] = (blob, now, epoch)

    def get(self, key, *, epoch):
        """``(JSON bytes, fetched_at)`` for this period, or None."""
        with self._lock:
            got = self._entries.get(key)
        if got is None or got[2] != epoch:
            return None
        return zlib.decompress(got[0]), got[1]

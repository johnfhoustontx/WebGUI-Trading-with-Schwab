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
import logging
import math
import threading
import time
import zlib
from collections import OrderedDict
from dataclasses import dataclass
from datetime import date, datetime, time as _time, timedelta
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
        # ISO dates compare correctly as strings. A reversed window is covered
        # by nothing: it is never served from a cut.
        return (self.plain and other.plain and self.symbol == other.symbol
                and other.from_date <= other.to_date
                and self.from_date <= other.from_date
                and self.to_date >= other.to_date)

    def days(self) -> int | None:
        """The window's length in days, or None when a date does not parse."""
        try:
            return (date.fromisoformat(self.to_date)
                    - date.fromisoformat(self.from_date)).days
        except (TypeError, ValueError):
            return None


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


def _in_window(exp_key: str, lo: str, hi: str) -> bool:
    return lo <= exp_key.split(":")[0] <= hi


def _keeps_any(entry: _ChainEntry, lo: str, hi: str) -> bool:
    """Whether a cut to ``[lo, hi]`` keeps at least one expiration. A cut that
    keeps none is never served: Schwab answers an empty window with
    ``underlyingPrice`` 0.0, which the stored header cannot reproduce."""
    return any(_in_window(exp_key, lo, hi)
               for frags in (entry.calls, entry.puts) for exp_key in frags)


def _render(entry: _ChainEntry, lo=None, hi=None) -> bytes:
    """The entry as response JSON, cut to expirations in ``[lo, hi]`` when
    given. Built by joining stored fragments once; nothing is re-parsed.
    Uncut, the header is Schwab's own; a cut recounts ``numberOfContracts``."""
    cutting = lo is not None
    pieces, contracts = [b""], 0          # pieces[0] is the header, set last
    for opener, frags in ((b'"callExpDateMap":{', entry.calls),
                          (b',"putExpDateMap":{', entry.puts)):
        pieces.append(opener)
        first = True
        for exp_key, (blob, count) in frags.items():
            if cutting and not _in_window(exp_key, lo, hi):
                continue
            if not first:
                pieces.append(b",")
            first = False
            pieces += (json.dumps(exp_key).encode(), b":", zlib.decompress(blob))
            contracts += count
        pieces.append(b"}")
    pieces.append(b"}")
    header = entry.header
    if cutting and "numberOfContracts" in header:
        header = {**header, "numberOfContracts": contracts}
    head = json.dumps(header, separators=(",", ":")).encode()
    pieces[0] = head[:-1] + (b"," if header else b"")
    return b"".join(pieces)


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


# The widest held window a near miss may refetch in place of the one asked for.
# The refetch exists for the collector's roughly one-week window; a held 45- or
# 120-day chain must never be fetched in place of a short one.
WIDE_REFETCH_MAX_DAYS = 10


class ChainStore:
    """Fetched chains, one per request identity. The bound is a count of
    entries, not bytes: a few hundred wide chains can reach a couple of hundred
    megabytes."""

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
        if not calls and not puts:
            return False    # an empty chain is refetched, never re-served
        try:
            entry = _ChainEntry(
                key=key, fetched_at=now, state=state,
                header={k: v for k, v in payload.items() if k not in _SIDES},
                calls=_pack_side(calls), puts=_pack_side(puts))
        except (ValueError, TypeError):
            return False
        bound = None
        if max_entries is not None:
            try:
                bound = max(1, int(max_entries))
            except (TypeError, ValueError, OverflowError):
                pass                      # keep the bound already in force
        with self._lock:
            if bound is not None:
                self._max = bound
            self._entries[key] = entry
            self._entries.move_to_end(key)
            while len(self._entries) > self._max:
                self._entries.popitem(last=False)
        return True

    @staticmethod
    def _fresh(entry, max_age, now, state) -> bool:
        if not _real_number(max_age) or max_age <= 0:
            return False                  # NaN, infinity, a bool: no limit given
        age = now - entry.fetched_at
        return entry.state == state and 0 <= age <= max_age

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
            if (e.key.covers(key) and self._fresh(e, max_age, now, state)
                    and _keeps_any(e, key.from_date, key.to_date)):
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
        Schwab for expirations in the past. Nor is one longer than
        ``WIDE_REFETCH_MAX_DAYS``, or one that held no expiration inside
        ``key``'s window (its cut would be empty, so the wide fetch would be
        followed by a second one)."""
        if not key.plain:
            return None
        start = today.isoformat()
        with self._lock:
            held = [e for k, e in self._entries.items()
                    if k.symbol == key.symbol and k != key
                    and k.from_date == start and k.covers(key)]
        usable = []
        for e in held:
            days = e.key.days()
            if (days is not None and days <= WIDE_REFETCH_MAX_DAYS
                    and _keeps_any(e, key.from_date, key.to_date)):
                usable.append(e.key)
        if not usable:
            return None
        return min(usable, key=lambda k: k.to_date)

    def cut(self, wide: ChainKey, key: ChainKey) -> bytes | None:
        """``key``'s window out of the entry stored under ``wide``, regardless
        of age — for the moment right after ``wide`` was fetched. None when
        ``wide`` is not held, does not cover ``key`` (a strike-filtered or
        reversed request never is), or holds no expiration in the window."""
        if not wide.covers(key):
            return None
        with self._lock:
            entry = self._entries.get(wide)
        if entry is None or not _keeps_any(entry, key.from_date, key.to_date):
            return None
        return _render(entry, key.from_date, key.to_date)


#############################################
# QUOTES
#############################################

class QuoteStore:
    """Schwab's raw per-symbol quote blocks, each with its own fetch time.

    Bounded: past ``max_symbols`` the oldest-stored symbols are dropped (the
    public pages let visitors choose symbols). Blocks are handed back by
    reference, so callers must not mutate what they get back."""

    def __init__(self, max_symbols: int = 5000):
        self._lock = threading.Lock()
        self._quotes: "OrderedDict[str, tuple]" = OrderedDict()
        self._max = max(1, int(max_symbols))

    def put_many(self, payload, *, now: float) -> None:
        if not isinstance(payload, dict):
            return
        with self._lock:
            for symbol, block in payload.items():
                # "errors" is Schwab's invalid-symbols bucket, not a quote.
                if symbol != "errors" and isinstance(block, dict):
                    self._quotes[symbol] = (block, now)
                    self._quotes.move_to_end(symbol)
            while len(self._quotes) > self._max:
                self._quotes.popitem(last=False)

    def split(self, symbols, *, max_age: float, now: float):
        """``(fresh {symbol: block}, missing [symbol], oldest fresh age)``."""
        fresh, missing, oldest = {}, [], 0.0
        # NaN, infinity or a bool is no limit at all: nothing is fresh.
        limit = max_age if _real_number(max_age) and max_age > 0 else None
        with self._lock:
            for symbol in symbols:
                got = self._quotes.get(symbol)
                age = None if got is None or limit is None else now - got[1]
                if age is not None and 0 <= age <= limit:
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
    """Daily price series, one per (symbol, range), valid for one bar period.
    Bounded: past ``max_entries`` the oldest-stored series are dropped."""

    def __init__(self, max_entries: int = 4000):
        self._lock = threading.Lock()
        self._entries: "OrderedDict[tuple, tuple]" = OrderedDict()
        self._max = max(1, int(max_entries))

    def put(self, key, payload, *, now: float, epoch) -> None:
        if not isinstance(payload, dict) or not payload.get("candles"):
            return          # an empty series is refetched, never re-served
        blob = zlib.compress(
            json.dumps(payload, separators=(",", ":")).encode(), 1)
        with self._lock:
            self._entries[key] = (blob, now, epoch)
            self._entries.move_to_end(key)
            while len(self._entries) > self._max:
                self._entries.popitem(last=False)

    def get(self, key, *, epoch):
        """``(JSON bytes, fetched_at)`` for this period, or None."""
        with self._lock:
            got = self._entries.get(key)
        if got is None or got[2] != epoch:
            return None
        return zlib.decompress(got[0]), got[1]


#############################################
# GATEWAY
#############################################

def effective_max_age(requested, cfg, *, closed: bool) -> float:
    """The caller's ``maxAge`` when it is a usable number, else the configured
    limit for the current market state."""
    if requested is not None:
        try:
            value = float(requested)
        except (TypeError, ValueError):
            value = float("nan")
        if math.isfinite(value) and value >= 0:
            return value
    return float(cfg["closed_max_age_sec"] if closed else cfg["max_age_sec"])


class KeyedLocks:
    """One lock per request identity, so identical concurrent misses make one
    upstream call. Keys carry dates, so the map grows by a few hundred a day;
    the proxy restarts on every promote, long before that matters."""

    def __init__(self):
        self._guard = threading.Lock()
        self._locks: dict = {}

    def get(self, key) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(key, threading.Lock())


class Gateway:
    """Decides, per request, between a stored answer and a call to Schwab.

    ``fetch(endpoint, params)`` returns Schwab's JSON or raises
    ``UpstreamError``. ``config`` is ``shared.marketdata_config`` (``mode()``,
    ``store_on(name)``, ``section(name)``, ``today_bar()``). ``calendar`` is
    ``shared.market_calendar``. ``record(endpoint, caller, outcome)`` is the
    detail counter.

    Two rules hold everywhere: an upstream error is raised, never papered over
    with an old entry; and a bug in store code falls through to a plain fetch
    and is counted in ``degrades``.

    Called from many worker threads at once. A per-request lock is held only
    around the re-check and the upstream fetch, and ``fetch`` is never called
    while a store's own lock is held."""

    def __init__(self, *, fetch, config, calendar, record,
                 clock=time.time, now_ct=None, log=None):
        self._fetch, self._cfg, self._cal = fetch, config, calendar
        self._record, self._clock = record, clock
        self._now_ct = now_ct or (lambda: datetime.now(CT))
        self._log = log or logging.getLogger("market_store")
        self._locks = KeyedLocks()
        self.chain_store = ChainStore()
        self.quote_store = QuoteStore()
        self.bar_store = BarStore()
        self.degrades: dict = {}

    # ---- shared ----------------------------------------------------------
    def _mode(self, store: str) -> str:
        try:
            mode = self._cfg.mode() if self._cfg.store_on(store) else "off"
        except Exception:  # noqa: BLE001 — unreadable config means no store.
            return "off"
        # Anything that is not one of the two store modes never answers locally.
        return mode if mode in ("shadow", "on") else "off"

    def _passthrough(self, label, endpoint, params, caller) -> Served:
        data = self._fetch(endpoint, params)
        self._record(label, caller, "upstream")
        return Served("pass", 0.0, data=data)

    def _degraded(self, area: str) -> None:
        self.degrades[area] = self.degrades.get(area, 0) + 1
        self._log.warning("market store degraded in %s; fetching directly",
                          area, exc_info=True)

    def _guarded(self, label, endpoint, params, caller, work) -> Served:
        try:
            return work()
        except UpstreamError:
            raise
        except Exception:  # noqa: BLE001 — a store bug must not take data down.
            self._degraded(label)
            return self._passthrough(label, endpoint, params, caller)

    # ---- chains ----------------------------------------------------------
    def chains(self, params, caller, max_age=None) -> Served:
        mode = self._mode("chains")
        if mode == "off":
            return self._passthrough("chains", "/chains", params, caller)
        return self._guarded("chains", "/chains", params, caller,
                             lambda: self._chains(params, caller, max_age, mode))

    def _chains(self, params, caller, max_age, mode) -> Served:
        cfg = self._cfg.section("chains")
        now_ct = self._now_ct()
        state = self._cal.session_at(now_ct).name
        key = ChainKey.from_params(params)
        store = self.chain_store

        if mode == "shadow":
            would = store.lookup(key, max_age=float(cfg["shadow_compare_max_age_sec"]),
                                 now=self._clock(), state=state)
            data = self._fetch("/chains", params)
            self._record("chains", caller, "upstream")
            if would is not None:
                same = chain_shape(json.loads(would.body)) == chain_shape(data)
                verdict = "match" if same else "mismatch"
                self._record("chains", caller, f"shadow_{would.kind}_{verdict}")
                if not same:
                    self._log.warning("shadow: stored %s answer for %s differs "
                                      "from Schwab's", would.kind, key)
            store.put(key, data, now=self._clock(), state=state,
                      max_entries=cfg["max_entries"])
            return Served("pass", 0.0, data=data)

        limit = effective_max_age(max_age, cfg, closed=(state == "CLOSED"))
        hit = store.lookup(key, max_age=limit, now=self._clock(), state=state)
        if hit is not None:
            self._record("chains", caller, hit.kind)
            return hit
        wide = store.wide_key(key, today=now_ct.date())
        fetch_key = wide or key
        with self._locks.get(("chains", fetch_key)):
            again = store.lookup(key, max_age=limit, now=self._clock(), state=state)
            if again is not None:
                self._record("chains", caller, "coalesced")
                return Served("coalesced", again.age, body=again.body)
            data = self._fetch("/chains", fetch_key.params())
            kept = store.put(fetch_key, data, now=self._clock(), state=state,
                             max_entries=cfg["max_entries"])
            self._record("chains", caller, "upstream")
        if wide is None:
            return Served("miss", 0.0, data=data)
        # Cut only from what was JUST stored. When the store would not keep the
        # wide chain, the entry still held under ``wide`` is the OLD one, and a
        # cut of it would be an old answer served as a new one.
        cut = store.cut(wide, key) if kept else None
        if cut is not None:
            return Served("miss", 0.0, body=cut)
        # The wide chain came back empty, in a shape the store would not keep,
        # or with no expiration in the window: answer the request exactly as it
        # was asked.
        return self._passthrough("chains", "/chains", params, caller)

    # ---- quotes ----------------------------------------------------------
    def quotes(self, symbols_csv, caller, max_age=None) -> Served:
        params = {"symbols": symbols_csv, "fields": "quote"}
        mode = self._mode("quotes")
        if mode == "off":
            return self._passthrough("quotes", "/quotes", params, caller)
        return self._guarded("quotes", "/quotes", params, caller,
                             lambda: self._quotes(symbols_csv, caller, max_age, mode))

    def _quotes(self, symbols_csv, caller, max_age, mode) -> Served:
        cfg = self._cfg.section("quotes")
        symbols = list(dict.fromkeys(
            s for s in (part.strip() for part in str(symbols_csv).split(",")) if s))
        if not symbols:
            # Nothing to look up. Schwab answered this request before the
            # store existed, so it still does.
            return self._passthrough("quotes", "/quotes",
                                     {"symbols": symbols_csv, "fields": "quote"},
                                     caller)
        limit = effective_max_age(
            max_age, {"max_age_sec": cfg["max_age_sec"],
                      "closed_max_age_sec": cfg["max_age_sec"]}, closed=False)
        store = self.quote_store
        fresh, missing, oldest = store.split(symbols, max_age=limit, now=self._clock())

        def call(wanted):
            return self._fetch("/quotes", {"symbols": ",".join(wanted),
                                           "fields": "quote"})

        if mode == "shadow":
            data = call(symbols)
            self._record("quotes", caller, "upstream")
            if not missing:
                self._record("quotes", caller, "shadow_hit")
            store.put_many(data, now=self._clock())
            return Served("pass", 0.0, data=data)

        if not missing:
            self._record("quotes", caller, "hit")
            return Served("hit", oldest, data={s: fresh[s] for s in symbols})
        data = call(missing)
        store.put_many(data, now=self._clock())
        kind = "partial" if fresh else "miss"
        self._record("quotes", caller, "partial" if fresh else "upstream")
        if not isinstance(data, dict):
            return Served(kind, 0.0, data=data)
        return Served(kind, 0.0, data={**fresh, **data})

    # ---- daily bars ------------------------------------------------------
    def pricehistory(self, params, caller) -> Served:
        daily = (str(params.get("frequencyType")) == "daily"
                 and str(params.get("needExtendedHoursData", "false")).lower() == "false")
        mode = self._mode("bars") if daily else "off"
        if mode == "off":
            return self._passthrough("pricehistory", "/pricehistory", params, caller)
        return self._guarded("pricehistory", "/pricehistory", params, caller,
                             lambda: self._bars(params, caller, mode))

    def _today_quote(self, symbol, cfg, now_ct):
        """The stored quote today's bar may be built from or judged against,
        or None. Only asked during the session in progress.

        A quote fetched before today's regular open still carries the PRIOR
        session's open, high and low. So the quote must be no older than the
        configured limit AND no older than the session itself."""
        opened = self._cal.regular_open_on(now_ct.date())
        limit = min(float(cfg["today_quote_max_age_sec"]),
                    (now_ct - opened).total_seconds())
        if not limit > 0:
            return None
        return self.quote_store.get(symbol, max_age=limit, now=self._clock())

    def _bars(self, params, caller, mode) -> Served:
        cfg = self._cfg.section("bars")
        now_ct = self._now_ct()
        epoch = bar_epoch(now_ct, float(cfg["settle_min"]), self._cal)
        live = epoch[1] == "live"
        key = bar_key(params)
        symbol = key[0]
        store = self.bar_store

        if mode == "shadow":
            had = store.get(key, epoch=epoch)
            data = self._fetch("/pricehistory", params)
            self._record("pricehistory", caller, "upstream")
            if had is not None:
                self._record("pricehistory", caller, "shadow_hit")
            if live:
                quote = self._today_quote(symbol, cfg, now_ct)
                verdict = compare_today_bar(data, quote, now_ct.date())
                if verdict != "no_quote":
                    self._record("pricehistory", caller, f"shadow_bar_{verdict}")
            store.put(key, data, now=self._clock(), epoch=epoch)
            return Served("pass", 0.0, data=data)

        seen = store.get(key, epoch=epoch)
        if seen is not None:
            body, fetched_at = seen
            age = self._clock() - fetched_at
            if not live:
                self._record("pricehistory", caller, "hit")
                return Served("hit", age, body=body)
            if self._cfg.today_bar() == "quote":
                quote = self._today_quote(symbol, cfg, now_ct)
                composed = (compose_today(json.loads(body), quote, now_ct.date())
                            if quote is not None else None)
                if composed is not None:
                    self._record("pricehistory", caller, "composed")
                    return Served("composed", 0.0, data=composed)
            elif age <= float(cfg["session_ttl_sec"]):
                self._record("pricehistory", caller, "hit")
                return Served("hit", age, body=body)
        with self._locks.get(("bars", key)):
            again = store.get(key, epoch=epoch)
            if again is not None and (seen is None or again[1] > seen[1]):
                self._record("pricehistory", caller, "coalesced")
                return Served("coalesced", self._clock() - again[1], body=again[0])
            data = self._fetch("/pricehistory", params)
            store.put(key, data, now=self._clock(), epoch=epoch)
            self._record("pricehistory", caller, "upstream")
        return Served("miss", 0.0, data=data)

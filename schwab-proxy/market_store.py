"""What the proxy has already fetched from Schwab, kept in memory.

Three stores (chains, quotes, daily bars) and the ``Gateway`` that decides, per
request, whether a stored copy is fresh and covers the request or whether to
call Schwab. No FastAPI and no repo imports: the handlers in ``schwab_proxy.py``
are thin adapters over ``Gateway``, and everything here is unit-testable.

Nothing here is written to disk or to Redis. A proxy restart starts empty.

Design: docs/plans/2026-10-03-market-data-store-design.md
"""
from __future__ import annotations

import itertools
import json
import logging
import math
import threading
import time
import zlib
from collections import OrderedDict
from contextlib import contextmanager
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


def _bound(value) -> int | None:
    """A store's size bound as given on one put: at least 1, or None (keep the
    bound already in force) when nothing usable was given."""
    if value is None:
        return None
    try:
        return max(1, int(value))
    except (TypeError, ValueError, OverflowError):
        return None


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
        # allow_nan=False: NaN and the infinities are not JSON, and a stored
        # body is sent as it is. A ValueError here means "do not store".
        blob = zlib.compress(
            json.dumps(strikes, separators=(",", ":"), allow_nan=False).encode(), 1)
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
    Values are left out: they move every second. (Shadow mode's verdict is
    :func:`chain_difference`, which reads more than this.)"""
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


# Header fields that do not move between two fetches of one request. The
# moving ones (the underlying's price and quote, interest rate, volatility)
# are left out: they differ on every comparison.
_STABLE_HEADER = ("symbol", "status", "strategy", "isDelayed", "isIndex")


def _has_price(payload) -> bool:
    price = payload.get("underlyingPrice")
    return _real_number(price) and price > 0


def chain_difference(stored, fresh) -> str | None:
    """The first way two chains for one request differ, in words, or None.

    Shadow mode's verdict. It reads what a stored answer must reproduce and
    nothing that moves: the stable header fields, whether there is an
    underlying price at all (Schwab sends 0.0 for a window holding no
    expiration), the full expiration keys (date AND day count), the strikes
    under each, how many contracts sit at each strike, and last the header's
    own contract count (last, so a missing expiration is named as itself)."""
    if not isinstance(stored, dict) or not isinstance(fresh, dict):
        return "one answer is not a chain"
    for field in _STABLE_HEADER:
        if stored.get(field) != fresh.get(field):
            return (f"{field}: stored {stored.get(field)!r}, "
                    f"Schwab {fresh.get(field)!r}")
    if _has_price(stored) != _has_price(fresh):
        return (f"underlyingPrice: stored {stored.get('underlyingPrice')!r}, "
                f"Schwab {fresh.get('underlyingPrice')!r}")
    for side in _SIDES:
        ours, theirs = stored.get(side), fresh.get(side)
        if not isinstance(ours, dict) or not isinstance(theirs, dict):
            if ours != theirs:
                return f"{side}: one answer has no expiration map"
            continue
        if set(ours) != set(theirs):
            only_ours = sorted(set(ours) - set(theirs))
            only_theirs = sorted(set(theirs) - set(ours))
            return (f"{side} expirations: only stored {only_ours[:3]}, "
                    f"only Schwab {only_theirs[:3]}")
        for exp_key in ours:
            a, b = ours[exp_key], theirs[exp_key]
            if not isinstance(a, dict) or not isinstance(b, dict):
                if a != b:
                    return f"{side} {exp_key}: one answer has no strike map"
                continue
            if set(a) != set(b):
                return (f"{side} {exp_key} strikes: only stored "
                        f"{sorted(set(a) - set(b))[:3]}, only Schwab "
                        f"{sorted(set(b) - set(a))[:3]}")
            for strike in a:
                na = len(a[strike]) if isinstance(a[strike], list) else None
                nb = len(b[strike]) if isinstance(b[strike], list) else None
                if na != nb:
                    return (f"{side} {exp_key} strike {strike}: stored {na} "
                            f"contracts, Schwab {nb}")
    if stored.get("numberOfContracts") != fresh.get("numberOfContracts"):
        return (f"numberOfContracts: stored {stored.get('numberOfContracts')!r}, "
                f"Schwab {fresh.get('numberOfContracts')!r}")
    return None


# The widest held window a near miss may refetch in place of the one asked for.
# The refetch exists for the collector's one-week window, so the ceiling IS that
# window: at 10 the collector's own 7-day request for a symbol would be turned
# into the held 10-day term-structure window, or a 9-day Strategy Finder one.
# The built-in value; the gateway passes ``[chains] wide_refetch_max_days``.
WIDE_REFETCH_MAX_DAYS = 7


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
            header = {k: v for k, v in payload.items() if k not in _SIDES}
            json.dumps(header, allow_nan=False)     # rendered later, checked now
            entry = _ChainEntry(
                key=key, fetched_at=now, state=state, header=header,
                calls=_pack_side(calls), puts=_pack_side(puts))
        except (ValueError, TypeError):
            return False
        bound = _bound(max_entries)
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
        """The NEWEST fresh answer held for ``key``: its own entry (``hit``)
        or a cut of a wider one that covers it (``subset``). A fresh entry of
        its own does not stop a newer covering chain being served: the
        collector's week, 80 seconds old, must not be handed back while the
        scan's 45-day chain for the symbol is 10 seconds old. On a tie the
        entry of its own wins."""
        with self._lock:
            exact = self._entries.get(key)
            others = ([e for k, e in self._entries.items()
                       if k.symbol == key.symbol and k != key]
                      if key.plain else [])
        best = exact if (exact is not None
                         and self._fresh(exact, max_age, now, state)) else None
        for e in others:
            if (e.key.covers(key) and self._fresh(e, max_age, now, state)
                    and _keeps_any(e, key.from_date, key.to_date)):
                if best is None or e.fetched_at > best.fetched_at:
                    best = e
        if best is None:
            return None
        if best is exact:
            return Served("hit", now - exact.fetched_at, body=_render(exact))
        return Served("subset", now - best.fetched_at,
                      body=_render(best, key.from_date, key.to_date))

    def wide_key(self, key: ChainKey, *, today,
                 max_days=WIDE_REFETCH_MAX_DAYS) -> ChainKey | None:
        """The wider window to fetch INSTEAD of ``key``, or None to fetch
        ``key`` as asked.

        It is the narrowest plain window already held for this symbol, at ANY
        age, that starts today and covers ``key`` - in practice the collector's
        today -> +7. Derived from what is held rather than configured, so it
        cannot drift from the window the collector actually asks for. A window
        that started on an earlier day is never reused: refetching it would ask
        Schwab for expirations in the past. Nor is one longer than ``max_days``
        (``WIDE_REFETCH_MAX_DAYS`` when that is not a usable number), or one
        that held no expiration inside ``key``'s window (its cut would be
        empty, so the wide fetch would be followed by a second one)."""
        if not key.plain:
            return None
        if not _real_number(max_days) or max_days < 0:
            max_days = WIDE_REFETCH_MAX_DAYS
        start = today.isoformat()
        with self._lock:
            held = [e for k, e in self._entries.items()
                    if k.symbol == key.symbol and k != key
                    and k.from_date == start and k.covers(key)]
        usable = []
        for e in held:
            days = e.key.days()
            if (days is not None and days <= max_days
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

    def put_many(self, payload, *, now: float, max_symbols=None) -> None:
        """Keep every quote block in one reply. ``max_symbols`` is the bound to
        enforce on THIS put (the gateway passes the configured value, so a
        saved setting applies without a restart)."""
        if not isinstance(payload, dict):
            return
        bound = _bound(max_symbols)
        with self._lock:
            if bound is not None:
                self._max = bound
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
    """``(session date, "pre" | "live" | "closing" | "settled")``.

    ``cal`` is ``shared.market_calendar`` (or a stand-in with the same four
    calls). An entry is served only inside the period it was fetched in, so
    each boundary refetches once — which is also what picks up a split
    adjustment to the history.

    ``closing`` runs from the regular close until the bar settles. It was part
    of ``live``, so a series fetched at 14:45 was served at 15:05 as the day's
    bar (506.25 against a 506.50 close - audit AC-101). Nothing is served from
    the store during it: see ``Gateway._bars``."""
    d = now_ct.date()
    if cal.is_trading_day(d):
        close = cal.regular_close_on(d)
        settled_at = close + timedelta(minutes=float(settle_min))
        if now_ct >= settled_at:
            return (d.isoformat(), "settled")
        if now_ct >= close:
            return (d.isoformat(), "closing")
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
    """Shadow verdict on whether the quote-built bar agrees with Schwab's on
    its four PRICES (open, high, low, close): ``match`` / ``mismatch`` /
    ``no_today`` (Schwab sent no bar for today) / ``no_quote``. Volume has a
    verdict of its own, :func:`compare_today_volume`."""
    composed = compose_today(upstream, quote, today)
    if composed is None:
        return "no_quote"
    candles = (upstream or {}).get("candles") or []
    if not candles or _candle_date(candles[-1]) != today:
        return "no_today"
    theirs, ours = candles[-1], composed["candles"][-1]
    for field in ("open", "close", "high", "low"):
        a, b = theirs.get(field), ours[field]
        if not _real_number(a) or abs(a - b) > _BAR_TOLERANCE * abs(b):
            return "mismatch"
    return "match"


# How far a STORED today's bar sits from the one Schwab sends now, as a share
# of the fresh close. 50 basis points is ``_BAR_TOLERANCE``, the standard a
# quote-built bar is held to; 10 is a finer band below it.
_MOVING_SMALL, _MOVING_LARGE = 0.001, _BAR_TOLERANCE


def compare_moving_bar(stored, fresh, today) -> str | None:
    """Shadow verdict on the one bar :func:`series_difference` does not judge:
    today's, during the session in progress. ``same`` / ``under_10bp`` /
    ``under_50bp`` / ``over_50bp`` - how far the STORED close sits from the
    close Schwab sends now. None when either series does not end on a bar for
    ``today``, or a close is not a usable number.

    That bar legitimately moves, so it cannot count against the series
    verdict. But it is the ONLY bar a stored series can be stale on: a repeat
    inside the session recorded ``shadow_hit_match`` while ``on`` would have
    answered with a close minutes old (audit AC-100: 501.50 held, 501.97
    fresh). This is the outcome that shows it, by size.
    """
    ours = stored.get("candles") if isinstance(stored, dict) else None
    theirs = fresh.get("candles") if isinstance(fresh, dict) else None
    if not ours or not theirs or not isinstance(ours, list) or not isinstance(theirs, list):
        return None
    a, b = ours[-1], theirs[-1]
    if not isinstance(a, dict) or not isinstance(b, dict):
        return None
    if _candle_date(a) != today or _candle_date(b) != today:
        return None
    held, now = a.get("close"), b.get("close")
    if not _real_number(held) or not _real_number(now) or now <= 0:
        return None
    moved = abs(held - now) / now
    if moved == 0:
        return "same"
    if moved < _MOVING_SMALL:
        return "under_10bp"
    return "under_50bp" if moved <= _MOVING_LARGE else "over_50bp"


# Today's volume only grows, and the quote may be up to two minutes OLDER than
# Schwab's bar. So the quote's volume may fall short of the bar's by what trades
# in two minutes (about 0.5% of an even session; far more in the first minutes),
# and should never be meaningfully ahead of it.
VOLUME_MAX_SHORTFALL = 0.05     # the quote's volume may be up to 5% below the bar's
VOLUME_MAX_EXCESS = 0.01        # and at most 1% above it


def compare_today_volume(upstream, quote, today) -> str | None:
    """Shadow verdict on the volume a quote-built bar would WRITE for
    ``today`` against the volume of Schwab's bar: ``match`` / ``mismatch``, or
    None when there is nothing to judge - Schwab sent no bar for today, or
    neither side carries a volume (an index).

    A quote with no usable volume makes :func:`compose_today` write 0. Against
    a bar of Schwab's that HAS volume that is a mismatch, and it must be
    reported: quote mode would zero today's volume for that symbol.

    Kept apart from :func:`compare_today_bar` so that a symbol with no volume
    on either side still gets its price verdict."""
    candles = upstream.get("candles") if isinstance(upstream, dict) else None
    if not candles or _candle_date(candles[-1]) != today:
        return None
    theirs = candles[-1].get("volume")
    block = quote.get("quote") if isinstance(quote, dict) else None
    quoted = block.get("totalVolume") if isinstance(block, dict) else None
    if not _real_number(quoted) or quoted <= 0:
        # The bar would be written with no volume. Wrong only when Schwab's has some.
        return "mismatch" if _real_number(theirs) and theirs > 0 else None
    if not _real_number(theirs):
        return "mismatch"
    lowest = theirs * (1 - VOLUME_MAX_SHORTFALL)
    highest = theirs * (1 + VOLUME_MAX_EXCESS)
    return "match" if lowest <= quoted <= highest else "mismatch"


_CANDLE_FIELDS = ("open", "high", "low", "close", "volume")


_SHOWN_MAX = 30          # characters of one value in a described difference
_DIFFERENCE_MAX = 120    # characters of the whole description: one log line


def _shown(value) -> str:
    """One value for a log line, cut short however large it is."""
    try:
        text = repr(value)
    except Exception:  # noqa: BLE001 — an int too long to print, for one.
        text = f"<{type(value).__name__}>"
    return text if len(text) <= _SHOWN_MAX else text[:_SHOWN_MAX - 3] + "..."


def series_difference(stored, fresh, *, moving=None) -> str | None:
    """The FIRST difference between a stored daily series and the one Schwab
    sends now, in a few words for the log, or None when they are the same
    series: the same bars on the same days with the same open, high, low,
    close and volume. ``stored`` is named first, ``fresh`` second.

    ``moving`` is today's date during the session in progress. Today's bar
    legitimately moves then, so when the last bar is today's its values are
    not compared; its presence and its day still are. Anything else that
    differs is a real difference: a revised bar, a split-adjusted history, a
    bar added or dropped.

    What an operator reads it for: ``length 251 vs 252`` is a bar added
    (today's, appearing after a first fetch at the open); ``bar 0 (oldest):
    datetime ...`` is the window's first bar sliding; ``last bar: volume ...``
    is a late revision to the settled bar."""
    ours = stored.get("candles") if isinstance(stored, dict) else None
    theirs = fresh.get("candles") if isinstance(fresh, dict) else None
    if not isinstance(ours, list) or not isinstance(theirs, list):
        return "not a series"
    if len(ours) != len(theirs):
        return f"length {len(ours)} vs {len(theirs)}"
    last = len(ours) - 1

    def at(i, what):
        where = ("last bar" if i == last else "bar 0 (oldest)" if i == 0
                 else f"bar {last - i} from the end")
        return f"{where}: {what}"[:_DIFFERENCE_MAX]

    for i, (a, b) in enumerate(zip(ours, theirs)):
        if not isinstance(a, dict) or not isinstance(b, dict):
            return at(i, "not a bar")
        if a.get("datetime") != b.get("datetime"):
            day_a, day_b = _candle_date(a), _candle_date(b)
            if day_a is not None and day_b is not None and day_a != day_b:
                return at(i, f"datetime {day_a.isoformat()} vs {day_b.isoformat()}")
            return at(i, f"datetime {_shown(a.get('datetime'))} vs "
                         f"{_shown(b.get('datetime'))}")
        if i == last and moving is not None and _candle_date(b) == moving:
            continue
        for field in _CANDLE_FIELDS:
            if a.get(field) != b.get(field):
                return at(i, f"{field} {_shown(a.get(field))} vs {_shown(b.get(field))}")
    return None


def session_slot(key, at: float, ttl: float) -> int:
    """Which reuse window of length ``ttl`` the moment ``at`` falls in, for the
    series ``key``.

    Each series has its own window boundaries: they are offset by a stable
    hash of the key (``crc32``, not ``hash()``, which Python salts per
    process). A series is reused only inside the window it was fetched in, so
    it is never older than ``ttl``, and the series one quarter-hour scan
    fetched together do not all age out together (audit PF-100)."""
    offset = zlib.crc32(repr(key).encode()) % max(1, int(ttl))
    return int((at + offset) // ttl)


def series_agree(stored, fresh, *, moving=None) -> bool:
    """Whether a stored daily series is the one Schwab sends now. Exactly
    ``series_difference(...) is None``: one comparison, read two ways."""
    return series_difference(stored, fresh, moving=moving) is None


class BarStore:
    """Daily price series, one per (symbol, range), valid for one bar period.
    Bounded: past ``max_entries`` the oldest-stored series are dropped."""

    def __init__(self, max_entries: int = 4000):
        self._lock = threading.Lock()
        self._entries: "OrderedDict[tuple, tuple]" = OrderedDict()
        self._max = max(1, int(max_entries))

    def put(self, key, payload, *, now: float, epoch, max_entries=None) -> None:
        """Keep one series. ``max_entries`` is the bound to enforce on THIS put
        (the gateway passes the configured value)."""
        if not isinstance(payload, dict) or not payload.get("candles"):
            return          # an empty series is refetched, never re-served
        try:
            text = json.dumps(payload, separators=(",", ":"), allow_nan=False)
        except (ValueError, TypeError):
            return          # NaN or infinity is not JSON: never stored
        blob = zlib.compress(text.encode(), 1)
        bound = _bound(max_entries)
        with self._lock:
            if bound is not None:
                self._max = bound
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

# The most a caller's own age limit can ask for. The longest legitimate request
# is the collector's few minutes; a typo such as 1e12 must not make stale data
# look fresh.
MAX_REQUEST_AGE_SEC = 3600


def effective_max_age(requested, cfg, *, closed: bool) -> float:
    """The caller's ``maxAge`` when it is a usable number (at most
    ``MAX_REQUEST_AGE_SEC``), else the configured limit for the current market
    state. The hint is optional advice: text, NaN, infinity or a negative
    number all mean "none given"."""
    if requested is not None:
        try:
            value = float(requested)
        except (TypeError, ValueError, OverflowError):
            value = float("nan")
        if math.isfinite(value) and value >= 0:
            return min(value, float(MAX_REQUEST_AGE_SEC))
    return float(cfg["closed_max_age_sec"] if closed else cfg["max_age_sec"])


def _request_age(requested):
    """A caller's ``maxAge`` as seconds, or None for "none given": text, NaN,
    infinity and negatives are all none. Capped at ``MAX_REQUEST_AGE_SEC``."""
    if requested is None:
        return None
    try:
        value = float(requested)
    except (TypeError, ValueError, OverflowError):
        return None
    if not (math.isfinite(value) and value >= 0):
        return None
    return min(value, float(MAX_REQUEST_AGE_SEC))


# Statuses that would be the same for the request as asked: not authorized,
# and rate limited (where a second call also adds to the load).
_SAME_AGAIN = (401, 429)


class _FetchFailed(Exception):
    """``fetch`` raised something that is not an ``UpstreamError``.

    Tagged at the one place ``fetch`` is called, so the gateway can tell a
    failure to reach Schwab from a bug in its own store code. Never leaves the
    gateway: the caller gets ``original``."""

    def __init__(self, original):
        super().__init__(repr(original))
        self.original = original


class KeyedLocks:
    """One lock per request identity, so identical concurrent misses make one
    upstream call.

    Keys carry dates, so the map grows by a few hundred a day and the proxy
    runs for weeks. ``prune`` drops the keys nobody is using. It can tell:
    every request is counted from before it starts waiting until after it
    lets go, so a lock that is held, or that anyone is waiting on, is never
    dropped."""

    def __init__(self):
        self._guard = threading.Lock()
        self._locks: dict = {}    # key -> [lock, requests holding or awaiting it]

    @contextmanager
    def holding(self, key):
        """Hold ``key``'s lock for the length of the ``with`` block."""
        with self._guard:
            entry = self._locks.setdefault(key, [threading.Lock(), 0])
            entry[1] += 1
        try:
            with entry[0]:
                yield
        finally:
            with self._guard:
                entry[1] -= 1

    def prune(self) -> list:
        """Drop every key no request holds or waits on. Returns them."""
        with self._guard:
            idle = [key for key, entry in self._locks.items() if entry[1] == 0]
            for key in idle:
                del self._locks[key]
        return idle


class Gateway:
    """Decides, per request, between a stored answer and a call to Schwab.

    ``fetch(endpoint, params)`` returns Schwab's JSON or raises
    ``UpstreamError``. ``config`` is ``shared.marketdata_config`` (``mode()``,
    ``store_on(name)``, ``section(name)``, ``today_bar()``). ``calendar`` is
    ``shared.market_calendar``. ``record(endpoint, caller, outcome)`` is the
    detail counter.

    Two rules hold everywhere: an upstream error is raised, never papered over
    with an old entry; and a bug in store code falls through to a plain fetch
    and is counted in ``degrades``. A failure of ``fetch`` itself is neither:
    whatever it raised reaches the caller as it was, after one call. And a
    store bug AFTER the fetch never costs the answer already in hand or a
    second call: it is counted, logged, and the caller gets what Schwab sent.
    (One exception: a wider window was fetched and cannot be cut without the
    store, so the request is then fetched as asked.)

    The wider window on a near miss is this gateway's choice, so its failure
    is not the caller's answer: the request is then fetched once as it was
    asked (outcome ``wide_failed``, then ``upstream``), and only that call's
    failure is raised. A 401 or a 429 is raised at once: the second call
    would say the same.

    Every stored entry is stamped with the moment its fetch BEGAN, the
    conservative age: the data cannot be newer than the request for it.

    Shadow mode always calls Schwab and returns Schwab's answer, and its counts
    are read as "calls mode on would have saved". So it makes on's decision
    with on's limits, and it leaves the store as on would have: a request on
    would have answered locally stores nothing, so the held entry keeps
    ageing. Outcomes recorded in shadow, after ``upstream``:

    * chains - ``shadow_hit_match`` / ``shadow_hit_mismatch`` /
      ``shadow_subset_match`` / ``shadow_subset_mismatch``: on would have
      answered, and whether that answer is the one Schwab sends now in every
      respect that does not move (:func:`chain_difference`; the first
      mismatch per request logs what differed).
      ``shadow_cmp_match`` / ``shadow_cmp_mismatch``: on would NOT have
      answered; an entry inside ``shadow_compare_max_age_sec`` was compared
      anyway. A comparison, never a saving.
    * quotes - ``shadow_hit`` (every symbol was fresh) / ``shadow_partial``
      (some were; on would have fetched the rest).
    * daily bars - ``shadow_hit_match`` / ``shadow_hit_mismatch``: on would
      have served the stored series, and whether it is the series Schwab sends
      now (:func:`series_difference`; the first mismatch per series logs what
      differed). ``shadow_composed``: on would have built
      today's bar from the quote. And the today's-bar verdicts, which claim no
      saving: ``shadow_bar_match`` / ``shadow_bar_mismatch`` /
      ``shadow_bar_no_today`` on the four prices, and
      ``shadow_bar_volume_match`` / ``shadow_bar_volume_mismatch`` on volume.

    Two things shadow cannot reproduce, both of which make it count LOW: the
    wide refetch on a near miss (it fetches the request as asked), and
    concurrent identical requests sharing one call.

    Called from many worker threads at once. A per-request lock is held only
    around the re-check and the upstream fetch, and ``fetch`` is never called
    while a store's own lock is held."""

    def __init__(self, *, fetch, config, calendar, record,
                 clock=time.time, now_ct=None, log=None):
        self._upstream, self._cfg, self._cal = fetch, config, calendar
        self._record, self._clock = record, clock
        self._now_ct = now_ct or (lambda: datetime.now(CT))
        self._log = log or logging.getLogger("market_store")
        self._locks = KeyedLocks()
        self._degrade_lock = threading.Lock()
        # Order of arrival, for requests that wait on one another. A counter,
        # not the clock: two readings of a clock can be equal.
        self._tickets = itertools.count(1)
        # {lock key: (ticket when the fetch failed, what failed)}, where what
        # failed is (status_code, detail) for an UpstreamError and the tagged
        # exception for anything else. Read and written only while holding
        # that key's lock.
        self._failures: dict = {}
        # What has already been warned about, so a difference that repeats on
        # every request is one log line. The counter carries the count.
        self._warned: set = set()
        self._warned_lock = threading.Lock()
        # The Central date the per-day state below was last pruned on.
        self._day = None
        self._day_lock = threading.Lock()
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

    def _fetch(self, endpoint, params):
        """The one call to Schwab. Anything it raises that is not an
        ``UpstreamError`` is tagged, so it is never read as a store bug."""
        try:
            return self._upstream(endpoint, params)
        except UpstreamError:
            raise
        except Exception as e:  # noqa: BLE001 — re-raised to the caller by _answer.
            raise _FetchFailed(e) from e

    def _fetch_once(self, lock_key, ticket, endpoint, params):
        """Call Schwab for the holder of ``lock_key``'s lock: ``(data, the
        moment the call began)``.

        When the call fails, every request already waiting on the lock gets the
        same error instead of making the call again one after another. A
        request that arrives after the failure calls Schwab itself."""
        failed = self._failures.get(lock_key)
        if failed is not None and ticket < failed[0]:
            what = failed[1]              # it failed while this request waited
            if isinstance(what, _FetchFailed):
                raise _FetchFailed(what.original)
            raise UpstreamError(*what)
        began = self._clock()
        try:
            data = self._fetch(endpoint, params)
        except UpstreamError as error:
            # Its status and detail, not the exception: an exception keeps its
            # traceback, and every frame in it, alive until the next success.
            self._failures[lock_key] = (next(self._tickets),
                                        (error.status_code, error.detail))
            raise
        except _FetchFailed as error:
            self._failures[lock_key] = (next(self._tickets), error)
            raise
        self._failures.pop(lock_key, None)
        return data, began

    def _passthrough(self, label, endpoint, params, caller) -> Served:
        data = self._fetch(endpoint, params)
        self._record(label, caller, "upstream")
        return Served("pass", 0.0, data=data)

    def _degraded(self, area: str, *, answered: bool = False) -> None:
        """Count and log a bug in store code. ``answered``: it happened after
        the upstream fetch, and the caller is given what was fetched."""
        with self._degrade_lock:      # worker threads degrade concurrently
            self.degrades[area] = self.degrades.get(area, 0) + 1
        self._log.warning(
            "market store degraded in %s after the fetch; answering with what "
            "Schwab sent" if answered else
            "market store degraded in %s; fetching directly", area, exc_info=True)

    def _new_day(self, today) -> None:
        """Drop the per-request state nobody is using when the Central date
        changes. Lock keys and failure records carry request dates and what
        was warned about is per request, so all three grow without this; the
        proxy runs for weeks.

        Safe while requests are in flight: a lock that is held or awaited is
        kept (``KeyedLocks.prune``), and so is its failure record, which its
        waiters are about to read."""
        if today == self._day:
            return
        with self._day_lock:
            if today == self._day:
                return
            self._day = today
        for key in self._locks.prune():
            self._failures.pop(key, None)
        with self._warned_lock:
            self._warned.clear()          # so a difference is logged once a day

    def _warn_once(self, token, message, *args) -> None:
        """Log ``message`` at WARNING the first time ``token`` is seen."""
        with self._warned_lock:
            if token in self._warned:
                return
            self._warned.add(token)
        self._log.warning(message, *args)

    def _guarded(self, label, endpoint, params, caller, work) -> Served:
        try:
            return work()
        except (UpstreamError, _FetchFailed):
            raise                         # Schwab or the network, not the store
        except Exception:  # noqa: BLE001 — a store bug must not take data down.
            self._degraded(label)
            return self._passthrough(label, endpoint, params, caller)

    def _answer(self, label, endpoint, params, caller, mode, work) -> Served:
        """One request: straight through when the store is off, else ``work``
        with the store-bug fallback. Whatever ``fetch`` itself raised leaves
        here as the original exception."""
        try:
            if mode == "off":
                return self._passthrough(label, endpoint, params, caller)
            return self._guarded(label, endpoint, params, caller, work)
        except _FetchFailed as tagged:
            original = tagged.original
        # Raised outside the handler, so the original keeps its own cause and
        # context instead of gaining the tag as one.
        raise original

    # ---- chains ----------------------------------------------------------
    def chains(self, params, caller, max_age=None) -> Served:
        mode = self._mode("chains")
        return self._answer("chains", "/chains", params, caller, mode,
                            lambda: self._chains(params, caller, max_age, mode))

    def _chains(self, params, caller, max_age, mode) -> Served:
        cfg = self._cfg.section("chains")
        now_ct = self._now_ct()
        state = self._cal.session_at(now_ct).name
        key = ChainKey.from_params(params)
        store = self.chain_store
        self._new_day(now_ct.date())

        limit = effective_max_age(max_age, cfg, closed=(state == "CLOSED"))

        if mode == "shadow":
            asked_at = self._clock()
            would = store.lookup(key, max_age=limit, now=asked_at, state=state)
            # On would have fetched. Is there still an entry worth comparing?
            compare = None if would is not None else store.lookup(
                key, max_age=float(cfg["shadow_compare_max_age_sec"]),
                now=asked_at, state=state)
            data = self._fetch("/chains", params)
            try:
                self._record("chains", caller, "upstream")
                held = would or compare
                if held is not None:
                    differs = chain_difference(json.loads(held.body), data)
                    same = differs is None
                    verdict = "match" if same else "mismatch"
                    name = held.kind if would is not None else "cmp"
                    outcome = f"shadow_{name}_{verdict}"
                    self._record("chains", caller, outcome)
                    if not same:
                        # Once per request and outcome: a cut that differs
                        # systematically differs on every request.
                        self._warn_once(("chains", key, outcome),
                                        "shadow: the stored %s answer for %s differs "
                                        "from Schwab's (%s): %s",
                                        held.kind, key, outcome, differs)
                if would is None:
                    # On would have fetched and stored this. When on would have
                    # answered locally there was no fetch: the entry keeps ageing.
                    store.put(key, data, now=asked_at, state=state,
                              max_entries=cfg["max_entries"])
            except Exception:  # noqa: BLE001 — never costs the answer in hand.
                self._degraded("chains", answered=True)
            return Served("pass", 0.0, data=data)

        hit = store.lookup(key, max_age=limit, now=self._clock(), state=state)
        if hit is not None:
            self._record("chains", caller, hit.kind)
            return hit
        wide = store.wide_key(key, today=now_ct.date(),
                              max_days=cfg["wide_refetch_max_days"])
        if wide is None:
            return self._chain_as_asked(key, caller, state, limit, cfg)
        lock_key = ("chains", wide)
        ticket = next(self._tickets)
        failed, kept = False, False
        with self._locks.holding(lock_key):
            again = store.lookup(key, max_age=limit, now=self._clock(), state=state)
            if again is not None:
                self._record("chains", caller, "coalesced")
                return Served("coalesced", again.age, body=again.body)
            try:
                data, began = self._fetch_once(lock_key, ticket, "/chains",
                                               wide.params())
            except (UpstreamError, _FetchFailed) as error:
                if (isinstance(error, UpstreamError)
                        and error.status_code in _SAME_AGAIN):
                    raise
                failed = True
            else:
                try:
                    kept = store.put(wide, data, now=began, state=state,
                                     max_entries=cfg["max_entries"])
                except Exception:  # noqa: BLE001 — never costs the answer in hand.
                    # A wider window cannot be cut without the store, so the
                    # request is fetched as asked below.
                    self._degraded("chains")
                self._record("chains", caller, "upstream")
        if failed:
            # The week was this gateway's choice, not the caller's, and every
            # request waiting on it was handed its one failure. Each is now
            # fetched once exactly as it was asked, outside the week's lock.
            self._record("chains", caller, "wide_failed")
            return self._chain_as_asked(key, caller, state, limit, cfg)
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

    def _chain_as_asked(self, key, caller, state, limit, cfg) -> Served:
        """Fetch ``key`` exactly as it was asked and keep it under its own
        request. Identical concurrent requests share the one call."""
        store = self.chain_store
        lock_key = ("chains", key)
        ticket = next(self._tickets)
        with self._locks.holding(lock_key):
            again = store.lookup(key, max_age=limit, now=self._clock(), state=state)
            if again is not None:
                self._record("chains", caller, "coalesced")
                return Served("coalesced", again.age, body=again.body)
            data, began = self._fetch_once(lock_key, ticket, "/chains", key.params())
            try:
                store.put(key, data, now=began, state=state,
                          max_entries=cfg["max_entries"])
            except Exception:  # noqa: BLE001 — never costs the answer in hand.
                self._degraded("chains", answered=True)
            self._record("chains", caller, "upstream")
        return Served("miss", 0.0, data=data)

    # ---- quotes ----------------------------------------------------------
    def quotes(self, symbols_csv, caller, max_age=None) -> Served:
        params = {"symbols": symbols_csv, "fields": "quote"}
        mode = self._mode("quotes")
        return self._answer("quotes", "/quotes", params, caller, mode,
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
        asked_at = self._clock()          # the stamp of anything fetched below
        fresh, missing, oldest = store.split(symbols, max_age=limit, now=asked_at)

        def call(wanted):
            return self._fetch("/quotes", {"symbols": ",".join(wanted),
                                           "fields": "quote"})

        if mode == "shadow":
            data = call(symbols)
            try:
                self._record("quotes", caller, "upstream")
                if not missing:
                    self._record("quotes", caller, "shadow_hit")
                else:
                    if fresh:
                        self._record("quotes", caller, "shadow_partial")
                    # On would have fetched, and so stored, only the symbols
                    # that were not fresh. The fresh ones keep ageing.
                    fetched = ({k: v for k, v in data.items() if k not in fresh}
                               if isinstance(data, dict) else data)
                    store.put_many(fetched, now=asked_at,
                                   max_symbols=cfg["max_symbols"])
            except Exception:  # noqa: BLE001 — never costs the answer in hand.
                self._degraded("quotes", answered=True)
            return Served("pass", 0.0, data=data)

        if not missing:
            self._record("quotes", caller, "hit")
            return Served("hit", oldest, data={s: fresh[s] for s in symbols})
        data = call(missing)
        try:
            store.put_many(data, now=asked_at, max_symbols=cfg["max_symbols"])
        except Exception:  # noqa: BLE001 — never costs the answer in hand.
            self._degraded("quotes", answered=True)
        if not fresh:
            self._record("quotes", caller, "upstream")
            return Served("miss", 0.0, data=data)     # Schwab's answer, untouched
        self._record("quotes", caller, "partial")
        if not isinstance(data, dict):
            return Served("partial", oldest, data=data)
        # The caller's order, then whatever else Schwab sent (its bucket of
        # invalid symbols). As old as the oldest symbol that was not fetched.
        merged = {**fresh, **data}
        answer = {s: merged[s] for s in symbols if s in merged}
        answer.update((k, v) for k, v in merged.items() if k not in answer)
        return Served("partial", oldest, data=answer)

    # ---- daily bars ------------------------------------------------------
    def pricehistory(self, params, caller, max_age=None) -> Served:
        """``max_age`` is the caller's own limit on a stored answer, in seconds
        (0 = fetch). It can only tighten the store's rules, never loosen them."""
        daily = (str(params.get("frequencyType")) == "daily"
                 and str(params.get("needExtendedHoursData", "false")).lower() == "false")
        mode = self._mode("bars") if daily else "off"
        return self._answer("pricehistory", "/pricehistory", params, caller, mode,
                            lambda: self._bars(params, caller, mode, max_age))

    def _today_quote(self, symbol, cfg, now_ct):
        """``(quote, its age)`` for the stored quote today's bar may be built
        from or judged against, or ``(None, 0.0)``. Only asked during the
        session in progress.

        A quote fetched before today's regular open still carries the PRIOR
        session's open, high and low. So the quote must be no older than the
        configured limit AND no older than the session itself.

        From the regular close until the bar settles there is no usable quote
        either: its last price can include prints after the close, which the
        day's bar does not."""
        today = now_ct.date()
        if now_ct >= self._cal.regular_close_on(today):
            return None, 0.0
        limit = min(float(cfg["today_quote_max_age_sec"]),
                    (now_ct - self._cal.regular_open_on(today)).total_seconds())
        if not limit > 0:
            return None, 0.0
        fresh, _missing, age = self.quote_store.split(
            [symbol], max_age=limit, now=self._clock())
        return fresh.get(symbol), age

    def _bar_local(self, seen, live, key, cfg, now_ct) -> Served | None:
        """The answer mode on gives from the stored series ``seen``, or None
        when it fetches. Records nothing: on and shadow both ask it, so shadow
        cannot count a saving on would not make."""
        if seen is None:
            return None
        body, fetched_at = seen
        now = self._clock()
        age = now - fetched_at
        if not live:
            return Served("hit", age, body=body)
        if self._cfg.today_bar() == "quote":
            quote, quote_age = self._today_quote(key[0], cfg, now_ct)
            composed = (compose_today(json.loads(body), quote, now_ct.date())
                        if quote is not None else None)
            if composed is not None:
                # As old as its newest part: the quote the last bar came from.
                return Served("composed", quote_age, data=composed)
        # No usable quote, or ttl mode: the series as fetched, inside the
        # session limit. So quote mode is never worse than ttl mode.
        ttl = float(cfg["session_ttl_sec"])
        if not age <= ttl:
            return None
        # ``is True``: a missing or mistyped setting leaves the plain limit.
        if (cfg.get("session_spread") is True and ttl >= 1
                and session_slot(key, now, ttl) != session_slot(key, fetched_at, ttl)):
            return None
        return Served("hit", age, body=body)

    def _bars(self, params, caller, mode, max_age=None) -> Served:
        cfg = self._cfg.section("bars")
        now_ct = self._now_ct()
        epoch = bar_epoch(now_ct, float(cfg["settle_min"]), self._cal)
        live = epoch[1] == "live"
        # Between the close and the settle the day's bar is still being
        # finalised: every request is fetched, none is answered from the store.
        closing = epoch[1] == "closing"
        limit = _request_age(max_age)
        key = bar_key(params)
        symbol = key[0]
        store = self.bar_store
        self._new_day(now_ct.date())

        if mode == "shadow":
            would = (None if closing else self._within(
                self._bar_local(store.get(key, epoch=epoch), live, key,
                                cfg, now_ct), limit))
            began = self._clock()
            data = self._fetch("/pricehistory", params)
            try:
                self._shadow_bars(would, data, began, key, epoch, cfg, now_ct, caller)
            except Exception:  # noqa: BLE001 — never costs the answer in hand.
                self._degraded("pricehistory", answered=True)
            return Served("pass", 0.0, data=data)

        seen = None if closing else store.get(key, epoch=epoch)
        local = self._within(self._bar_local(seen, live, key, cfg, now_ct), limit)
        if local is not None:
            self._record("pricehistory", caller, local.kind)
            return local
        lock_key = ("bars", key)
        ticket = next(self._tickets)
        with self._locks.holding(lock_key):
            again = None if closing else store.get(key, epoch=epoch)
            if again is not None and (seen is None or again[1] > seen[1]):
                self._record("pricehistory", caller, "coalesced")
                return Served("coalesced", self._clock() - again[1], body=again[0])
            data, began = self._fetch_once(lock_key, ticket, "/pricehistory", params)
            try:
                store.put(key, data, now=began, epoch=epoch,
                          max_entries=cfg["max_entries"])
            except Exception:  # noqa: BLE001 — never costs the answer in hand.
                self._degraded("pricehistory", answered=True)
            self._record("pricehistory", caller, "upstream")
        return Served("miss", 0.0, data=data)

    @staticmethod
    def _within(local, limit):
        """``local`` unless it is older than the caller's own ``limit``."""
        if local is None or limit is None or local.age <= limit:
            return local
        return None

    def _shadow_bars(self, would, data, began, key, epoch, cfg, now_ct, caller):
        """Shadow's work on a daily series Schwab has just sent: what on would
        have answered, whether that was right, and the store as on would have
        left it. ``would`` is on's local answer, or None."""
        live, symbol = epoch[1] == "live", key[0]
        self._record("pricehistory", caller, "upstream")
        if would is not None and would.kind == "hit":
            # On would have served the stored series. Is it the series Schwab
            # sends now? Once settled it is served all evening and all
            # weekend, so a difference here is a wrong answer for hours.
            stored = json.loads(would.body)
            difference = series_difference(stored, data,
                                           moving=now_ct.date() if live else None)
            verdict = "match" if difference is None else "mismatch"
            self._record("pricehistory", caller, f"shadow_hit_{verdict}")
            if live:
                # The verdict above skips today's bar, the one bar the stored
                # series can be stale on. Judge it on its own.
                moved = compare_moving_bar(stored, data, now_ct.date())
                if moved is not None:
                    self._record("pricehistory", caller, f"shadow_moving_{moved}")
                    if moved != "same":
                        self._warn_once(
                            ("moving", key), "shadow: on would have served %s "
                            "today's bar closing %s; Schwab now sends %s "
                            "(entry %.0fs old)", key,
                            _shown(stored["candles"][-1].get("close")),
                            _shown(data["candles"][-1].get("close")), would.age)
            if difference is not None:
                # What differed, so each mismatch can be explained: a bar
                # added, the window's first bar sliding, a revised value.
                self._warn_once(("bars", key), "shadow: the stored daily series "
                                "for %s differs from Schwab's: %s", key, difference)
        elif would is not None:
            # A composed bar's content verdict is shadow_bar_*, below.
            self._record("pricehistory", caller, f"shadow_{would.kind}")
        if live:
            quote, _age = self._today_quote(symbol, cfg, now_ct)
            verdict = compare_today_bar(data, quote, now_ct.date())
            if verdict != "no_quote":
                self._record("pricehistory", caller, f"shadow_bar_{verdict}")
                volume = compare_today_volume(data, quote, now_ct.date())
                if volume is not None:
                    self._record("pricehistory", caller,
                                 f"shadow_bar_volume_{volume}")
        if would is None:
            # On would have fetched and stored this series. Otherwise the held
            # one keeps ageing, as it would under on.
            self.bar_store.put(key, data, now=began, epoch=epoch,
                               max_entries=cfg["max_entries"])

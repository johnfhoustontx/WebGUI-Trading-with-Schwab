"""The Paper Ledger's open trades, marked live from the option stream.

In the regular session every open Ledger row is priced every
``[live_marks] interval_sec`` (3 s) from level-one quotes, the way the Portfolio
is, and the marks are published on ``cache:options:paper_live`` for the Ledger
page. With ``[live_marks] exits`` on, the operator's OWN rows are also checked
against the same exit rules the hourly pass applies (``ledger_exits``) and
closed on them. Design: docs/plans/2026-11-24-ledger-live-marks-design.md.

The arithmetic is not repeated here. A chain-shaped dict is built from the
streamed quotes and handed to the repricers' own valuation halves
(``signal_repricer.value_legs`` / ``value_swing``,
``structure_marks.value_from_chains``), so a live mark is the hourly mark's
arithmetic on newer quotes.

Rules this module keeps:

* A contract's symbol is read off a REAL chain (one request per symbol and
  expiration, when a row first appears and after every reconnect), never built:
  the root depends on the series. That answer also seeds the quotes, because a
  quiet contract the stream has not ticked yet has no quote of its own.
* A mark that cannot be made (a leg with no market, a chain not resolved yet,
  the stream down) is NO mark, never a zero, and the page falls back to the
  Ledger view's own figure.
* Only the operator's own rows are closed here. The self-managed rows are
  marked for display; their stops stay with ``idea_ledger.mark_cycle``.
* An exit fires only after the rule has said so for ``confirm_sec``, and only
  under ``paper_lock.BOOK_LOCK`` on a re-read row that is still OPEN.

Two daemon threads: the stream (one ``/stream/options`` connection, merging
partial ticks into a bid/ask book) and the evaluator. Split like
``flow_stream``: the decisions are pure and unit-tested over injected pieces;
the real socket is verified live. It imports nothing from ``compute``.
"""
import datetime as _dt
import logging
import threading
import time
from zoneinfo import ZoneInfo

import requests

from repo_paths import PROXY_URL

from services import _degrade, _proxy, _sse
from services.options_svc import idea_ledger as _ideas  # puts options-scanner on sys.path
from services.options_svc import ledger_exits as _exits
from services.options_svc import profit_trail as _trail
from shared import market_calendar as _mc
from shared import paper_limits as _limits
from shared import structures as _structures
from shared.numeric import finite as _num

import paper_lock as _paper_lock  # noqa: E402  (the one lock for both paper books)

log = logging.getLogger(__name__)

CACHE_LIVE = "cache:options:paper_live"
EVENT_LIVE = "events:options:paper_live"
_CT = ZoneInfo("America/Chicago")

# These follow the proxy and the Ledger store, not an operator's choice, so they
# are constants rather than config (as in ``flow_stream``):
ROWS_EVERY_SEC = 15.0        # re-read the open rows this often
RESOLVE_RETRY_SEC = 60.0     # a chain that could not be read is tried again after this
RECHECK_SEC = 5.0            # a connected stream looks for a changed set this often
IDLE_POLL_SEC = 5.0          # an idle stream looks for its first contract this often
CONNECT_TIMEOUT_SEC = 10
READ_TIMEOUT_SEC = 45        # three missed 15 s keepalives: a dead proxy
RECONNECT_WAIT_SEC = 3.0
RECONNECT_WAIT_MAX_SEC = 60.0
STREAM_HARD_MAX = 400        # the whole set travels in one query string
THREAD_STREAM = "options-ledger-live-stream"
THREAD_EVAL = "options-ledger-live-marks"


# ── which contracts a row is priced from ─────────────────────────────────────

def wanted_legs(trade):
    """``[(expiration, kind, strike)]`` for every contract ``trade`` is priced
    from, or None when it cannot be priced here. PURE.

    The SAME legs the hourly mark reads: a self-managed row's ``legs`` (each on
    its own expiration, as ``structure_marks`` prices them), a DEBIT row's
    ``legs`` on the trade's expiration (``reprice_legs`` fetches that one chain),
    a CREDIT row's strike columns (``reprice_swing``)."""
    import structure_marks
    t = trade or {}
    exp = str(t.get("expiration") or "")[:10]
    if _ideas.is_self_managed(t):
        legs = structure_marks.legs_of({"legs_json": t.get("legs")})
        return ([(lg["expiration"], lg["kind"], lg["strike"]) for lg in legs]
                if legs else None)
    if not exp:
        return None
    if _exits.is_debit(t):
        out = []
        for lg in t.get("legs") or []:
            strike = _num(lg.get("strike")) if isinstance(lg, dict) else None
            kind = lg.get("kind") if isinstance(lg, dict) else None
            if kind not in ("call", "put") or strike is None:
                return None
            out.append((exp, kind, strike))
        return out or None
    strat = t.get("strategy")
    cols = {"PCS": (("put", "short_strike"), ("put", "long_strike")),
            "CCS": (("call", "short_strike"), ("call", "long_strike")),
            "IC": (("put", "short_strike"), ("put", "long_strike"),
                   ("call", "call_short"), ("call", "call_long"))}.get(strat)
    if cols is None and _structures.is_single_leg(strat):
        cols = ((("put" if _structures.is_put_side(strat) else "call"), "short_strike"),)
    if cols is None:
        return None
    out = []
    for kind, field in cols:
        strike = _num(t.get(field))
        if strike is None:
            return None
        out.append((exp, kind, strike))
    return out


def find_contract(chain, kind, strike):
    """``(strike key, contract)`` for ``kind`` at ``strike`` in ``chain``, matched
    as a NUMBER (``structure_marks._quote``'s rule), or None. PURE. The key is
    the chain's own string, so the chain this module builds is looked up exactly
    as the real one would be (``signal_repricer`` formats to one decimal)."""
    side = "callExpDateMap" if kind == "call" else "putExpDateMap"
    for strikes in ((chain or {}).get(side) or {}).values():
        for key, contracts in (strikes or {}).items():
            try:
                hit = abs(float(key) - float(strike)) < 1e-6
            except (TypeError, ValueError):
                continue
            if hit and contracts and isinstance(contracts[0], dict):
                return key, contracts[0]
    return None


# ── the quote book ──────────────────────────────────────────────────────────

class QuoteBook:
    """Contract symbols per (symbol, expiration, kind, strike), and the latest
    bid / ask per contract symbol. Thread-safe: the stream writes, the evaluator
    reads."""

    def __init__(self):
        self._lock = threading.Lock()
        self._osi = {}        # (symbol, exp, kind, strike) -> (osi, strike key)
        self._quotes = {}     # osi -> {"bid", "ask"}
        self._fresh = set()   # (symbol, exp) read since the last reconnect
        self._tried = {}      # (symbol, exp, kind, strike) -> monotonic time last looked for

    def needs_read(self, symbol, exp, legs, now, retry):
        """Must ``(symbol, exp)``'s chain be read for ``legs``? When it has not
        been read since the last reconnect, or a leg has no contract yet and was
        not looked for within ``retry`` seconds (a NEW row on an expiration
        already read is looked for at once)."""
        with self._lock:
            if (symbol, exp) not in self._fresh:
                return True
            for lexp, kind, strike in legs:
                key = (symbol, lexp, kind, float(strike))
                if key in self._osi:
                    continue
                tried = self._tried.get(key)
                if tried is None or now - tried >= retry:
                    return True
            return False

    def forget_resolution(self):
        """Every chain is read again (and its quotes re-seeded): after a
        reconnect, whatever traded while the stream was down is not in the book."""
        with self._lock:
            self._fresh.clear()
            self._tried.clear()

    def seed(self, symbol, exp, chain, legs, now):
        """Record the contracts of ``legs`` found in ``chain`` (one expiration)
        and their quotes. A leg not in the chain stays unresolved."""
        with self._lock:
            self._fresh.add((symbol, exp))
            for lexp, kind, strike in legs:
                if lexp != exp:
                    continue
                self._tried[(symbol, exp, kind, float(strike))] = now
                hit = find_contract(chain, kind, strike)
                if hit is None:
                    continue
                key, ctr = hit
                osi = ctr.get("symbol")
                if not isinstance(osi, str) or not osi:
                    continue
                self._osi[(symbol, exp, kind, float(strike))] = (osi, key)
                self._quotes[osi] = {"bid": _num(ctr.get("bid")),
                                     "ask": _num(ctr.get("ask"))}

    def merge(self, tick):
        """One streamed tick. A tick carries only the fields that changed, so a
        field it does not carry keeps its last value."""
        osi = tick.get("symbol") if isinstance(tick, dict) else None
        if not osi:
            return
        with self._lock:
            q = self._quotes.setdefault(osi, {"bid": None, "ask": None})
            for f in ("bid", "ask"):
                v = _num(tick.get(f))
                if v is not None:
                    q[f] = v

    def osis(self):
        with self._lock:
            return sorted({osi for osi, _key in self._osi.values()})

    def chain(self, symbol, exp, legs):
        """A chain-shaped dict holding ``legs``' contracts on ``exp`` that have a
        full quote, keyed by the real chain's own strike strings. A leg with no
        resolved contract or half a quote is left out, so the valuation sees it
        as no market."""
        out = {"putExpDateMap": {exp: {}}, "callExpDateMap": {exp: {}}}
        with self._lock:
            for lexp, kind, strike in legs:
                if lexp != exp:
                    continue
                hit = self._osi.get((symbol, exp, kind, float(strike)))
                if hit is None:
                    continue
                osi, key = hit
                q = self._quotes.get(osi) or {}
                if q.get("bid") is None or q.get("ask") is None:
                    continue
                side = "callExpDateMap" if kind == "call" else "putExpDateMap"
                out[side][exp][key] = [{"bid": q["bid"], "ask": q["ask"]}]
        return out


# ── a mark ───────────────────────────────────────────────────────────────────

def mark(trade, book):
    """``trade``'s live mark (the hourly mark's keys, P&L per contract), or None
    when it cannot be made. PURE but for reading ``book``."""
    import signal_repricer
    import structure_marks
    legs = wanted_legs(trade)
    if not legs:
        return None
    # A row past its (front) expiration awaits settlement, which is not a mark:
    # the hourly mark answers "expired" for it too.
    if signal_repricer._is_expired(min(lg[0] for lg in legs)):
        return None
    symbol = trade.get("symbol")
    try:
        if _ideas.is_self_managed(trade):
            chains = {exp: book.chain(symbol, exp, legs) for exp in {lg[0] for lg in legs}}
            rep = structure_marks.value_from_chains(
                {**trade, "legs_json": trade.get("legs")}, chains)
        else:
            chain = book.chain(symbol, legs[0][0], legs)
            rep = (signal_repricer.value_legs(trade, chain) if _exits.is_debit(trade)
                   else signal_repricer.value_swing(trade, chain))
    except Exception:  # noqa: BLE001 - a leg with no market raises: no mark
        return None
    if (not rep or rep.get("error") or _num(rep.get("unrealized_pnl")) is None
            or _num(rep.get("current_value")) is None):
        return None
    return rep


def display(trade, rep) -> dict:
    """What the page shows for a mark: the TOTAL P&L (per contract x quantity,
    as the Ledger view's own reprice stores it) and the per-share value."""
    try:
        qty = int(trade.get("quantity") or 1)
    except (TypeError, ValueError):
        qty = 1
    return {"unrealized_pnl": round(float(rep["unrealized_pnl"]) * qty, 2),
            "current_value": rep["current_value"]}


# ── exits ────────────────────────────────────────────────────────────────────

class ExitWatch:
    """Holds a rule's "close" until it has lasted ``confirm_sec``. A HOLD, a mark
    that could not be made, or a different rule starts it over."""

    def __init__(self):
        self._first = {}      # trade_id -> (code, monotonic time first seen)

    def observe(self, trade_id, rec, now, confirm_sec) -> bool:
        if rec is None:
            self._first.pop(trade_id, None)
            return False
        code = rec.get("code")
        seen = self._first.get(trade_id)
        if seen is None or seen[0] != code:
            self._first[trade_id] = (code, now)
            seen = self._first[trade_id]
        return now - seen[1] >= confirm_sec

    def drop(self, trade_id):
        self._first.pop(trade_id, None)

    def keep_only(self, trade_ids):
        for tid in list(self._first):
            if tid not in trade_ids:
                del self._first[tid]


class Peaks:
    """The best live P&L per trade (per contract), held between marks and
    written at most every ``peak_write_sec``. It only rises, like the stored one."""

    def __init__(self):
        self._best = {}       # trade_id -> best pnl
        self._written = {}    # trade_id -> (pnl written, monotonic time)

    def update(self, trade, pnl):
        """This mark taken into the best, which also counts the stored
        ``mfe_pnl``. Returns the best."""
        tid = trade.get("trade_id")
        best = _trail.best(trade.get("mfe_pnl"), self._best.get(tid), pnl)
        if best is not None:
            self._best[tid] = best
        return best

    def due(self, now, every):
        """``[(trade_id, pnl)]`` whose best rose since it was last written and
        whose last write is at least ``every`` seconds old."""
        out = []
        for tid, best in self._best.items():
            wrote = self._written.get(tid)
            if wrote is not None and (best <= wrote[0] or now - wrote[1] < every):
                continue
            out.append((tid, best))
        return out

    def wrote(self, trade_id, pnl, now):
        self._written[trade_id] = (pnl, now)

    def best(self, trade_id):
        return self._best.get(trade_id)

    def keep_only(self, trade_ids):
        for d in (self._best, self._written):
            for tid in list(d):
                if tid not in trade_ids:
                    del d[tid]


def protect_inputs(peak, now_ct) -> dict:
    """The trail's ctx keys for a live mark: ``profit_trail.ledger_inputs``'
    answer without its write (the best is written on ``Peaks``' schedule)."""
    import signal_recommender
    if not _trail.ledger_on():
        return {}
    return signal_recommender.protect_inputs("ledger", peak, now_ct)


def _store_peak(trade_id, pnl, now_ct) -> None:
    import trades_db
    conn = trades_db.connect()
    try:
        trades_db.raise_peak(conn, trade_id, round(pnl, 2),
                             now_ct.isoformat(timespec="seconds"))
        conn.commit()
    finally:
        conn.close()


@_paper_lock.serialized
def close_live(trade_id, rep, peak, now_ct):
    """Close ``trade_id`` on live mark ``rep`` if the rule still says so. Returns
    the exit code, or None when nothing was closed.

    Under the book lock: the row is RE-READ, must still be OPEN and the
    operator's own, and the rule is asked AGAIN on that row with the same mark,
    so a row closed by hand, by the hourly pass or by expiry since the mark was
    made is never closed twice. The held best goes in first, so the trail and the
    stored row both see it."""
    import paper_trader
    t = next((r for r in paper_trader.get_all_trades()
              if r.get("trade_id") == trade_id), None)
    if t is None or not _exits.managed(t):
        return None
    if _num(peak) is not None and _trail.ledger_on():
        _store_peak(trade_id, peak, now_ct)
        best = _trail.best(t.get("mfe_pnl"), peak)
        if best != _num(t.get("mfe_pnl")):
            t["mfe_pnl"], t["mfe_at"] = round(best, 2), now_ct.isoformat(timespec="seconds")
    rec = _exits.decide(t, rep, now_ct.date(), _trail.ledger_inputs(t, rep, now_ct))
    if rec is None:
        return None
    code = rec.get("code") or "MANAGED"
    row = paper_trader.close_paper_trade(dict(t), float(rep["current_value"]), code)
    paper_trader.update_trade(trade_id, row)
    log.info("ledger LIVE EXIT %s %s %s: %s (%s)", trade_id, t.get("symbol"),
             t.get("strategy"), code, rec.get("reason"))
    return code


# ── the evaluator ────────────────────────────────────────────────────────────

class State:
    """What the two threads share."""

    def __init__(self):
        self.book = QuoteBook()
        self.watch = ExitWatch()
        self.peaks = Peaks()
        self.connected = threading.Event()   # the stream holds a live connection
        self.rows = []
        self.rows_at = None
        self.failed = {}                     # (symbol, exp) -> monotonic time of a failed read


def open_rows():
    import paper_trader
    return [t for t in paper_trader.get_all_trades()
            if (t.get("status") or "").upper() == "OPEN"]


def _fetch_chain(client, symbol, exp):
    """One expiration's chain, fetched now (never a stored answer: it seeds the
    quotes an exit may act on). None on any failure."""
    kwargs = {"max_age": 0} if getattr(client, "supports_max_age", False) is True else {}
    day = _dt.date.fromisoformat(exp)
    r = client.get_option_chain(symbol, from_date=day, to_date=day,
                                contract_type=client.Options.ContractType.ALL, **kwargs)
    return r.json() if r.status_code == 200 else None


def resolve(state, rows, now, client, fetch=_fetch_chain):
    """Read the chain of every (symbol, expiration) the open rows need that is
    not resolved yet, and seed the book. A failed read waits
    ``RESOLVE_RETRY_SEC`` before it is tried again."""
    import signal_repricer
    need = {}
    for t in rows:
        legs = wanted_legs(t) or ()
        # A past expiration has no chain (Schwab answers 400), only a settlement.
        if not legs or signal_repricer._is_expired(min(lg[0] for lg in legs)):
            continue
        for lg in legs:
            need.setdefault((t.get("symbol"), lg[0]), []).append(lg)
    for (symbol, exp), legs in need.items():
        if not state.book.needs_read(symbol, exp, legs, now, RESOLVE_RETRY_SEC):
            continue
        failed = state.failed.get((symbol, exp))
        if failed is not None and now - failed < RESOLVE_RETRY_SEC:
            continue
        try:
            chain = fetch(client, symbol, exp)
        except Exception:  # noqa: BLE001 - one chain never stops the others
            chain = None
        if not chain:
            state.failed[(symbol, exp)] = now
            continue
        state.failed.pop((symbol, exp), None)
        state.book.seed(symbol, exp, chain, legs, now)


def evaluate(state, now_ct, now, cfg, *, client=None, on_close=None, fetch=_fetch_chain):
    """One pass: ``{trade_id: display}`` for every open row it could mark, after
    acting on any exit that is due. Off the regular session, with the switch
    off, or with the stream down, it marks nothing.

    ``now`` is a monotonic clock (the confirmation and the peak schedule);
    ``now_ct`` is Central wall time (the session and the rules)."""
    if not cfg["enabled"] or not _mc.is_regular_hours(now_ct):
        return {}
    if state.rows_at is None or now - state.rows_at >= ROWS_EVERY_SEC:
        state.rows, state.rows_at = open_rows(), now
        ids = {t.get("trade_id") for t in state.rows}
        state.watch.keep_only(ids)
        state.peaks.keep_only(ids)
    resolve(state, state.rows, now, client if client is not None else _proxy.schwab_py_client,
            fetch=fetch)
    if not state.connected.is_set():
        return {}
    marks = {}
    for t in list(state.rows):
        rep = mark(t, state.book)
        tid = t.get("trade_id")
        if rep is None:
            state.watch.drop(tid)
            continue
        marks[tid] = display(t, rep)
        if not (cfg["exits"] and _exits.managed(t)):
            continue
        peak = state.peaks.update(t, rep["unrealized_pnl"])
        rec = _exits.decide(t, rep, now_ct.date(), protect_inputs(peak, now_ct))
        if not state.watch.observe(tid, rec, now, cfg["confirm_sec"]):
            continue
        state.watch.drop(tid)
        if close_live(tid, rep, peak, now_ct):
            marks.pop(tid, None)
            state.rows = [r for r in state.rows if r.get("trade_id") != tid]
            if on_close is not None:
                on_close()
    if not _trail.ledger_on():          # ledger_inputs writes no best then either
        return marks
    for tid, pnl in state.peaks.due(now, cfg["peak_write_sec"]):
        try:
            _store_peak(tid, pnl, now_ct)
            state.peaks.wrote(tid, pnl, now)
        except Exception:  # noqa: BLE001 - a failed write costs the stored best one mark
            _degrade.degraded("options.ledger_live.peak", detail=tid)
    return marks


def publish(bus, marks) -> None:
    """``cache:options:paper_live``. ``skip_unchanged``, so an unchanged set of
    marks bumps no version and fires no repaint, and so no timestamp of its own."""
    bus.cache_set(CACHE_LIVE, {"marks": marks}, event=EVENT_LIVE, skip_unchanged=True)


def _eval_worker(bus, state, stop, on_close) -> None:
    """Mark, act, publish, every ``interval_sec``, until ``stop``. NEVER raises out."""
    while not stop.is_set():
        cfg = _limits.live_marks()
        try:
            marks = evaluate(state, _dt.datetime.now(_CT), time.monotonic(), cfg,
                             on_close=on_close)
            publish(bus, marks)
        except Exception:  # noqa: BLE001 - the loop must outlive any one pass
            _degrade.degraded("options.ledger_live.evaluate")
        stop.wait(cfg["interval_sec"])


# ── the stream ───────────────────────────────────────────────────────────────

def stream_params(osis) -> dict:
    """The query for ``/stream/options``. Contract symbols go as they are: the
    padding inside one (``"SPY   261009C00770000"``) is part of it."""
    return {"symbols": ",".join(osis)}


def _consume(session, osis, state, stop) -> bool:
    """One connection, until it ends. True when it was left because the wanted
    set changed (reconnect at once). Raises on a connection or HTTP error."""
    next_check = time.monotonic() + RECHECK_SEC
    with session.get(f"{PROXY_URL}/stream/options", params=stream_params(osis),
                     stream=True,
                     timeout=(CONNECT_TIMEOUT_SEC, READ_TIMEOUT_SEC)) as resp:
        resp.raise_for_status()
        state.connected.set()
        try:
            for raw in resp.iter_lines(decode_unicode=True):
                if stop.is_set():
                    return False
                now = time.monotonic()
                if now >= next_check:
                    next_check = now + RECHECK_SEC
                    if set(osis) != set(state.book.osis()):
                        return True
                tick = _sse.parse_sse_line(raw)
                if tick is not None:
                    state.book.merge(tick)
        finally:
            state.connected.clear()
    return False


def _wanted(state):
    osis = state.book.osis()
    if len(osis) > STREAM_HARD_MAX:
        log.warning("ledger live: %d contracts, streaming the first %d",
                    len(osis), STREAM_HARD_MAX)
        osis = osis[:STREAM_HARD_MAX]
    return osis


def _stream_worker(state, stop) -> None:
    """Hold the stream until ``stop``. Each new connection has the chains read
    again, since nothing that traded while it was down reached the book. Capped
    exponential backoff on a failure. NEVER raises out."""
    session = requests.Session()
    failures = 0
    while not stop.is_set():
        osis = _wanted(state)
        if not osis or not _limits.live_marks()["enabled"]:
            stop.wait(IDLE_POLL_SEC)
            continue
        try:
            changed = _consume(session, osis, state, stop)
        except Exception as e:  # noqa: BLE001 - the loop must outlive any one stream
            log.warning("ledger live stream disconnected (attempt %d): %s", failures + 1, e)
            state.book.forget_resolution()
            if stop.is_set():
                break
            stop.wait(_sse.reconnect_delay(failures, base=RECONNECT_WAIT_SEC,
                                           cap=RECONNECT_WAIT_MAX_SEC))
            failures += 1
            continue
        failures = 0
        if not changed:
            state.book.forget_resolution()
            if not stop.is_set():
                stop.wait(_sse.reconnect_delay(0, base=RECONNECT_WAIT_SEC,
                                               cap=RECONNECT_WAIT_MAX_SEC))


_RUNNING: dict = {"threads": (), "stop": None}


def start(bus, on_close=None) -> threading.Event:
    """Launch both threads; return their shared ``stop`` Event. ONE pair per
    process: while they are alive their own stop event is returned (the
    scheduler loop that calls this is restarted after a crash)."""
    threads = _RUNNING["threads"]
    if threads and all(th.is_alive() for th in threads):
        return _RUNNING["stop"]
    stop, state = threading.Event(), State()
    pair = (threading.Thread(target=_stream_worker, args=(state, stop), daemon=True,
                             name=THREAD_STREAM),
            threading.Thread(target=_eval_worker, args=(bus, state, stop, on_close),
                             daemon=True, name=THREAD_EVAL))
    _RUNNING.update(threads=pair, stop=stop)
    for th in pair:
        th.start()
    return stop

#!/usr/bin/env python
"""Background GEX/Charm snapshot collector.

Now runs primarily in-tool: the gamma tool auto-starts the collector on a
background thread. This script is a manual standalone fallback for running the
collector without the gamma tool open; an advisory lock (data/gex_collector.lock)
ensures only one collector runs at a time. Polls Schwab every 5 minutes on
wall-clock boundaries and writes snapshots to gex_history.db.
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import flow_skew
from gamma_tool import GammaEngine
import gex_history_db as db
import iv_analysis

TZ = ZoneInfo("America/Chicago")
# POLICY: every symbol a named UI surface renders lives in this STATIC base, not
# in `Top 20.xlsx`. That workbook is GITIGNORED, so anything load-bearing left to
# it works on this box and silently degrades on a fresh clone (or the moment
# someone edits a row). Two surfaces depend on that guarantee:
#
#   $NDX      — tools/nq_hud.py. It was already collected, but only because it
#               happens to sit in the workbook; without it the HUD degrades to
#               its QQQ proxy, whose structural call-overwriting flow can invert
#               the apparent gamma sign. (When $NDX was added the universe was 82
#               symbols before and after — it was already in the workbook, so
#               that particular change fetched no extra chains.)
#   Net Prem  — the Dealer Positioning group view (services/options_svc/
#               net_premium.py). A symbol it groups but nobody collects has no
#               premium history, so it renders as a permanently empty line.
#               test_net_premium.py pins this against SYMBOLS, not against
#               collection_symbols(), for exactly the fail-open reason above.
#
# COST of the 2026-08-05 Net Prem additions, which is NOT uniform — state both:
# the 11 SPDR sectors were collected by nothing, while IWM/DIA and the ten
# mega-caps merely happen to be in this box's workbook already. So
# collection_symbols() goes 82 -> 93 (+11) WITH the workbook, but 5 -> 28 (+23)
# on a fresh clone. Over the 440-min 1-min-poll window that is ~4.8k extra
# /chains calls/day here, ~10.1k on a clone — quote BOTH, never just the local
# one; which number applies depends on a file you cannot see in git.
# Two further costs are easy to miss: poll_once's pool overlaps the FETCH only
# (see its docstring on historically dropping ~37% of 1-min slots), so each added
# symbol also costs a SERIAL engine calc + SQLite insert inside the 60s budget;
# and each writes one gex_history.db row per poll, on a DB that has already
# needed a manual ~1 GB VACUUM.
SYMBOLS = [
    "$SPX", "$VIX", "SPY", "QQQ", "$NDX",
    # Everything below — NEW 2026-08-05, added for the Net Prem view.
    # Broad ETFs and mega-caps are usually in the workbook already (so typically
    # no extra fetch); the SPDR sectors are the genuinely new collection.
    "IWM", "DIA",
    "XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY",
    # BIG10 mega-caps (also the Net Prem "Mega-caps" group).
    "NVDA", "MSFT", "GOOGL", "AMZN", "META", "AAPL", "TSLA", "AVGO", "PLTR", "AMD",
]
POLL_INTERVAL_MIN = 1
START_HOUR, START_MIN = 8, 0
STOP_HOUR, STOP_MIN = 15, 20
# Chain-fetch pool for poll_once: modest, because the proxy still spaces upstream
# calls ~0.2s apart — the pool's job is to overlap per-call LATENCY, not to burst.
POLL_FETCH_WORKERS = 6

log = logging.getLogger("gex_collector")


def collection_symbols():
    """Dynamic collection universe: the index base (SYMBOLS) unioned with the
    scan watchlist (BASE ∪ Top 20.xlsx), deduped + order-preserving.

    SYMBOLS is listed FIRST so ``$VIX`` (absent from the watchlist) is always
    retained. Defensive: any watchlist import/read failure falls back to the
    static SYMBOLS so a poll never crashes over the watchlist."""
    try:
        import watchlist
        extra = watchlist.get_scan_symbols()
    except Exception:
        log.warning("watchlist unavailable; collecting index base only",
                    exc_info=True)
        return list(SYMBOLS)
    out, seen = [], set()
    for s in list(SYMBOLS) + list(extra or []):
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


LOCK_PATH = Path(__file__).parent / "data" / "gex_collector.lock"
# Derived from POLL_INTERVAL_MIN (defined above) so it can't silently drift if
# the poll interval changes. == 120 at POLL_INTERVAL_MIN=1.
LOCK_TTL_SEC = POLL_INTERVAL_MIN * 60 * 2  # 2 poll intervals; matches gex_status.STALE_AFTER_SEC


def read_lock(path):
    """Parse the lock file. Missing or corrupt -> None (never raises)."""
    try:
        return json.loads(Path(path).read_text())
    except (FileNotFoundError, ValueError, OSError):
        return None


def is_lock_fresh(lock, now, *, ttl=LOCK_TTL_SEC):
    """True if the lock dict's heartbeat is within ttl seconds of now."""
    if not lock:
        return False
    hb = lock.get("heartbeat")
    return hb is not None and (now - hb) <= ttl


def acquire_collector_lock(path, *, source, owner, now, ttl=LOCK_TTL_SEC):
    """Try to take ownership of the collector lock.

    Returns True (and writes our identity + heartbeat) when the lock is
    absent, stale, or already ours. Returns False (defer) when a FRESH lock
    owned by someone else exists.

    NOTE: this is an ADVISORY, best-effort lock. The check is read-then-write,
    not atomic (no O_EXCL), so a tiny TOCTOU window lets two processes both
    acquire. Correctness ultimately rests on the idempotent per-boundary
    snapshot writes in poll_once: rows share a snapped `ts`, so a concurrent
    writer replaces rather than duplicates. Do not assume hard mutual exclusion.
    """
    path = Path(path)
    existing = read_lock(path)
    if (existing and is_lock_fresh(existing, now, ttl=ttl)
            and existing.get("owner") != owner):
        return False
    _write_lock(path, source=source, owner=owner, now=now)
    return True


def wait_for_lock(path, *, source, owner, now_fn, interrupted,
                  check_interval=30, ttl=LOCK_TTL_SEC):
    """Block until we can acquire the collector lock, then return True.

    Unlike a single ``acquire_collector_lock`` call, this keeps retrying so an
    **orphaned** lock — one a previous instance was killed without releasing —
    is taken over as soon as it goes stale (past ``ttl``). A *live* foreign
    owner that keeps heartbeating is never stolen from; we simply keep waiting.

    ``now_fn()`` returns the current epoch seconds. ``interrupted(timeout)``
    sleeps up to ``timeout`` seconds and returns True if we should stop waiting
    (e.g. the window is closing) — when it returns True we give up and return
    False. This is what lets a restart-within-TTL recover instead of leaving
    the new in-tool collector idle for the whole session.
    """
    while True:
        if acquire_collector_lock(path, source=source, owner=owner,
                                  now=now_fn(), ttl=ttl):
            return True
        # A fresh foreign lock exists — wait, then re-check. We take over only
        # once it ages past ttl (previous owner stopped heartbeating).
        if interrupted(check_interval):
            return False


def touch_lock(path, *, source, owner, now):
    """Refresh the heartbeat (called after each successful poll)."""
    _write_lock(Path(path), source=source, owner=owner, now=now)


def release_lock(path, *, owner):
    """Delete the lock iff we own it (never raises)."""
    path = Path(path)
    existing = read_lock(path)
    if existing and existing.get("owner") == owner:
        try:
            path.unlink()
        except OSError:
            pass


def _write_lock(path, *, source, owner, now):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(
            {"owner": owner, "source": source, "heartbeat": now}))
    except OSError as e:
        log.warning("Could not write collector lock %s: %s", path, e)


def next_boundary(now: datetime) -> datetime:
    """Next POLL_INTERVAL_MIN-aligned wall-clock time strictly after `now`."""
    minute = (now.minute // POLL_INTERVAL_MIN + 1) * POLL_INTERVAL_MIN
    base = now.replace(second=0, microsecond=0)
    if minute >= 60:
        return base.replace(minute=0) + timedelta(hours=1)
    return base.replace(minute=minute)


def sleep_to_next_boundary() -> None:
    now = datetime.now(TZ)
    target = next_boundary(now)
    time.sleep(max(0, (target - now).total_seconds()))


def _maybe_lock(lock):
    return lock if lock is not None else contextlib.nullcontext()


def poll_once(client, engine, conn, lock=None, symbols=None, on_chain=None,
              poll_term=None) -> None:
    """Fetch + store one snapshot per symbol. Per-symbol exceptions are logged,
    not propagated, so one bad symbol doesn't kill the whole poll.

    ``symbols`` defaults to ``collection_symbols()`` (the index base unioned
    with the scan watchlist).

    ``on_chain(symbol, chain)`` — optional callback invoked with each
    successfully-fetched chain (never for failed/empty fetches), so the caller
    can reuse a chain this tick already paid for (e.g. the options service hands
    the currently-viewed symbol's chain to ``gamma_snapshot`` instead of
    refetching it seconds later). Best-effort: a raising callback is logged and
    the poll continues."""
    if symbols is None:
        symbols = collection_symbols()
    now = datetime.now(TZ)
    # snap down to nearest POLL_INTERVAL_MIN boundary so all rows in one poll
    # cycle share the same ts (idempotent re-runs replace, don't duplicate).
    snapped_min = (now.minute // POLL_INTERVAL_MIN) * POLL_INTERVAL_MIN
    ts_boundary = int(now.replace(minute=snapped_min, second=0, microsecond=0).timestamp())
    today = now.date()

    def _fetch(symbol):
        """(symbol, chain|None); fetch failures are logged here, never raised."""
        try:
            with _maybe_lock(lock):
                r = client.get_option_chain(
                    symbol,
                    contract_type=client.Options.ContractType.ALL,
                    from_date=today,
                    to_date=today + timedelta(days=7),
                )
            chain = r.json() if getattr(r, "status_code", 500) == 200 else None
            if not chain:
                log.warning("No chain for %s", symbol)
            return symbol, chain
        except Exception as e:  # noqa: BLE001 — one bad symbol can't kill the poll
            log.error("Poll failed for %s: %s", symbol, e)
            return symbol, None

    # The per-symbol chain fetches are independent, I/O-bound round-trips —
    # OVERLAP them in a small pool. Serially they consumed 15-35s of the 60s
    # collection slot and (measured 2026-07-17) ~37% of 1-min slots were dropped.
    # The proxy's rate limiter only SPACES upstream calls ~0.2s (no lock held
    # across the Schwab round-trip), so concurrency genuinely overlaps latency.
    # Engine compute + SQLite inserts stay on THIS thread below: the connection
    # has thread affinity and the engine mutates _last_dte per calc.
    if len(symbols) > 1:
        with ThreadPoolExecutor(
                max_workers=min(POLL_FETCH_WORKERS, len(symbols))) as ex:
            fetched = list(ex.map(_fetch, symbols))
    else:
        fetched = [_fetch(s) for s in symbols]

    for symbol, chain in fetched:
        if not chain:
            continue
        try:
            if on_chain is not None:
                try:
                    on_chain(symbol, chain)
                except Exception:
                    log.debug("on_chain callback failed for %s", symbol,
                              exc_info=True)
            # Options-flow skew computed ONCE per symbol from the already-fetched
            # chain (NO extra get_option_chain call) and merged into EACH view's
            # summary so any view row persists the scalars. Fully defensive: a
            # skew-compute failure must never break the poll — degrade to None.
            try:
                rr = flow_skew.risk_reversal_25d(chain)
                vol = flow_skew.index_call_put_volume(chain)
                prem = flow_skew.index_call_put_premium(chain)
                # ATM IV LEVEL (percent, e.g. 25.5) — the forward-only column that
                # feeds the IV-direction regime (collapsing vs spiking). Pure +
                # defensive: extract_atm_iv returns None on a thin/absent chain.
                skew_fields = {
                    "rr_25d": (rr or {}).get("rr"),
                    "call_vol": (vol or {}).get("call_vol"),
                    "put_vol": (vol or {}).get("put_vol"),
                    "call_prem": (prem or {}).get("call_prem"),
                    "put_prem": (prem or {}).get("put_prem"),
                    "atm_iv": iv_analysis.extract_atm_iv(chain),
                }
            except Exception:
                log.debug("skew compute failed for %s", symbol, exc_info=True)
                skew_fields = {"rr_25d": None, "call_vol": None, "put_vol": None,
                               "call_prem": None, "put_prem": None, "atm_iv": None}
            # Single pass yields GEX, Charm, DEX, Vanna — all persisted below.
            gex, charm, dex, vanna = engine.calc_all_from_chain(chain, use_volume=False)
            dte = engine._last_dte
            if gex:
                gex_summary = GammaEngine.snapshot_summary(gex)
                gex_summary["ts"] = ts_boundary
                gex_summary.update(skew_fields)
                db.insert_snapshot(
                    conn, symbol, "gex",
                    gex_summary, gex["gex"], dte,
                )
            if charm:
                charm_summary = GammaEngine.snapshot_summary(charm)
                charm_summary["ts"] = ts_boundary
                charm_summary.update(skew_fields)
                db.insert_snapshot(
                    conn, symbol, "charm",
                    charm_summary, charm["gex"], dte,
                )
            if dex:
                dex_summary = GammaEngine.snapshot_summary(dex, view="dex")
                dex_summary["ts"] = ts_boundary
                dex_summary.update(skew_fields)
                db.insert_snapshot(
                    conn, symbol, "dex",
                    dex_summary, dex["gex"], dte,
                )
            if vanna:
                vanna_summary = GammaEngine.snapshot_summary(vanna)
                vanna_summary["ts"] = ts_boundary
                vanna_summary.update(skew_fields)
                db.insert_snapshot(
                    conn, symbol, "vanna",
                    vanna_summary, vanna["gex"], dte,
                )
            # Per-strike traded PREMIUM, stored as a FIFTH view string ("prem")
            # rather than a new table: ``snapshots.view`` is free-form and a
            # premium cell is {call, put, net} floats — exactly the shape the
            # columnar float32 packer accepts. So this reuses insert_snapshot,
            # _encode_grid and load_date_with_grid with no schema change at all.
            # It feeds the Premium Divergence panel's strike ladder, which needs
            # premium BY STRIKE at each timestamp (index_call_put_premium above
            # collapses the same chain to two scalars).
            #
            # Its own try/except, and additive by construction: the ladder is one
            # section of one view, while the four Greek rows above back the
            # heatmap the whole page is built around. A premium failure costs the
            # ladder, never them. An empty/None grid writes NO row, so the panel
            # can tell "not collected yet" from "collected, nothing traded".
            try:
                prem_grid = flow_skew.premium_by_strike(chain)
                if prem_grid:
                    db.insert_snapshot(
                        conn, symbol, "prem",
                        {"ts": ts_boundary,
                         # Spot comes from the Greek pass so the ladder is drawn
                         # against the SAME underlying the cursor reads; the
                         # premium sums carry no spot of their own.
                         "spot": (gex or {}).get("spot"),
                         "net_total": sum(c["net"] for c in prem_grid.values()),
                         **skew_fields},
                        prem_grid, dte,
                    )
            except Exception:
                log.debug("premium_by_strike failed for %s", symbol, exc_info=True)
        except Exception as e:
            log.error("Poll failed for %s: %s", symbol, e)
    # Term-structure snapshot for SPXW (additive; per-symbol failures isolated).
    # The term chain is the widest SPX fetch in the system (Schwab has 502'd on
    # it) and the display shows only 5 expirations — poll it every TERM cadence,
    # not every 1-min slot. ``poll_term=None`` derives the gate from the snapped
    # minute; an explicit bool forces it (tests / manual runs).
    do_term = (snapped_min % TERM_POLL_INTERVAL_MIN == 0) if poll_term is None else poll_term
    if do_term:
        poll_term_once(
            client, engine, conn,
            ts_iso=datetime.fromtimestamp(ts_boundary, TZ).isoformat(),
            lock=lock,
        )
    conn.commit()


# SPX weeklies (SPXW root) come back inside the regular $SPX chain — the
# chain endpoint returns all expirations for a given underlying, both
# monthly third-Friday and weekly. We filter to the next 5 client-side.
TERM_SYMBOL = "$SPX"
TERM_TOP_N = 5
# Term structure updates slowly (5-expiration display); poll it every 5 min, not
# every 1-min slot — saves ~4/5 of the heaviest chain fetch + term-table writes.
TERM_POLL_INTERVAL_MIN = 5
# Cap how far out we ask for. Schwab 502s with "protocol.http.TooBigBody"
# on wider SPX windows — 30 days returns too much data to serialize.
# SPX has Mon/Wed/Fri weekly expirations + Fri monthly, so a 10-day
# horizon comfortably yields 5+ expirations to pick the nearest from.
# Matches the proven envelope of the existing 0-DTE collector (7 days).
TERM_DTE_HORIZON_DAYS = 10


def poll_term_once(client, engine, conn, ts_iso: str = None, lock=None) -> None:
    """Fetch the SPX chain (which includes SPXW weeklies), compute the
    term grid, persist the nearest TERM_TOP_N expirations.

    On HTTP error or empty grid: log + return (does not raise).
    Per design: never let a term-collection failure break the main 0-DTE poll.
    """
    if ts_iso is None:
        ts_iso = datetime.now(TZ).replace(second=0, microsecond=0).isoformat()
    try:
        today = datetime.now(TZ).date()
        with _maybe_lock(lock):
            r = client.get_option_chain(
                TERM_SYMBOL,
                contract_type=client.Options.ContractType.ALL,
                from_date=today,
                to_date=today + timedelta(days=TERM_DTE_HORIZON_DAYS),
            )
        status = getattr(r, "status_code", "?")
        chain = r.json() if status == 200 else None
        if not chain:
            log.warning(
                "No chain for %s at %s (status=%s)",
                TERM_SYMBOL, ts_iso, status,
            )
            return
        # Diagnostic: how many expirations did we get back?
        n_call_exps = len(chain.get("callExpDateMap", {}))
        n_put_exps = len(chain.get("putExpDateMap", {}))
        if n_call_exps == 0 and n_put_exps == 0:
            log.warning(
                "Chain returned for %s but expiration maps are empty "
                "(status=%s, top-level keys=%s)",
                TERM_SYMBOL, status, list(chain.keys())[:10],
            )
            return
        grid = engine.compute_term_grid(chain, top_n=TERM_TOP_N)
        if not grid["expirations"]:
            log.warning("Term grid empty for %s at %s", TERM_SYMBOL, ts_iso)
            return
        underlying = grid["underlying_price"]
        rows = []
        total_abs_net = 0.0
        for exp in grid["expirations"]:
            for K, cell in grid["cells"][exp].items():
                rows.append((
                    ts_iso, "SPX", exp, float(K),
                    cell["call_gex_usd"], cell["put_gex_usd"],
                    cell["net_gex_usd"], underlying,
                ))
                total_abs_net += abs(cell["net_gex_usd"])
        if rows:
            db.insert_term_snapshot_rows(conn, rows)
        # Heuristic: if EVERY cell across 5 expirations is zero, Schwab is
        # almost certainly returning OI=0 (their feed wipes/withholds OI
        # outside trading hours). Loud warning so a blank heatmap doesn't
        # look like a code bug.
        if rows and total_abs_net == 0.0:
            log.warning(
                "Term snapshot persisted at %s but ALL cells are $0 "
                "across %d expirations -- Schwab open-interest fields "
                "are empty (typical for off-hours requests). Heatmap "
                "will show '%s' colors only. Retry during market hours.",
                ts_iso, len(grid["expirations"]), "midpoint",
            )
    except Exception as e:
        log.error("Term poll failed for %s: %s", TERM_SYMBOL, e)


def _publish_sentiment_bridge():
    """Best-effort: publish the sentiment bridge in a clean subprocess. Never raises."""
    try:
        import subprocess, sys, pathlib
        root = pathlib.Path(__file__).resolve().parents[1]
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from repo_paths import SENTIMENT
        script = SENTIMENT / "publish_bridge.py"
        result = subprocess.run([sys.executable, str(script)], timeout=150,
                                capture_output=True)
        if result.returncode == 0:
            log.info("sentiment bridge published")
        else:
            log.warning("sentiment publish exited %s: %s", result.returncode,
                        (result.stderr or b'')[-300:].decode('utf-8', 'replace'))
    except Exception:
        log.exception("sentiment bridge publish failed; continuing")


def _default_clock():
    return datetime.now(TZ)


def run_collector_loop(client, engine, conn, *, stop_event=None,
                       clock=_default_clock, sleeper=time.sleep,
                       poll=None, lock=None) -> None:
    """Collection loop. Polls on POLL_INTERVAL_MIN boundaries between
    START and STOP hours; exits past STOP. Stops promptly when stop_event
    is set. `lock` (a threading.Lock or None) is threaded into the default
    poll to serialize the shared client."""
    if poll is None:
        poll = lambda c, e, cn: poll_once(c, e, cn, lock=lock)

    def _interrupted(timeout):
        # True if we should stop; honors stop_event when present.
        if stop_event is not None:
            return stop_event.wait(timeout)
        sleeper(timeout)
        return False

    while True:
        if stop_event is not None and stop_event.is_set():
            break
        now = clock()
        if (now.hour, now.minute) >= (STOP_HOUR, STOP_MIN):
            log.info("Past stop time; exiting")
            break
        target = next_boundary(now)
        if _interrupted(max(0, (target - now).total_seconds())):
            break
        now = clock()
        if (now.hour, now.minute) < (START_HOUR, START_MIN):
            continue
        if (now.hour, now.minute) >= (STOP_HOUR, STOP_MIN):
            break
        log.info("Polling at %s", now.strftime("%H:%M:%S"))
        # A single poll failure (transient Schwab/network error, a SQLite
        # "database is locked" on commit, etc.) must NOT kill the loop —
        # otherwise the in-tool collector silently stops for the rest of the
        # session. Log it and continue to the next boundary; the next poll
        # recovers once the transient condition clears.
        try:
            poll(client, engine, conn)
        except Exception:
            log.exception(
                "Poll failed at %s; continuing to next boundary",
                now.strftime("%H:%M:%S"))
        try:
            _publish_sentiment_bridge()
        except Exception:
            log.exception("sentiment hook crashed; continuing")


def main(
    client=None,
    engine=None,
    clock=_default_clock,
    sleeper=time.sleep,
    poll=None,
) -> None:
    """Main collection loop. Exits cleanly past STOP_HOUR:STOP_MIN CT.

    Parameters are injectable for testing; defaults build real objects.
    """
    conn = db.connect()
    db.init_schema(conn)
    db.purge_old(conn)
    log.info("Collector started")
    try:
        run_collector_loop(client, engine, conn, clock=clock,
                           sleeper=sleeper, poll=poll)
    finally:
        conn.close()
        log.info("Collector exited cleanly")


DEFAULT_LOG_PATH = Path(__file__).parent / "logs" / "gex_collector.log"


def ensure_file_logging(log_path=DEFAULT_LOG_PATH):
    """Attach a durable FileHandler to the ``gex_collector`` logger (idempotent).

    Used by BOTH the standalone entry and the in-tool collector so a poll
    failure / crash traceback always lands in gex_collector.log. Previously the
    in-tool collector configured no file handler, so its failures were invisible
    (logged to the root logger / console only). Safe to call repeatedly — it
    adds at most one FileHandler for a given path.
    """
    log_path = Path(log_path)
    # FileHandler stores baseFilename as os.path.abspath(filename); match that
    # normalization so the idempotency check is reliable on Windows.
    target = os.path.abspath(str(log_path))
    for h in log.handlers:
        if isinstance(h, logging.FileHandler) and \
                getattr(h, "baseFilename", None) == target:
            return  # already attached
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(str(log_path), encoding="utf-8")
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)


def _init_logging():
    ensure_file_logging()


def _build_live_deps():
    """Construct production client + engine. Kept separate so tests don't trip
    on Schwab auth when importing the module.

    This webgui repo has no ``dashboard.py`` (the old Tk UI was not copied), so
    the Schwab client is resolved through the schwab-proxy — the canonical
    client source for every app in this repo — rather than dashboard.init_client.
    """
    import pathlib
    import sys as _sys
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    for _p in (str(repo_root), str(repo_root / "schwab-proxy")):
        if _p not in _sys.path:
            _sys.path.insert(0, _p)
    from proxy_client import SchwabPyProxyClient
    client = SchwabPyProxyClient()
    if client is None:
        raise RuntimeError("Schwab proxy client init failed - is the proxy on :8100 up?")
    engine = GammaEngine()
    return client, engine


def make_heartbeat_poll(lock_path, *, source, owner, client_lock=None):
    """Return a poll(client, engine, conn) that polls once then refreshes the
    lock heartbeat — shared by the standalone entry and the in-tool collector.

    ``client_lock`` (a threading.Lock or None) is threaded into ``poll_once`` to
    serialize the shared Schwab client across the refresh and collector threads.
    """
    def _poll(c, e, conn):
        poll_once(c, e, conn, lock=client_lock)
        touch_lock(lock_path, source=source, owner=owner, now=int(time.time()))
    return _poll


def _entrypoint(lock_path=LOCK_PATH, owner=None, source="standalone"):
    """Standalone run guarded by the collector lock. Defers (no-op) if an
    in-tool collector already owns a fresh lock."""
    if owner is None:
        owner = f"pid:{os.getpid()}"
    now = int(time.time())
    if not acquire_collector_lock(lock_path, source=source, owner=owner,
                                  now=now):
        log.info("Another collector owns %s; standalone deferring.", lock_path)
        return
    # Heartbeat each poll by composing poll_once + touch_lock.
    _poll = make_heartbeat_poll(lock_path, source=source, owner=owner)
    try:
        client, engine = _build_live_deps()
        main(client=client, engine=engine, poll=_poll)
    finally:
        release_lock(lock_path, owner=owner)


if __name__ == "__main__":
    _init_logging()
    try:
        _entrypoint()
    except Exception:
        log.exception("Fatal error in collector")
        sys.exit(1)

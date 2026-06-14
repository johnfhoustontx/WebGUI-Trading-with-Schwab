"""
daily_trade_log.py - Daily potential-trade journal for the 0-DTE futures-options strategy
Version: 1.0.0
Last Updated: 2026-06-13

Captures, once per trading day in the ~13:00 CT entry window, the exact
directional 0-DTE credit-spread setups the backtested strategy would take, with
REAL strikes/deltas/credit from the live chain, then reviews them (outcome +
running tally) in the EOD report.

WHY $SPX / $NDX: Schwab's option-chain API does not serve options on futures
(/ES, /NQ). $SPX and $NDX are the cash-settled equivalents that track the
futures ~1:1, so we read their live 0-DTE chains as the proxy and translate to
/ES ($50/pt) and /NQ ($20/pt) contract dollars. Credit is logged in INDEX
POINTS (from the proxy chain marks) and dollarized per the futures multiplier;
SPX/ES option premiums in points are close enough for a paper journal.

INTEGRATION (no new process):
  * scanner.run_scan() calls capture_if_due() each cycle (fires once, in-window).
  * eod_report.generate() calls settle_and_render() to append the review section.

All Schwab access goes through the proxy (PROXY_URL) via urllib, so it is
independent of the caller's client object and the fetchers are injectable for
tests.

Version 1.0.0 Changes:
- Initial implementation
"""

import sys
import json
import math
import sqlite3
import pathlib
import urllib.request
import urllib.parse
import datetime
import logging
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # repo root
from repo_paths import PROXY_URL

log = logging.getLogger("daily_trade_log")
TZ = ZoneInfo("America/Chicago")

#############################################
# CONFIG
#############################################

TARGET_DELTA = 0.16
STOP_MULT = 2.0
ENTRY_WINDOW_CT = (12.75, 13.5)   # capture between 12:45 and 13:30 CT
DB_PATH = pathlib.Path(__file__).parent / "data" / "daily_trade_log.db"

# chain_symbol -> the proxy underlying; fut/mult -> futures translation; wing in pts
STRATEGY = [
    {"name": "S&P",    "chain_symbol": "$SPX", "fut": "/ES", "mult": 50.0,
     "micro": "/MES", "micro_mult": 5.0,  "wing": 25},
    {"name": "Nasdaq", "chain_symbol": "$NDX", "fut": "/NQ", "mult": 20.0,
     "micro": "/MNQ", "micro_mult": 2.0,  "wing": 100},
]

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS daily_potential_trades (
    date            TEXT NOT NULL,
    captured_ts     TEXT,
    name            TEXT,
    chain_symbol    TEXT,
    fut             TEXT,
    mult            REAL,
    side            TEXT,    -- 'PCS' (bullish put spread) | 'CCS' (bearish call spread)
    short_strike    REAL,
    long_strike     REAL,
    short_delta     REAL,
    wing            REAL,
    credit_pts      REAL,
    underlying_entry REAL,
    trend_dir       TEXT,    -- 'put' | 'call' | NULL ; the trend rule's pick
    chosen          INTEGER, -- 1 if this side is what the trend rule would trade
    status          TEXT,    -- 'OPEN' | 'SETTLED'
    settle_underlying REAL,
    outcome         TEXT,    -- 'worthless' | 'breach'
    realized_pnl_pts REAL,
    PRIMARY KEY (date, fut, side)
);
"""


#############################################
# PROXY FETCHERS (injectable for tests)
#############################################

def _proxy_json(path):
    with urllib.request.urlopen(f"{PROXY_URL}{path}", timeout=90) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def fetch_0dte_chain(symbol, date_str, strike_count=150):
    """Live 0-DTE chain for `symbol` (today's expiration) via the proxy.

    strike_count=150: high-priced underlyings like $NDX have fine strike
    spacing, so ~150 strikes are needed to reach the ~0.16-delta wing; $SPX
    needs far fewer but the extra strikes are harmless (single expiration)."""
    q = urllib.parse.urlencode({
        "symbol": symbol, "contractType": "ALL",
        "fromDate": date_str, "toDate": date_str, "strikeCount": strike_count,
    })
    return _proxy_json(f"/chains?{q}")


def fetch_quote(symbol):
    """Underlying last/close via the proxy."""
    d = _proxy_json(f"/quote?{urllib.parse.urlencode({'symbol': symbol})}")
    # proxy returns {symbol_or_resolved: {"quote": {...}}}
    for v in d.values():
        q = v.get("quote", {}) if isinstance(v, dict) else {}
        px = q.get("lastPrice") or q.get("closePrice") or q.get("mark")
        if px:
            return px
    return None


def fetch_sma20(symbol):
    """20-day SMA of prior closes (excludes today) for the trend filter."""
    q = urllib.parse.urlencode({"symbol": symbol, "periodType": "month",
                                "period": 3, "frequencyType": "daily", "frequency": 1})
    try:
        candles = _proxy_json(f"/pricehistory?{q}").get("candles", [])
    except Exception:
        return None
    closes = [c["close"] for c in candles]
    return sum(closes[-21:-1]) / 20.0 if len(closes) >= 21 else None


#############################################
# CHAIN PARSING + CANDIDATE SELECTION
#############################################

def parse_0dte_strikes(chain, date_str):
    """Return {'put': {strike: opt}, 'call': {strike: opt}} for the 0-DTE
    (today's) expiration. opt = {strike, delta, mark}."""
    out = {"put": {}, "call": {}}
    for side_key, map_key in (("put", "putExpDateMap"), ("call", "callExpDateMap")):
        for exp_key, strikes in (chain.get(map_key) or {}).items():
            if not exp_key.startswith(date_str):
                continue  # only the 0-DTE expiration
            for strike_str, contracts in strikes.items():
                if not contracts:
                    continue
                c = contracts[0]
                d = c.get("delta")
                mark = c.get("mark")
                if d is None or mark is None:
                    continue
                k = float(strike_str)
                out[side_key][k] = {"strike": k, "delta": d, "mark": mark}
    return out


def pick_short_by_delta(strikes_map, target_delta=TARGET_DELTA):
    """Pick the strike whose |delta| is closest to target. strikes_map is the
    per-strike dict for ONE side. Returns the opt dict or None."""
    if not strikes_map:
        return None
    return min(strikes_map.values(),
               key=lambda o: abs(abs(o["delta"]) - target_delta))


def build_candidate(strikes_map, side, wing, target_delta=TARGET_DELTA):
    """Build one credit-spread candidate (short at ~target delta, long `wing`
    points further OTM at the nearest available strike). side: 'put'|'call'.
    Returns dict or None."""
    short = pick_short_by_delta(strikes_map, target_delta)
    if short is None:
        return None
    target_long = short["strike"] - wing if side == "put" else short["strike"] + wing
    # nearest available long strike to the target wing distance
    long = min(strikes_map.values(), key=lambda o: abs(o["strike"] - target_long))
    if long["strike"] == short["strike"]:
        return None
    credit = short["mark"] - long["mark"]
    return {
        "short_strike": short["strike"], "long_strike": long["strike"],
        "short_delta": short["delta"], "wing": abs(short["strike"] - long["strike"]),
        "credit_pts": round(credit, 2),
    }


#############################################
# CAPTURE
#############################################

def _connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.executescript(SCHEMA_SQL)
    return conn


def _already_captured(conn, date_str):
    return conn.execute("SELECT COUNT(*) FROM daily_potential_trades WHERE date=?",
                        (date_str,)).fetchone()[0] > 0


def _hour_ct(now):
    return now.hour + now.minute / 60.0


def capture(now=None, *, chain_fetcher=fetch_0dte_chain, quote_fetcher=fetch_quote,
            sma_fetcher=fetch_sma20, conn=None):
    """Capture today's candidate spreads for every strategy underlying / side.
    Writes OPEN rows. Returns the list of rows written. (Timing/idempotency are
    the caller's job; see capture_if_due.)"""
    now = now or datetime.datetime.now(TZ)
    date_str = now.date().isoformat()
    own = conn is None
    conn = conn or _connect()
    written = []
    try:
        for inst in STRATEGY:
            try:
                chain = chain_fetcher(inst["chain_symbol"], date_str)
            except Exception as e:
                log.warning("chain fetch failed for %s: %s", inst["chain_symbol"], e)
                continue
            strikes = parse_0dte_strikes(chain, date_str)
            und = chain.get("underlyingPrice") or quote_fetcher(inst["chain_symbol"])
            sma20 = sma_fetcher(inst["chain_symbol"])
            trend_dir = None
            if sma20 and und:
                trend_dir = "put" if und >= sma20 else "call"  # bullish->put spread

            for side, side_key in (("PCS", "put"), ("CCS", "call")):
                cand = build_candidate(strikes[side_key], side_key, inst["wing"])
                if cand is None:
                    continue
                chosen = 1 if trend_dir == side_key else 0
                conn.execute(
                    "INSERT OR REPLACE INTO daily_potential_trades (date, captured_ts,"
                    " name, chain_symbol, fut, mult, side, short_strike, long_strike,"
                    " short_delta, wing, credit_pts, underlying_entry, trend_dir,"
                    " chosen, status, settle_underlying, outcome, realized_pnl_pts)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (date_str, now.isoformat(), inst["name"], inst["chain_symbol"],
                     inst["fut"], inst["mult"], side, cand["short_strike"],
                     cand["long_strike"], cand["short_delta"], cand["wing"],
                     cand["credit_pts"], und, trend_dir, chosen, "OPEN",
                     None, None, None),
                )
                written.append((date_str, inst["fut"], side, cand["credit_pts"]))
        conn.commit()
    finally:
        if own:
            conn.close()
    log.info("daily_trade_log: captured %d candidate(s) for %s", len(written), date_str)
    return written


def capture_if_due(now=None, **kw):
    """Scanner hook: capture once per day inside the entry window. Safe to call
    every scan cycle. Returns rows written (empty if not due)."""
    now = now or datetime.datetime.now(TZ)
    if now.weekday() >= 5:
        return []
    lo, hi = ENTRY_WINDOW_CT
    if not (lo <= _hour_ct(now) <= hi):
        return []
    conn = _connect()
    try:
        if _already_captured(conn, now.date().isoformat()):
            return []
        return capture(now, conn=conn, **kw)
    finally:
        conn.close()


#############################################
# SETTLEMENT
#############################################

def _settle_value_pts(side, short_k, long_k, settle):
    """Intrinsic spread value (pts) at settlement for the SHORT vertical."""
    if side == "PCS":  # short put / long put below
        v = max(0.0, short_k - settle) - max(0.0, long_k - settle)
    else:              # CCS: short call / long call above
        v = max(0.0, settle - short_k) - max(0.0, settle - long_k)
    return max(0.0, v)


def settle_open(as_of_date, *, quote_fetcher=fetch_quote, conn=None):
    """Settle OPEN rows dated <= as_of_date using each underlying's close.
    realized_pnl_pts = credit - settlement value. Returns count settled."""
    own = conn is None
    conn = conn or _connect()
    n = 0
    try:
        rows = conn.execute(
            "SELECT rowid, date, chain_symbol, side, short_strike, long_strike, "
            "credit_pts FROM daily_potential_trades WHERE status='OPEN' AND date<=?",
            (as_of_date,)).fetchall()
        settle_cache = {}
        for rowid, d, sym, side, sk, lk, credit in rows:
            if sym not in settle_cache:
                settle_cache[sym] = quote_fetcher(sym)
            settle = settle_cache[sym]
            if settle is None:
                continue
            val = _settle_value_pts(side, sk, lk, settle)
            pnl = credit - val
            conn.execute(
                "UPDATE daily_potential_trades SET status='SETTLED', "
                "settle_underlying=?, outcome=?, realized_pnl_pts=? WHERE rowid=?",
                (settle, "worthless" if val <= 1e-9 else "breach", round(pnl, 2), rowid))
            n += 1
        conn.commit()
    finally:
        if own:
            conn.close()
    return n


#############################################
# REVIEW (EOD report section)
#############################################

def _fmt_usd(pts, mult):
    return f"${pts * mult:+,.0f}"


def render_eod_section(date_str, *, conn=None):
    """Markdown lines: today's captured candidates + recent outcomes + tally."""
    own = conn is None
    conn = conn or _connect()
    try:
        lines = ["## 5. Potential Trades - 0-DTE futures-options strategy",
                 "",
                 "_Paper journal: $SPX->/ES, $NDX->/NQ proxy. Credit in index points;"
                 " $ at the futures multiplier. `*` = the trend rule's pick._", ""]

        today = conn.execute(
            "SELECT name, fut, mult, side, short_strike, long_strike, short_delta,"
            " credit_pts, underlying_entry, trend_dir, chosen, status, outcome,"
            " realized_pnl_pts FROM daily_potential_trades WHERE date=? "
            "ORDER BY fut, side", (date_str,)).fetchall()
        if today:
            lines.append("### Captured today")
            lines.append("| | Inst | Side | Short/Long | Delta | Credit (pt / $) | "
                         "Underlying | Outcome | PnL $ |")
            lines.append("|---|---|---|---|---|---|---|---|---|")
            for (name, fut, mult, side, sk, lk, dlt, credit, und, trend, chosen,
                 status, outcome, pnl) in today:
                star = "*" if chosen else ""
                pnl_s = _fmt_usd(pnl, mult) if pnl is not None else "-"
                lines.append(
                    f"| {star} | {fut} | {side} | {sk:.0f}/{lk:.0f} | {dlt:+.2f} | "
                    f"{credit:.2f} / {credit*mult:,.0f} | {und:.0f} | "
                    f"{outcome or status} | {pnl_s} |")
            lines.append("")
        else:
            lines.append("_No potential trades captured today "
                         "(entry-window scan may not have run)._")
            lines.append("")

        # running tally over SETTLED chosen-side trades (what the rule would trade)
        tally = conn.execute(
            "SELECT fut, mult, COUNT(*), "
            " SUM(CASE WHEN outcome='worthless' THEN 1 ELSE 0 END), "
            " SUM(realized_pnl_pts) FROM daily_potential_trades "
            "WHERE status='SETTLED' AND chosen=1 GROUP BY fut, mult").fetchall()
        if tally:
            lines.append("### Running tally (chosen side, settled, 1 contract)")
            for fut, mult, n, wins, pnl_pts in tally:
                pnl_pts = pnl_pts or 0.0
                wr = (wins or 0) / n * 100 if n else 0
                lines.append(f"- **{fut}**: {n} trades, {wins} worthless "
                             f"({wr:.0f}% win), net {_fmt_usd(pnl_pts, mult)}")
            lines.append("")
        return lines
    finally:
        if own:
            conn.close()


def settle_and_render(date_str, *, quote_fetcher=fetch_quote):
    """EOD hook: settle prior OPEN rows, then return the review markdown lines."""
    conn = _connect()
    try:
        try:
            settle_open(date_str, quote_fetcher=quote_fetcher, conn=conn)
        except Exception as e:
            log.warning("settle_open failed: %s", e)
        return render_eod_section(date_str, conn=conn)
    finally:
        conn.close()


#############################################
# CLI
#############################################

def main(argv):
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    cmd = argv[1] if len(argv) > 1 else "review"
    if cmd == "capture":
        print(capture())
    elif cmd == "settle":
        print("settled:", settle_open(datetime.datetime.now(TZ).date().isoformat()))
    else:  # review
        print("\n".join(settle_and_render(datetime.datetime.now(TZ).date().isoformat())))


if __name__ == "__main__":
    main(sys.argv)

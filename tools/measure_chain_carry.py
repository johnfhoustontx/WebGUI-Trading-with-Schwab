"""How close is a carried chain to a real one?

The GEX collector can fetch watchlist-only symbols every Nth minute and carry
the last fetched chain forward to the live price in between
(options-scanner/chain_carry.py). That carry is a MODEL. Before it replaces real
fetches, this tool measures it: it fetches a few symbols' chains every minute
for a short window, and each minute compares the GEX engine's output from the
chain fetched THAT minute with its output from each chain fetched in the
minutes before and carried forward to the live price.

How far back it looks is the INTERVAL being considered (``--interval``, 2 to 5;
by default the configured ``collection.tail_interval_min`` when that is above 1,
else 3). At an interval of N the collector carries a chain for up to N - 1
minutes, so the tool holds N - 1 chains per symbol and reports the error by how
old the carried chain was.

It reports the error in net gamma, the error in the flip level, how often the
walls differ, and how often the carry's gamma cap bound. The cap is the
configured ``collection.max_gamma_ratio`` unless ``--max-gamma-ratio`` says
otherwise. Comparisons made on an expiration day (the nearest expiration settles
today) are also stated apart from the rest: a fast move there understates
carried gamma, and one median over both would hide it.

Run on the box, during the regular session:

    .venv/bin/python tools/measure_chain_carry.py --symbols SOFI,UBER,HOOD --minutes 12
    .venv/bin/python tools/measure_chain_carry.py --symbols SOFI,UBER,HOOD --minutes 12 --interval 5
    .venv/bin/python tools/measure_chain_carry.py --symbols SOFI,UBER,HOOD --minutes 12 --dry-run

Cost, stated by ``--dry-run`` before anything is called: one chain call per
symbol per minute plus one quote call a minute. Every chain fetch sends
``maxAge=0``, so it is a real fetch whatever the proxy's store is set to; the
quote call goes through the proxy like any other.

Refuses to run outside the regular session unless ``--force`` is given: a
carried chain measured against a closed market says nothing (the price is not
moving, and off-hours the chain's own price is the previous close).

The proxy counts requests by caller. Unless ``TRADING_CALLER`` is already set,
the tool names itself ``measure_chain_carry``.
"""
from __future__ import annotations

import argparse
import datetime as dt
import math
import os
import pathlib
import sys
import time
from zoneinfo import ZoneInfo

ROOT = pathlib.Path(__file__).resolve().parents[1]
CT = ZoneInfo("America/Chicago")

CALLER = "measure_chain_carry"
# The window the collector asks for (gex_collector.poll_once: today -> +7 days),
# so the chains measured here are the chains it would carry.
CHAIN_FORWARD_DAYS = 7
ROUND_SEC = 60
# The intervals the collector will run (services/options_svc/compute.py reads
# anything above 5 as 5), and the one measured when the setting is still 1.
MIN_INTERVAL, MAX_INTERVAL = 2, 5
DEFAULT_INTERVAL = 3
# The keys of GammaEngine.snapshot_summary that are compared.
SUMMARY_KEYS = ("net_total", "flip", "top_pos_strike", "top_neg_strike")


#############################################
# THE ARITHMETIC (pure)
#############################################

def _num(d, key):
    """A real, finite reading, or None. Never a bool, never NaN."""
    v = d.get(key) if isinstance(d, dict) else None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return None
    return v


def compare(fresh: dict, carried: dict) -> dict:
    """One comparison row. A reading either side lacks is None, never 0."""
    f_net, c_net = _num(fresh, "net_total"), _num(carried, "net_total")
    f_flip, c_flip = _num(fresh, "flip"), _num(carried, "flip")
    net = (abs(c_net - f_net) / abs(f_net)
           if f_net not in (None, 0) and c_net is not None else None)
    flip = abs(c_flip - f_flip) if f_flip is not None and c_flip is not None else None
    walls = (fresh.get("top_pos_strike") == carried.get("top_pos_strike")
             and fresh.get("top_neg_strike") == carried.get("top_neg_strike"))
    return {"net_rel_err": net, "flip_abs_err": flip, "walls_agree": walls}


def max_carry_age(interval: int, slack) -> float:
    """The oldest chain worth comparing at ``interval``: the collector carries a
    chain for at most ``interval - 1`` minutes, plus the slack it allows when it
    asks the proxy for a stored one."""
    return (interval - 1) * ROUND_SEC + slack


def report(rows: list, interval=None) -> str:
    """The summary printed at the end of a run. Rows that carry a ``capped``
    count (how many contracts hit the carry's gamma cap), an ``age_min`` (how
    many whole minutes old the carried chain was) or an ``expiration_day`` flag
    each add their own lines."""
    nets = [r["net_rel_err"] for r in rows if r["net_rel_err"] is not None]
    flips = [r["flip_abs_err"] for r in rows if r["flip_abs_err"] is not None]
    off = sum(1 for r in rows if not r["walls_agree"])
    lines = [f"interval considered: {interval} minutes"] if interval is not None else []
    lines.append(f"{len(rows)} comparisons")
    if nets:
        nets.sort()
        lines.append(f"net gamma error: median {nets[len(nets) // 2]:.1%}, "
                     f"worst {nets[-1]:.1%}")
    if flips:
        flips.sort()
        lines.append(f"flip level error: median {flips[len(flips) // 2]:.2f}, "
                     f"worst {flips[-1]:.2f}")
    lines.append(f"walls differ in {off} of {len(rows)}")
    for age in sorted({r["age_min"] for r in rows if "age_min" in r}):
        lines.append(f"carried {age} minute{'' if age == 1 else 's'} old: "
                     + _group_text([r for r in rows if r.get("age_min") == age]))
    caps = [r["capped"] for r in rows if "capped" in r]
    if caps:
        lines.append(f"gamma cap bound on {sum(caps)} contract(s), in "
                     f"{sum(1 for c in caps if c)} of {len(caps)} comparisons")
    if any("expiration_day" in r for r in rows):
        marked = [r for r in rows if "expiration_day" in r]
        lines.append(
            "net gamma error on expiration day: "
            + _group_text([r for r in marked if r["expiration_day"]])
            + "; other days: "
            + _group_text([r for r in marked if not r["expiration_day"]]))
    return "\n".join(lines)


def _group_text(rows: list) -> str:
    """Count, median and worst net-gamma error for one group of comparisons."""
    text = f"{len(rows)} comparisons"
    if not rows:
        return text
    nets = sorted(r["net_rel_err"] for r in rows if r["net_rel_err"] is not None)
    if not nets:
        return text + ", no reading"
    return text + f", median {nets[len(nets) // 2]:.1%}, worst {nets[-1]:.1%}"


def row_text(row: dict) -> str:
    """One comparison as it is printed while the run goes on."""
    net, flip = row["net_rel_err"], row["flip_abs_err"]
    return "  ".join([
        "net " + ("n/a" if net is None else f"{net:.1%}"),
        "flip " + ("n/a" if flip is None else f"{flip:.2f}"),
        "walls " + ("agree" if row["walls_agree"] else "DIFFER"),
        f"capped {row.get('capped', 0)}",
    ])


def parse_symbols(text: str) -> list:
    """``"SOFI, UBER,,SOFI"`` -> ``["SOFI", "UBER"]``: stripped, in order, once each."""
    out: list = []
    for part in (text or "").split(","):
        sym = part.strip()
        if sym and sym not in out:
            out.append(sym)
    return out


def planned_calls(symbols: list, minutes: int) -> dict:
    """What a run would ask Schwab for: one chain per symbol per minute (each a
    real fetch, ``maxAge=0``) and one batched quote call a minute."""
    chains = len(symbols) * minutes
    return {"symbols": list(symbols), "minutes": minutes, "chain_calls": chains,
            "quote_calls": minutes, "total_calls": chains + minutes}


def plan_text(plan: dict, max_ratio=None, interval=None, slack=0) -> str:
    lines = [
        f"symbols: {', '.join(plan['symbols'])} ({len(plan['symbols'])})",
        f"window: {plan['minutes']} minutes, one round a minute",
        f"Schwab calls: {plan['chain_calls']} chain + {plan['quote_calls']} quote "
        f"= {plan['total_calls']}",
    ]
    if interval is not None:
        lines.append(
            f"interval: {interval} minutes (a carried chain is up to "
            f"{interval - 1} minute{'' if interval == 2 else 's'} old, "
            f"at most {max_carry_age(interval, slack):g} seconds)")
    if max_ratio is not None:
        lines.append(f"gamma cap: {max_ratio:g} times Schwab's value")
    return "\n".join(lines)


def _gamma_ratio(text: str) -> float:
    """``--max-gamma-ratio``: a finite number of at least 1."""
    value = float(text)
    if not math.isfinite(value) or value < 1:
        raise argparse.ArgumentTypeError("must be a number of at least 1")
    return value


def _collection_settings() -> dict:
    """config/marketdata.toml [collection], as the collector would read it."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from shared import marketdata_config
    return marketdata_config.section("collection")


def configured_gamma_ratio() -> float:
    """``collection.max_gamma_ratio``, as the collector would use it."""
    return _collection_settings()["max_gamma_ratio"]


def configured_slack():
    """``collection.carry_slack_sec``, as the collector would use it."""
    return _collection_settings()["carry_slack_sec"]


def configured_interval() -> int:
    """The interval to measure when none is asked for: the configured
    ``collection.tail_interval_min`` when it is above 1 (read as 5 above 5, as
    the collector reads it), else 3 — the step being considered while the
    setting is still at 1."""
    configured = int(_collection_settings()["tail_interval_min"])
    if configured <= 1:
        return DEFAULT_INTERVAL
    return min(configured, MAX_INTERVAL)


#############################################
# THE RUN
#############################################

def _engine_modules():
    """The engine modules, imported late so the arithmetic above needs none of
    them. Same path bootstrap as the other tools in this folder."""
    for p in (ROOT, ROOT / "options-scanner", ROOT / "schwab-proxy"):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))
    import chain_carry
    import gamma_tool
    import gex_collector
    from shared import market_calendar
    return chain_carry, gamma_tool, gex_collector, market_calendar


def _default_client():
    from proxy_client import SchwabPyProxyClient
    return SchwabPyProxyClient()


def _name_the_caller() -> None:
    """Give the proxy's per-caller counts a name for this run, unless the
    operator already set one."""
    if not (os.environ.get("TRADING_CALLER") or "").strip():
        os.environ["TRADING_CALLER"] = CALLER


def measure(client, symbols, minutes, *, clock, sleep, force=False,
            max_ratio=None, interval=DEFAULT_INTERVAL, slack=0) -> list:
    """Run the window and return the comparison rows, printing one line each.

    ``interval - 1`` chains are held per symbol, and one older than
    ``max_carry_age(interval, slack)`` is not compared: the collector would
    never carry it."""
    held_per_symbol = interval - 1
    oldest = max_carry_age(interval, slack)
    chain_carry, gt, gc, mc = _engine_modules()

    def summarize(chain):
        """``(summary, whether the engine read an expiration that is today)``."""
        engine = gt.GammaEngine()
        gex, *_ = engine.calc_all_from_chain(chain, use_volume=False)
        if not gex:
            return {}, False             # _last_dte is 0 on an engine that read nothing
        return gt.GammaEngine.snapshot_summary(gex), engine._last_dte == 0

    held: dict = {}                      # symbol -> [(fetched_at, chain), ...]
    rows: list = []
    for minute in range(minutes):
        started = clock()
        if not force and not mc.is_regular_hours(started):
            print(f"{started:%H:%M} session closed; stopping after "
                  f"{minute} of {minutes} minutes")
            break
        today = started.date()
        # Chains first, then ONE quote call: the order the collector works in.
        fresh: dict = {}
        for sym in symbols:
            r = client.get_option_chain(
                sym, contract_type=client.Options.ContractType.ALL,
                from_date=today,
                to_date=today + dt.timedelta(days=CHAIN_FORWARD_DAYS),
                max_age=0)
            chain = r.json() if getattr(r, "status_code", 500) == 200 else None
            if not chain:
                print(f"{started:%H:%M} {sym}: no chain "
                      f"(status {getattr(r, 'status_code', '?')})", file=sys.stderr)
                continue
            fresh[sym] = (clock(), chain)
        quotes = client.get_quotes(list(symbols))
        spots = gc.live_spots(
            quotes.json() if getattr(quotes, "status_code", 500) == 200 else None)
        for sym, (fetched_at, chain) in fresh.items():
            fresh_summary, expiration_day = summarize(chain)
            for old_at, old in held.get(sym, []):
                age = (fetched_at - old_at).total_seconds()
                if sym not in spots or age > oldest:
                    continue
                carried = chain_carry.carry_chain(
                    old, spots[sym], age_sec=age, now=fetched_at, max_ratio=max_ratio)
                row = compare(fresh_summary, summarize(carried)[0])
                row["capped"] = chain_carry.capped_gammas(
                    old, spots[sym], age_sec=age, now=fetched_at, max_ratio=max_ratio)
                row["expiration_day"] = expiration_day
                row["age_min"] = round(age / ROUND_SEC)
                rows.append(row)
                print(f"{fetched_at:%H:%M} {sym:6s} age {age:5.0f}s  {row_text(row)}")
            held[sym] = (held.get(sym, []) + [(fetched_at, chain)])[-held_per_symbol:]
        if minute < minutes - 1:
            sleep(max(0.0, ROUND_SEC - (clock() - started).total_seconds()))
    return rows


def main(argv=None, *, clock=None, client_factory=None, sleep=time.sleep) -> int:
    """``clock`` (a callable giving the Central wall clock), ``client_factory``
    and ``sleep`` are injectable for tests."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--symbols", default="SOFI,UBER,HOOD",
                    help="comma-separated symbols to measure")
    ap.add_argument("--minutes", type=int, default=12,
                    help="rounds to run, one a minute (more than --interval)")
    ap.add_argument("--interval", type=int, default=None,
                    choices=range(MIN_INTERVAL, MAX_INTERVAL + 1),
                    help="the tail interval being considered, in minutes "
                         "(default: the configured collection.tail_interval_min "
                         "when above 1, else 3)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the symbols, minutes and Schwab calls, and exit")
    ap.add_argument("--force", action="store_true",
                    help="run even outside the regular session")
    ap.add_argument("--max-gamma-ratio", type=_gamma_ratio, default=None,
                    help="the carry's gamma cap (default: the configured "
                         "collection.max_gamma_ratio)")
    args = ap.parse_args(argv)

    symbols = parse_symbols(args.symbols)
    if not symbols:
        ap.error("--symbols names no symbol")
    interval = args.interval if args.interval is not None else configured_interval()
    if args.minutes <= interval:
        ap.error(f"--minutes must be more than --interval ({interval}): a chain "
                 f"is first {interval - 1} minute(s) old in minute {interval}, and "
                 "one comparison at that age is not a measurement")
    slack = configured_slack()
    plan = planned_calls(symbols, args.minutes)
    max_ratio = (args.max_gamma_ratio if args.max_gamma_ratio is not None
                 else configured_gamma_ratio())
    if args.dry_run:
        print(plan_text(plan, max_ratio, interval, slack))
        print("Dry run: nothing was called.")
        return 0

    clock = clock or (lambda: dt.datetime.now(CT))
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from shared import market_calendar as mc
    now = clock()
    if not args.force and not mc.is_regular_hours(now):
        print(f"Refusing to run at {now:%Y-%m-%d %H:%M} CT: outside the regular "
              "session a carried chain measured against a closed market says "
              "nothing. Run during the regular session, or pass --force.",
              file=sys.stderr)
        return 2

    print(plan_text(plan, max_ratio, interval, slack))
    _name_the_caller()
    if client_factory is None:
        _engine_modules()                # puts schwab-proxy on the path
        client_factory = _default_client
    rows = measure(client_factory(), symbols, args.minutes, clock=clock,
                   sleep=sleep, force=args.force, max_ratio=max_ratio,
                   interval=interval, slack=slack)
    print()
    print(report(rows, interval=interval))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

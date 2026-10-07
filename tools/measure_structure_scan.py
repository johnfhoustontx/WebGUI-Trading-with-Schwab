#!/usr/bin/env python
"""Time the Market Scanner's structures pass, per symbol.

`scanner_engine.run_full_scan` builds structures other than credit spreads
(`options-scanner/structure_scan.py`) for every symbol in every scan, 30 scans a
day, on the calling thread after the chains have been fetched. The pass makes no
Schwab call, so its whole cost is CPU, and this script measures it.

PURE, like `tools/sweep_strategy_gates.py`, whose synthetic Black-Scholes chain
it reuses: no Schwab call, no SQLite, no network. It builds the three chain
windows the scan holds per symbol - today..+4, +5..+15 and +20..+45 days, each
with several expirations and a dense strike ladder - then runs the real
`structure_scan.build_window` and `structure_scan.select` for both windows, the
way `run_full_scan` calls them, and prints the time per symbol.

    python tools/measure_structure_scan.py
    python tools/measure_structure_scan.py --strikes 400 --symbols 91

`--strikes` is the number of strikes per side per expiration (an index ETF lists
a few hundred). The budget the design set is ONE SECOND per symbol; over it, the
levers are `[structures] families` and `max_per_family` in config/scanner.toml.

⚠ A synthetic chain. Real chains differ in expiration count and ladder density,
and the first scan after a restart also pays the imports. Read the first live
scan's duration before relying on this number.
"""
from __future__ import annotations

import argparse
import importlib.util
import math
import statistics
import sys
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))
from repo_paths import OPTIONS_SCANNER  # noqa: E402

sys.path.insert(0, str(OPTIONS_SCANNER))
import structure_scan as sx  # noqa: E402
from shared import scanner_config as _scfg  # noqa: E402

# The expirations each of the scan's three windows holds for a name with daily
# and weekly listings - the dense case.
ZERO_DAYS = (0, 1, 2, 3, 4)
SWING_DAYS = (5, 6, 7, 8, 9, 12, 13, 14, 15)
BACK_DAYS = (21, 28, 35, 42)
NEUTRAL_VIEW = {"direction": "neutral", "conviction": 0.1, "vol_regime": "mid"}


def _sweep():
    p = Path(__file__).resolve().parent / "sweep_strategy_gates.py"
    spec = importlib.util.spec_from_file_location("sweep_strategy_gates", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def chains(spot, iv, strikes):
    """The three windows' chains, `strikes` strikes each side of spot."""
    sweep = _sweep()
    step = round(spot * sweep.SPAN / strikes, 4)
    return (sweep.chain(spot, iv, ZERO_DAYS, step),
            sweep.chain(spot, iv, SWING_DAYS, step),
            sweep.chain(spot, iv, BACK_DAYS, step))


def scan_symbol(zero, swing, back, spot, iv, cfg):
    """Both windows for one symbol, as run_full_scan runs them. Returns the rows."""
    daily_em = spot * iv * math.sqrt(1 / 365.0)
    band = (cfg["short_delta_min"], _scfg.selection()["max_entry_short_delta"])
    out = []
    for chain, back_chain, lo, hi, back_hi in ((zero, None, 0, 4, None),
                                               (swing, back, 5, 15, 45)):
        cands = sx.build_window(chain, "MEASURE", spot, iv, lo, hi,
                                families=cfg["families"], short_band=band,
                                back_chain=back_chain, back_dte_max=back_hi)
        out += sx.select(
            cands, view=NEUTRAL_VIEW, atm_iv=iv, daily_em=daily_em, dte_min=lo,
            iv_rank=50, floor=30, ceiling=0, spans_earnings=lambda s: False,
            earnings_date=None, keep_long_through_earnings=True,
            min_score=cfg["min_score"],
            excluded_grades=tuple(cfg["excluded_grades"]),
            max_per_family=cfg["max_per_family"])
    return out


def measure(symbols, strikes, spot=100.0, iv=0.28):
    cfg = _scfg.structures()
    zero, swing, back = chains(spot, iv, strikes)
    scan_symbol(zero, swing, back, spot, iv, cfg)      # warm the imports
    times, rows = [], 0
    for _ in range(symbols):
        t0 = time.perf_counter()
        rows = len(scan_symbol(zero, swing, back, spot, iv, cfg))
        times.append(time.perf_counter() - t0)
    return {"symbols": symbols, "strikes": strikes, "rows_per_symbol": rows,
            "mean": statistics.mean(times), "worst": max(times),
            "total": sum(times), "families": list(cfg["families"])}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--symbols", type=int, default=45)
    ap.add_argument("--strikes", type=int, default=200,
                    help="strikes each side of spot, per expiration")
    a = ap.parse_args(argv)
    m = measure(a.symbols, a.strikes)
    print(f"structures pass -- {m['symbols']} symbols, {m['strikes']} strikes a "
          f"side, expirations {len(ZERO_DAYS)}/{len(SWING_DAYS)}/{len(BACK_DAYS)} "
          f"per window, families {m['families']}")
    print(f"  per symbol: mean {m['mean'] * 1000:.0f} ms, worst "
          f"{m['worst'] * 1000:.0f} ms ({m['rows_per_symbol']} rows kept)")
    print(f"  whole scan: {m['total']:.1f} s on one thread")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Fetch-once panel construction for the Phase-4 study.

Wraps ``fit_swing_model``'s fetch + panel build behind the cache, so a study
run costs one fetch and every variant afterwards is free and identical. The
cache key covers the universe, the window, the horizon and the factor registry
— see ``panel_cache``.

Never imported by a service.
"""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))   # repo root
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))   # trade-analyzer

from repo_paths import SWING_MODEL                          # noqa: E402
from src.analysis import factors as F                       # noqa: E402
from research import panel_cache as pc                      # noqa: E402

RESEARCH_DIR = SWING_MODEL.parent / "research"


def build_or_load(universe, years=5, horizon=20, refresh=False, log=print):
    """``(panel, forward, meta)`` for this universe, from cache when possible.

    ``universe`` maps symbol -> sector ETF."""
    import fit_swing_model as FSM

    key = pc.panel_key(list(universe), years, horizon, list(F.FACTORS))
    path = RESEARCH_DIR / f"panel-{key}.pkl"
    if not refresh:
        hit = pc.load(path)
        if hit is not None:
            log(f"panel cache HIT {path.name} "
                f"({hit[2].get('universe_n')} symbols, fetched {hit[2].get('fetched')})")
            return hit
    log(f"panel cache MISS — fetching {len(universe)} symbols x {years}yr ...")

    spy_df = FSM.fetch_daily("SPY", years=years)
    if spy_df is None:
        raise SystemExit("could not fetch SPY — is the proxy up on :8100?")
    spy_close = FSM._close(spy_df)
    etfs = sorted(set(universe.values()))
    sector_closes = {e: FSM._close(d)
                     for e, d in FSM.fetch_all(etfs).items() if d is not None}
    hist = FSM.fetch_all(list(universe))
    missing = sorted(s for s, d in hist.items() if d is None)
    panel, forward, used = FSM.build_panel(
        hist, spy_close, sector_closes, horizon=horizon, universe=universe)

    from datetime import date
    meta = {"universe_n": used, "requested_n": len(universe), "years": years,
            "horizon": horizon, "fetched": date.today().isoformat(),
            "missing": missing, "factors": sorted(F.FACTORS)}
    pc.save(path, panel, forward, meta)
    log(f"fetched {used}/{len(universe)} symbols; cached {path.name}"
        + (f"; NO DATA for {', '.join(missing)}" if missing else ""))
    return panel, forward, meta

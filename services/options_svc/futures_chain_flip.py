"""The /ES and /NQ gamma flip, found by the feed's own rule.

Every other symbol's flip is ``gamma_tool.calc_flip_point``: where one strike's
net gamma changes sign against the next, kept only when the sign holds for two
more strikes, the crossing nearest the price winning. On the futures' first live
session (2026-11-02, 06:01 to 06:12 Central) that flip jumped between captures
two minutes apart: /ES 7641.65, none, 7641.81, 7641.77, 7775.24, none, 7641.77,
and /NQ between 28609 and 29133. Net gamma near the future was small and changed
sign from strike to strike (/ES 7635 -0.7M, 7640 -13.7M, 7645 +24.9M, 7650
+104.5M, 7655 -44.3M, 7660 +1785M), so the crossing near the price passed the
two-strike test in one capture and failed it in the next.

The cumulative flip is where the running total of net gamma, summed up the
strikes from the lowest captured, changes sign. On the same captures it held at
/ES 7656.7 to 7657.0 and /NQ 28793 to 28797. ``config/futures_chain.toml``
``[flip] rule`` chooses it ("cumulative", shipped) or the shared rule
("per_strike"); the shared rule itself is not changed for any symbol.

The flip is written into the stored ``gex`` rows after ``derive_minute`` has
written them, from each row's own stored grid, so the Desk (``latest_flip``), the
heatmap's Flip track (the stored column) and the page's summary (``stored``) read
one number. Imports nothing from ``compute``.
"""
from __future__ import annotations

import math

from shared import futures_chain_config

# The view whose flip the feed's rule sets. Charm, delta and vanna keep the shared
# rule: only the gamma flip was measured.
VIEW = "gex"


def cumulative_flip(grid, spot, band_pct) -> float | None:
    """Where the running total of ``grid``'s ``net``, summed up the strikes,
    changes sign, interpolated between the two strikes it crosses between. Only
    crossings with the upper strike within ``band_pct`` percent of ``spot``
    count, the nearest to ``spot`` winning. ``None`` when none does: a real
    answer, not a failure."""
    if not isinstance(spot, (int, float)) or not math.isfinite(spot) or spot <= 0:
        return None
    reach = spot * band_pct / 100.0
    running, previous, crossings = 0.0, None, []
    for strike in sorted(grid or {}):
        net = (grid[strike] or {}).get("net")
        if not isinstance(net, (int, float)) or not math.isfinite(net):
            continue
        total = running + net
        if previous is not None and running * total < 0 and abs(strike - spot) <= reach:
            crossings.append(round(previous + (strike - previous) * -running
                                   / (total - running), 2))
        running, previous = total, strike
    return min(crossings, key=lambda level: abs(level - spot)) if crossings else None


def flip_of(grid, spot, settings: dict) -> float | None:
    """The flip by ``settings`` (``futures_chain_config.flip()``)."""
    if settings["rule"] == "cumulative":
        return cumulative_flip(grid, spot, settings["band_pct"])
    import gamma_tool
    return gamma_tool.calc_flip_point(grid, spot, settings["band_pct"] / 100.0)


def restamp(conn, gex_history, symbol: str, span, settings: dict | None = None) -> int:
    """Rewrite the stored gamma flip of ``symbol``'s rows inside ``span`` (a
    ``gex_history_db.SessionSpan``) by the feed's rule. Returns how many rows
    changed. Does not commit: the caller's transaction holds the rows it wrote."""
    settings = settings or futures_chain_config.flip()
    changed = 0
    for ts, spot, stored_flip, *_rest, grid in gex_history.load_date_with_grid(
            conn, symbol, VIEW, date=span):
        level = flip_of(grid, spot, settings)
        if level != stored_flip:
            conn.execute("UPDATE snapshots SET flip = ? WHERE symbol = ? AND view = ? "
                         "AND ts = ?", (level, symbol, VIEW, ts))
            changed += 1
    return changed


def stored(conn, gex_history, symbol: str, ts: int) -> float | None:
    """The gamma flip stored for ``symbol`` at minute ``ts``, or ``None``."""
    return gex_history.latest_flip(conn, symbol, VIEW,
                                   gex_history.SessionSpan(int(ts), int(ts) + 1))

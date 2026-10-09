"""The Dealer Positioning display window: which strikes sit around spot, and the
figures computed inside that window.

A sibling of ``compute`` and never an importer of it: ``compute.py`` has a line
ceiling (tests/test_compute_module_shape.py), so new gamma code lands here and
``compute`` imports what its own code calls. ``window_around`` lived in
``compute`` as ``_window_around`` until 2026-10-09 and moved out unchanged in
behaviour.

Design: docs/plans/2026-10-09-gamma-heatmap-value-scale-frame-design.md
"""
from shared.numeric import finite


def window_around(strikes, spot, n_side):
    """The nearest ``n_side`` strikes below spot, the strike at spot, and the
    nearest ``n_side`` above: mirrors the page's ``gamma.strikes_around``.
    A set of floats; an unusable spot keeps every numeric strike (no crop)."""
    s = sorted({x for x in (strikes or []) if isinstance(x, (int, float))
                and not isinstance(x, bool)})
    if finite(spot) is None:
        return set(s)
    below = [x for x in s if x < spot][-n_side:]
    above = [x for x in s if x > spot][:n_side]
    return set(below + [x for x in s if x == spot] + above)


def crop_keep(strikes, spot, path, n_side, edge_side=0):
    """The strikes a view's history keeps, or None for "keep everything".

    The display window (``n_side`` each side) around the current spot, or the
    first spot on the path when there is no current one; every strike the
    session's path crossed; and ``edge_side`` strikes each side of the session's
    LOW and of its HIGH. That last part is what lets the page centre each column
    on its own spot: without it a column at the day's low has almost nothing
    below it, on exactly the trending days a spot-centred chart is for. The page
    draws its spot frame ``edge_side`` strikes tall for that reason.

    ``edge_side=0`` is the rule as it was before the spot frame, exactly. Any
    larger value only ever adds strikes, and each one is paid for in every
    view's history, every minute: measured on stored sessions with
    ``tools/measure_gamma_crop.py``, 20 added 36% to $SPX's history on a 1.1%
    day. It is ``config/gamma_heat.toml [window] spot_side``.

    None when there is no usable spot at all, current or stored: the grids are
    then left uncropped, because there is nothing to window around."""
    path = [p for p in path or () if finite(p) is not None]
    anchor = spot if finite(spot) is not None else (path[0] if path else None)
    if anchor is None:
        return None
    keep = window_around(strikes, anchor, n_side)
    if path:
        lo, hi = min(path), max(path)
        keep |= {k for k in strikes if isinstance(k, (int, float))
                 and not isinstance(k, bool) and lo <= k <= hi}
        if edge_side > 0:
            keep |= (window_around(strikes, lo, edge_side)
                     | window_around(strikes, hi, edge_side))
    return keep


# ── the heatmap's locked colour scale ────────────────────────────────────────
_LOCK_VALUES = ("net", "call", "put", "size")


def _cell_values(cell):
    """``{value: number}`` for one stored cell. A side the cell lacks is absent:
    a bare number is its own net and has no call, put or size."""
    if not isinstance(cell, dict):
        net = finite(cell)
        return {} if net is None else {"net": net}
    out = {}
    net, call, put = (finite(cell.get("net")), finite(cell.get("call")),
                      finite(cell.get("put")))
    if net is not None:
        out["net"] = net
    if call is not None:
        out["call"] = call
    if put is not None:
        out["put"] = put
    if call is not None and put is not None:
        out["size"] = abs(call) + abs(put)
    return out


def _float_keys(grid):
    """``{float strike: cell}``. A grid stored in the legacy JSON format carries
    its strikes as text; a key that is not a number is dropped."""
    out = {}
    for raw, cell in grid.items():
        try:
            strike = finite(float(raw))
        except (TypeError, ValueError):
            continue
        if strike is not None and not isinstance(raw, bool):
            out[strike] = cell
    return out


def scale_lock(rows, *, n_side, minutes, quantile, headroom):
    """The heatmap's locked colour maximum per value, or None before it exists.

    Taken from the session's first ``minutes`` only: the ``quantile`` of the
    absolute cell, inside the display window around each row's OWN spot, times
    ``headroom``. Rows are append-only for a session, so the answer is the same
    on every build once those minutes have passed. That is what lets the page
    print it as a fixed figure: a lock computed from the cropped rows the page
    holds would drift as the crop followed spot.

    ``rows`` are the UNCROPPED history rows ``(ts, spot, …, grid)``, and are not
    written to. Returns ``{"minutes", "net", "call", "put", "size"}``; a value no
    cell carries is None. Total: anything unreadable yields None, never an
    exception. The three parameters are ``config/gamma_heat.toml [lock]``."""
    usable = [r for r in rows or ()
              if isinstance(r, (list, tuple)) and len(r) > 6
              and finite(r[0]) is not None]
    if not usable:
        return None
    end = usable[0][0] + minutes * 60
    if usable[-1][0] < end:
        return None
    seen = {name: [] for name in _LOCK_VALUES}
    for row in usable:
        if row[0] >= end:
            break
        spot, grid = finite(row[1]), row[6]
        if spot is None or not isinstance(grid, dict):
            continue
        by_strike = _float_keys(grid)
        for strike in window_around(by_strike, spot, n_side):
            for name, value in _cell_values(by_strike[strike]).items():
                if value:
                    seen[name].append(abs(value))
    if not seen["net"]:
        return None
    out = {"minutes": minutes}
    for name, values in seen.items():
        values.sort()
        out[name] = (values[min(len(values) - 1, int(quantile * (len(values) - 1)))]
                     * headroom) if values else None
    return out

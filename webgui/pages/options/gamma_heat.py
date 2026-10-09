"""The Dealer Positioning heatmap's transforms: which number a cell holds, how it
is scaled, and what the vertical axis measures.

PURE. No NiceGUI, no bus, and nothing imported from ``gamma`` (``gamma`` imports
this). Every function builds new lists: the history rows arrive through
``bus_client.read_shared`` and are shared by every open tab, so a transform that
wrote to its input would corrupt every other tab's chart.

Design: docs/plans/2026-10-09-gamma-heatmap-value-scale-frame-design.md
"""
from pages import fmt as _fmt

# value key -> the control's label. The order is the picker's order.
VALUES = {"net": "Net", "call": "Calls", "put": "Puts", "size": "Size"}


def cell_value(cell, mode="net"):
    """The number one stored cell draws for ``mode``, or None when it has none.

    ``size`` is ``abs(call) + abs(put)`` carrying the sign of ``net``: how much is
    at the strike, coloured by which way it leans. A strike whose calls and puts
    cancel is large here and zero in ``net``, which is the point of the mode.
    A cell with exactly zero net sits on the call side by convention.
    """
    if mode not in VALUES:
        raise ValueError(f"unknown heatmap value: {mode!r}")
    if not isinstance(cell, dict):
        return _fmt.num(cell) if mode == "net" else None
    if mode == "net":
        return _fmt.num(cell.get("net"))
    call, put = _fmt.num(cell.get("call")), _fmt.num(cell.get("put"))
    if mode == "call":
        return call
    if mode == "put":
        return put
    if call is None or put is None:
        return None
    net = _fmt.num(cell.get("net"))
    size = abs(call) + abs(put)
    return -size if (call + put if net is None else net) < 0 else size


def has_sides(grids):
    """Whether any cell in these grids carries BOTH a call and a put.

    Rows stored before the cells were split are bare numbers; with none that
    carry sides, Calls, Puts and Size have nothing to draw and are switched off
    rather than drawn as zero."""
    for grid in grids or ():
        if not isinstance(grid, dict):
            continue
        for cell in grid.values():
            if (isinstance(cell, dict) and _fmt.num(cell.get("call")) is not None
                    and _fmt.num(cell.get("put")) is not None):
                return True
    return False


def balanced_marks(grid, strikes, *, max_polarity, min_size_quantile, max_marks):
    """The strikes in ``strikes`` that hold a lot and lean little, largest first.

    A strike qualifies when ``abs(net) / size`` is at or under ``max_polarity``
    and its size is at or above the ``min_size_quantile`` rank of the strikes
    given. These are the strikes ``net`` draws as empty, and ``size`` draws in a
    hue that a small change of net can flip, so they are named on the axis.
    The three thresholds are ``config/gamma_heat.toml [balanced]``."""
    sized = []
    for strike in strikes or ():
        cell = (grid or {}).get(strike)
        size, net = cell_value(cell, "size"), cell_value(cell, "net")
        if not size or net is None:
            continue
        sized.append((abs(size), abs(net) / abs(size), strike))
    if not sized:
        return []
    ranked = sorted(s for s, _, _ in sized)
    floor = ranked[min(len(ranked) - 1, int(min_size_quantile * (len(ranked) - 1)))]
    hits = sorted(((s, k) for s, lean, k in sized
                   if s >= floor and lean <= max_polarity), reverse=True)
    return [k for _, k in hits[:max(0, int(max_marks))]]

#!/usr/bin/env python
"""Draw the manuals' architecture diagrams: one SVG and one PNG each.

    python docs/manuals/diagrams/make_diagrams.py          # all
    python docs/manuals/diagrams/make_diagrams.py system-overview

Each diagram is drawn ONCE here, in matplotlib, and saved twice beside this
file: ``<name>.svg`` is inlined into the HTML manual (the in-app manuals are
single self-contained files; a linked image would not be served), and
``<name>.png`` is embedded in the Word build, which cannot take SVG. A manual
places one with a paragraph of its own: ``{{diagram: <name> | Caption}}``
(``build_docs.py`` expands it; ``tests/test_manual_diagrams.py`` checks every
marker names a diagram drawn here and every diagram drawn here is placed).

⚠ A diagram is documentation: when a port, a process or a flow moves, redraw
it here, rerun this script, and rebuild the manuals in the same commit. Draw
what RUNS; anything not built yet is drawn dashed and says "planned".

Coordinates are pixels on a 1000-wide canvas, y growing downwards.
"""
from __future__ import annotations

import pathlib
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyBboxPatch  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
W = 1000

plt.rcParams.update({
    "font.family": "sans-serif",
    "font.sans-serif": ["DejaVu Sans"],
    "svg.fonttype": "none",          # text stays text in the SVG (small, searchable)
    "svg.hashsalt": "manual-diagrams",  # stable ids: a rebuild is not a diff
})

# fill, edge, text -- light ramps; the manuals are printed and read light.
COLORS = {
    "gray": ("#F1EFE8", "#5F5E5A", "#2C2C2A"),
    "teal": ("#E1F5EE", "#0F6E56", "#085041"),
    "purple": ("#EEEDFE", "#534AB7", "#3C3489"),
    "amber": ("#FAEEDA", "#854F0B", "#633806"),
}
LINE = "#5F5E5A"
DASH = (0, (5, 4))


def canvas(h):
    fig = plt.figure(figsize=(W / 100, h / 100), dpi=100)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, W)
    ax.set_ylim(h, 0)
    ax.axis("off")
    return fig, ax


def box(ax, x, y, w, h, title, sub=None, color="gray", planned=False):
    fill, edge, text = COLORS[color]
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0,rounding_size=8",
                                fc=fill, ec=edge, lw=1.0,
                                ls=DASH if planned else "-"))
    cx = x + w / 2
    if sub:
        ax.text(cx, y + h / 2 - 10, title, ha="center", va="center", fontsize=11,
                fontweight="bold", color=text)
        ax.text(cx, y + h / 2 + 11, sub, ha="center", va="center", fontsize=9, color=edge)
    else:
        ax.text(cx, y + h / 2, title, ha="center", va="center", fontsize=11,
                fontweight="bold", color=text)


def container(ax, x, y, w, h, label, color, label_x=None, bottom=False):
    """A dashed group. ``label_x`` (absolute) moves the label clear of an arrow
    that enters the group; ``bottom`` puts it along the bottom edge instead."""
    _, edge, _ = COLORS[color]
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0,rounding_size=14",
                                fc="none", ec=edge, lw=1.0, ls=DASH))
    ax.text(x + 16 if label_x is None else label_x, y + h - 16 if bottom else y + 18,
            label, ha="left", va="center", fontsize=9.5, color=edge)


def arrow(ax, p1, p2, both=False, dashed=False):
    ax.annotate("", xy=p2, xytext=p1, arrowprops=dict(
        arrowstyle="<|-|>" if both else "-|>", color=LINE, lw=1.0,
        shrinkA=0, shrinkB=0, mutation_scale=11, linestyle="--" if dashed else "-"))


def path(ax, points, dashed=False):
    """A polyline with an arrowhead on its last segment."""
    xs, ys = zip(*points[:-1])
    ax.plot(list(xs) + [points[-2][0]], list(ys) + [points[-2][1]], color=LINE, lw=1.0,
            ls="--" if dashed else "-")
    arrow(ax, points[-2], points[-1], dashed=dashed)


def note(ax, x, y, text, ha="left"):
    ax.text(x, y, text, ha=ha, va="center", fontsize=8.5, color=LINE, style="italic")


def legend(ax, y, items):
    x = 40
    for label, color, dashed in items:
        _, edge, _ = COLORS[color]
        ax.add_patch(FancyBboxPatch((x, y - 7), 22, 14, boxstyle="round,pad=0,rounding_size=3",
                                    fc="none", ec=edge, lw=1.0, ls=DASH if dashed else "-"))
        ax.text(x + 30, y, label, ha="left", va="center", fontsize=9, color=LINE)
        x += 40 + 7.2 * len(label)


def save(fig, name):
    fig.savefig(HERE / f"{name}.svg", metadata={"Date": None})
    fig.savefig(HERE / f"{name}.png", dpi=200, metadata={"Software": None})
    plt.close(fig)


# ---------------------------------------------------------------------------

def system_overview():
    fig, ax = canvas(730)
    box(ax, 190, 20, 220, 44, "You, in a browser")
    box(ax, 590, 20, 220, 44, "Public visitors")
    box(ax, 150, 100, 700, 56, "Caddy (HTTPS)", "app. needs password + TOTP  ·  live. has no login")
    arrow(ax, (300, 64), (300, 98))
    arrow(ax, (700, 64), (700, 98))

    container(ax, 40, 185, 920, 115, "Tier 1 · web: renders pages, imports no engine", "purple",
              label_x=345)
    box(ax, 150, 218, 300, 60, "webgui :8500", "The private app", "purple")
    box(ax, 550, 218, 300, 60, "webgui_live :8501", "Public screens, read-only", "purple")
    arrow(ax, (300, 156), (300, 216))
    arrow(ax, (700, 156), (700, 216))

    box(ax, 250, 345, 500, 60, "Redis :6379", "Cache views  ·  events  ·  command streams", "amber")
    arrow(ax, (300, 278), (300, 343), both=True)
    arrow(ax, (700, 278), (700, 343), both=True)
    note(ax, 310, 322, "reads, subscribes, sends commands")
    note(ax, 710, 322, "reads; three public request paths")

    container(ax, 40, 440, 920, 150, "Tier 2 · services: each owns its scheduler, commands and engines", "teal")
    services = [("sentiment", ":8210"), ("options", ":8211"), ("portfolio", ":8212"),
                ("trade", ":8213"), ("market", ":8215"), ("news", ":8216"), ("blog", ":8217")]
    for i, (name, port) in enumerate(services):
        box(ax, 63 + i * 126, 478, 118, 60, name, port, "teal")
    note(ax, 500, 565, "publish views and events to Redis  ·  consume their own cmd:{domain}", ha="center")
    arrow(ax, (500, 405), (500, 438), both=True)

    box(ax, 60, 640, 260, 60, "schwab-proxy :8100", "The only Schwab client", "gray")
    box(ax, 380, 640, 200, 60, "Schwab API", "Chains, quotes, accounts", "gray")
    box(ax, 640, 640, 150, 60, "Public feeds", "RSS, EDGAR, FRED", "gray")
    box(ax, 820, 640, 140, 60, "Claude API", "Briefings", "gray")
    arrow(ax, (190, 590), (190, 638))
    arrow(ax, (320, 670), (378, 670))
    arrow(ax, (752, 590), (715, 638))
    arrow(ax, (885, 590), (885, 638))
    save(fig, "system-overview")


def request_flow():
    fig, ax = canvas(260)
    steps = [("1  Page", "Sends a command", "purple"),
             ("2  cmd:{domain}", "A Redis stream", "amber"),
             ("3  Service", "Consumes, dispatches", "teal"),
             ("4  Compute", "Engines; proxy for data", "teal"),
             ("5  Cache view", "Write, bump :ver, event", "amber")]
    for i, (title, sub, color) in enumerate(steps):
        x = 25 + i * 198
        box(ax, x, 50, 165, 70, title, sub, color)
        if i:
            arrow(ax, (x - 33, 85), (x - 2, 85))
    path(ax, [(899.5, 120), (899.5, 190), (107.5, 190), (107.5, 122)])
    note(ax, 503, 208, "The page's poll sees the new version, reads the view and repaints",
         ha="center")
    save(fig, "request-flow")


def deploy_units():
    fig, ax = canvas(400)
    box(ax, 30, 120, 170, 64, "Redis :6379", "A system unit", "amber")
    container(ax, 240, 40, 730, 250, "trading-prod.target: a promote stops and starts all of it", "purple")
    box(ax, 270, 80, 180, 64, "schwab-proxy", ":8100, starts first", "gray")
    box(ax, 490, 80, 200, 64, "Seven services", ":8210 to :8217", "teal")
    box(ax, 730, 80, 210, 64, "webgui :8500", "Ordered after the proxy", "purple")
    box(ax, 730, 190, 210, 64, "webgui_live :8501", "Needs only Redis", "purple")
    arrow(ax, (200, 140), (268, 112))
    arrow(ax, (450, 112), (488, 112))
    arrow(ax, (690, 112), (728, 112))
    path(ax, [(115, 184), (115, 222), (728, 222)])
    box(ax, 240, 315, 460, 60, "Timers", "Backup, reports, captures: outside the target", "gray")
    note(ax, 720, 345, "Arrows: start order (After=)")
    save(fig, "deploy-units")


def minute_collection():
    fig, ax = canvas(500)
    box(ax, 380, 20, 240, 56, "Scheduler", "Every minute, in the window", "gray")
    box(ax, 380, 110, 240, 56, "fetch_minute", "A chain per symbol, 6 at a time", "teal")
    box(ax, 60, 110, 240, 56, "schwab-proxy", "Priority lane", "gray")
    arrow(ax, (500, 76), (500, 108))
    arrow(ax, (378, 138), (302, 138), both=True)

    box(ax, 380, 210, 240, 56, "derive_minute", "Priced at the minute", "teal")
    box(ax, 60, 210, 240, 56, "on_chain hooks", "Tally, tprem, UOA, HIRO", "teal")
    arrow(ax, (500, 166), (500, 208))
    arrow(ax, (378, 238), (302, 238))

    box(ax, 380, 310, 240, 56, "gex_history.db", "Five views + term rows", "gray")
    arrow(ax, (500, 266), (500, 308))
    box(ax, 380, 410, 240, 56, "After the poll", "Alerts, matrix, net premium", "teal")
    arrow(ax, (500, 366), (500, 408))
    box(ax, 60, 410, 240, 56, "Redis views", "Pages repaint from here", "amber")
    arrow(ax, (378, 438), (302, 438))

    container(ax, 680, 180, 290, 300, "Only in tee (config/feed.toml)", "amber", bottom=True)
    box(ax, 705, 215, 240, 56, "Raw chain store", "One file per session", "amber")
    box(ax, 705, 305, 240, 56, "Shadow check", "Re-derive and compare", "amber")
    box(ax, 705, 395, 240, 56, "cache:feed:shadow", "Differences, timings", "amber")
    path(ax, [(620, 138), (825, 138), (825, 213)])
    note(ax, 632, 126, "the same minute, before deriving")
    arrow(ax, (825, 271), (825, 303))
    arrow(ax, (825, 361), (825, 393))
    arrow(ax, (622, 338), (703, 333), dashed=True)
    save(fig, "minute-collection")


def raw_feed_target():
    fig, ax = canvas(780)
    box(ax, 170, 20, 220, 48, "Schwab API")
    arrow(ax, (280, 68), (280, 133))

    container(ax, 40, 100, 920, 130, "Feed target: left running by a market-hours promote (planned)",
              "teal", label_x=320)
    box(ax, 120, 135, 320, 64, "schwab-proxy :8100", "Sign-in, 5 calls a second", "teal")
    box(ax, 560, 135, 320, 64, "feed_svc :8218", "Fetches every minute (planned)", "teal", planned=True)
    arrow(ax, (442, 167), (558, 167))

    box(ax, 560, 270, 320, 64, "Raw chain store", "One SQLite file per session", "gray")
    arrow(ax, (720, 199), (720, 268))
    arrow(ax, (720, 334), (720, 408))

    container(ax, 40, 370, 920, 330, "App target: restarted by a promote, then catches up", "purple",
              label_x=100)
    box(ax, 560, 410, 320, 64, "options_svc deriver", "Behind a cursor (planned)", "purple", planned=True)
    box(ax, 120, 410, 320, 64, "gex_history.db", "GEX, flow and HIRO rows", "gray")
    arrow(ax, (558, 442), (442, 442))
    box(ax, 560, 510, 320, 64, "Redis :6379", "Cache views, events", "amber")
    arrow(ax, (720, 474), (720, 508))
    box(ax, 120, 510, 320, 64, "Other services", "Scans, sentiment, trade, market", "purple")
    arrow(ax, (442, 542), (558, 542))
    path(ax, [(120, 542), (80, 542), (80, 167), (118, 167)])
    box(ax, 560, 610, 320, 56, "Web apps :8500, :8501", "Read Redis only", "purple")
    arrow(ax, (720, 574), (720, 608))

    legend(ax, 740, [("Built", "gray", False), ("Planned (Phases 3 to 5)", "gray", True)])
    save(fig, "raw-feed-target")


DIAGRAMS = {
    "system-overview": system_overview,
    "request-flow": request_flow,
    "deploy-units": deploy_units,
    "minute-collection": minute_collection,
    "raw-feed-target": raw_feed_target,
}


def main(argv) -> int:
    names = argv[1:] or list(DIAGRAMS)
    for name in names:
        DIAGRAMS[name]()
        print(f"  [ok] {name}.svg + {name}.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

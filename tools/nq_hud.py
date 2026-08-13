"""
nq_hud.py - Dealer-Positioning Entry HUD (NQ + ES, side by side)
Version: 2.0.0
Last Updated: 2026-07-30

An always-on-top desktop HUD for MANUAL, RTH-only index-futures day trading.
Classifies the dealer-gamma regime from collected options data, converts the key
levels into futures points, and renders a LONG / SHORT / STAND DOWN verdict with
risk-management levels — for NQ and ES simultaneously, in adjacent panes.

Version 2.0.0 Changes:
- ES pane added beside NQ. Both instruments run the same pure logic; everything
  that differs between them is data on an Instrument spec (nq_instruments.py).
- One tape read and one DB connection now serve BOTH panes, so the second
  instrument costs no extra round-trips.
- Per-instrument stop bands. NDX trades near 4x SPX, so sharing NQ's
  point-denominated 15-45 clamp would have sized ES stops ~4x too wide.

WHY BOTH AT ONCE: NQ and ES rarely disagree about direction, but they routinely
disagree about STRUCTURE — one can sit mid-range in positive gamma while the
other is pressed against a wall or has broken into negative gamma. Seeing the
two maps together is what tells you whether a setup is an index-wide dealer
effect or a single-index artifact. Because the level-vs-spot differentials are
frame-independent, the two panes' numbers are directly comparable.

────────────────────────────────────────────────────────────────────────────
ARCHITECTURE — this is a TIER-1 READER. It does not compute market data.

  * Tape (futures + cash + VIX)  <- cache:market:dashboard via shared.bus.Bus
                                    (market_svc already polls /quotes ~2s RTH)
  * Gamma grids / flip / walls   <- options-scanner/gex_history.db, READ-ONLY
                                    (options_svc collects 1-min snapshots
                                     08:00-15:20 CT for the whole universe)

It makes NO Schwab calls, imports NO engines except the pure wall picker in
gamma_tool, opens the history DB read-only, and writes nothing anywhere. It
therefore cannot destabilise the running 8-process stack, and needs no port.

WHY NOT cache:options:gamma? That key holds exactly ONE symbol — whichever the
/options/gamma page currently has selected (handlers.refresh_gamma defaults to
$SPX). Reading it would silently show one index's gamma under the other's label,
which with two panes on screen is a mistake you would act on. The history DB is
per-symbol by construction, so it is the correct source.

SOURCE SYMBOL: each instrument prefers its cash index ($NDX / $SPX) and falls
back to its ETF proxy (QQQ / SPY). The ETFs carry heavy structural
call-overwriting flow, which can invert the apparent gamma sign, so the active
source is always shown in that pane's header.
────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import logging
import pathlib
import sys
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

# Repo root on sys.path -> repo_paths / shared / tools.* are importable (same
# pattern as webgui/proxy.py and shared/bus/client.py).
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from repo_paths import OPTIONS_SCANNER  # noqa: E402

# Per-instrument specs — cash index, tape tiles, contract, point values and the
# stop band. Everything that differs between NQ and ES lives there.
from tools.nq_instruments import INSTRUMENTS  # noqa: E402

# PURE signal logic — conversion / regime / session / stops / verdict. Kept in
# its own module so the whole decision surface is testable without Redis,
# SQLite or tkinter. See tools/nq_signal.py.
from tools.nq_signal import (  # noqa: E402
    MIN_REWARD_RISK,
    PHASE_NOTE,
    build_verdict,
    cash_scale,
    cash_stale_reason,
    classify_regime,
    pick_flip,
    session_phase,
    shift_verdict_levels,
    to_future,
    to_index,
)

# Append-only verdict-transition log (write-only; nothing reads it at runtime).
from tools.nq_signal_log import SignalLogger  # noqa: E402
# Current-state export for the NinjaTrader indicator (also write-only).
from tools.nq_state import StateWriter  # noqa: E402

# options-scanner on sys.path -> gamma_tool (pure wall picker) + gex_history_db.
# Imported lazily inside read_gamma_all so an import failure degrades the gamma
# panes rather than preventing the window from opening at all.
if str(OPTIONS_SCANNER) not in sys.path:
    sys.path.insert(0, str(OPTIONS_SCANNER))

log = logging.getLogger("nq_hud")

#############################################
# CONFIGURATION
#############################################

CT = ZoneInfo("America/Chicago")

# Snapshot staleness. The collector runs 1-min; >150s means it has stalled.
STALE_AFTER_SEC = 150

# Tape staleness. market_svc republishes cache:market:dashboard every ~2s during
# RTH and ~5s off-hours, so a minute of silence means it is dead. This matters
# more than it looks: the BASIS is derived from the tape, so a frozen
# future/cash pair silently shifts every converted level. Observed live —
# market_svc died overnight and the cache sat 12 hours stale while the HUD
# showed it as fine.
TAPE_STALE_AFTER_SEC = 60

REFRESH_SEC = 2.0

# Window geometry. One column per instrument. Height is derived from the built
# widget tree at startup (see Hud.__init__); these are only the floor and the
# screen allowance.
PANE_WIDTH = 420
WIN_WIDTH = PANE_WIDTH * len(INSTRUMENTS) + 30
WIN_MIN_HEIGHT = 680
WIN_SCREEN_MARGIN = 80   # taskbar + title bar
WRAP = PANE_WIDTH - 40   # wraplength for the reason line

# The regime / session-window / risk-sizing constants and the pure logic that
# closes over them live in tools/nq_signal.py (imported above), so they can be
# exercised without Redis, SQLite or tkinter.

# VIX is shared by both panes, so unlike the per-instrument tiles it is not on
# the Instrument spec. Name as written in services/market_svc/symbols.py.
TILE_VIX = "VIX"

CACHE_MARKET = "cache:market:dashboard"

#############################################
# COLORS
#############################################

BG = "#0d1220"
BG_PANEL = "#161d2f"
BG_INPUT = "#1e2740"
FG = "#e8ecf5"
FG_DIM = "#8792ab"
GREEN = "#2ecc71"
RED = "#e74c3c"
AMBER = "#f0a020"
BLUE = "#4a9eff"
PURPLE = "#9b7fe0"
GRAY = "#666f85"

# action -> colour. Presentation lives HERE, not in nq_signal.build_verdict:
# keeping colour out of the pure module is what lets it be imported (and tested)
# without tkinter. .get() falls back to GRAY so an unrecognised action can never
# raise on the UI thread.
ACTION_COLOR = {
    "LONG": GREEN,
    "SHORT": RED,
    "WAIT": AMBER,
    "STAND DOWN": GRAY,
}

REGIME_TEXT = {
    "positive": ("POSITIVE GAMMA — mean reversion", BLUE),
    "negative": ("NEGATIVE GAMMA — continuation", RED),
    "flip_zone": ("FLIP ZONE — whipsaw", AMBER),
    "unknown": ("REGIME UNKNOWN", GRAY),
}


#############################################
# TIME / SESSION HELPERS
#############################################

def market_now():
    """Current time in exchange (Central) time."""
    return datetime.now(CT)


#############################################
# DATA READERS (Tier-1: Redis + read-only SQLite)
#############################################

def read_tape(bus, specs=INSTRUMENTS):
    """Pull every instrument's future + cash price, plus VIX, in ONE cache read.

    Returns::

        {"vix": float|None, "age_s": float|None, "ok": bool,
         "<key>": {"fut": float|None, "fut_pct": float|None, "cash": float|None},
         ...}

    One read for all panes, deliberately: the dashboard payload is a single
    document, so re-reading it per instrument would double the Redis traffic AND
    let the two panes see different snapshots of the tape — exactly the kind of
    inconsistency that makes two side-by-side readouts untrustworthy.

    Defensive: any failure degrades to an all-None dict so the HUD paints
    "no data" rather than dying.
    """
    out = {"vix": None, "age_s": None, "ok": False}
    for spec in specs:
        out[spec.key] = {"fut": None, "fut_pct": None, "cash": None}
    try:
        env = bus.cache_get(CACHE_MARKET)
        if env is None:
            return out
        payload = env.payload or {}

        # tile display name -> (instrument key or None for shared, slot)
        wanted = {TILE_VIX: (None, "vix")}
        for spec in specs:
            wanted[spec.future_tile] = (spec.key, "fut")
            wanted[spec.cash_tile] = (spec.key, "cash")

        for cat in payload.get("categories", []):
            for tile in cat.get("tiles", []):
                dest = wanted.get(tile.get("display"))
                if dest is None:
                    continue
                key, slot = dest
                if key is None:
                    out[slot] = tile.get("last")
                else:
                    out[key][slot] = tile.get("last")
                    if slot == "fut":
                        out[key]["fut_pct"] = tile.get("change_pct")
        try:
            ts = datetime.fromisoformat(env.ts)
            out["age_s"] = (datetime.now(ts.tzinfo) - ts).total_seconds()
        except Exception:
            out["age_s"] = None
        # ok means USABLE, not merely present. The age was already being
        # computed here and then discarded, so a dead market_svc read as
        # healthy while its last prices aged for hours — and since the basis
        # comes from those prices, every level was quietly built on them.
        # An age we cannot establish fails closed for the same reason.
        #
        # This is the SHARED half of the check (is the publisher alive?).
        # Whether a given pane actually has its own two prices is decided per
        # pane by tape_usable, since one instrument's tile can be missing while
        # the other's is fine.
        out["ok"] = (out["age_s"] is not None
                     and out["age_s"] <= TAPE_STALE_AFTER_SEC
                     and any(out[s.key]["fut"] is not None for s in specs))
    except Exception:
        log.debug("tape read failed", exc_info=True)
    return out


def tape_usable(tape, spec):
    """True when ``spec``'s pane has a fresh future AND cash price.

    Both are required because the basis is their difference: with either one
    missing or stale, every converted level in that pane is wrong.
    """
    if not tape.get("ok"):
        return False
    leg = tape.get(spec.key) or {}
    return leg.get("fut") is not None and leg.get("cash") is not None


def pick_source_symbol(conn, gh, today, sources):
    """First symbol in ``sources`` that has a GEX row for ``today``.

    The cash index is the correct underlying; the ETF is the always-collected
    fallback. Returns (symbol, None) or (None, reason).
    """
    for sym in sources:
        try:
            if gh.latest_spot_flip(conn, sym, "gex", today) is not None:
                return sym, None
        except Exception:
            continue
    return None, "no GEX snapshots today for " + " or ".join(sources)


def _gamma_blank():
    return {"ok": False, "reason": "", "symbol": None, "spot": None,
            "flip": None, "call_wall": None, "put_wall": None,
            "net_total": None, "pin": None, "snap_age_s": None,
            "atr_proxy": None, "session_date": None,
            "pin_top_pos": None, "flip_stored": None, "flip_computed": None}


def read_gamma_all(specs=INSTRUMENTS, source_date=None):
    """Load the latest GEX snapshot for every instrument. Returns {key: result}.

    ONE read-only connection serves all panes and is closed on the way out —
    the collector holds the write lock and must never be blocked. Opening the DB
    per instrument would double that churn on every 2s poll for no benefit.
    """
    blank = {spec.key: _gamma_blank() for spec in specs}
    try:
        import gamma_tool as gt
        import gex_history_db as gh
    except Exception as exc:
        for res in blank.values():
            res["reason"] = f"engine import failed: {exc}"
        return blank

    try:
        conn = gh.connect(read_only=True)
    except Exception as exc:
        for res in blank.values():
            res["reason"] = f"gex_history.db unavailable: {exc}"
        return blank

    try:
        today = source_date or market_now().date()
        return {spec.key: read_gamma(spec, conn, gt, gh, today) for spec in specs}
    finally:
        try:
            conn.close()
        except Exception:
            pass


def read_gamma(spec, conn, gt, gh, today):
    """Load ``spec``'s latest GEX snapshot from an ALREADY-OPEN connection.

    Returns a dict with spot / flip / walls / net_total / pin / atr_proxy, or
    {"ok": False, "reason": ...}. Never raises: one instrument failing to read
    must leave the other pane fully functional.
    """
    res = _gamma_blank()
    try:
        symbol, why = pick_source_symbol(conn, gh, today, spec.sources)
        if symbol is None:
            # Off-hours / pre-open: fall back to the prior collected session.
            for back in range(1, 6):
                prior = _prior_day(today, back)
                symbol, why = pick_source_symbol(conn, gh, prior, spec.sources)
                if symbol is not None:
                    today = prior
                    break
        if symbol is None:
            res["reason"] = why or "no GEX data"
            return res

        res["symbol"] = symbol
        res["session_date"] = today

        # ── Newest snapshot's SUMMARY — grid-free. ───────────────────────────
        latest = gh.latest_spot_flip(conn, symbol, "gex", today)
        if latest is None:
            res["reason"] = f"no rows for {symbol}"
            return res
        ts, spot, flip_stored = latest
        res.update(spot=spot, flip=flip_stored, flip_stored=flip_stored)
        res["snap_age_s"] = max(0.0, time.time() - float(ts))

        # ── EXACTLY ONE grid decode per instrument: the newest row. ──────────
        # This poll runs every 2s. load_date_with_grid() over the whole session
        # zlib-decompresses + JSON-parses EVERY row's grid (~390 by the close,
        # ~114 strikes each) — the hotspot CLAUDE.md records being removed from
        # gamma_snapshot once already, at 1-min cadence. `since_ts` filters
        # `ts > since_ts`, so ts-1 returns just the newest row.
        grid, top_pos = {}, None
        try:
            newest = gh.load_date_with_grid(conn, symbol, "gex", today,
                                            since_ts=int(ts) - 1)
            if newest:
                top_pos = newest[-1][3]
                res["net_total"] = newest[-1][5]
                grid = newest[-1][6] or {}
        except Exception:
            log.debug("grid read failed", exc_info=True)

        # get_directional_walls expects the VIEW dict, so wrap the stored grid
        # under its "gex" key — the same wrapping compute.py uses when it picks
        # walls for the DEX view (services/options_svc/compute.py:1427).
        # _decode_grid hands back FLOAT strike keys, which both the wall
        # picker's `s > spot` and the pin's max() rely on.
        try:
            walls = gt.get_directional_walls({"gex": grid}, spot)
            res["call_wall"] = walls.get("call_wall")
            res["put_wall"] = walls.get("put_wall")
        except Exception:
            log.debug("wall picker failed", exc_info=True)

        # Recompute the FLIP from this snapshot's own grid rather than trusting
        # the stored column — the same treatment the walls and pin already get.
        # calc_flip_point takes the raw grid (it indexes gex[strike]["net"]),
        # NOT the {"gex": ...} view wrapper the wall picker wants. See
        # nq_signal.pick_flip for why the stored column is the wrong boundary.
        try:
            res["flip_computed"] = gt.calc_flip_point(grid, spot)
        except Exception:
            log.debug("flip recompute failed", exc_info=True)
        res["flip"] = pick_flip(res.get("flip_computed"), flip_stored)

        # Pin = the largest ABSOLUTE net-gamma strike; price gravitates to it
        # in a positive-gamma regime. NOTE (design §6): whether max(|net|) or
        # the stored top_pos_strike (= max(net) over POSITIVE strikes only) is
        # the better mean-reversion target is an OPEN question — pinning is
        # caused by positive dealer gamma, so a large negative-net strike is an
        # amplifier, not an attractor. The signal log records both to settle it.
        try:
            res["pin"] = max(grid.items(),
                             key=lambda kv: abs(kv[1].get("net", 0.0) or 0.0))[0]
        except Exception:
            res["pin"] = top_pos
        # The alternative candidate, carried alongside so the signal log can
        # record BOTH and the §6 question can be settled on data. Display and
        # the verdict still use res["pin"] only.
        res["pin_top_pos"] = top_pos

        # ── Session spot range for the ATR stop — grid-free. ─────────────────
        # load_flow_series reads the same gex-view rows by date but selects no
        # gex_json, so the range costs zero decodes.
        try:
            spots = [r[1] for r in gh.load_flow_series(conn, symbol, today) if r[1]]
            if len(spots) >= 2:
                res["atr_proxy"] = max(spots) - min(spots)
        except Exception:
            log.debug("spot series read failed", exc_info=True)

        res["ok"] = True
    except Exception as exc:
        res["reason"] = f"gamma read failed: {exc}"
        log.debug("gamma read failed", exc_info=True)
    return res


def _prior_day(d, back):
    from datetime import timedelta
    return d - timedelta(days=back)


#############################################
# PANE ASSEMBLY (pure, given the two reads)
#############################################

# Level keys carried through BOTH frames. pin_top_pos and flip_stored are the
# alternative-definition candidates: unused by the verdict, logged and exported
# so the open questions can be settled on data rather than argument.
LEVEL_KEYS = ("flip", "call_wall", "put_wall", "pin", "pin_top_pos",
              "flip_stored")


def build_pane(spec, tape, gamma, phase):
    """Assemble one instrument's decision state. PURE — no I/O, no clock.

    TWO FRAMES, deliberately (design §5).

    Decisions are made in CASH (index) terms, because that is what they were
    always really made in: basis is measured as future - cash, so a comparison
    of the future against a future-converted level reduces algebraically to
    cash-vs-level with the futures price cancelling out. Computing it in the
    cash frame makes the code say what it does instead of hiding a
    self-reference behind a conversion.

    Futures points are for DISPLAY — the numbers you type into NinjaTrader. The
    frames differ by an additive basis, so distances (ATR, stop size, wall
    proximity) are identical in both and only levels are shifted.
    """
    leg = tape.get(spec.key) or {}
    fut, cash = leg.get("fut"), leg.get("cash")

    scale = cash_scale(gamma.get("symbol"), cash, gamma.get("spot"))
    basis = None if fut is None or cash is None else fut - cash

    levels_cash = {k: to_index(gamma.get(k), scale) for k in LEVEL_KEYS}
    levels = {k: to_future(gamma.get(k), scale, basis) for k in LEVEL_KEYS}

    # Session range proxy for stop sizing. Frame-invariant (a difference), so
    # the same value serves both.
    atr_pts = (gamma["atr_proxy"] * scale
               if gamma.get("atr_proxy") and scale else None)

    regime, dist = classify_regime(cash, levels_cash.get("flip"))

    # Cash-freshness guard: the regime is anchored to an index that stops
    # ticking outside 08:30-15:00 CT. Refuse to assert a band rather than
    # showing a frozen one that looks live.
    stale = cash_stale_reason(phase, gamma.get("snap_age_s"), STALE_AFTER_SEC)
    # A frozen tape is a third way the regime becomes untrustworthy, and the
    # clock-based check cannot see it: during RTH the phase says cash is live
    # and the snapshot may be fresh, yet a dead market_svc leaves the basis
    # anchored to hours-old prices.
    if stale is None and not tape_usable(tape, spec):
        stale = "tape not updating"
    if stale:
        regime, dist = "unknown", None

    verdict_cash = build_verdict(regime, phase, cash, levels_cash, atr_pts, spec)
    # Back into futures points for the trader. The CASH-frame original is kept
    # so the state export can ship both: a NinjaTrader chart on a back-adjusted
    # continuous contract has to rebase entry/stop/target exactly as it rebases
    # the levels, or the two disagree by the adjustment offset.
    verdict = shift_verdict_levels(verdict_cash, basis)

    return {"spec": spec, "tape": leg, "gamma": gamma, "scale": scale,
            "basis": basis, "levels": levels, "levels_cash": levels_cash,
            "atr_pts": atr_pts, "regime": regime, "dist": dist,
            "regime_stale": stale, "verdict": verdict,
            "verdict_cash": verdict_cash}


def _longest_reason():
    """The longest string the reason line can ever display.

    Generated by exercising build_verdict across every regime x phase x
    spot-position combination rather than eyeballing the literals, so it stays
    correct when a reason is reworded. PHASE_NOTE is included because the
    not-tradeable branch returns those verbatim.
    """
    levels = {"call_wall": 100.0, "put_wall": 90.0, "pin": 95.0}
    texts = list(PHASE_NOTE.values())
    for regime in ("positive", "negative", "flip_zone", "unknown"):
        for phase in PHASE_NOTE:
            # Above the call wall, at it, mid-range, at the put wall, below it.
            for spot in (101.0, 100.0, 95.0, 90.0, 89.0):
                texts.append(build_verdict(regime, phase, spot, levels, 10.0,
                                           INSTRUMENTS[0])["reason"])
    return max(texts, key=len)


#############################################
# GUI
#############################################

class Hud:
    """Always-on-top CustomTkinter HUD. All data work runs off the UI thread."""

    def __init__(self, specs=INSTRUMENTS):
        import customtkinter as ctk
        self.ctk = ctk
        ctk.set_appearance_mode("dark")

        self.specs = tuple(specs)

        self.root = ctk.CTk()
        self.root.title("Dealer-Positioning HUD  ·  "
                        + " / ".join(s.label for s in self.specs))
        self.root.geometry(f"{WIN_WIDTH}x{WIN_MIN_HEIGHT}")
        self.root.attributes("-topmost", True)
        self.root.configure(fg_color=BG)

        self._bus = None
        self._state = None
        self._stop = threading.Event()
        self._labels = {}
        # Keys whose widget is a raw tkinter.Label rather than a CTkLabel — see
        # _wrap_label. They take `fg`, not `text_color`.
        self._raw_labels = set()
        self._poll_error = None
        # One logger per instrument, all appending to the SAME csv with an
        # `instrument` column: the interesting offline question is how the two
        # regimes relate, which a single file answers directly. Each tracks its
        # own previous state, so one pane's transition never suppresses the
        # other's.
        self._loggers = {s.key: SignalLogger(instrument=s.label)
                         for s in self.specs}
        # ONE state file holding both panes, written atomically, so the
        # NinjaTrader indicator can never render a half-updated pair.
        self._state_writer = StateWriter(stale_after_sec=STALE_AFTER_SEC)

        self._build()

        # Size to CONTENT, not to a literal. The panel stack needs ~900px at
        # 100% DPI — the original hardcoded 680 clipped the RISK panel at
        # "Entry", hiding the stop, target and dollar risk entirely (found by
        # actually rendering the window; every unit test passed regardless).
        # Measuring rather than bumping the constant keeps it correct under
        # different DPI scaling and font metrics, where reqheight differs.
        #
        # Measured against the WORST-CASE text, not the empty labels the tree
        # is built with. The wrapping reason line is three lines for a typical
        # verdict and four for the longest, so sizing to whatever happens to be
        # showing at startup (nothing) fixes the window ~90px too short and the
        # bottom clips as soon as a real verdict arrives.
        self._fill_worst_case_text()
        self.root.update_idletasks()
        height = max(WIN_MIN_HEIGHT, self.root.winfo_reqheight())
        self._clear_worst_case_text()
        # Never taller than the usable screen, or the bottom clips right back off.
        height = min(height, self.root.winfo_screenheight() - WIN_SCREEN_MARGIN)
        self.root.geometry(f"{WIN_WIDTH}x{height}")

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        threading.Thread(target=self._poll_loop, daemon=True).start()
        self._tick_ui()

    # ── layout ──────────────────────────────────────────────────────────
    def _panel(self, parent, pady=(0, 8)):
        f = self.ctk.CTkFrame(parent, fg_color=BG_PANEL, corner_radius=8)
        f.pack(fill="x", padx=10, pady=pady)
        return f

    def _fill_worst_case_text(self):
        """Put the longest text each wrapping label can ever hold into it.

        The reasons are a FINITE set — build_verdict only ever returns one of a
        handful of literals — so the worst case is enumerable rather than
        guessed: every (regime, phase, spot-position) combination is generated
        and the longest kept. That makes the startup height measurement an
        upper bound instead of a snapshot of an empty tree.
        """
        longest = _longest_reason()
        worst_regime = max(REGIME_TEXT.values(), key=lambda rv: len(rv[0]))[0] \
            + "   (+9,999 pts vs flip)   ·  gamma map 9999s stale"
        for key in self._raw_labels:
            if key.endswith(".reason"):
                self._labels[key].configure(text=longest)
            elif key.endswith(".regime"):
                self._labels[key].configure(text=worst_regime)
            elif key.endswith(".source"):
                # The degraded case, which is the long one. The ETF-proxy note
                # is NOT combined with it: that note only appears when a symbol
                # was found, and the long failure reasons are the ones where
                # none was, so the two cannot occur together.
                self._labels[key].configure(
                    text="source: —  ·  gex_history.db unavailable: unable to "
                         "open database file")
            elif key == "phase":
                self._labels[key].configure(
                    text=max(PHASE_NOTE.values(), key=len))

    def _clear_worst_case_text(self):
        for key in self._raw_labels:
            self._labels[key].configure(text="")

    def _wrap_label(self, parent, key, color=FG, size=11, bold=False,
                    center=False, bg=BG_PANEL, wrap=None):
        """A label whose text WRAPS to as many lines as it needs.

        Deliberately a raw tkinter.Label rather than a CTkLabel. Measured on
        customtkinter 6.0.0: a CTkLabel does not grow to fit wrapped text — with
        wraplength=380 a three-line verdict reason reports 28px where the text
        genuinely needs 66px, so everything after the first line is silently
        clipped. (A raw tk.Label with the identical text and wraplength reports
        382x66, which is the correct answer.) The reason line is what tells you
        whether to act on the headline, so losing it quietly is much worse than
        the cosmetic cost of a plain widget.

        tkinter is imported here, not at module scope, so nq_hud stays
        importable without a display — the discipline test_nq_hud_import pins.
        """
        import tkinter as tk

        lab = tk.Label(parent, text="", fg=color, bg=bg,
                       wraplength=wrap or WRAP,
                       justify="center" if center else "left",
                       anchor="center" if center else "w",
                       font=("Segoe UI", size, "bold" if bold else "normal"))
        self._labels[key] = lab
        self._raw_labels.add(key)
        return lab

    def _row(self, parent, key, label, value="—", vcolor=FG, bold=False):
        r = self.ctk.CTkFrame(parent, fg_color="transparent")
        r.pack(fill="x", padx=10, pady=2)
        self.ctk.CTkLabel(r, text=label, text_color=FG_DIM,
                          font=("Segoe UI", 11), anchor="w").pack(side="left")
        lab = self.ctk.CTkLabel(
            r, text=value, text_color=vcolor, anchor="e",
            font=("Consolas", 13, "bold" if bold else "normal"))
        lab.pack(side="right")
        self._labels[key] = lab
        return lab

    def _build(self):
        ctk = self.ctk

        # ── Shared header: publisher health, clock, VIX. These are one-per-app
        # facts, so duplicating them per pane would be noise.
        head = self._panel(self.root, pady=(10, 8))
        self._labels["health"] = ctk.CTkLabel(
            head, text="connecting…", text_color=AMBER, font=("Segoe UI", 11))
        self._labels["health"].pack(anchor="w", padx=10, pady=(6, 0))
        self._labels["vix"] = ctk.CTkLabel(
            head, text="VIX —", text_color=FG_DIM, font=("Segoe UI", 11))
        self._labels["vix"].pack(anchor="w", padx=10, pady=(0, 6))

        # ── One column per instrument, equal width.
        #
        # GRID with `uniform`, not pack. The obvious pack version needs
        # pack_propagate(False) to stop each column shrinking to its own
        # content — but propagation is not width-only: switching it off also
        # stops the column reporting its HEIGHT upward, so the window sizing in
        # __init__ measures a root that knows nothing about the panes and the
        # RISK panel gets clipped off the bottom. That is the exact defect the
        # measure-don't-assume sizing was written to prevent, and it came back
        # the moment propagation was disabled (caught by measuring the built
        # tree: every risk label sat unplaced at 1x1).
        #
        # grid's `uniform` makes the columns equal by construction, with
        # `minsize` as the floor, and leaves height propagation intact.
        cols = ctk.CTkFrame(self.root, fg_color="transparent")
        cols.pack(fill="both", expand=True)
        cols.grid_rowconfigure(0, weight=1)
        for i, spec in enumerate(self.specs):
            col = ctk.CTkFrame(cols, fg_color="transparent")
            col.grid(row=0, column=i, sticky="nsew")
            cols.grid_columnconfigure(i, weight=1, uniform="panes",
                                      minsize=PANE_WIDTH)
            self._build_pane(col, spec)

        # ── Shared session note.
        sf = self._panel(self.root)
        self._wrap_label(sf, "phase", color=FG_DIM,
                         wrap=WIN_WIDTH - 60).pack(fill="x", padx=10, pady=8)

    def _build_pane(self, parent, spec):
        ctk = self.ctk
        k = spec.key

        # Instrument banner + which gamma map is feeding it, on ONE row. Two
        # rows cost 28px of window height per column and the window already
        # sits within ~15px of the usable screen at the worst-case text.
        head = self._panel(parent, pady=(0, 8))
        row = ctk.CTkFrame(head, fg_color="transparent")
        row.pack(fill="x", padx=10, pady=6)
        ctk.CTkLabel(row, text=spec.label, text_color=FG,
                     font=("Segoe UI", 15, "bold")).pack(side="left")
        # Wrapping, because this line carries the gamma read's failure reason
        # when there is one, and those are long enough to need two lines.
        self._wrap_label(row, f"{k}.source", color=FG_DIM,
                         wrap=WRAP - 60).pack(side="left", padx=(10, 0),
                                              fill="x", expand=True)

        # Verdict — the headline.
        vf = self._panel(parent)
        self._labels[f"{k}.action"] = ctk.CTkLabel(
            vf, text="STAND DOWN", text_color=GRAY,
            font=("Segoe UI", 26, "bold"))
        self._labels[f"{k}.action"].pack(pady=(10, 2))
        self._wrap_label(vf, f"{k}.regime", color=FG_DIM, bold=True,
                         center=True).pack(fill="x", padx=10, pady=(0, 4))
        self._wrap_label(vf, f"{k}.reason").pack(fill="x", padx=10, pady=(0, 10))

        # Tape.
        tf = self._panel(parent)
        ctk.CTkLabel(tf, text="TAPE", text_color=BLUE,
                     font=("Segoe UI", 10, "bold")).pack(
                         anchor="w", padx=10, pady=(6, 2))
        self._row(tf, f"{k}.fut", spec.label, bold=True)
        self._row(tf, f"{k}.fut_pct", "Day %")
        self._row(tf, f"{k}.cash", f"{spec.cash_tile} cash")
        self._row(tf, f"{k}.basis", f"Basis ({spec.label}−{spec.cash_tile})")
        ctk.CTkLabel(tf, text="", height=4).pack()

        # Levels, in this instrument's futures points.
        lf = self._panel(parent)
        ctk.CTkLabel(lf, text=f"LEVELS  ({spec.label} points)",
                     text_color=PURPLE,
                     font=("Segoe UI", 10, "bold")).pack(
                         anchor="w", padx=10, pady=(6, 2))
        self._row(lf, f"{k}.call_wall", "Call wall", vcolor=RED)
        self._row(lf, f"{k}.flip", "Gamma flip", vcolor=BLUE, bold=True)
        self._row(lf, f"{k}.put_wall", "Put wall", vcolor=GREEN)
        self._row(lf, f"{k}.pin", "Pin (max γ)", vcolor=AMBER)
        ctk.CTkLabel(lf, text="", height=4).pack()

        # Risk.
        rf = self._panel(parent)
        ctk.CTkLabel(rf, text="RISK", text_color=AMBER,
                     font=("Segoe UI", 10, "bold")).pack(
                         anchor="w", padx=10, pady=(6, 2))
        self._row(rf, f"{k}.entry", "Entry")
        self._row(rf, f"{k}.stop", "Stop", vcolor=RED)
        self._row(rf, f"{k}.target", "Target", vcolor=GREEN)
        # Reward:risk sits directly under the three prices it is computed from,
        # because it is the number that decides whether they are worth taking —
        # and it is shown even when the setup was REFUSED for being too thin, so
        # a WAIT explains itself instead of just withholding.
        self._row(rf, f"{k}.rr", "Reward : risk", bold=True)
        self._row(rf, f"{k}.risk_full", f"Risk / 1 {spec.label}")
        self._row(rf, f"{k}.risk_micro", f"Risk / 1 {spec.micro_label}")
        ctk.CTkLabel(rf, text="", height=4).pack()

    # ── data thread ─────────────────────────────────────────────────────
    def _poll_loop(self):
        while not self._stop.is_set():
            try:
                self._state = self._collect()
                self._poll_error = None
            except Exception as exc:
                # Surface it on the health line, not just in the log. Without
                # this the window sits at its build-time "connecting…" forever
                # while the console fills with tracebacks — the failure is
                # invisible in the only place the trader is looking.
                self._poll_error = "{0}: {1}".format(type(exc).__name__, exc)
                log.exception("poll failed")
            self._stop.wait(REFRESH_SEC)

    def _collect(self):
        if self._bus is None:
            from shared.bus.client import Bus
            self._bus = Bus()

        now = market_now()
        phase = session_phase(now)
        # One tape read and one DB connection for BOTH panes, so they always
        # describe the same instant.
        tape = read_tape(self._bus, self.specs)
        gammas = read_gamma_all(self.specs)

        panes = {spec.key: build_pane(spec, tape, gammas[spec.key], phase)
                 for spec in self.specs}

        state = {"now": now, "phase": phase, "tape": tape, "panes": panes}

        # Record verdict TRANSITIONS for offline validation. Self-guarded and
        # write-only — nothing in the HUD reads it back, so a logging failure
        # can only cost a row.
        for spec in self.specs:
            self._loggers[spec.key].maybe_log(state, spec.key)
        # Export current state for the NinjaTrader indicator. Every poll, so
        # its timestamp doubles as a heartbeat.
        self._state_writer.write(state)
        return state

    # ── UI thread ───────────────────────────────────────────────────────
    def _tick_ui(self):
        try:
            self._paint()
        except Exception:
            log.exception("paint failed")
        self.root.after(int(REFRESH_SEC * 1000), self._tick_ui)

    def _set(self, key, text, color=None):
        lab = self._labels.get(key)
        if lab is None:
            return
        lab.configure(text=text)
        if color is not None:
            # The wrapping labels are raw tkinter.Labels (see _wrap_label), and
            # tkinter names the option `fg` while CustomTkinter names it
            # `text_color`. Tracked by key rather than probed with try/except,
            # so a genuine configure error still surfaces.
            lab.configure(**({"fg": color} if key in self._raw_labels
                             else {"text_color": color}))

    @staticmethod
    def _fmt(v, dp=2):
        return "—" if v is None else f"{v:,.{dp}f}"

    def _paint(self):
        st = self._state
        if st is None:
            # No successful poll yet. If one FAILED, say why — otherwise the
            # window reads "connecting…" indefinitely and the reason is only
            # in the console.
            if self._poll_error:
                self._set("health", self._poll_error, RED)
                for spec in self.specs:
                    self._set(f"{spec.key}.reason",
                              "The HUD cannot read its data. If a module is "
                              "missing, run it with the repo venv rather than "
                              "the system Python — see the console.")
            return

        tape = st["tape"]

        # ── Shared header. The gamma-snapshot ages are per-instrument, so the
        # WORST of them is what the shared health line reports — a stalled
        # collector on one symbol must not be hidden by the other being fresh.
        ages = [p["gamma"].get("snap_age_s") for p in st["panes"].values()
                if p["gamma"].get("snap_age_s") is not None]
        worst_age = max(ages) if ages else None
        problems = []
        if not tape.get("ok"):
            problems.append("tape stale")
        if worst_age is not None and worst_age > STALE_AFTER_SEC:
            problems.append(f"snapshot {int(worst_age)}s old")
        if problems:
            self._set("health", " · ".join(problems), RED)
        else:
            self._set("health",
                      f"live · snapshot {int(worst_age or 0)}s · "
                      f"{st['now'].strftime('%H:%M:%S')} CT", GREEN)
        self._set("vix", f"VIX {self._fmt(tape.get('vix'))}")
        self._set("phase", PHASE_NOTE.get(st["phase"], ""))

        for spec in self.specs:
            self._paint_pane(spec, st["panes"][spec.key])

    def _paint_pane(self, spec, pane):
        k = spec.key
        gamma, lv, v = pane["gamma"], pane["levels"], pane["verdict"]

        # Which gamma map is feeding this pane, and whether it is the good one.
        sym = gamma.get("symbol") or "—"
        preferred = sym == spec.sources[0]
        note = "" if preferred else "  (ETF proxy — overwrite-skewed)"
        detail = ""
        if not gamma.get("ok") and gamma.get("reason"):
            detail = "  ·  " + gamma["reason"]
        self._set(f"{k}.source", f"source: {sym}{note}{detail}",
                  FG_DIM if preferred and gamma.get("ok") else AMBER)

        # Verdict.
        self._set(f"{k}.action", v["action"],
                  ACTION_COLOR.get(v["action"], GRAY))
        # .get(), not [] — this runs on the UI thread inside the 2s repaint, and
        # a KeyError here kills the paint loop. classify_regime only emits these
        # four keys today, so this is purely defence against a fifth being added
        # later; the HUD's contract is that no read raises.
        rtext, rcolor = REGIME_TEXT.get(pane["regime"], ("REGIME UNKNOWN", GRAY))
        if pane["dist"] is not None:
            rtext += f"   ({pane['dist']:+,.0f} pts vs flip)"
        # Name WHY the regime is withheld, so "unknown" reads as a deliberate
        # refusal rather than a broken read.
        if pane.get("regime_stale"):
            rtext += "   ·  " + pane["regime_stale"]
        self._set(f"{k}.regime", rtext, rcolor)
        self._set(f"{k}.reason", v["reason"])

        # Tape.
        self._set(f"{k}.fut", self._fmt(pane["tape"].get("fut")))
        pct = pane["tape"].get("fut_pct")
        self._set(f"{k}.fut_pct", "—" if pct is None else f"{pct:+.2f}%",
                  GREEN if (pct or 0) >= 0 else RED)
        self._set(f"{k}.cash", self._fmt(pane["tape"].get("cash")))
        self._set(f"{k}.basis", self._fmt(pane.get("basis")))

        # Levels + distance from spot. The parenthesised differential is
        # frame-independent, which is what makes the two panes comparable.
        fut = pane["tape"].get("fut")
        for key in ("call_wall", "flip", "put_wall", "pin"):
            val = lv.get(key)
            if val is None:
                self._set(f"{k}.{key}", "—")
            elif fut:
                self._set(f"{k}.{key}", f"{val:,.0f}   ({val - fut:+,.0f})")
            else:
                self._set(f"{k}.{key}", f"{val:,.0f}")

        # Risk, in this instrument's dollars.
        self._set(f"{k}.entry", self._fmt(v.get("entry"), 0))
        self._set(f"{k}.stop", self._fmt(v.get("stop"), 0))
        self._set(f"{k}.target", self._fmt(v.get("target"), 0))
        # Colour by whether it clears the minimum, so a thin setup reads as thin
        # at a glance rather than needing the number to be interpreted.
        rr = v.get("rr")
        if rr is None:
            self._set(f"{k}.rr", "— (trailed)" if v.get("action") in ("LONG", "SHORT")
                      else "—", FG_DIM)
        else:
            self._set(f"{k}.rr", f"{rr:.2f} : 1",
                      GREEN if rr >= MIN_REWARD_RISK else RED)

        if v.get("entry") is not None and v.get("stop") is not None:
            pts = abs(v["entry"] - v["stop"])
            self._set(f"{k}.risk_full",
                      f"{pts:,.0f} pts = ${pts * spec.point_value:,.0f}")
            self._set(f"{k}.risk_micro",
                      f"{pts:,.0f} pts = ${pts * spec.micro_point_value:,.0f}")
        else:
            self._set(f"{k}.risk_full", "—")
            self._set(f"{k}.risk_micro", "—")

    def _on_close(self):
        self._stop.set()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


# Kept as an alias: the launcher command, the docs and the NinjaScript header
# all still say "nq_hud", and renaming the entry point would break those for a
# cosmetic gain.
NQHud = Hud


#############################################
# ENTRY POINT
#############################################

def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # Pre-flight the dependencies BEFORE opening a window. Getting this wrong
    # is easy and the symptom is misleading: the HUD opens, renders, and then
    # logs the same traceback every 2s while the panel shows "connecting…".
    #
    # The specific trap is the interpreter. `python tools\nq_hud.py` picks up
    # the SYSTEM python, which on this machine has customtkinter (from the ML
    # trading GUI) but NOT redis — so the window opens and only the tape fails.
    missing = []
    for module, hint in (("customtkinter", "pip install customtkinter"),
                         ("redis", "provided by the repo venv")):
        try:
            __import__(module)
        except ImportError:
            missing.append((module, hint))

    if missing:
        # ASCII only: this prints to a cp1252 Windows console, where an em dash
        # renders as "?" and undermines the message it is meant to carry.
        print("nq_hud cannot start - missing: "
              + ", ".join(m for m, _ in missing))
        for module, hint in missing:
            print("    {0:16s} {1}".format(module, hint))
        print()
        print("Run it with the repo venv, which has all of them:")
        print(r"    .venv\Scripts\python.exe tools\nq_hud.py")
        print("or activate first:")
        print(r"    .venv\Scripts\Activate.ps1   then   python tools\nq_hud.py")
        return 1

    Hud().run()
    return 0


if __name__ == "__main__":
    sys.exit(main())

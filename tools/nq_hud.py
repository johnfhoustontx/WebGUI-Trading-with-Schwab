"""
nq_hud.py - NQ Dealer-Positioning Entry HUD
Version: 1.0.0
Last Updated: 2026-07-29

An always-on-top desktop HUD for MANUAL, RTH-only NQ futures day trading.
Classifies the dealer-gamma regime from collected options data, converts the
key levels into NQ points, and renders a LONG / SHORT / STAND DOWN verdict
with risk-management levels.

Version 1.0.0 Changes:
- Initial implementation

────────────────────────────────────────────────────────────────────────────
ARCHITECTURE — this is a TIER-1 READER. It does not compute market data.

  * Tape (NQ + NDX + VIX spot)  <- cache:market:dashboard via shared.bus.Bus
                                   (market_svc already polls /quotes ~2s RTH)
  * Gamma grids / flip / walls  <- options-scanner/gex_history.db, READ-ONLY
                                   (options_svc collects 1-min snapshots
                                    08:00-15:20 CT for the whole universe)

It makes NO Schwab calls, imports NO engines except the pure wall picker in
gamma_tool, opens the history DB read-only, and writes nothing anywhere. It
therefore cannot destabilise the running 8-process stack, and needs no port.

WHY NOT cache:options:gamma? That key holds exactly ONE symbol — whichever the
/options/gamma page currently has selected (handlers.refresh_gamma defaults to
$SPX). Reading it would silently show SPX gamma under an NQ label. The history
DB is per-symbol by construction, so it is the correct source.

SOURCE SYMBOL: prefers $NDX when it is being collected; falls back to QQQ.
QQQ carries heavy structural call-overwriting flow, which can invert the
apparent gamma sign, so the active source is always shown in the header. To
upgrade, add "$NDX" to gex_collector.SYMBOLS and restart options_svc.
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

# Repo root on sys.path -> repo_paths / shared / nq_signal are importable (same
# pattern as webgui/proxy.py and shared/bus/client.py).
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from repo_paths import OPTIONS_SCANNER  # noqa: E402

# PURE signal logic — conversion / regime / session / stops / verdict. Kept in
# its own module so the whole decision surface is testable without Redis,
# SQLite or tkinter. See tools/nq_signal.py.
from tools.nq_signal import (  # noqa: E402
    PHASE_NOTE,
    build_verdict,
    cash_stale_reason,
    classify_regime,
    ndx_scale,
    session_phase,
    shift_verdict_levels,
    to_index,
    to_nq,
)

# Append-only verdict-transition log (write-only; nothing reads it at runtime).
from tools.nq_signal_log import SignalLogger  # noqa: E402
# Current-state export for the NinjaTrader indicator (also write-only).
from tools.nq_state import StateWriter  # noqa: E402

# options-scanner on sys.path -> gamma_tool (pure wall picker) + gex_history_db.
# Imported lazily inside _load_gamma so an import failure degrades the gamma
# panel rather than preventing the window from opening at all.
if str(OPTIONS_SCANNER) not in sys.path:
    sys.path.insert(0, str(OPTIONS_SCANNER))

log = logging.getLogger("nq_hud")

#############################################
# CONFIGURATION
#############################################

CT = ZoneInfo("America/Chicago")

# Source-symbol preference. $NDX is the correct underlying for NQ; QQQ is the
# fallback because it is always collected. First one with data today wins.
SOURCE_PREFERENCE = ("$NDX", "QQQ")

# NQ contract multipliers ($/point).
NQ_POINT_VALUE = 20.0
MNQ_POINT_VALUE = 2.0

# Snapshot staleness. The collector runs 1-min; >150s means it has stalled.
STALE_AFTER_SEC = 150

REFRESH_SEC = 2.0

# Window geometry. Height is derived from the built widget tree at startup
# (see NQHud.__init__); these are only the floor and the screen allowance.
WIN_WIDTH = 430
WIN_MIN_HEIGHT = 680
WIN_SCREEN_MARGIN = 80   # taskbar + title bar

# The regime / session-window / risk-sizing constants and the pure logic that
# closes over them live in tools/nq_signal.py (imported above), so they can be
# exercised without Redis, SQLite or tkinter.

# Tape tile display names as written in services/market_svc/symbols.py.
# NOTE: the tile DISPLAY name and the quote symbol differ, and BOTH are
# hardcoded here and in services/market_svc/symbols.py. At the quarterly
# roll they must change in lockstep, or the basis is measured against a
# contract nobody is trading.
TILE_NQ = "/NQ[U26]"
NQ_CONTRACT = "/NQU26"
TILE_NDX = "NDX"
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


#############################################
# TIME / SESSION HELPERS
#############################################

def market_now():
    """Current time in exchange (Central) time."""
    return datetime.now(CT)



#############################################
# DATA READERS (Tier-1: Redis + read-only SQLite)
#############################################

def read_tape(bus):
    """Pull NQ / NDX / VIX from cache:market:dashboard.

    Returns {"nq": float|None, "ndx": float|None, "vix": float|None,
             "nq_pct": float|None, "age_s": float|None, "ok": bool}.
    Defensive: any failure degrades to an all-None dict so the HUD paints
    "no data" rather than dying.
    """
    out = {"nq": None, "ndx": None, "vix": None, "nq_pct": None,
           "age_s": None, "ok": False}
    try:
        env = bus.cache_get(CACHE_MARKET)
        if env is None:
            return out
        payload = env.payload or {}
        wanted = {TILE_NQ: "nq", TILE_NDX: "ndx", TILE_VIX: "vix"}
        for cat in payload.get("categories", []):
            for tile in cat.get("tiles", []):
                slot = wanted.get(tile.get("display"))
                if slot is None:
                    continue
                out[slot] = tile.get("last")
                if slot == "nq":
                    out["nq_pct"] = tile.get("change_pct")
        try:
            ts = datetime.fromisoformat(env.ts)
            out["age_s"] = (datetime.now(ts.tzinfo) - ts).total_seconds()
        except Exception:
            out["age_s"] = None
        out["ok"] = out["nq"] is not None
    except Exception:
        log.debug("tape read failed", exc_info=True)
    return out


def pick_source_symbol(conn, gh, today):
    """First symbol in SOURCE_PREFERENCE that has a GEX row for ``today``.

    $NDX is the correct underlying for NQ but is not in the default collection
    universe; QQQ always is. Returns (symbol, None) or (None, reason).
    """
    for sym in SOURCE_PREFERENCE:
        try:
            if gh.latest_spot_flip(conn, sym, "gex", today) is not None:
                return sym, None
        except Exception:
            continue
    return None, "no GEX snapshots today for $NDX or QQQ"


def read_gamma(source_date=None):
    """Load the latest GEX snapshot for the best available source symbol.

    Returns a dict with spot / flip / walls / net_total / pin / atr_proxy, or
    {"ok": False, "reason": ...}. Opens the history DB READ-ONLY and closes it
    every poll — the collector holds the write lock and must never be blocked.
    """
    res = {"ok": False, "reason": "", "symbol": None, "spot": None,
           "flip": None, "call_wall": None, "put_wall": None,
           "net_total": None, "pin": None, "snap_age_s": None,
           "atr_proxy": None, "session_date": None}
    conn = None
    try:
        import gamma_tool as gt
        import gex_history_db as gh
    except Exception as exc:
        res["reason"] = f"engine import failed: {exc}"
        return res

    try:
        conn = gh.connect(read_only=True)
    except Exception as exc:
        res["reason"] = f"gex_history.db unavailable: {exc}"
        return res

    try:
        today = source_date or market_now().date()
        symbol, why = pick_source_symbol(conn, gh, today)
        if symbol is None:
            # Off-hours / pre-open: fall back to the prior collected session.
            for back in range(1, 6):
                prior = _prior_day(today, back)
                symbol, why = pick_source_symbol(conn, gh, prior)
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
        ts, spot, flip = latest
        res.update(spot=spot, flip=flip)
        res["snap_age_s"] = max(0.0, time.time() - float(ts))

        # ── EXACTLY ONE grid decode: the newest row. ─────────────────────────
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

        # Pin = the largest ABSOLUTE net-gamma strike; price gravitates to it
        # in a positive-gamma regime. NOTE (design §6): whether max(|net|) or
        # the stored top_pos_strike (= max(net) over POSITIVE strikes only) is
        # the better mean-reversion target is an OPEN question — pinning is
        # caused by positive dealer gamma, so a large negative-net strike is an
        # amplifier, not an attractor. Task 8 logs both to settle it.
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
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    return res


def _prior_day(d, back):
    from datetime import timedelta
    return d - timedelta(days=back)




#############################################
# GUI
#############################################

class NQHud:
    """Always-on-top CustomTkinter HUD. All data work runs off the UI thread."""

    def __init__(self):
        import customtkinter as ctk
        self.ctk = ctk
        ctk.set_appearance_mode("dark")

        self.root = ctk.CTk()
        self.root.title("NQ Dealer-Positioning HUD")
        self.root.geometry(f"{WIN_WIDTH}x{WIN_MIN_HEIGHT}")
        self.root.attributes("-topmost", True)
        self.root.configure(fg_color=BG)

        self._bus = None
        self._state = None
        self._stop = threading.Event()
        self._labels = {}
        self._poll_error = None
        self._logger = SignalLogger()
        self._state_writer = StateWriter(nq_contract=NQ_CONTRACT,
                                         stale_after_sec=STALE_AFTER_SEC)

        self._build()

        # Size to CONTENT, not to a literal. The panel stack needs ~894px at
        # 100% DPI — the original hardcoded 680 clipped the RISK panel at
        # "Entry", hiding the stop, target and dollar risk entirely (found by
        # actually rendering the window; every unit test passed regardless).
        # Measuring rather than bumping the constant keeps it correct under
        # different DPI scaling and font metrics, where reqheight differs.
        self.root.update_idletasks()
        height = max(WIN_MIN_HEIGHT, self.root.winfo_reqheight())
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

        # Header — source symbol + staleness. Always visible so you know which
        # gamma map you are reading.
        head = self._panel(self.root, pady=(10, 8))
        self._labels["source"] = ctk.CTkLabel(
            head, text="source: —", text_color=FG_DIM, font=("Segoe UI", 11))
        self._labels["source"].pack(anchor="w", padx=10, pady=(6, 0))
        self._labels["health"] = ctk.CTkLabel(
            head, text="connecting…", text_color=AMBER, font=("Segoe UI", 11))
        self._labels["health"].pack(anchor="w", padx=10, pady=(0, 6))

        # Verdict — the headline.
        vf = self._panel(self.root)
        self._labels["action"] = ctk.CTkLabel(
            vf, text="STAND DOWN", text_color=GRAY,
            font=("Segoe UI", 30, "bold"))
        self._labels["action"].pack(pady=(10, 2))
        self._labels["regime"] = ctk.CTkLabel(
            vf, text="—", text_color=FG_DIM, font=("Segoe UI", 12, "bold"))
        self._labels["regime"].pack(pady=(0, 4))
        self._labels["reason"] = ctk.CTkLabel(
            vf, text="", text_color=FG, font=("Segoe UI", 11),
            wraplength=380, justify="left")
        self._labels["reason"].pack(padx=10, pady=(0, 10))

        # Tape.
        tf = self._panel(self.root)
        ctk.CTkLabel(tf, text="TAPE", text_color=BLUE,
                     font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=10, pady=(6, 2))
        self._row(tf, "nq", "NQ", bold=True)
        self._row(tf, "nq_pct", "Day %")
        self._row(tf, "ndx", "NDX cash")
        self._row(tf, "basis", "Basis (NQ−NDX)")
        self._row(tf, "vix", "VIX")
        ctk.CTkLabel(tf, text="", height=4).pack()

        # Levels, in NQ points.
        lf = self._panel(self.root)
        ctk.CTkLabel(lf, text="LEVELS  (NQ points)", text_color=PURPLE,
                     font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=10, pady=(6, 2))
        self._row(lf, "call_wall", "Call wall", vcolor=RED)
        self._row(lf, "flip", "Gamma flip", vcolor=BLUE, bold=True)
        self._row(lf, "put_wall", "Put wall", vcolor=GREEN)
        self._row(lf, "pin", "Pin (max γ)", vcolor=AMBER)
        ctk.CTkLabel(lf, text="", height=4).pack()

        # Risk.
        rf = self._panel(self.root)
        ctk.CTkLabel(rf, text="RISK", text_color=AMBER,
                     font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=10, pady=(6, 2))
        self._row(rf, "entry", "Entry")
        self._row(rf, "stop", "Stop", vcolor=RED)
        self._row(rf, "target", "Target", vcolor=GREEN)
        self._row(rf, "risk_nq", "Risk / 1 NQ")
        self._row(rf, "risk_mnq", "Risk / 1 MNQ")
        ctk.CTkLabel(rf, text="", height=4).pack()

        # Session note.
        sf = self._panel(self.root)
        self._labels["phase"] = ctk.CTkLabel(
            sf, text="—", text_color=FG_DIM, font=("Segoe UI", 11),
            wraplength=380, justify="left")
        self._labels["phase"].pack(padx=10, pady=8)

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
        tape = read_tape(self._bus)
        gamma = read_gamma()

        scale = ndx_scale(gamma.get("symbol"), tape.get("ndx"), gamma.get("spot"))
        basis = None
        if tape.get("nq") is not None and tape.get("ndx") is not None:
            basis = tape["nq"] - tape["ndx"]

        _LEVEL_KEYS = ("flip", "call_wall", "put_wall", "pin", "pin_top_pos")

        # TWO FRAMES, deliberately (design §5).
        #
        # Decisions are made in CASH (index) terms, because that is what they
        # were always really made in: basis is measured as NQ - NDX, so a
        # comparison of NQ against an NQ-converted level reduces algebraically
        # to cash-vs-level with the futures price cancelling out. Computing it
        # in the cash frame makes the code say what it does instead of hiding
        # a self-reference behind a conversion.
        #
        # NQ points are for DISPLAY — the numbers you type into NinjaTrader.
        # The frames differ by an additive basis, so distances (ATR, stop size,
        # wall proximity) are identical in both and only levels are shifted.
        levels_cash = {k: to_index(gamma.get(k), scale) for k in _LEVEL_KEYS}
        levels = {k: to_nq(gamma.get(k), scale, basis) for k in _LEVEL_KEYS}

        # Session range proxy for stop sizing. Frame-invariant (a difference),
        # so the same value serves both.
        atr_pts = gamma["atr_proxy"] * scale if gamma.get("atr_proxy") and scale else None

        regime, dist = classify_regime(tape.get("ndx"), levels_cash.get("flip"))

        # Cash-freshness guard: the regime is anchored to an index that stops
        # ticking outside 08:30-15:00 CT. Refuse to assert a band rather than
        # showing a frozen one that looks live.
        stale = cash_stale_reason(phase, gamma.get("snap_age_s"), STALE_AFTER_SEC)
        if stale:
            regime, dist = "unknown", None
        verdict = build_verdict(regime, phase, tape.get("ndx"), levels_cash, atr_pts)
        # Back into NQ points for the trader.
        verdict = shift_verdict_levels(verdict, basis)

        state = {"now": now, "phase": phase, "tape": tape, "gamma": gamma,
                 "scale": scale, "basis": basis, "levels": levels,
                 "atr_nq": atr_pts, "regime_stale": stale,
                 "levels_cash": levels_cash,
                 "regime": regime, "dist": dist, "verdict": verdict}

        # Record verdict TRANSITIONS for offline validation. Self-guarded and
        # write-only — nothing in the HUD reads it back, so a logging failure
        # can only cost a row.
        self._logger.maybe_log(state)
        # Export current state for the NinjaTrader indicator. Every poll,
        # so its timestamp doubles as a heartbeat.
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
            lab.configure(text_color=color)

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
                self._set("reason",
                          "The HUD cannot read its data. If a module is "
                          "missing, run it with the repo venv rather than the "
                          "system Python — see the console for details.")
            return

        tape, gamma, lv = st["tape"], st["gamma"], st["levels"]
        v = st["verdict"]

        # Header.
        sym = gamma.get("symbol") or "—"
        note = "" if sym == "$NDX" else "  (QQQ proxy — overwrite-skewed)"
        self._set("source", f"source: {sym}{note}", FG_DIM if sym == "$NDX" else AMBER)

        problems = []
        if not tape.get("ok"):
            problems.append("tape stale")
        if not gamma.get("ok"):
            problems.append(gamma.get("reason") or "no gamma")
        snap_age = gamma.get("snap_age_s")
        if snap_age is not None and snap_age > STALE_AFTER_SEC:
            problems.append(f"snapshot {int(snap_age)}s old")
        if problems:
            self._set("health", " · ".join(problems), RED)
        else:
            self._set("health",
                      f"live · snapshot {int(snap_age or 0)}s · "
                      f"{st['now'].strftime('%H:%M:%S')} CT", GREEN)

        # Verdict.
        self._set("action", v["action"], ACTION_COLOR.get(v["action"], GRAY))
        rmap = {"positive": ("POSITIVE GAMMA — mean reversion", BLUE),
                "negative": ("NEGATIVE GAMMA — continuation", RED),
                "flip_zone": ("FLIP ZONE — whipsaw", AMBER),
                "unknown": ("REGIME UNKNOWN", GRAY)}
        # .get(), not [] — this runs on the UI thread inside the 2s repaint, and
        # a KeyError here kills the paint loop. classify_regime only emits these
        # four keys today, so this is purely defence against a fifth being added
        # later; the HUD's contract is that no read raises.
        rtext, rcolor = rmap.get(st["regime"], ("REGIME UNKNOWN", GRAY))
        if st["dist"] is not None:
            rtext += f"   ({st['dist']:+,.0f} pts vs flip)"
        # Name WHY the regime is withheld, so "unknown" reads as a deliberate
        # refusal rather than a broken read.
        if st.get("regime_stale"):
            rtext += "   ·  " + st["regime_stale"]
        self._set("regime", rtext, rcolor)
        self._set("reason", v["reason"])

        # Tape.
        self._set("nq", self._fmt(tape.get("nq")))
        pct = tape.get("nq_pct")
        self._set("nq_pct", "—" if pct is None else f"{pct:+.2f}%",
                  GREEN if (pct or 0) >= 0 else RED)
        self._set("ndx", self._fmt(tape.get("ndx")))
        self._set("basis", self._fmt(st.get("basis")))
        self._set("vix", self._fmt(tape.get("vix")))

        # Levels + distance from spot.
        nq = tape.get("nq")
        for key in ("call_wall", "flip", "put_wall", "pin"):
            val = lv.get(key)
            if val is None:
                self._set(key, "—")
            elif nq:
                self._set(key, f"{val:,.0f}   ({val - nq:+,.0f})")
            else:
                self._set(key, f"{val:,.0f}")

        # Risk.
        self._set("entry", self._fmt(v.get("entry"), 0))
        self._set("stop", self._fmt(v.get("stop"), 0))
        self._set("target", self._fmt(v.get("target"), 0))
        if v.get("entry") is not None and v.get("stop") is not None:
            pts = abs(v["entry"] - v["stop"])
            self._set("risk_nq", f"{pts:,.0f} pts = ${pts * NQ_POINT_VALUE:,.0f}")
            self._set("risk_mnq", f"{pts:,.0f} pts = ${pts * MNQ_POINT_VALUE:,.0f}")
        else:
            self._set("risk_nq", "—")
            self._set("risk_mnq", "—")

        self._set("phase", PHASE_NOTE.get(st["phase"], ""))

    def _on_close(self):
        self._stop.set()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


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

    NQHud().run()
    return 0


if __name__ == "__main__":
    sys.exit(main())

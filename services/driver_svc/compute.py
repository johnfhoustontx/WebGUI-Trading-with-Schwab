"""Driver compute module — NiceGUI-free engine-call layer for ``driver_svc``.

The autonomous decision layer's bus-free brain: ``build_packet`` projects the
cached scanner menu + driver-paper-account P&L into the model-facing decision
packet, ``run_cycle`` wires ``build_packet → decider.decide → guardrails`` (never
raising — any failure stands down), and ``open_driver_position`` /
``run_driver_manage_cycle`` open + manage the driver's OWN isolated paper book.
``fetch_market_context`` supplies the VIX/SPX context the guardrails' VIX gate
reads.

This module must NOT import ``nicegui`` or anything from ``webgui/``. It needs
only ``claude-driver/config.py``'s legacy ``RISK_LIMITS`` (the fallback daily-loss
cap), imported standalone (its dir on ``sys.path``). Because ``driver_svc`` runs
in its own process, pinning ``config`` as a top-level module cannot collide with
the other domains' engines (the same isolation ``sentiment_svc`` relies on for
``scoring`` and ``trade_svc`` for ``technical``). ``config.PAPER_TRADE`` is True —
this service never modifies that flag.

Every public function is defensive: a thrown engine degrades to an ``error`` /
empty payload rather than raising, so one bad cycle can never crash the service.
"""
import sys

import requests

from repo_paths import CLAUDE_DRIVER, PROXY_URL

# ── isolated engine imports (separate process — no cross-app name collision) ──
# claude-driver folder on sys.path so its hyphen-free top-level modules import by
# name (``config`` is generic — safe only because this is a dedicated process).
if str(CLAUDE_DRIVER) not in sys.path:
    sys.path.insert(0, str(CLAUDE_DRIVER))

# Legacy risk envelope — source the daily loss cap here so it can't drift from the
# old rule-tree config (``claude-driver/config.py`` is on sys.path via CLAUDE_DRIVER).
try:  # noqa: SIM105
    from config import RISK_LIMITS as _RISK_LIMITS  # noqa: E402
except Exception:  # noqa: BLE001 — defensive: fall back to the documented default.
    _RISK_LIMITS = {}

# Autonomous decision layer (Phase 4): the pure guardrails safety core + the static
# tunables. ``decider`` is imported lazily inside ``run_cycle`` (the file already
# imports its engine deps at top, but the decider is monkeypatched in tests, so a
# late import keeps the patch point at ``services.driver_svc.decider.decide``).
from services.driver_svc import guardrails as _g  # noqa: E402
from services.driver_svc import settings as _st  # noqa: E402


def _daily_max_loss() -> float:
    """The daily-loss halt for the autonomous driver (defensive → 250.0).

    Prefers the driver's OWN ``settings.DAILY_LOSS_HALT`` (all aggression knobs live in
    settings.py now); falls back to the legacy ``config.RISK_LIMITS['daily_max_loss']``,
    then 250.0. Never raises."""
    try:
        v = getattr(_st, "DAILY_LOSS_HALT", None)
        if v is not None:
            return float(v)
    except (TypeError, ValueError):
        pass
    try:
        return float(_RISK_LIMITS.get("daily_max_loss", 250.0))
    except (TypeError, ValueError):
        return 250.0


# ── cumulative MTD banking target (2026-07-09) ───────────────────────────────
# The banking target carries the $500/day deficit/excess forward month-to-date, clamped
# to [floor, cap]. Pure + defensive; the −$1,500 loss halt + per-trade caps are untouched.
import datetime as _dt


def effective_target(base, n_trading_days, mtd_before_today, *, cap, floor) -> float:
    """The cumulative MTD banking target (clamped to ``[floor, cap]``).

    ``N*base − MTD_realized_before_today`` = what today must bank to be back on the
    (N trading days × base) pace; clamped so a behind month ratchets up to ``cap``
    (recover over days, never one shot) and an ahead month eases to ``floor``. Any
    unparseable input → ``base`` (safe fallback). Never raises.
    """
    try:
        raw = float(n_trading_days) * float(base) - float(mtd_before_today)
    except (TypeError, ValueError):
        return float(base)
    return max(float(floor), min(float(cap), raw))


def _iso_date(ts):
    """The date from an ISO-ish timestamp's first 10 chars, or ``None``."""
    try:
        return _dt.date.fromisoformat(str(ts)[:10])
    except (TypeError, ValueError):
        return None


def mtd_realized_before_today(closed_positions, today_ct) -> float:
    """Σ realized_pnl of driver closed positions with an exit date in the current month
    AND strictly before ``today_ct``. Junk-tolerant (bad rows skipped); never raises."""
    ym, total = (today_ct.year, today_ct.month), 0.0
    for p in closed_positions or []:
        if not isinstance(p, dict):
            continue
        d = _iso_date(p.get("exit_ts") or p.get("exit_time"))
        if d is None or (d.year, d.month) != ym or d >= today_ct:
            continue
        try:
            total += float(p.get("realized_pnl") or 0.0)
        except (TypeError, ValueError):
            pass
    return total


def _mtd_trading_days(today_ct) -> int:
    """Trading days from the 1st of ``today_ct``'s month through today inclusive
    (weekdays − NYSE holidays). Never raises.

    Reads ``shared.market_calendar`` directly — it used to borrow the scheduler's
    ``_HOLIDAYS`` alias (lazily, to dodge a compute<->scheduler import cycle),
    but that set stopped at 2027, so from 2028 this would have counted every
    holiday as a trading day and inflated the cumulative MTD target's day count.
    The shared calendar derives closures per year and imports nothing from this
    service, so there is no cycle to dodge."""
    try:
        from shared.market_calendar import is_trading_day as _is_td
    except Exception:  # noqa: BLE001 — degrade to weekdays-only.
        def _is_td(day):
            return day.weekday() < 5
    d, n = today_ct.replace(day=1), 0
    while d <= today_ct:
        if _is_td(d):
            n += 1
        d += _dt.timedelta(days=1)
    return n


# ── autonomous decision cycle (Phase 4) ──────────────────────────────────────
# This module is BUS-FREE: the handler (Unit 5) reads the Redis cache views and
# passes ``scan_view`` / ``paper_view`` in as plain dicts. ``build_packet`` is a
# pure transform of those views into a model-facing packet (plus a ``menu_by_id``
# the guardrails use to resolve ids back to RAW scanner signals for verbatim paper
# execution); ``run_cycle`` wires build_packet → decider → guardrails defensively.

# Day-P&L field on the paper snapshot. The REAL key is ``session_pnl``
# (``paper_engine.account_snapshot`` = session_realized_pnl + open_unrealized); the
# others are tolerant fallbacks for forward/back-compat.
_DAY_PNL_KEYS = ("session_pnl", "day_pnl", "realized_day_pnl")


def _day_pnl(paper_view) -> float | None:
    """The paper account's day P&L, or ``None`` if absent/unparseable.

    Reads the snapshot's ``session_pnl`` (the real ``account_snapshot`` field),
    tolerating legacy key spellings; a missing snapshot or a non-numeric value
    degrades to ``None`` (→ the packet's gap is the full target) rather than
    raising.
    """
    snap = (paper_view or {}).get("snapshot") or {}
    for k in _DAY_PNL_KEYS:
        if snap.get(k) is not None:
            try:
                return float(snap[k])
            except (TypeError, ValueError):
                pass
    return None


def _menu_item(sig, mid) -> dict:
    """Compact, model-facing projection of a scanner signal (+ stable id).

    The ``structure`` is resolved via ``guardrails.signal_structure`` (structure →
    type → trade_type) because a real ``cache:options:scan`` signal stores the code
    in ``type`` and uses ``trade_type`` for the DTE bucket — reading ``trade_type``
    as the structure would mislabel every signal "0-DTE". ``expiry`` reads the real
    ``expiration`` key (``expiry`` fallback) and ``pop`` the real ``pop_pct``
    (``pop`` fallback). Only the id + this projection are shown to the model — the
    RAW signal stays in ``menu_by_id`` for verbatim execution.

    ``credit``/``max_loss`` are shown **NET of round-trip commission** (the scanner
    attaches ``net_credit``/``net_max_loss``/``commission``; gross fields are the
    fallback for pre-fix cached signals) so the model's perceived edge is
    net-of-fees. Sizing/BP still key off the RAW gross ``max_loss`` in
    ``menu_by_id`` (structural margin; commission is a transaction cost, not margin).
    """
    net_credit = sig.get("net_credit", sig.get("credit"))
    net_max_loss = sig.get("net_max_loss", sig.get("max_loss"))
    return {
        "id": mid,
        "symbol": sig.get("symbol"),
        "structure": _g.signal_structure(sig),
        "expiry": sig.get("expiration") or sig.get("expiry"),
        "credit": net_credit,
        "max_loss": net_max_loss,
        "commission": sig.get("commission"),
        "pop": sig.get("pop_pct") if sig.get("pop_pct") is not None else sig.get("pop"),
        "score": sig.get("composite_score"),
    }


def _market_state_line(market) -> str | None:
    """A concise decider-facing ``Market state`` line from the five-state context.

    Reads ``market["market_state"]`` (``{state, label, evidence}`` from
    ``cache:sentiment:composite`` ``derived.trend``, merged into the market context
    by the handler). Renders e.g.
    ``"Market state: Lack of Bearishness — put-skew Δ -1.2 · aggression +0.30"``.
    Returns ``None`` when absent / malformed / label-blank so ``build_packet`` omits
    the field entirely (no empty ``Market state:`` line).

    This is REASONING CONTEXT ONLY — ``regime_filter`` already hard-gates the scanner
    menu the driver reads, so the state changes NO hard rule (the code-authoritative
    ``guardrails`` never see it). Defensive: never raises.
    """
    ms = (market or {}).get("market_state")
    if not isinstance(ms, dict):
        return None
    label = str(ms.get("label") or "").strip()
    if not label:
        return None
    evidence = ms.get("evidence")
    ev = (" · ".join(s for s in (str(e).strip() for e in evidence) if s)
          if isinstance(evidence, list) else "")
    return f"Market state: {label} — {ev}" if ev else f"Market state: {label}"


# ── market-read context helpers (Phase: driver market-context block) ─────────
# The decider gets an additive ``market_read`` (gamma structure + breadth + sentiment)
# to sharpen selection. Every helper here is PURE + defensive (degrades, never raises);
# REASONING CONTEXT ONLY — the guardrails never see any of it.

# Market-dashboard tile ``color_state`` → a signed risk tilt (risk-on positive).
_RISK_WEIGHT = {"risk_on_strong": 2, "risk_on_mild": 1, "flat": 0,
                "risk_off_mild": -1, "risk_off_strong": -2, "no_data": 0}


def _dashboard_risk_read(dashboard) -> dict:
    """Breadth spread + an aggregate risk-on/off label from ``cache:market:dashboard``.

    Reads the ``$ADVN-$DECN`` tile's ``last`` (the breadth spread) and sums every tile's
    ``color_state`` into a net tilt → ``risk_on`` / ``neutral`` / ``risk_off``. Defensive
    → ``{}`` on a missing / empty / malformed dashboard. Never raises.
    """
    try:
        cats = (dashboard or {}).get("categories") or []
        tiles = [t for c in cats for t in (c.get("tiles") or []) if isinstance(t, dict)]
        if not tiles:
            return {}
        breadth = next((t.get("last") for t in tiles
                        if t.get("display") == "$ADVN-$DECN"), None)
        score = sum(_RISK_WEIGHT.get(t.get("color_state"), 0) for t in tiles)
        out = {"risk": "risk_on" if score > 0 else "risk_off" if score < 0 else "neutral"}
        if breadth is not None:
            out["breadth_spread"] = breadth
        return out
    except Exception:  # noqa: BLE001 — context is best-effort; never block a cycle.
        return {}


def _pick_latest_briefing(payloads, today_ct):
    """The freshest TODAY gamma_analyze ``analysis`` across the scheduled-slot payloads.

    ``payloads`` = the scheduled-slot payloads (``{analysis, slot, generated_at}``; keys
    tolerated absent). Keeps only those whose ``generated_at`` date == ``today_ct`` (a
    prior-session briefing's walls mislead → dropped) with a non-empty ``analysis``, and
    returns the latest by ``generated_at`` (ISO sorts lexically), stamping
    ``_slot``/``_generated_at`` onto a COPY. ``None`` when nothing usable. Never raises.
    """
    try:
        today = today_ct.isoformat() if hasattr(today_ct, "isoformat") else str(today_ct)
        best = None
        for p in payloads or []:
            if not isinstance(p, dict):
                continue
            analysis, gen = p.get("analysis"), str(p.get("generated_at") or "")
            if not isinstance(analysis, dict) or not analysis or gen[:10] != today:
                continue
            if best is None or gen > best[0]:
                best = (gen, p.get("slot"), analysis)
        if best is None:
            return None
        gen, slot, analysis = best
        return {**analysis, "_slot": slot, "_generated_at": gen}
    except Exception:  # noqa: BLE001 — context is best-effort; never block a cycle.
        return None


_READ_INDEX_SYMBOLS = ("$SPX", "SPY", "QQQ")
_SPOT_KEY = {"$SPX": "spx_spot", "SPY": "spy_spot", "QQQ": "qqq_spot"}
# Market-dashboard tile display name per broad-index symbol (the CSV symbol, e.g. $SPX→SPX).
_DASH_DISPLAY = {"$SPX": "SPX", "SPY": "SPY", "QQQ": "QQQ"}


def _dashboard_change_pct(dashboard) -> dict:
    """Map each broad-index symbol → its ``change_pct`` from the dashboard tile.

    The market_read's per-index direction input for the directional gate. Only present
    symbols with a non-``None`` change land in the map. Defensive → ``{}``. Never raises.
    """
    try:
        cats = (dashboard or {}).get("categories") or []
        by_disp = {t.get("display"): t.get("change_pct")
                   for c in cats for t in (c.get("tiles") or []) if isinstance(t, dict)}
        return {sym: by_disp[disp] for sym, disp in _DASH_DISPLAY.items()
                if by_disp.get(disp) is not None}
    except Exception:  # noqa: BLE001 — context is best-effort.
        return {}


def _posture(spot, flip) -> str:
    """One-word gamma posture from spot vs the gamma flip (``''`` if unknown)."""
    try:
        if spot is None or flip is None:
            return ""
        return ("below flip (negative gamma)" if float(spot) < float(flip)
                else "above flip (positive gamma)")
    except (TypeError, ValueError):
        return ""


def _as_of(briefing) -> str:
    """A short ``slot HH:MM CT`` freshness stamp from the briefing meta (``''`` if unknown)."""
    slot = str(briefing.get("_slot") or "").strip()
    gen = str(briefing.get("_generated_at") or "")
    hhmm = gen[11:16] if len(gen) >= 16 else ""
    return " ".join(x for x in (slot, (hhmm + " CT") if hhmm else "") if x).strip()


_REGIME_TOP_N = 2      # how many memberships to surface (the mix, not all five)
# Display labels for the membership keys, so the decider sees "Stressed" (not the
# internal "crisis" key) consistent with the rest of the app. DUPLICATED from
# ``sentiment-dashboard/scoring/market_regime.REGIME_DISPLAY`` on purpose — this
# service must not import that package (the documented cross-app ``scoring``
# module-name collision); keep the words in step with it.
_REGIME_LABELS = {"mean_reversion": "Balanced", "trending": "Trending",
                  "breakout": "Breakout", "choppy": "Whipsaw", "crisis": "Stressed"}
# Direction rewords the two regimes that HAVE one; balanced/whipsaw/stressed are
# directionless by construction.
_REGIME_DIRECTIONAL = {
    "trending": {(1, True): "Rallying", (1, False): "Firming",
                 (-1, True): "Retreating", (-1, False): "Softening"},
    "breakout": {(-1, True): "Breakdown", (-1, False): "Breakdown"},
}


def _regime_label(key, direction=0, strong=False) -> str:
    key = str(key)
    base = _REGIME_LABELS.get(key, key)
    words = _REGIME_DIRECTIONAL.get(key)
    if not words or direction not in (-1, 1):
        return base
    return words.get((direction, bool(strong)), base)


def _regime_direction(payload) -> tuple:
    """(direction, strong) off a regime payload — junk or absent reads neutral,
    so an older payload without the field simply renders the base labels."""
    d = payload.get("direction")
    d = d if d in (-1, 0, 1) and not isinstance(d, bool) else 0
    return d, bool(payload.get("direction_strong") is True)


def _structural_regime(payload) -> dict:
    """The blended structural market regime (``cache:sentiment:regime``) projected
    for the decider — label, the top-N membership mix, and any in-progress
    transition. ``{}`` when unusable.

    Named ``market_regime`` in the read (NOT ``structure``): ``structure`` already
    means the SPREAD structure (PCS/CCS/IC) everywhere in this service. PURE,
    total over junk — this is reasoning context, never a gate.
    """
    try:
        p = payload if isinstance(payload, dict) else {}
        label = p.get("label")
        if not label:
            return {}
        direction = _regime_direction(p)
        out = {"label": str(label), "unclear": bool(p.get("unclear"))}
        conf = p.get("confidence")
        if isinstance(conf, (int, float)) and not isinstance(conf, bool):
            out["confidence"] = round(float(conf), 2)
        mem = p.get("memberships")
        if isinstance(mem, dict) and mem:
            pairs = [(str(k), float(v)) for k, v in mem.items()
                     if isinstance(v, (int, float)) and not isinstance(v, bool)]
            pairs.sort(key=lambda kv: kv[1], reverse=True)
            if pairs:
                out["top"] = [(_regime_label(k, *direction), round(v, 2))
                              for k, v in pairs[:_REGIME_TOP_N]]
        tr = p.get("transition")
        if isinstance(tr, dict) and tr.get("from") and tr.get("to"):
            prog = tr.get("progress")
            pct = f" {round(float(prog) * 100):.0f}%" if isinstance(
                prog, (int, float)) and not isinstance(prog, bool) else ""
            out["transition"] = (f"{_regime_label(tr['from'], *direction)} -> "
                                 f"{_regime_label(tr['to'], *direction)}{pct}")
        return out
    except Exception:  # noqa: BLE001 — context is best-effort; never block a cycle.
        return {}


def _market_read_summary(read) -> str:
    """One-line summary for the /driver decision log (regime · bias · breadth · sent)."""
    parts = []
    mr = read.get("market_regime") or {}
    if mr.get("label"):
        parts.append(str(mr["label"]))
    if read.get("regime"):
        parts.append(str(read["regime"]))
    if read.get("bias") is not None:
        parts.append(f"bias {read['bias']}")
    if read.get("breadth_spread") is not None:
        parts.append(f"breadth {read['breadth_spread']} {read.get('risk', '')}".strip())
    elif read.get("risk"):
        parts.append(str(read["risk"]))
    if read.get("sentiment_score") is not None:
        parts.append(f"sent {read['sentiment_score']}")
    return " · ".join(parts)


def _market_read(market) -> dict:
    """Assemble the decider's ``market_read`` from the enriched market context (pure).

    Joins the freshest gamma briefing (``market['briefing']`` — regime/bias/headline +
    per-index flip/walls/what-if), a LIVE per-index spot
    (``market['{spx,spy,qqq}_spot']`` from ``fetch_market_context``; the briefing spot
    is the fallback), the market-dashboard breadth/risk (``market['dashboard']``), and
    the sentiment 0-10 score/bias (``market['sentiment']``). ``{}`` when NONE of the
    three sources is usable (→ ``build_packet`` omits the key; byte-identical to today).
    Never raises — a partial context yields a partial read. REASONING CONTEXT ONLY (the
    guardrails never see it; it changes no hard rule).
    """
    try:
        m = market or {}
        briefing = m.get("briefing") if isinstance(m.get("briefing"), dict) else None
        dash = _dashboard_risk_read(m.get("dashboard"))
        sent = m.get("sentiment") if isinstance(m.get("sentiment"), dict) else None
        read = {}
        if briefing:
            as_of = _as_of(briefing)
            if as_of:
                read["as_of"] = as_of
            for k in ("regime", "bias", "bias_label", "headline"):
                if briefing.get(k) is not None:
                    read[k] = briefing[k]
            by_sym = {i.get("symbol"): i for i in (briefing.get("indices") or [])
                      if isinstance(i, dict)}
            chg = _dashboard_change_pct(m.get("dashboard"))   # per-index direction
            idx_out = []
            for sym in _READ_INDEX_SYMBOLS:
                i = by_sym.get(sym)
                if not i:
                    continue
                spot = m.get(_SPOT_KEY[sym]) or i.get("spot")
                idx_out.append({
                    "symbol": sym, "spot": spot, "flip": i.get("gamma_flip"),
                    "put_wall": i.get("put_wall"), "call_wall": i.get("call_wall"),
                    "max_pain": i.get("max_pain"), "exp_move": i.get("expected_move"),
                    "pc_ratio": i.get("pc_ratio"), "change_pct": chg.get(sym),
                    "posture": _posture(spot, i.get("gamma_flip")),
                    "what_if": i.get("what_if")})
            if idx_out:
                read["indices"] = idx_out
        if dash.get("breadth_spread") is not None:
            read["breadth_spread"] = dash["breadth_spread"]
        if dash.get("risk"):
            read["risk"] = dash["risk"]
        if sent:
            if sent.get("score") is not None:
                read["sentiment_score"] = sent["score"]
            if sent.get("bias"):
                read["sentiment_bias"] = sent["bias"]
        # Structural regime (mean-reversion / trending / breakout / choppy / crisis)
        # — CONTEXT ONLY, additive: absent cache → no key → packet unchanged.
        structural = _structural_regime(m.get("regime"))
        if structural:
            read["market_regime"] = structural
        if not read:
            return {}
        read["summary"] = _market_read_summary(read)
        return read
    except Exception:  # noqa: BLE001 — context is best-effort; never block a cycle.
        return {}


def _directional_posture(market_read) -> str:
    """The broad-tape direction from the market_read — ``up`` / ``down`` / ``neutral``.

    Keys on PRICE TRUTH (broad-index change_pct + $ADVN-$DECN breadth), deliberately NOT
    on sentiment/bias (which were inverted during the loss period that motivated the gate)
    nor the gamma flip (a volatility regime, not a direction). Decisive only when the
    $SPX/QQQ change and the breadth AGREE; otherwise ``neutral``. Missing/partial data →
    ``neutral`` (so the gate is inert without a clear read). Never raises. The threshold is
    validated/tuned by the offline backtest before the gate is enabled.
    """
    try:
        mr = market_read or {}
        breadth = mr.get("breadth_spread")
        ups = downs = 0
        for i in (mr.get("indices") or []):
            if i.get("symbol") in ("$SPX", "QQQ") and i.get("change_pct") is not None:
                c = float(i["change_pct"])
                ups += c > 0
                downs += c < 0
        b_up = breadth is not None and float(breadth) > 0
        b_down = breadth is not None and float(breadth) < 0
        if ups > downs and b_up:
            return "up"
        if downs > ups and b_down:
            return "down"
        return "neutral"
    except Exception:  # noqa: BLE001 — context is best-effort; default to no gate.
        return "neutral"


def build_packet(scan_view, paper_view, *, target, limits, market) -> dict:
    """Project the cache views into the model's decision packet (pure).

    Merges the scanner's 0-DTE + swing signals, keeps only allowlisted defined-risk
    spreads (``guardrails.is_allowed``), sorts by composite score descending, caps
    to ``settings.MENU_TOP_N``, and assigns stable ids ``m0..``. Returns the
    model-facing fields (target / day P&L / gap-to-target / VIX / the compact menu /
    open positions / limits) PLUS ``menu_by_id`` mapping each id → the RAW scanner
    signal (the guardrails resolve ids back to raw signals for verbatim paper
    execution; ``run_cycle`` strips ``menu_by_id`` before the model sees the packet).

    Defensive on every field: a missing/empty scan → an empty menu; a paper_view
    with no snapshot → ``day_pnl=None`` and ``gap_to_target == target``.
    """
    raw = list((scan_view or {}).get("signals_0dte", []) or []) + \
        list((scan_view or {}).get("signals_swing", []) or [])
    # Filter to dicts first so a malformed (None/str) list element can't AttributeError
    # in is_allowed — build_packet stays defensive even when called directly.
    allowed = [s for s in raw if isinstance(s, dict) and _g.is_allowed(s)]
    allowed.sort(key=lambda s: (s.get("composite_score") or 0), reverse=True)

    menu, menu_by_id = [], {}
    for i, sig in enumerate(allowed[: _st.MENU_TOP_N]):
        mid = f"m{i}"
        menu.append(_menu_item(sig, mid))
        menu_by_id[mid] = sig

    day_pnl = _day_pnl(paper_view)
    positions = list((paper_view or {}).get("positions", []) or [])
    # v1 attribution: prefer driver-tagged positions; if NONE are tagged, the whole
    # paper account counts (the account is dedicated to the driver during the trial).
    driver_positions = [p for p in positions if str(p.get("source", "")) == "driver"]
    open_positions = driver_positions or positions

    packet = {
        "target": target,
        "day_pnl": day_pnl,
        "gap_to_target": (target - day_pnl) if day_pnl is not None else target,
        "vix": (market or {}).get("vix"),
        "menu": menu,
        "menu_by_id": menu_by_id,
        "open_positions": open_positions,
        "open_count": len(open_positions),
        "limits": limits,
    }
    # Additive REASONING CONTEXT: the five-state market state (label + evidence), if
    # present. Only added when non-blank so an absent state leaves no empty line. It
    # NEVER filters the menu — the menu/allowed set above is computed without it.
    ms_line = _market_state_line(market)
    if ms_line:
        packet["market_state"] = ms_line
    # Additive REASONING CONTEXT: the market read (gamma briefing + dashboard breadth +
    # sentiment), if any source is present. Like market_state it NEVER filters the menu
    # (the allowed set above is computed without it) — the guardrails never see it.
    mr = _market_read(market)
    if mr:
        packet["market_read"] = mr
    return packet


def run_cycle(scan_view, paper_view, *, target, limits, market, client=None) -> dict:
    """Full decision cycle: build_packet → decider.decide → apply_guardrails.

    The per-checkpoint brain a handler (Unit 5) calls. Builds the packet, strips the
    non-JSON ``menu_by_id`` before handing the packet to the model (the model never
    sees the raw signals — only the compact menu + its ids), asks the decider, and
    runs the result through the code-authoritative guardrails (which resolve the ids
    back to raw signals via ``menu_by_id`` and clamp/reject/halt). The daily loss cap
    is sourced from the legacy ``config.RISK_LIMITS`` (``_daily_max_loss``) so it can't
    drift from the old rule tree.

    NEVER raises: any exception anywhere in build/decide/guardrails degrades to a
    stand-down result with the full renderable shape (the handler reads
    ``executable`` / ``rejected`` / ``halted`` / ``halt_reason`` / ``day_pnl`` /
    ``open_positions`` / ``decision`` unconditionally). Returns the guardrails output
    (``executable`` / ``rejected`` / ``halted`` / ``halt_reason``) merged with
    ``decision`` (the audit) + ``day_pnl`` + ``open_positions``.
    """
    try:
        # Imported inside the try (keeps the monkeypatch point at
        # services.driver_svc.decider.decide) so even a decider import error stands down.
        from services.driver_svc import decider
        packet = build_packet(scan_view, paper_view, target=target, limits=limits,
                              market=market)
        model_facing = {k: v for k, v in packet.items() if k != "menu_by_id"}
        decision = decider.decide(model_facing, client=client)
        # Directional gate — compute the decisive posture ALWAYS (from price truth), but
        # only ENFORCE it when the flag is on; otherwise "neutral" keeps the live gate inert
        # (byte-identical execution to today). Ships off until the shadow evidence below
        # justifies flipping it (settings.DIRECTIONAL_GATE_ENABLED).
        gate_on = _st.DIRECTIONAL_GATE_ENABLED
        decisive_posture = _directional_posture(packet.get("market_read"))
        posture = decisive_posture if gate_on else "neutral"
        # One trade per symbol: the symbols already open in the driver book block any new
        # trade on the same underlying (caps per-name concentration). Always enforced.
        open_symbols = frozenset(
            p.get("symbol") for p in packet.get("open_positions", [])
            if isinstance(p, dict) and p.get("symbol"))
        guarded = _g.apply_guardrails(
            decision, packet["menu_by_id"], limits,
            open_count=packet["open_count"], day_pnl=packet["day_pnl"],
            vix=packet["vix"], daily_max_loss=_daily_max_loss(), posture=posture,
            open_symbols=open_symbols)
        # Shadow gate (log-only): what a LIVE directional gate WOULD have blocked among the
        # trades that fired, evaluated at the decisive posture even while the gate is inert.
        # Empty when gate_on (wrong-side trades are already in `rejected`). Recorded on the
        # /driver decision log so DIRECTIONAL_GATE_ENABLED can be flipped on real evidence.
        shadow = _g.shadow_gate(guarded.get("executable", []), decisive_posture)
        shadow["enabled"] = gate_on
        return {"decision": decision, "day_pnl": packet["day_pnl"],
                "open_positions": packet["open_positions"],
                # Threaded out for /driver observability (its one-line summary is stamped
                # onto the decision-log row); None when no market-read source was present.
                "market_read": packet.get("market_read"),
                "shadow_gate": shadow, **guarded}
    except Exception as exc:  # noqa: BLE001 — the cycle never raises; stand down.
        return {"decision": {"stand_down": True, "day_thesis": "", "confidence": 0.0,
                             "trades": [], "error": str(exc)},
                "executable": [], "rejected": [], "halted": False, "halt_reason": None,
                "shadow_gate": {"posture": "neutral", "would_block": [], "n": 0,
                                "enabled": False},
                "day_pnl": None, "open_positions": []}


def _index_price(quote_dict, *fields):
    """Extract a price from a Schwab ``/quotes`` entry (defensive → ``None``).

    Indices ($VIX / $SPX / $VIX1D) nest their values under a ``"quote"`` sub-key
    (``{"assetMainType": "INDEX", "quote": {"lastPrice": 20.3, ...}}``); ETFs carry
    them flat at the top level. Checks both shapes, trying each requested field (or
    a sensible default set) in order. Replicates the legacy
    ``morning_agent._index_price`` so this module no longer imports morning_agent.
    Never raises — a missing/unparseable value yields ``None``.
    """
    if not quote_dict:
        return None
    search_dicts = [quote_dict, quote_dict.get("quote", {})]
    default_fields = fields or ("lastPrice", "mark", "closePrice", "close")
    for d in search_dicts:
        for field in default_fields:
            val = d.get(field)
            if val is not None:
                try:
                    return float(val)
                except (TypeError, ValueError):
                    pass
    return None


def fetch_market_context() -> dict:
    """VIX/SPX/VIX1D + SPY/QQQ spot context for the packet (defensive → ``{}`` on failure).

    Self-contained: fetches ``$VIX,$SPX,$VIX1D,SPY,QQQ`` straight from the schwab-proxy
    (``PROXY_URL``) via ``requests``, replicating the legacy
    ``morning_agent.fetch_market_conditions`` index-quote parsing (index quotes nest
    their values under a ``"quote"`` sub-key — ``_index_price`` handles both nested
    + flat shapes). Only ``vix`` is consumed by the guardrails (the VIX gate; a
    missing ``vix`` → skip that gate); ``spx_spot`` / ``vix1d`` and the live
    ``spy_spot`` / ``qqq_spot`` ride along as context — the ETF spots give the packet's
    ``market_read`` a FRESH per-index spot for distance-to-flip/wall (the briefing spot
    is the fallback).

    ANY failure — a down/slow proxy, a non-200, malformed JSON — degrades to ``{}``
    so a cycle is never blocked or crashed (``build_packet`` then reads
    ``market.get("vix")`` → ``None``).
    """
    try:
        resp = requests.get(f"{PROXY_URL}/quotes",
                            params={"symbols": "$VIX,$SPX,$VIX1D,SPY,QQQ"}, timeout=10)
        resp.raise_for_status()
        quotes = resp.json() or {}
        return {
            "vix": _index_price(quotes.get("$VIX", {})),
            "spx_spot": _index_price(quotes.get("$SPX", {})),
            "vix1d": _index_price(quotes.get("$VIX1D", {})),
            "spy_spot": _index_price(quotes.get("SPY", {})),
            "qqq_spot": _index_price(quotes.get("QQQ", {})),
        }
    except Exception:  # noqa: BLE001 — defensive: never block/crash a cycle.
        return {}

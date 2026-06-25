"""Driver compute module — NiceGUI-free engine-call layer for ``driver_svc``.

The service-side orchestration that the legacy ``claude-driver/morning_agent.py``
``run_morning_agent()`` performed, MINUS its side effects: ``run_morning()``
calls the SAME building-block functions (service health → ML/Schwab signals →
GEX → market conditions → P&L → day grade → ``trade_selector.select_trades``)
but **returns** the pending-trade payload instead of writing
``pending_trade.json`` and HTTP-posting it to the :8300 approval server. The
3-tier flow caches that payload at ``cache:driver:approvals`` and the GUI's
APPROVE/SKIP buttons drive ``execute`` / a status transition.

This module must NOT import ``nicegui`` or anything from ``webgui/``. It depends
only on the copied ``claude-driver`` engines, imported standalone (their dir on
``sys.path``). Because ``driver_svc`` runs in its own process, pinning
``morning_agent``/``config``/``trade_selector``/``order_executor``/``perf_report``
as top-level modules cannot collide with the other domains' engines (the same
isolation ``sentiment_svc`` relies on for ``scoring`` and ``trade_svc`` for
``technical``). ``config.PAPER_TRADE`` is True, so ``execute`` only *simulates*
orders — this service never modifies that flag.

Every public function is defensive: a thrown engine degrades to an ``error`` /
empty payload rather than raising, so one bad cycle can never crash the service.
"""
import sys
from datetime import date, datetime, timezone

from repo_paths import CLAUDE_DRIVER

# ── isolated engine imports (separate process — no cross-app name collision) ──
# claude-driver folder on sys.path so its hyphen-free top-level modules import by
# name (``config`` is generic — safe only because this is a dedicated process).
if str(CLAUDE_DRIVER) not in sys.path:
    sys.path.insert(0, str(CLAUDE_DRIVER))

import morning_agent  # noqa: E402  (orchestration building blocks: grade + fetchers)
import order_executor  # noqa: E402  (execute_trades — PAPER_TRADE=True simulates)
import perf_report  # noqa: E402  (read-only trade-log/perf-db aggregation)
import trade_selector  # noqa: E402  (select_trades by grade + signals)

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
    """The daily loss cap from the legacy config (defensive → 250.0)."""
    try:
        return float(_RISK_LIMITS.get("daily_max_loss", 250.0))
    except (TypeError, ValueError):
        return 250.0


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _today():
    return date.today().isoformat()


def run_morning() -> dict:
    """Run the morning pipeline and return the approval payload (never raises).

    Mirrors ``morning_agent.run_morning_agent`` without its ``pending_trade.json``
    write or the HTTP post to the approval server — the caching/publishing is the
    handler's job. Returns one of:

    * ``status="pending"`` — graded A/B/C with ≥1 qualifying proposed trade.
    * ``status="no_trade"`` — market holiday, graded X, or no qualifying setups
      (``reasons`` explains).
    * ``status="error"`` — a pipeline exception (``error`` carries the message).
    """
    try:
        if morning_agent.is_market_holiday():
            return {"date": _today(), "grade": "", "status": "no_trade",
                    "reasons": ["Market holiday — no run today."],
                    "proposed_trades": [], "timestamp": _now_iso()}

        health = morning_agent.check_service_health()
        ml_signals = morning_agent.fetch_all_ml_signals(health=health)
        gex = morning_agent.fetch_gex_snapshot()
        conditions = morning_agent.fetch_market_conditions()
        pnl = morning_agent.fetch_current_pnl()
        grade, reasons = morning_agent.grade_day(conditions, pnl)

        base = {
            "date": _today(), "grade": grade, "grade_reasons": reasons,
            "conditions": conditions,
            "pnl_today": pnl.get("today"), "pnl_week": pnl.get("week"),
            "timestamp": _now_iso(),
        }

        if grade == "X":
            return {**base, "status": "no_trade", "reasons": reasons,
                    "proposed_trades": []}

        proposed = trade_selector.select_trades(
            grade=grade, ml_signals=ml_signals, gex=gex,
            conditions=conditions, pnl=pnl) or []
        if not proposed:
            return {**base, "status": "no_trade",
                    "reasons": ["No qualifying setups."], "proposed_trades": []}

        return {**base, "status": "pending", "decision": None,
                "proposed_trades": proposed}
    except Exception as exc:  # noqa: BLE001 — degrade, never crash the service.
        return {"date": _today(), "status": "error", "error": str(exc),
                "proposed_trades": [], "timestamp": _now_iso()}


def execute(proposed_trades) -> list:
    """Execute approved trades via ``order_executor`` (PAPER_TRADE simulates).

    Defensive: an executor explosion degrades to a single failed-result entry
    rather than raising, so the approve path always produces a renderable result.
    """
    try:
        return order_executor.execute_trades(proposed_trades or [])
    except Exception as exc:  # noqa: BLE001
        return [{"success": False, "error": str(exc)}]


def build_perf_report() -> dict:
    """Read-only performance aggregation (``perf_report.build_report``).

    Defensive: a missing/locked DB degrades to an empty report rather than
    raising.
    """
    try:
        rep = perf_report.build_report()
        return {"summary": rep.get("summary", {}), "trades": rep.get("trades", [])}
    except Exception:  # noqa: BLE001
        return {"summary": {}, "trades": []}


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
    """
    return {
        "id": mid,
        "symbol": sig.get("symbol"),
        "structure": _g.signal_structure(sig),
        "expiry": sig.get("expiration") or sig.get("expiry"),
        "credit": sig.get("credit"),
        "max_loss": sig.get("max_loss"),
        "pop": sig.get("pop_pct") if sig.get("pop_pct") is not None else sig.get("pop"),
        "score": sig.get("composite_score"),
    }


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
    allowed = [s for s in raw if _g.is_allowed(s)]
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

    return {
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
    from services.driver_svc import decider
    try:
        packet = build_packet(scan_view, paper_view, target=target, limits=limits,
                              market=market)
        model_facing = {k: v for k, v in packet.items() if k != "menu_by_id"}
        decision = decider.decide(model_facing, client=client)
        guarded = _g.apply_guardrails(
            decision, packet["menu_by_id"], limits,
            open_count=packet["open_count"], day_pnl=packet["day_pnl"],
            vix=packet["vix"], daily_max_loss=_daily_max_loss())
        return {"decision": decision, "day_pnl": packet["day_pnl"],
                "open_positions": packet["open_positions"], **guarded}
    except Exception as exc:  # noqa: BLE001 — the cycle never raises; stand down.
        return {"decision": {"stand_down": True, "day_thesis": "", "confidence": 0.0,
                             "trades": [], "error": str(exc)},
                "executable": [], "rejected": [], "halted": False, "halt_reason": None,
                "day_pnl": None, "open_positions": []}

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

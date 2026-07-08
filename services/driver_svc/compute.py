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
    """VIX/SPX/VIX1D context for the decision packet (defensive → ``{}`` on failure).

    Self-contained: fetches ``$VIX,$SPX,$VIX1D`` straight from the schwab-proxy
    (``PROXY_URL``) via ``requests``, replicating the legacy
    ``morning_agent.fetch_market_conditions`` index-quote parsing (index quotes nest
    their values under a ``"quote"`` sub-key — ``_index_price`` handles both nested
    + flat shapes). Only ``vix`` is consumed downstream (the guardrails' VIX gate; a
    missing ``vix`` → skip that gate); ``spx_spot`` / ``vix1d`` ride along as
    context.

    ANY failure — a down/slow proxy, a non-200, malformed JSON — degrades to ``{}``
    so a cycle is never blocked or crashed (``build_packet`` then reads
    ``market.get("vix")`` → ``None``).
    """
    try:
        resp = requests.get(f"{PROXY_URL}/quotes",
                            params={"symbols": "$VIX,$SPX,$VIX1D"}, timeout=10)
        resp.raise_for_status()
        quotes = resp.json() or {}
        return {
            "vix": _index_price(quotes.get("$VIX", {})),
            "spx_spot": _index_price(quotes.get("$SPX", {})),
            "vix1d": _index_price(quotes.get("$VIX1D", {})),
        }
    except Exception:  # noqa: BLE001 — defensive: never block/crash a cycle.
        return {}

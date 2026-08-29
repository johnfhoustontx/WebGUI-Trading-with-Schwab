"""What the autonomous driver is ALLOWED to open — shared by both enforcers.

The driver's risk rules used to live only in ``driver_svc/guardrails.py``, on the
DECISION path. But the path that actually opens a position is
``options_svc.compute.open_driver_position``, reached by the ``driver_paper_create``
command — a different service, which cannot import ``driver_svc``. It re-checked
almost none of the envelope, so anything that enqueued that command directly (a
Redis stream replay, or any local process — Memurai is unauthenticated) reached
the paper book without passing a single structure or capacity gate.

So the *policy* lives here, where both tiers can read it, and each enforces it on
its own path:

* ``driver_svc/guardrails`` — the full cycle-level pass (halt, budget across
  trades, slots, per-cycle cap, directional gate, one-per-symbol). It imports
  these primitives rather than defining its own, so there is one allowlist.
* ``options_svc.compute.open_driver_position`` — the last line of defence,
  re-checking the invariants that are properties of the SIGNAL and the BOOK
  (structure, defined risk, concurrency, deployed risk) rather than of the
  decision. Cycle-only concepts (max trades per cycle, the model's stand-down)
  are deliberately not re-checked there; they are meaningless for a single open.

Pure: no I/O, no service imports.
"""
import math

_STRUCT_MAP = {
    "put_credit_spread": "PCS", "pcs": "PCS",
    "call_credit_spread": "CCS", "ccs": "CCS",
    "iron_condor": "IC", "ic": "IC",
}

# The ONLY structures the autonomous driver may execute: defined-risk credit
# spreads. Anything else (naked, debit, single-leg, futures, equities) is
# rejected regardless of what the model proposed - or of what enqueued the
# command.
ALLOWED = {"PCS", "CCS", "IC"}

# An option contract is 100 shares. The scanner stores ``max_loss`` PER-SHARE
# (e.g. 7.05 for a $SPX spread = width - credit) while the driver's caps are
# DOLLARS, so affordability must be evaluated in per-CONTRACT dollars or a $705
# position is counted as $7 and the cap under-enforces by 100x.
CONTRACT_MULTIPLIER = 100


def normalize_structure(s) -> str:
    """Canonicalize a structure label to ``PCS``/``CCS``/``IC`` (else upper).

    Falsy/``None`` -> ``""``. An unrecognized non-empty value is upper-cased and
    returned as-is so callers can decide (it simply won't be in ``ALLOWED``).
    """
    if not s:
        return ""
    key = str(s).strip().lower()
    return _STRUCT_MAP.get(key, str(s).strip().upper())


def signal_structure(signal) -> str:
    """The structure code from a signal, tolerating either key family.

    Reads, in order: ``structure`` (the projected menu item), ``type`` (a real
    ``cache:options:scan`` signal stores PCS/CCS/IC there), ``strategy``, then
    ``trade_type`` (legacy).

    ``strategy`` is load-bearing for the OPEN path specifically: that path
    normalizes a raw signal with ``setdefault("strategy", signal.get("type"))``,
    i.e. type -> strategy, so a driver signal can arrive carrying ONLY
    ``strategy`` and no ``type`` at all. Omitting it made the open-path gate
    reject a legitimate PCS - caught by ``test_driver_paper_e2e``.

    ⚠ ``trade_type`` is last on purpose: it is the DTE bucket ("0-DTE"/"SWING"),
    NOT the structure, so reading it earlier would mislabel every real signal.
    """
    if not isinstance(signal, dict):
        return ""
    raw = (signal.get("structure") or signal.get("type")
           or signal.get("strategy") or signal.get("trade_type"))
    return normalize_structure(raw)


def max_loss_per_share(signal):
    """The signal's max loss as a float, or ``None`` if missing/unparseable.

    Never raises. A ``None``, non-numeric or **non-finite** value returns
    ``None`` -> "no defined risk -> reject". The finite check matters: the
    scanner derives ``max_loss`` by rounding option marks and ``round(nan, 2)``
    is still ``NaN``, which would slip a ``> 0`` gate and crash ``math.floor``
    downstream.
    """
    if not isinstance(signal, dict):
        return None
    ml = signal.get("max_loss")
    try:
        v = float(ml) if ml is not None else None
    except (TypeError, ValueError):
        return None
    return v if (v is not None and math.isfinite(v)) else None


def defined_risk_per_share(signal):
    """The signal's defined risk per share, DERIVING it when ``max_loss`` is absent.

    A credit spread's risk is ``width - credit`` whether or not the producer
    spelled it out. Menu signals from ``cache:options:scan`` carry ``max_loss``;
    the raw signals that reach the OPEN path carry ``width`` + ``entry_credit``
    and leave it to the sizer. Requiring the explicit field would reject a
    perfectly well-defined spread - caught by ``test_driver_paper_e2e`` before
    this shipped, which is precisely why the open-path gate needed an end-to-end
    test and not only unit tests over synthetic signals.

    An EXPLICIT ``max_loss`` always wins, including an explicit ``0`` (that is a
    statement: no risk, therefore no real position). Derivation happens only when
    the field is absent or unparseable.
    """
    ml = max_loss_per_share(signal)
    if ml is not None:
        return ml
    if not isinstance(signal, dict):
        return None
    try:
        width = float(signal.get("width"))
        credit = float(signal.get("entry_credit", signal.get("credit")))
    except (TypeError, ValueError):
        return None
    risk = width - credit
    return risk if math.isfinite(risk) else None


def max_loss_dollars(signal):
    """Max loss in PER-CONTRACT DOLLARS, or ``None``. See CONTRACT_MULTIPLIER."""
    ml = defined_risk_per_share(signal)
    return ml * CONTRACT_MULTIPLIER if ml is not None else None


def is_allowed(signal) -> bool:
    """True iff ``signal`` is a defined-risk credit spread with real risk.

    Two gates: the structure must be in ``ALLOWED``, AND the defined risk must be
    a positive finite number (``<= 0`` or ``None`` means no real position).
    """
    if signal_structure(signal) not in ALLOWED:
        return False
    ml = defined_risk_per_share(signal)
    return ml is not None and ml > 0


def open_risk_dollars(positions) -> float:
    """Total max-loss dollars currently deployed across open driver positions.

    Rows come from ``paper_account_db``, which stores the per-contract dollar
    ``max_loss`` already multiplied out, alongside ``quantity``. A row whose
    numbers are missing or non-finite contributes 0 rather than poisoning the
    sum with NaN - the caller compares this against a budget, and a NaN total
    would make every ``>`` comparison False and silently disable the cap.
    """
    total = 0.0
    for p in positions or ():
        if not isinstance(p, dict):
            continue
        try:
            ml = float(p.get("max_loss_total") or 0.0)
            if math.isfinite(ml) and ml > 0:
                total += ml
                continue
            per = float(p.get("max_loss") or 0.0)
            qty = float(p.get("quantity") or 0.0)
            if math.isfinite(per) and math.isfinite(qty):
                total += max(0.0, per * qty)
        except (TypeError, ValueError):
            continue
    return round(total, 2)

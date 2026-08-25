"""Realized-outcome calibration -> ``cache:options:calibration``.

The Trade detail panel shows two numbers about expected value. One is structural
(the breakeven win rate a trade's own price demands) and Tier 1 computes it
alone. The other is THIS: what signals in the same family and score band
actually returned, measured in R over `signals.db`.

It matters because every probability the app displays is extracted from the
option's own price, which makes ``EV = p*b - (1-p)`` ~0 by construction and
useless as a recommendation. Realized outcomes are the one independent source of
``p`` this repo holds -- and Tier 1 cannot read SQLite, so the number has to
arrive over the bus.

**A bucket only speaks when it can.** Below ``MIN_N`` it is not published at all;
inside the day-clustered t gate it is published with ``speaks: False``, because
one scan emits a dozen correlated signals and a naive t counts them as a dozen
independent bets. See ``shared/calibration.py`` and
``docs/plans/2026-08-25-ev-in-trade-detail-design.md``.
"""
import logging

from shared.calibration import bucket_key, bucket_stats

log = logging.getLogger(__name__)

# A bucket must clear BOTH gates to make a claim. min_n is about the sample; the
# t gate is about whether its mean is distinguishable from zero at all.
MIN_N = 15
T_GATE = 2.0

_ROUND = {"ev_r": 3, "realized_p": 4, "b": 2, "t_day": 2, "t_stat": 2}


def build_calibration(rows, min_n=MIN_N, t_gate=T_GATE) -> dict:
    """The published payload. PURE, never raises, bounded in size.

    ⚠ No ``computed_at``. ``cache_set(skip_unchanged=True)`` compares payloads to
    decide whether to write, and a timestamp would force a write and a version
    bump every night even when no bucket moved. The bus already stamps a
    ``{key}:ts`` side key for freshness.
    """
    groups, seen = {}, 0
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        seen += 1
        key = bucket_key(r.get("scanner_type"), r.get("entry_score"))
        if key is None:
            continue
        groups.setdefault(key, []).append(r)

    buckets = {}
    for key, rs in groups.items():
        s = bucket_stats(rs)
        if s["n"] < min_n:
            continue                       # too thin to publish at all
        t_day = s["t_day"]
        buckets[key] = {
            "n": s["n"], "days": s["days"],
            "speaks": bool(t_day is not None and abs(t_day) >= t_gate),
            **{k: (round(s[k], d) if isinstance(s[k], float) else s[k])
               for k, d in _ROUND.items()},
        }

    return {"buckets": dict(sorted(buckets.items())),
            "min_n": min_n, "t_gate": t_gate, "rows": seen}


def load_and_build(db_path=None, min_n=MIN_N, t_gate=T_GATE) -> dict:
    """Read ``signals.db`` and build the payload. Degrades to empty, never raises.

    A missing database is the fresh-clone case (``options-scanner/data/`` is
    gitignored), and it must not take the nightly slot down.
    """
    try:
        from tools.signal_calibration import DEFAULT_DB, load_rows
        rows = load_rows(db_path or DEFAULT_DB)
    except Exception as exc:  # noqa: BLE001 - see the docstring
        log.warning("calibration: cannot read signals.db (%r)", exc)
        rows = []
    return build_calibration(rows, min_n, t_gate)

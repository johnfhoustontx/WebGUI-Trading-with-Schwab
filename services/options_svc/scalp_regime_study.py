"""The Opening Scalp's net-gamma regime study: the gap rule, the cells and the
usable sessions (design 2026-10-25-scalp-gamma-regime-study, section 3.1).

The study asks whether the 15-minute calls' long trades win more often when the
dealers' net gamma was negative or positive when the call was made. This module
holds the pure part that decides WHAT is counted; the test and the report are
built on it.

Rules:

* **The regime comes from ``intraday_readings`` ONLY.** A reading's recorded
  ``snapshot`` is the one source; ``prob_sources`` is never read.
* **A gap is ``None``, never a regime.** A matrix that is not fresh, a symbol with
  no row, an untrusted wall (net GEX of 0, missing or not a number) all give
  ``None``; a gap is dropped, not counted as either regime.
* **A backfilled snapshot is read as recorded.** The extract applied the age when
  it read, so ``"backfill"`` carries no age to check.
* **Long roles decide** (``long_with`` and ``long_against``), labelled through
  ``scalp_study._label`` (skip is no label), at one volatility multiplier.
* **Only the three blocks are used.** A slot with no block (the 08:36 record) and
  a day that closes early are never counted.
* Pure functions of what they are given: no bus, no store, no import from
  ``compute``.
"""
import random
from datetime import date

from shared import scalp_config
from shared.numeric import finite as _finite

from services.options_svc import scalp_confirm as _confirm
from services.options_svc import scalp_study as _study

NEGATIVE = "negative"
POSITIVE = "positive"

LONG_ROLES = ("long_with", "long_against")
CREDIT_ROLES = ("credit_with", "credit_against")

#: The block the decision is made on.
_DECISION_BLOCK = "afternoon"


def _fresh(source) -> bool:
    """True when the matrix source was read recently enough, or was backfilled."""
    source = source if isinstance(source, dict) else {}
    state = source.get("state")
    if state == "backfill":
        return True
    if state != "ok":
        return False
    age = _finite(source.get("age_min"))
    return age is not None and age <= scalp_config.confirm_max_age_min()


def regime_of(snapshot, symbol):
    """``"negative"`` or ``"positive"`` for the symbol's net GEX in ``snapshot``
    (a ``scalp_confirm.snapshot``), or ``None`` for a gap."""
    if not isinstance(snapshot, dict):
        return None
    sources = snapshot.get("sources")
    if not _fresh((sources if isinstance(sources, dict) else {}).get("matrix")):
        return None
    matrix = snapshot.get("matrix")
    want = _confirm._norm(symbol)
    row = None
    for name, candidate in (matrix.items() if isinstance(matrix, dict) else ()):
        if _confirm._norm(name) == want:
            row = candidate
            break
    if not isinstance(row, dict):
        return None
    view = _confirm._wall_view(row)
    # The view derives the flag from net GEX; a row may also carry the flag as
    # recorded, and either one makes the reading a gap.
    if view.get("walls_untrusted") or row.get("walls_untrusted") is True:
        return None
    net_gex = _finite(row.get("net_gex"))
    if net_gex is None or net_gex == 0:
        return None
    return NEGATIVE if net_gex < 0 else POSITIVE


def regimes(readings) -> dict:
    """``{(day, slot, symbol): regime}`` from ``{(day, slot, symbol): reading_body}``;
    a body with no usable snapshot reads as a gap."""
    out = {}
    for key, body in readings.items():
        body = body if isinstance(body, dict) else {}
        out[key] = regime_of(body.get("snapshot"), key[2])
    return out


def cells(outcomes, scale, block, roles) -> dict:
    """``{(day, symbol, slot): [wins, n, timeouts]}`` over the labelled rows of
    ``block`` for ``roles`` at the multiplier ``scale``."""
    want = _study._scale_key(scale)
    out = {}
    for row in outcomes:
        if row.get("block") != block or row.get("role") not in roles:
            continue
        if _study._scale_key(row.get("iv_scale")) != want:
            continue
        label = _study._label(row.get("result"))
        if label is None:
            continue
        cell = out.setdefault((row["day"], row["symbol"], row["slot"]), [0, 0, 0])
        cell[0] += label
        cell[1] += 1
        cell[2] += 1 if row.get("result") == "timeout" else 0
    return out


def usable_sessions(regime_map, outcomes) -> list:
    """The sorted ISO days the study counts: on or after the declared start, not an
    early close, with at least one afternoon call (day, slot, symbol) that has BOTH
    a non-gap regime AND a labelled long outcome at the decision multiplier. The
    statistic joins regime and outcome by slot and symbol, so a day whose regime
    and outcome sit on different calls would count and contribute nothing."""
    start = scalp_config.regime_start_day().isoformat()
    early = scalp_config.early_close_days()
    with_regime = {key for key, regime in regime_map.items()
                   if regime is not None
                   and scalp_config.intraday_block(key[1]) == _DECISION_BLOCK}
    scale = _study._scale_key(scalp_config.iv_scale())
    days = set()
    for row in outcomes:
        if (row.get("block") == _DECISION_BLOCK and row.get("role") in LONG_ROLES
                and _study._scale_key(row.get("iv_scale")) == scale
                and _study._label(row.get("result")) is not None
                and (row["day"], row["slot"], row["symbol"]) in with_regime):
            days.add(row["day"])
    return sorted(day for day in days
                  if day >= start and not _is_early_close(day, early))


def _is_early_close(day, early) -> bool:
    try:
        return date.fromisoformat(day) in early
    except (TypeError, ValueError):
        return False


# ── the effect, the permutation and the declared verdict (design 3.3 - 3.5) ──

def _shares(cell_map, regime_map, symbols=None):
    """``(negative, positive)``, each ``{(day, symbol): [wins, n]}`` summed over the
    slots of that pair whose regime is that one."""
    pooled = {NEGATIVE: {}, POSITIVE: {}}
    for (day, symbol, slot), (wins, n, _timeouts) in cell_map.items():
        if symbols is not None and symbol not in symbols:
            continue
        side = pooled.get(regime_map.get((day, slot, symbol)))
        if side is None:
            continue
        pair = side.setdefault((day, symbol), [0, 0])
        pair[0] += wins
        pair[1] += n
    return pooled[NEGATIVE], pooled[POSITIVE]


def _mean_share(side):
    shares = [wins / n for wins, n in side.values() if n]
    return sum(shares) / len(shares) if shares else None


def effect(cell_map, regime_map, symbols=None):
    """The negative-gamma target rate minus the positive-gamma one, in percentage
    points: each side the mean over (day, symbol) pairs of that pair's pooled share.
    ``None`` when either side has no pair. ``symbols`` limits the pairs."""
    negative, positive = _shares(cell_map, regime_map, symbols)
    low, high = _mean_share(negative), _mean_share(positive)
    if low is None or high is None:
        return None
    return 100.0 * (low - high)


def permutation_p(cell_map, regime_map, sessions, observed, n, seed) -> float:
    """The share of ``n`` relabelings whose effect is at least ``observed``.

    A relabeling gives each session another session's regimes (the whole day moved
    together, slot for slot and symbol for symbol), so the day's own outcomes keep
    their serial structure and only the regime they are paired with is shuffled; a
    slot missing a regime on either day becomes a gap."""
    rng = random.Random(seed)
    by_day = {}
    for (day, slot, symbol), regime in regime_map.items():
        by_day.setdefault(day, []).append((slot, symbol, regime))
    hits = 0
    for _ in range(n):
        moved = rng.sample(sessions, len(sessions))
        fake = {}
        for a, b in zip(sessions, moved):
            for slot, symbol, regime in by_day.get(a, ()):
                other = regime_map.get((b, slot, symbol))
                fake[(a, slot, symbol)] = other if (regime is not None and other is not None) else None
        e = effect(cell_map, fake)
        if e is not None and e >= observed - 1e-9:
            hits += 1
    return hits / n


def _timeout_share(cell_map, regime_map) -> dict:
    """The timeouts over n of the long rows, pooled by regime."""
    out = {}
    for name in (NEGATIVE, POSITIVE):
        n = timeouts = 0
        for (day, symbol, slot), cell in cell_map.items():
            if regime_map.get((day, slot, symbol)) == name:
                n += cell[1]
                timeouts += cell[2]
        out[name] = timeouts / n if n else None
    return out


def _precondition(agreement) -> dict:
    if not isinstance(agreement, dict):
        return {"checked": False, "passed": False, "share": None, "sessions": None}
    share = _finite(agreement.get("share"))
    sessions = agreement.get("sessions")
    sessions = sessions if isinstance(sessions, int) and not isinstance(sessions, bool) else None
    passed = (share is not None and share >= scalp_config.regime_min_sign_agreement()
              and sessions is not None
              and sessions >= scalp_config.regime_agree_min_sessions())
    return {"checked": True, "passed": passed, "share": share, "sessions": sessions}


def _empty_block(name, reasons) -> dict:
    return {"block": name, "tested": False, "passed": False, "reasons": reasons,
            "effect_pp": {str(_study._scale_key(s)): None for s in scalp_config.iv_scales()},
            "p": None, "negative_pairs": 0, "positive_pairs": 0,
            "reported": {"per_symbol": {s: None for s in scalp_config.symbols()},
                         "credit_pp": None, "long_with_pp": None,
                         "long_against_pp": None,
                         "timeout_share": {NEGATIVE: None, POSITIVE: None}}}


def _test_block(name, regime_map, outcomes, sessions, precondition) -> dict:
    decision = scalp_config.iv_scale()
    long_cells = cells(outcomes, decision, name, LONG_ROLES)
    observed = effect(long_cells, regime_map)
    effects = {str(_study._scale_key(s)):
               effect(cells(outcomes, s, name, LONG_ROLES), regime_map)
               for s in scalp_config.iv_scales()}
    negative, positive = _shares(long_cells, regime_map)

    reasons, p = [], None
    if observed is None:
        reasons.append("effect undefined")
    else:
        p = permutation_p(long_cells, regime_map, sessions, observed,
                          scalp_config.regime_permutations(), scalp_config.regime_seed())
        if p > scalp_config.regime_alpha():
            reasons.append("p above alpha")
        if observed < scalp_config.regime_min_effect_pp():
            reasons.append("effect below the floor")
        if not all(v is not None and v > 0 for v in effects.values()):
            reasons.append("not above 0 at every multiplier")
    if not precondition["checked"]:
        reasons.append("precondition not checked")
    elif not precondition["passed"]:
        reasons.append("precondition failed")

    def pp(roles):
        return effect(cells(outcomes, decision, name, roles), regime_map)

    return {"block": name, "tested": True, "passed": not reasons, "reasons": reasons,
            "effect_pp": effects, "p": p,
            "negative_pairs": len(negative), "positive_pairs": len(positive),
            "reported": {
                "per_symbol": {s: effect(long_cells, regime_map, (s,))
                               for s in scalp_config.symbols()},
                "credit_pp": pp(CREDIT_ROLES),
                "long_with_pp": pp(("long_with",)),
                "long_against_pp": pp(("long_against",)),
                "timeout_share": _timeout_share(long_cells, regime_map)}}


def evaluate(regime_map, outcomes, agreement) -> dict:
    """The declared verdict. Nothing is computed until the declared number of
    usable sessions has been collected; then only the first that many are used,
    each block in the declared order is tested only when the one before passed, and
    a block passes when none of its reasons applies."""
    usable = usable_sessions(regime_map, outcomes)
    needed = scalp_config.regime_min_sessions()
    if len(usable) < needed:
        return {"status": "collecting", "sessions": len(usable), "needed": needed,
                "first": usable[0] if usable else None,
                "start_day": scalp_config.regime_start_day().isoformat()}
    sessions = usable[:needed]
    chosen = set(sessions)
    regime_map = {k: v for k, v in regime_map.items() if k[0] in chosen}
    outcomes = [r for r in outcomes if r.get("day") in chosen]
    precondition = _precondition(agreement)

    blocks, previous = [], None
    for name in scalp_config.regime_order():
        if previous is not None and not previous["passed"]:
            blocks.append(_empty_block(name, [f"not tested: {previous['block']} did not pass"]))
        else:
            blocks.append(_test_block(name, regime_map, outcomes, sessions, precondition))
        previous = blocks[-1]
    return {"status": "ready", "sessions": sessions, "precondition": precondition,
            "blocks": blocks}

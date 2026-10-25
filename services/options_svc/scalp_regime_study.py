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
    early close, with a non-gap afternoon regime AND a labelled long afternoon
    outcome at the decision multiplier."""
    start = scalp_config.regime_start_day().isoformat()
    early = scalp_config.early_close_days()
    with_regime = set()
    for (day, slot, _symbol), regime in regime_map.items():
        if regime is not None and scalp_config.intraday_block(slot) == _DECISION_BLOCK:
            with_regime.add(day)
    scale = _study._scale_key(scalp_config.iv_scale())
    with_outcome = set()
    for row in outcomes:
        if (row.get("block") == _DECISION_BLOCK and row.get("role") in LONG_ROLES
                and _study._scale_key(row.get("iv_scale")) == scale
                and _study._label(row.get("result")) is not None):
            with_outcome.add(row["day"])
    days = []
    for day in with_regime & with_outcome:
        if day < start or _is_early_close(day, early):
            continue
        days.append(day)
    return sorted(days)


def _is_early_close(day, early) -> bool:
    try:
        return date.fromisoformat(day) in early
    except (TypeError, ValueError):
        return False

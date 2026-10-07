"""Which collected symbols get a real chain fetch every minute.

The one-minute collector has two tiers once the proxy's chain store is on (see
``gex_collector.poll_once`` and config/marketdata.toml [collection]): CORE
symbols are fetched every minute, and a symbol collected only because it is on
the watchlist is fetched every Nth minute and carried forward in between. This
module decides, each poll, which symbols are which.

It lived in ``compute.py`` until 2026-10-04 and was moved out unchanged (audit
CQ-03: that file had passed 10,000 lines). ``compute`` re-exports
``collection_tiers``, ``_flip_alert_symbols`` and ``MAX_TAIL_INTERVAL_MIN``, so
its callers are untouched. It imports nothing from ``compute``.
"""
import logging

from services import _degrade

log = logging.getLogger(__name__)

# The longest tail interval the collector will run. The Opportunity Board's flow
# acceleration reads a 15-minute window (matrix.py); past 5 minutes it holds too
# few real fetches to mean anything. 3 and 5 divide 15; the others in range put
# an uneven number of real fetches in each window.
MAX_TAIL_INTERVAL_MIN = 5

# Said once per process, not once a minute: a clamped setting stays clamped.
_TIER_WARNED: set = set()


def flip_alert_symbols(flow_cfg):
    """What the gamma-flip alert watches, for ``collection_tiers(flip=...)``:
    a set of symbols, an EMPTY set for "every symbol", or None for "nothing".

    Read exactly as ``handlers.run_flow_alerts`` / ``_run_gamma_flip`` read it:
    the alert runs unless the top-level ``enabled`` or its own is falsy (so a
    hand-typed ``"false"`` is ON, as it is there), and ``symbols or the whole
    flow universe`` — an empty or missing list is every symbol.

    A list naming symbols keeps them on the one-minute tier even while the
    alert is off (as for the hedging-flow symbols). A value that names no
    usable symbol is "every symbol" while the alert runs: the reading that can
    never let the alert fire from modelled gamma."""
    from services.options_svc import flow_alerts

    gf = flow_alerts.section(flow_cfg, "gamma_flip")
    top = flow_cfg.get("enabled", True) if isinstance(flow_cfg, dict) else True
    running = bool(top) and bool(gf.get("enabled", True))
    raw = gf.get("symbols")
    if isinstance(raw, str):
        raw = [raw]
    named = ({s for s in raw if isinstance(s, str) and s}
             if isinstance(raw, (list, tuple, set, frozenset)) else set())
    if named:
        return named
    return set() if running else None


def collection_tiers(universe, *, base, capture=None, hiro=None, flip=None):
    """The collector's tiers for this poll (see ``gex_collector.poll_once``), or
    None while the proxy's chain store is not on.

    A dict WHENEVER the store is on, even when nothing may be carried: the
    tiers are also what makes the collector send its fresh-age limit with every
    chain request. Without them it sends none and the proxy's own applies —
    1,800 seconds while every session is closed, which the collector's minutes
    before 08:30 and after 15:00 CT are, so those polls were all answered with
    one stored chain. The tail is EMPTY at an interval of 1, when no polled
    symbol is watchlist-only, and when the flip alert watches every symbol.

    The TAIL is every polled symbol that is collected only because it is on the
    watchlist. Kept on the one-minute tier: the symbols named in
    config/symbols.toml [collection] (``base``), the symbol open on the Dealer
    Positioning page and the public page's hot symbols (``capture``), the
    hedging-flow symbols (``hiro``), whose rows are measured from fresh chains,
    and the symbols the gamma-flip alert watches (``flip``): a carried row's
    flip level comes from modelled gamma, and that alert must never fire from
    one. ``flip`` is None for "the alert watches nothing"; an EMPTY ``flip``
    means it watches every symbol, which leaves no tail at all.

    While the store is NOT on (shadow, off, or the chain store switched off)
    nothing is carried - the tail is empty at an interval of 1 - but the
    fresh-age limit is still sent. It used to be None there, so in shadow the
    collector sent no limit at all: the proxy counted the collector's own
    off-session repeats as savings "on" does not deliver, and a checkout whose
    file said shadow while it borrowed an "on" proxy was handed its own
    previous chain (audit AC-102). The limit is ignored by a proxy that is off.
    None only when the settings cannot be read."""
    try:
        from shared import marketdata_config as mdc

        store_on = mdc.mode() == "on" and mdc.store_on("chains")
        cfg = mdc.section("collection")
        if not store_on:
            out = {"tail": frozenset(), "interval_min": 1,
                   "fresh_max_age_sec": int(cfg["fresh_max_age_sec"])}
            # How long a symbol with nothing listed rests does not depend on
            # the store: it applies in every mode.
            if "empty_retry_min" in cfg:
                out["empty_retry_min"] = cfg["empty_retry_min"]
            return out
        interval = max(1, int(cfg["tail_interval_min"]))
        if interval > MAX_TAIL_INTERVAL_MIN:
            if ("tail_interval_min", interval) not in _TIER_WARNED:
                _TIER_WARNED.add(("tail_interval_min", interval))
                log.warning("collection tail_interval_min=%s is above %s; using %s",
                            interval, MAX_TAIL_INTERVAL_MIN, MAX_TAIL_INTERVAL_MIN)
            interval = MAX_TAIL_INTERVAL_MIN
        core = set(base) | set(capture or ()) | set(hiro or ())
        watches_all = False
        if flip is not None:
            flip = set(flip)
            watches_all = not flip     # the flip alert watches every symbol
            core |= flip
        tail = frozenset(s for s in universe if s not in core)
        if interval <= 1 or watches_all:
            tail = frozenset()         # every fetch real; the limit still sent
        out = {"tail": tail, "interval_min": interval,
               "fresh_max_age_sec": int(cfg["fresh_max_age_sec"])}
        # The carry's limits, when the settings name them. The collector
        # checks each and falls back to its own built-in value.
        for key in ("max_gamma_ratio", "carry_slack_sec", "cap_refetch_max",
                    "empty_retry_min"):
            if key in cfg:
                out[key] = cfg[key]
        return out
    except Exception:
        _degrade.degraded("options.collection_tiers")
        return None

"""The per-minute context the GEX collection's ``on_chain`` hooks run in.

Every chain the one-minute poll fetches is handed to ``on_chain`` before its
rows are written: the gamma refresh's chain stash, extended-hours eligibility,
the bought/sold tally, traded premium, the unusual-activity and big-delta
detectors and the HIRO row. This module builds that callback for ONE minute.

**The hooks run on the minute's clock, never the wall clock.** The raw feed
derives minutes after the fact (catch-up after a restart), and a hook that read
the wall clock would stamp a replayed 10:42 with 13:00. Live collection uses the
same context, so a live minute and a replayed one are computed identically
(docs/plans/2026-10-14-raw-feed-and-catch-up-design.md). The minute is the
collector's own key: the minute-floored unix second its rows carry.

It lived inside ``compute.collect_gex_snapshots`` until 2026-10-14. The stash
helpers it calls still live in ``compute`` (tests patch them there), so they are
passed in as ``hooks`` and this module imports nothing from ``compute``.
"""
from __future__ import annotations

import datetime as _dt
import logging
from dataclasses import dataclass, field
from typing import Callable
from zoneinfo import ZoneInfo

from services import _degrade

log = logging.getLogger(__name__)

CT = ZoneInfo("America/Chicago")


@dataclass
class DeriveContext:
    #: The callback ``gex_collector.poll_once`` / ``derive_minute`` calls per chain.
    on_chain: Callable
    #: Minute-floored unix seconds, and the same instant as a CT datetime.
    minute_ts: int
    minute_dt: _dt.datetime
    #: The flow-alert thresholds this minute was read with (one read serves all).
    uoa_cfg: dict = field(default_factory=dict)
    #: The HIRO symbols (the one-minute tier needs them) and this minute's rows.
    hiro_syms: set = field(default_factory=set)
    hiro_rows: dict = field(default_factory=dict)
    hiro_date: str | None = None
    #: Extended-hours eligibility seen this minute, per symbol.
    eth_seen: dict = field(default_factory=dict)


def minute_of(ts) -> int:
    """The minute-floored unix second of ``ts``."""
    return int(ts) // 60 * 60


def build_context(minute_ts, capture, hooks) -> DeriveContext:
    """The context for one collected minute.

    ``capture``: symbols whose chains are stashed for the same tick's gamma
    refresh (None or empty: none). ``hooks``: an object with ``clear_stashes()``,
    ``stash_tick_chain(sym, chain)``, ``stash_uoa(sym, hits)``,
    ``stash_big_delta(sym, hits)`` and ``hiro_tick_row(sym, chain, date, ts)`` --
    in production, ``compute``'s own functions. Clears the stashes first, as the
    poll always has."""
    # Contract-level UOA + big_delta both ride the poll's on_chain hook (reusing
    # each fetched chain -- no re-fetch); results are stashed per symbol and
    # consumed once by run_flow_alerts. flow_alerts is PURE, imported lazily. ONE
    # load_thresholds() call serves both detectors. Per-symbol extended-trading-
    # hours eligibility rides the SAME hook: it is a root field of the chain.
    from services.options_svc import eth, flow_alerts, flow_sides_tick, traded_premium
    from shared import market_calendar as _mc

    minute_ts = minute_of(minute_ts)
    minute_dt = _dt.datetime.fromtimestamp(minute_ts, CT)
    hooks.clear_stashes()
    uoa_cfg = flow_alerts.load_thresholds()
    # Kill-switch: when the feature is disabled, skip the per-symbol UOA compute
    # entirely (nothing computed/published). The chain-capture stash stays on.
    uoa_on = uoa_cfg.get("enabled", True)
    # big_delta has its OWN enabled flag, read through flow_alerts.section and
    # required to be literally True: a scalar `big_delta = 5` in config/local
    # used to raise here, before the poll, stopping all GEX collection; a
    # hand-typed "false" switched the detector ON. Both now fail safe.
    big_delta_on = flow_alerts.section(uoa_cfg, "big_delta").get("enabled") is True
    # HIRO (docs/plans/2026-10-01-hiro-alert-design.md): REGULAR session only --
    # off-hours the chain's underlyingPrice can be stale and index OI reads zero.
    # Safe defaults FIRST, derived inside a guard: flow_alerts._merge lets a
    # scalar override replace the [hiro] table, and a setup error here must
    # never stop GEX collection.
    hiro_on = False
    hiro_syms: set = set()
    try:
        hiro_cfg = uoa_cfg.get("hiro", {})
        if not isinstance(hiro_cfg, dict):
            hiro_cfg = {}
        raw_syms = hiro_cfg.get("symbols")
        if isinstance(raw_syms, str):
            raw_syms = [raw_syms]
        if isinstance(raw_syms, (list, tuple)):
            hiro_syms = {s for s in raw_syms if isinstance(s, str)}
        hiro_on = (hiro_cfg.get("enabled") is True and bool(hiro_syms)
                   and _mc.is_regular_hours(minute_dt))
    except Exception:
        _degrade.degraded("options.hiro_setup")
        hiro_on = False
    wanted = set(capture) if capture else set()
    ctx = DeriveContext(on_chain=None, minute_ts=minute_ts, minute_dt=minute_dt,
                        uoa_cfg=uoa_cfg, hiro_syms=hiro_syms,
                        hiro_date=minute_dt.date().isoformat())

    def on_chain(sym, chain):
        if sym in wanted:
            hooks.stash_tick_chain(sym, chain)
        # Ungated: a pure dict read, no compute, and the map must stay complete
        # even when the UOA kill-switch is off.
        ctx.eth_seen[sym] = eth.chain_eth_eligible(chain)
        flow_sides_tick.on_chain(sym, chain, minute_dt)   # bought/sold tally; never raises
        traded_premium.on_chain(sym, chain, minute_dt)    # traded premium; never raises
        if uoa_on:
            # Best-effort -- a UOA detect failure must NEVER break collection.
            try:
                hooks.stash_uoa(sym, flow_alerts.detect_uoa(sym, chain, uoa_cfg))
            except Exception:
                log.debug("UOA detect failed for %s", sym, exc_info=True)
        if big_delta_on:
            # Best-effort -- a big_delta detect failure must NEVER break collection.
            try:
                hooks.stash_big_delta(sym, flow_alerts.detect_big_delta(sym, chain, uoa_cfg))
            except Exception:
                log.debug("big_delta detect failed for %s", sym, exc_info=True)
        if hiro_on and sym in hiro_syms:
            # hiro_tick_row never raises; a None row (unusable spot) is skipped.
            row = hooks.hiro_tick_row(sym, chain, ctx.hiro_date, minute_ts)
            if row is not None:
                ctx.hiro_rows[sym] = row

    ctx.on_chain = on_chain
    return ctx

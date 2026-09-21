"""Readers for the PUBLIC chain (``cache:options:pub_chain:<SYMBOL>``). PURE.

One public chain per symbol, shared by the public Rescue form and the public
Calculator (``shared.public_tools.chain_view`` is ``public_rescue.ladder_view``),
so both pages read it through these helpers and cannot disagree about what an
expiration or a strike is. Its shape (``services/options_svc/public_chain.py``):

    {"symbol", "api", "spot", "expirations": [...], "loaded_at",
     "strikes": {expiry: {"call": [...], "put": [...]}},
     "quotes":  {expiry: {"call"|"put": {"500.0": {bid, ask, mark, delta}}}}}

``quotes`` is present ONLY while the one quotes switch
(``public_scan.show_leg_quotes``) is on; without it the chain is strikes only.
Every function here is total: junk in, empty out, never a raise.
"""
from __future__ import annotations

import datetime as dt
import math
from zoneinfo import ZoneInfo

CT = ZoneInfo("America/Chicago")


# ── the strikes list ────────────────────────────────────────────────────────

def listed_expirations(ladder) -> list:
    """Every expiration the symbol lists."""
    exps = (ladder or {}).get("expirations")
    return [e for e in exps if isinstance(e, str)] if isinstance(exps, list) else []


def loaded_expirations(ladder) -> list:
    """The listed expirations whose strikes are here, in listing order."""
    strikes = (ladder or {}).get("strikes")
    strikes = strikes if isinstance(strikes, dict) else {}
    return [e for e in listed_expirations(ladder) if e in strikes]


def ladder_strikes(ladder, expiry, otype) -> list:
    """Strikes for one expiration and side; the union across loaded expirations
    when no expiration is set yet (the editor asks before a template has one)."""
    strikes = (ladder or {}).get("strikes")
    strikes = strikes if isinstance(strikes, dict) else {}
    side = "put" if str(otype or "").lower() == "put" else "call"
    if expiry:
        return list((strikes.get(expiry) or {}).get(side) or [])
    out = set()
    for per in strikes.values():
        out.update((per or {}).get(side) or [])
    return sorted(out)


# ── the quotes block ────────────────────────────────────────────────────────

_QUOTE_FIELDS = ("bid", "ask", "mark", "delta")
_MAPS = (("call", "callExpDateMap"), ("put", "putExpDateMap"))


def _finite(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v) if math.isfinite(v) else None


def _strike(s):
    try:
        f = float(s)
    except (TypeError, ValueError, OverflowError):
        return None
    return f if math.isfinite(f) and f > 0 else None


def has_quotes(ladder) -> bool:
    """Whether the chain carries a quotes block (the switch was on when it was
    published). An empty block counts as none: nothing to draw a grid from."""
    q = (ladder or {}).get("quotes") if isinstance(ladder, dict) else None
    return isinstance(q, dict) and bool(q)


def grid_chain(ladder) -> dict | None:
    """The quotes block in the thinned-chain shape ``chain_grid`` reads -
    ``{"callExpDateMap": {"<expiry>:0": {"500.0": [{bid, ask, mark, delta}]}}}``
    - or None when there is no quotes block.

    The entry panel's grid and the chain readers (``extract_price``,
    ``extract_delta``) key on that shape, so adapting the block ONCE here lets
    the public page reuse them unchanged. The ``:0`` suffix stands where
    Schwab's days-to-expiry would be; every reader splits it off. Only the four
    published fields are carried, each a finite float or None, and a strike key
    that is not a finite positive number is dropped."""
    if not has_quotes(ladder):
        return None
    out = {m: {} for _r, m in _MAPS}
    for expiry, sides in ladder["quotes"].items():
        if not isinstance(expiry, str) or not isinstance(sides, dict):
            continue
        for right, map_key in _MAPS:
            per = sides.get(right)
            if not isinstance(per, dict):
                continue
            rows = {}
            for s, q in per.items():
                f = _strike(s)
                if f is None or not isinstance(q, dict):
                    continue
                rows[str(f)] = [{k: _finite(q.get(k)) for k in _QUOTE_FIELDS}]
            if rows:
                out[map_key][f"{expiry}:0"] = rows
    # An expiration whose strikes are here but which carries no quotes block (a
    # chain published before the switch went on, then merged) is drawn with
    # its strikes and empty quotes, so the grid shows dashes - never a
    # "Loading strikes" line waiting for quotes that are not coming.
    strikes = ladder.get("strikes") if isinstance(ladder.get("strikes"), dict) else {}
    empty = {k: None for k in _QUOTE_FIELDS}
    for expiry, sides in strikes.items():
        if not isinstance(expiry, str) or not isinstance(sides, dict):
            continue
        for right, map_key in _MAPS:
            have = out[map_key].setdefault(f"{expiry}:0", {})
            for s in sides.get(right) or []:
                f = _strike(s)
                if f is not None and str(f) not in have:
                    have[str(f)] = [dict(empty)]
            if not have:
                out[map_key].pop(f"{expiry}:0")
    return out if any(out.values()) else None


def quotes_as_of(ladder) -> str | None:
    """"Quotes as of HH:MM CT" from the chain's ``loaded_at``, or None when it
    carries no quotes or no readable time. ⚠ A merged expiration keeps the
    chain's first ``loaded_at`` (``public_chain.load``), so this is the OLDEST
    quote's time - the honest stamp for the whole block."""
    if not has_quotes(ladder):
        return None
    try:
        when = dt.datetime.fromisoformat(str(ladder.get("loaded_at")))
    except (TypeError, ValueError):
        return None
    when = when if when.tzinfo else when.replace(tzinfo=dt.timezone.utc)
    return f"Quotes as of {when.astimezone(CT):%H:%M} CT"

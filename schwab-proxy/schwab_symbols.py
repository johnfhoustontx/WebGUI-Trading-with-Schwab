"""
SchwabProxy - symbol spelling at the Schwab boundary

A class share is written with a dot in this app (``BRK.B``, which the ticker
allow-list ``shared.symbols.SYMBOL_RE`` accepts) and with a slash at Schwab
(``BRK/B``). Schwab refuses the dotted form outright: measured 2026-10-04,
``BRK.B``, ``BF.B``, ``BRK.A`` and ``HEI.A`` all came back under
``errors.invalidSymbols`` while their slash forms answered.

So the proxy translates in ONE place, the call to Schwab: dot to slash on the
way out, and the answer's own symbol names back on the way in. Everything above
that call (the market-data store, every service, every page, every Redis key)
only ever sees the app's spelling.

Pure: no I/O, no repo imports.
"""
import re
from typing import Dict, Optional, Tuple

# Letters, ONE dot, one or two letters. Not an index (``$NYHGH.X`` keeps its
# dot at Schwab) and not a futures root (``/ESU26`` already has its slash).
_CLASS_SHARE = re.compile(r"[A-Z]{1,6}\.[A-Z]{1,2}")

# The request parameters that carry symbols: one, or a comma-separated list.
_ONE = "symbol"
_MANY = "symbols"


def to_schwab(symbol):
    """Schwab's spelling of one app symbol; anything that is not a class share
    is returned exactly as given."""
    if isinstance(symbol, str) and _CLASS_SHARE.fullmatch(symbol):
        return symbol.replace(".", "/")
    return symbol


def outbound(params: Optional[Dict]) -> Tuple[Optional[Dict], Dict[str, str]]:
    """``(params for Schwab, {schwab spelling: app spelling})``.

    The mapping holds only what THIS request translated, and is what
    ``inbound`` undoes. With nothing to translate the caller's own object comes
    back with an empty mapping; the caller's dict is never changed in place."""
    if not isinstance(params, dict):
        return params, {}
    back: Dict[str, str] = {}
    out = params
    one = params.get(_ONE)
    if isinstance(one, str):
        sent = to_schwab(one)
        if sent != one:
            back[sent] = one
            out = dict(out)
            out[_ONE] = sent
    many = params.get(_MANY)
    if isinstance(many, str):
        names = many.split(",")
        sent_names = [to_schwab(n) for n in names]
        if sent_names != names:
            back.update({s: n for s, n in zip(sent_names, names) if s != n})
            if out is params:
                out = dict(out)
            out[_MANY] = ",".join(sent_names)
    return out, back


def _restore_field(holder, back) -> None:
    """Put the app's spelling back in ``holder["symbol"]`` when it is one this
    request translated."""
    if isinstance(holder, dict):
        name = holder.get("symbol")
        if isinstance(name, str) and name in back:
            holder["symbol"] = back[name]


def inbound(data, back: Dict[str, str]):
    """Schwab's answer with the symbols this request translated spelled the
    app's way again.

    Covers what Schwab's marketdata answers actually name the underlying in: a
    quote answer's top-level keys and each quote's ``symbol``; a chain's or a
    price history's ``symbol`` and ``underlying.symbol``; each entry of
    ``instruments``; and ``errors.invalidSymbols``. Option contract symbols
    (``BRKB  261009C00505000``) carry no slash and are not touched. Never
    raises: a shape it does not know is returned as it came."""
    if not back or not isinstance(data, dict):
        return data
    try:
        out = {back.get(k, k) if isinstance(k, str) else k: v for k, v in data.items()}
        _restore_field(out, back)
        for value in out.values():
            _restore_field(value, back)
        instruments = out.get("instruments")
        if isinstance(instruments, list):
            for item in instruments:
                _restore_field(item, back)
        errors = out.get("errors")
        if isinstance(errors, dict) and isinstance(errors.get("invalidSymbols"), list):
            errors["invalidSymbols"] = [
                back.get(s, s) if isinstance(s, str) else s for s in errors["invalidSymbols"]]
        return out
    except Exception:  # a translation fault must never cost the answer
        return data

"""On-demand per-symbol dossier fetch (Symbol Dossier, Task D3).

For a symbol the options service already collects, everything the Symbol
Dossier page shows is in cache. For a ticker OUTSIDE that universe nothing is,
so the page enqueues a ``dossier`` command and the service runs
:func:`build_dossier`. Design: docs/plans/2026-09-17-symbol-dossier-design.md.

Nothing here derives a number. Four legs, each a thin call into a helper the
rest of the service already trusts:

* **quote** - ``_proxy.schwab_py_client.get_quotes`` (Schwab's RAW nested shape)
  read by ``compute.quote_last``. ⚠ Never the flattened ``_proxy.schwab_client``:
  its dicts default ``"last": 0`` and are always truthy, so a miss would never
  read as one.
* **gex** - ``compute._light_gex_context`` -> ``compute._gex_from_snapshot``,
  which assigns wall SIDES from spot, never from list position.
* **vol** - ``run_iv_analysis`` over ``se.fetch_price_history``, the same pair
  ``rate_trade._score`` uses, so Vol Rank / IV / HV are the scan's definitions.
* **earnings** - ``compute.scan_earnings``: ONE read for ``(status, date)``, the
  status kept three-valued (``upcoming`` / ``none_scheduled`` / ``not_listed``).

Schwab cost of one full fetch: **4 calls, 5 at most** - quote, the GEX chain
(today..+7d), the daily price history, the IV chain (+20..+45d), and
``run_iv_analysis``' own today..+60d fallback when that window came back empty.
A no-quote result spends exactly ONE: a typo must not pay for the other three.

``error`` is ``None`` on success, and otherwise one of two words that must never
be confused, because the page tells the user different things for each:

* ``"no_quote"`` - the quote was ANSWERED and holds no usable price for this
  symbol (omitted from a 200 response, or a zero / NaN / non-numeric last).
  That really does mean "check the symbol".
* ``"fetch_failed"`` - the quote could not be fetched at all: a non-200 status
  or a raised exception (proxy down, timeout, expired token). The ticker may be
  fine; telling the user to check it during an outage is the dead-service /
  quiet-tape confusion ``webgui/pages/copy.py`` exists to prevent.

Both short-circuit the other three legs.

Absence is ``None`` at every key, never ``0``. One leg failing blanks only its
own keys and speaks through ``_degrade``; the success and the degraded payload
are both built from :data:`DOSSIER_KEYS`, so a reader can never ``KeyError``.
"""
import datetime as _dt
import math

from services import _degrade
from services.options_svc import compute

#: The payload's complete key set. BOTH shapes are built from it.
DOSSIER_KEYS = ("symbol", "error", "fetched_at", "spot", "day_pct",
                "flip", "put_wall", "call_wall",
                "iv_rank", "current_iv", "hv_current",
                "earnings_status", "earnings_date")

#: Which keys each leg owns - a leg can only ever write these.
_QUOTE_KEYS = ("spot", "day_pct")
_GEX_KEYS = ("flip", "put_wall", "call_wall")
_VOL_KEYS = ("iv_rank", "current_iv", "hv_current")
_EARNINGS_KEYS = ("earnings_status", "earnings_date")

NO_QUOTE = "no_quote"
FETCH_FAILED = "fetch_failed"


class QuoteFetchFailed(Exception):
    """The quote request itself failed - an outage, not an unknown symbol."""


def _usable_price(v):
    """A real, positive, finite price - or None. ``bool`` is not a price."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    v = float(v)
    return v if math.isfinite(v) and v > 0 else None


def _quote(symbol):
    """``{"spot", "day_pct"}``, or None when there is no usable last price.

    ``day_pct`` is None: no helper in this service reads a day change off a raw
    quote (``apply_live_spots`` measures from the SESSION OPEN, which an
    uncollected symbol has none of), and writing a second quote parser here is
    how the ``get_quotes`` envelope bug shipped green once already.

    Raises :class:`QuoteFetchFailed` on any non-200. The status code is what
    separates an outage from an unknown ticker here, because of how the two
    layers below answer:

    * ``SchwabPyProxyClient._get`` NEVER raises - a connection error or timeout
      to the proxy comes back as a synthetic **502**; and the proxy's own
      ``/quotes`` passes Schwab's status through (401 token, 5xx, **504** on a
      Schwab timeout). So a non-200 is, in practice, the outage signal.
    * Schwab answers an unknown symbol with **200** and the symbol simply
      absent from the body (it lists it under ``errors.invalidSymbols``), which
      ``quote_last`` reads as None -> ``no_quote``.

    The one ambiguous code is **400** (a malformed request). D4's handler
    validates the symbol against ``SYMBOL_RE`` before this runs, so a 400 is a
    request we built wrong, not a ticker the user typed wrong - it stays under
    ``fetch_failed``, the less misleading word: "could not fetch" is true of a
    bad symbol too, while "check the symbol" is false during an outage.
    """
    resp = compute._proxy.schwab_py_client.get_quotes([symbol])
    status = getattr(resp, "status_code", None)
    if status != 200:
        raise QuoteFetchFailed(f"quotes {symbol}: HTTP {status}")
    raw = resp.json()
    spot = _usable_price(compute.quote_last(raw, symbol))
    if spot is None:
        return None
    return {"spot": spot, "day_pct": None}


def _gex(symbol):
    """``{"flip", "put_wall", "call_wall"}`` or None. Walls by side of spot."""
    return compute._gex_from_snapshot(compute._light_gex_context(symbol))


def _vol(symbol, spot):
    """``{"iv_rank", "current_iv", "hv_current"}`` on the scan's definitions.

    ``run_iv_analysis`` fetches its own +20..+45 DTE chain. ``.get`` throughout:
    its ``hv_current`` is set only when there is both an ATM IV and an HV series.
    """
    client = compute._proxy.schwab_py_client
    hist = compute.se.fetch_price_history(client, symbol)
    iv = compute.run_iv_analysis(client, symbol, price=spot, hist=hist) or {}
    return {k: iv.get(k) for k in _VOL_KEYS}


def _earnings(symbol):
    """``{"earnings_status", "earnings_date"}`` from ONE read, status unchanged."""
    status, date = compute.scan_earnings(symbol)
    return {"earnings_status": status, "earnings_date": date}


def _stamp():
    # Naive CT: a tz-naive datetime in this project means Central.
    return _dt.datetime.now(compute._PROJ_CT_TZ).replace(
        tzinfo=None).isoformat(timespec="seconds")


def _merge(out, part, keys):
    """Copy ONLY ``keys`` out of a leg's result; anything else it returned is
    dropped, so a leg cannot widen the payload or write a sibling's key."""
    if isinstance(part, dict):
        for k in keys:
            out[k] = part.get(k)


def build_dossier(symbol):
    """The dossier payload for ``symbol``. Never raises; always ``DOSSIER_KEYS``."""
    out = dict.fromkeys(DOSSIER_KEYS)
    out["symbol"] = symbol
    out["fetched_at"] = _stamp()

    try:
        quote = _quote(symbol)
    except Exception:  # noqa: BLE001 - an outage, NOT an unknown symbol
        _degrade.degraded("options.dossier_quote", detail=symbol)
        out["error"] = FETCH_FAILED
        return out
    if not quote or quote.get("spot") is None:
        out["error"] = NO_QUOTE
        return out
    _merge(out, quote, _QUOTE_KEYS)

    for name, leg, keys, args in (
            ("gex", _gex, _GEX_KEYS, (symbol,)),
            ("vol", _vol, _VOL_KEYS, (symbol, out["spot"])),
            ("earnings", _earnings, _EARNINGS_KEYS, (symbol,))):
        try:
            _merge(out, leg(*args), keys)
        except Exception:  # noqa: BLE001 - one leg degrades alone
            _degrade.degraded(f"options.dossier_{name}", detail=symbol)
    return out

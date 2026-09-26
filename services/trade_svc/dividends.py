"""The daily watchlist dividend pull: proxy ``/quotes`` → ``shared/dividends.py``.

The WRITE half of the dividends calendar (news v2, owner decision 8). news_svc
reads the store and never calls the proxy; this module is the one caller. Each
followed symbol (``shared.news_config.ticker_set()`` less ``$`` indices) costs
ONE proxy call — the proxy's ``/passthrough`` splits ``params`` on commas, so a
multi-symbol request would be mangled.

⚠ **Schwab's field names are UNVERIFIED on prod.** The quote ``fundamental``
block is documented as ``divExDate`` / ``divPayDate`` / ``divPayAmount`` /
``divAmount`` / ``divFreq`` / ``nextDivExDate`` / ``nextDivPayDate`` /
``declarationDate``; the instruments endpoint spells them ``dividendDate`` /
``dividendPayDate`` / ``dividendPayAmount`` / ``dividendAmount`` /
``dividendFreq`` / ``nextDividendDate`` / ``nextDividendPayDate``. Every name
lives in :data:`_FIELDS` and nowhere else, so a live reply that differs is a
one-table edit. A missing or odd field is tolerated: an amount that is absent,
NaN, infinite or a bool is stored **None, never 0** (a zero would read as "pays
nothing").

Per symbol the fetch lands in one of three coverage states (the store's
:func:`shared.dividends.coverage`): ``ok`` (a dividend date was read), ``none``
(a readable block with no dividend — a non-payer) and ``error`` (we could not
tell). ``ok`` and ``none`` REPLACE the symbol's forward rows, so a revised or
cancelled dividend leaves no stale row; ``error`` keeps what it had.
"""
import datetime as dt
import logging
import math
from zoneinfo import ZoneInfo

from services import _degrade
from shared import dividends as store

log = logging.getLogger(__name__)

_CT = ZoneInfo("America/Chicago")
_DEFAULT_LOOKBACK_DAYS = 3

# ── the ONE field mapping (see the module docstring) ────────────────────────
# Each entry is (quote spelling, instruments spelling); the first present wins.
_FIELDS = {
    "ex_date": ("divExDate", "dividendDate"),
    "pay_date": ("divPayDate", "dividendPayDate"),
    "pay_amount": ("divPayAmount", "dividendPayAmount"),   # per payment
    "annual_amount": ("divAmount", "dividendAmount"),      # per year
    "frequency": ("divFreq", "dividendFreq"),              # payments a year
    "next_ex_date": ("nextDivExDate", "nextDividendDate"),
    "next_pay_date": ("nextDivPayDate", "nextDividendPayDate"),
    "declared_date": ("declarationDate", "dividendDeclarationDate"),
}


def _field(fund, key):
    for name in _FIELDS[key]:
        value = fund.get(name)
        if value not in (None, ""):
            return value
    return None


def _number(value):
    """A finite float or None (bool, NaN, inf, junk → None)."""
    if value is None or isinstance(value, bool):
        return None
    try:
        v = float(str(value).replace(",", "").replace("$", "").strip())
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _date(value):
    """``YYYY-MM-DD`` from a date, an ISO timestamp, ``YYYY-MM-DD HH:MM:SS.f`` or
    epoch milliseconds; None for anything else."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if not math.isfinite(value) or value <= 0:
            return None
        try:
            stamp = dt.datetime.fromtimestamp(value / 1000.0, tz=dt.timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
        return stamp.date().isoformat()
    text = str(value).strip()
    if len(text) < 10 or (len(text) > 10 and text[10] not in "T "):
        return None
    try:
        return dt.date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        return None


def _frequency(fund):
    f = _number(_field(fund, "frequency"))
    if f is None or f <= 0 or f != int(f):
        return None
    return int(f)


def _amount(fund):
    """Per-payment amount: ``divPayAmount`` if usable, else annual ÷ frequency.

    None when neither gives a finite positive number — never 0."""
    pay = _number(_field(fund, "pay_amount"))
    if pay is not None and pay > 0:
        return round(pay, 6)
    annual = _number(_field(fund, "annual_amount"))
    freq = _frequency(fund)
    if annual is None or annual <= 0 or freq is None:
        return None
    return round(annual / freq, 6)


def _is_non_payer(fund):
    """At least one amount field is present and every present one reads zero.

    A block with NO amount field at all is not evidence of a non-payer: it may
    just mean the live field names differ from the documented ones, and reading
    that as "pays nothing" would fail open on every symbol."""
    amounts = [_number(_field(fund, k)) for k in ("pay_amount", "annual_amount")]
    present = [a for a in amounts if a is not None]
    return bool(present) and all(a == 0.0 for a in present)


def parse_fundamental(symbol, fund):
    """``(rows, status)`` from one quote's ``fundamental`` block. Never raises.

    ``status`` is ``ok`` (rows hold at least one ex date), ``none`` (the block
    says the symbol pays nothing — zero amounts, a stale date ignored) or
    ``error`` (not a block, or no date and no zero amount to show it pays
    nothing). The ``next*`` date, when Schwab gives one after the current, is a
    second row with amount None: it is a projection, and its amount unknown."""
    try:
        if not isinstance(fund, dict):
            return [], "error"
        if _is_non_payer(fund):
            return [], "none"
        ex = _date(_field(fund, "ex_date"))
        next_ex = _date(_field(fund, "next_ex_date"))
        if not ex and not next_ex:
            return [], "error"
        amount, freq = _amount(fund), _frequency(fund)
        rows = []
        if ex:
            rows.append({"symbol": symbol, "ex_date": ex,
                         "pay_date": _date(_field(fund, "pay_date")),
                         "amount": amount, "frequency": freq,
                         "declared_date": _date(_field(fund, "declared_date"))})
        if next_ex and (ex is None or next_ex > ex):
            rows.append({"symbol": symbol, "ex_date": next_ex,
                         "pay_date": _date(_field(fund, "next_pay_date")),
                         "amount": None if ex else amount, "frequency": freq,
                         "declared_date": None})
        return rows, "ok"
    except Exception:  # a parser must never take the pull down
        log.warning("dividends: unparseable fundamental block for %s", symbol,
                    exc_info=True)
        return [], "error"


def _fundamental_of(reply, symbol):
    """The ``fundamental`` block for ``symbol`` out of a raw /quotes reply."""
    if not isinstance(reply, dict):
        return None
    entry = reply.get(symbol)
    if not isinstance(entry, dict):
        return None
    return entry.get("fundamental")


def _today_ct():
    return dt.datetime.now(_CT).date().isoformat()


def _default_symbols():
    from shared import news_config
    return news_config.ticker_set()


def _default_fetch():
    from services import _proxy
    return _proxy.schwab_client.get_quote_raw


def _lookback_days():
    try:
        from shared import news_config
        days = int(news_config.calendar_config()["dividends"]["lookback_days"])
        return days if days >= 0 else _DEFAULT_LOOKBACK_DAYS
    except Exception:
        return _DEFAULT_LOOKBACK_DAYS


def refresh(fetch=None, symbols=None, db_path=None, today=None):
    """Pull every followed symbol's dividend dates once a day.

    Returns how many symbols were FETCHED (one proxy call each): 0 when today's
    run is already recorded, so a restart does not refetch. ``$`` indices are
    skipped. A symbol whose fetch fails, or whose reply is unreadable, is an
    ``error`` coverage row and one ``trade.dividends`` degrade — never an abort. ``last_run_day`` is
    recorded only after the loop, so a crash mid-loop retries."""
    today = _date(today) or _today_ct()
    fetch = fetch or _default_fetch()
    wanted = symbols if symbols is not None else _default_symbols()
    syms, seen = [], set()
    for raw in wanted:
        sym = (raw or "").strip().upper()
        if sym and not sym.startswith("$") and sym not in seen:
            seen.add(sym)
            syms.append(sym)

    conn = store.init_db(db_path)
    try:
        if store.last_run_day(conn) == today:
            return 0
        statuses, fetched = {}, 0
        for sym in syms:
            fetched += 1
            try:
                rows, status = parse_fundamental(sym, _fundamental_of(fetch(sym), sym))
            except Exception:
                _degrade.degraded("trade.dividends", detail=sym)
                statuses[sym] = "error"
                continue
            if status == "error":   # no reply, no block, or an unreadable one
                _degrade.degraded("trade.dividends", detail=f"{sym}: unreadable reply",
                                  exc_info=False)
            if status in ("ok", "none"):
                try:
                    store.replace_symbol(conn, sym, rows, today)
                except Exception:
                    _degrade.degraded("trade.dividends", detail=sym)
                    status = "error"
            statuses[sym] = status
        store.set_coverage(conn, statuses, day=today)
        cutoff = (dt.date.fromisoformat(today)
                  - dt.timedelta(days=_lookback_days())).isoformat()
        store.prune(conn, cutoff)
        store.set_last_run_day(conn, today)
        n_ok = sum(1 for s in statuses.values() if s == "ok")
        log.info("dividends: %d symbols fetched (%d ok, %d none, %d error)",
                 fetched, n_ok, sum(1 for s in statuses.values() if s == "none"),
                 sum(1 for s in statuses.values() if s == "error"))
        return fetched
    finally:
        store.close_db(conn)

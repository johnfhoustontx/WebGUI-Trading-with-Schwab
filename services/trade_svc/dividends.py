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
cancelled dividend leaves no stale row; ``error`` keeps what it had. The exact
past/forward rules are on :func:`parse_fundamental`.
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


# A parsed date outside this range is a unit or data error (an epoch read in
# the wrong unit lands in 1970 or the far future), never a dividend.
_MIN_DATE = "2000-01-01"
_MAX_DATE = "2100-12-31"
# Below this an epoch number is SECONDS, at or above it MILLISECONDS: 1e11 s
# is the year 5138, 1e11 ms is 1973 - no plausible date sits on either side
# of the wrong reading.
_EPOCH_MS_FROM = 1e11


def _in_range(day):
    return day if day and _MIN_DATE <= day <= _MAX_DATE else None


def _date(value):
    """``YYYY-MM-DD`` or None, and never a date before 2000 or after 2100.

    A string goes through the store's STRICT parser
    (:func:`shared.dividends.iso_date`: ``YYYY-MM-DD`` then ``T``, a space or
    the end - ``"2026-W40-1"`` is None). A number is an epoch: seconds below
    1e11, milliseconds from there up (UTC date)."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if not math.isfinite(value) or value <= 0:
            return None
        seconds = value / 1000.0 if value >= _EPOCH_MS_FROM else float(value)
        try:
            stamp = dt.datetime.fromtimestamp(seconds, tz=dt.timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
        return _in_range(stamp.date().isoformat())
    return _in_range(store.iso_date(value))


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


def parse_fundamental(symbol, fund, today=None):
    """``(rows, status)`` from one quote's ``fundamental`` block. Never raises.

    ``today`` (``YYYY-MM-DD``, a date, or None for today in CT) splits the ex
    dates into past and FORWARD (``>= today``); only forward rows reach the
    calendar (``store.replace_symbol`` ignores the rest).

    * The current ex date (``divExDate``) carries the per-payment amount. The
      ``next*`` date, when after it, is a second row: with amount None while
      the current one is still ahead (a projection - its amount unknown), but
      carrying the amount once the current one has PASSED, since it is then the
      next payment of that same known dividend.
    * ``ok``: at least one forward row. A future ex date whose amounts read
      zero is still ``ok``, with amount None - a dated dividend is evidence of
      a payment, a zero amount is not evidence against it.
    * No forward row, but a known (positive) amount and a past date: ``ok``
      with only past rows. That is a payer whose next date Schwab has not
      published yet; ``ok`` clears its forward rows (the source names none)
      and coverage then means "checked, nothing dated ahead", not that forward
      data exists.
    * No forward row and no known amount (zero or absent): ``none`` with no
      rows - a non-payer, or a stale date with nothing behind it.
    * ``error``: not a block, or no date at all and no zero amount to show it
      pays nothing."""
    try:
        if not isinstance(fund, dict):
            return [], "error"
        today = store.iso_date(today) if today is not None else _today_ct()
        if not today:
            return [], "error"
        zero = _is_non_payer(fund)
        ex = _date(_field(fund, "ex_date"))
        next_ex = _date(_field(fund, "next_ex_date"))
        if not ex and not next_ex:
            return [], "none" if zero else "error"
        amount = None if zero else _amount(fund)
        freq = _frequency(fund)
        rows = []
        if ex:
            rows.append({"symbol": symbol, "ex_date": ex,
                         "pay_date": _date(_field(fund, "pay_date")),
                         "amount": amount, "frequency": freq,
                         "declared_date": _date(_field(fund, "declared_date"))})
        if next_ex and (ex is None or next_ex > ex):
            carries = ex is None or ex < today
            rows.append({"symbol": symbol, "ex_date": next_ex,
                         "pay_date": _date(_field(fund, "next_pay_date")),
                         "amount": amount if carries else None, "frequency": freq,
                         "declared_date": None})
        if any(r["ex_date"] >= today for r in rows):
            return rows, "ok"
        if amount is not None:
            return rows, "ok"
        return [], "none"
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
    """``[calendar.dividends] lookback_days`` via
    :func:`shared.news_config.dividends_config` (validated there: an int >= 0).

    ⚠ Not ``calendar_config()["dividends"]`` - that accessor returns the
    ``[calendar]`` scalars only, so the lookup always fell back to 3."""
    try:
        from shared import news_config
        days = news_config.dividends_config()["lookback_days"]
    except Exception:
        _degrade.degraded("trade.dividends", detail="lookback_days config")
        return _DEFAULT_LOOKBACK_DAYS
    if isinstance(days, bool) or not isinstance(days, int) or days < 0:
        return _DEFAULT_LOOKBACK_DAYS
    return days


def _clean_symbols(wanted):
    """Upper-cased, de-duplicated, ``$`` indices and non-strings dropped."""
    syms, seen = [], set()
    for raw in wanted:
        if not isinstance(raw, str):
            continue
        sym = raw.strip().upper()
        if sym and not sym.startswith("$") and sym not in seen:
            seen.add(sym)
            syms.append(sym)
    return syms


def refresh(fetch=None, symbols=None, db_path=None, today=None, force=False):
    """Pull every followed symbol's dividend dates once a day.

    Returns how many symbols were FETCHED (one proxy call each): 0 when today's
    run is already recorded, so a restart does not refetch - unless ``force``
    (a manual refresh command), which runs regardless. ``$`` indices and
    non-string entries are skipped. A symbol whose fetch fails, or whose reply
    is unreadable, is an ``error`` coverage row and one ``trade.dividends``
    degrade - never an abort.

    ``today`` (CT day) defaults to now; one GIVEN but unusable raises
    ValueError before anything is fetched or written - a caller bug, not a day.

    ``last_run_day`` is recorded once the per-symbol loop finishes, and in a
    ``finally`` around the post-loop steps (coverage, prune): a DB-lock error
    there still propagates, but cannot make every scheduler tick refetch the
    whole watchlist. A crash INSIDE the loop records nothing, so it retries.
    ⚠ A proxy outage is NOT a loop crash - every symbol lands as ``error`` and
    the day IS recorded, so there is no same-day retry: the budget is one call
    per symbol per day, and a manual ``force`` refresh is the way to re-pull."""
    if today is None:
        today = _today_ct()
    else:
        day = store.iso_date(today)
        if not day:
            raise ValueError(f"unusable today: {today!r} (want YYYY-MM-DD or a date)")
        today = day
    fetch = fetch or _default_fetch()
    wanted = symbols if symbols is not None else _default_symbols()
    syms = _clean_symbols(wanted)

    conn = store.init_db(db_path)
    try:
        if not force and store.last_run_day(conn) == today:
            return 0
        statuses, fetched = {}, 0
        for sym in syms:
            fetched += 1
            try:
                rows, status = parse_fundamental(
                    sym, _fundamental_of(fetch(sym), sym), today=today)
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
        try:
            store.set_coverage(conn, statuses, day=today)
            cutoff = (dt.date.fromisoformat(today)
                      - dt.timedelta(days=_lookback_days())).isoformat()
            store.prune(conn, cutoff)
        finally:
            store.set_last_run_day(conn, today)
        n_ok = sum(1 for s in statuses.values() if s == "ok")
        log.info("dividends: %d symbols fetched (%d ok, %d none, %d error)",
                 fetched, n_ok, sum(1 for s in statuses.values() if s == "none"),
                 sum(1 for s in statuses.values() if s == "error"))
        return fetched
    finally:
        store.close_db(conn)

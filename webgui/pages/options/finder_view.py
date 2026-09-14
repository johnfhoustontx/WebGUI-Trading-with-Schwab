"""View model for the redesigned Strategy Finder (``/options/swing``) - PURE.

The page shows a summary strip, instant-filter strategy chips, up to four top-pick
cards and a slim ranked list. Everything those widgets need to *say* is computed
here from the ``cache:options:swing`` payload, so it is unit-tested without a
browser (``webgui/tests/test_finder_view.py``); ``swing.py`` only builds widgets
and wires them to these functions.

Deliberately imports no widget code - not even ``strategy_table``, whose import of
``scanner`` drags the UI library in. The few facts shared with that module (the
unbounded-side predicates) are restated here with a pointer back.

Design: ``docs/plans/2026-09-13-strategy-finder-redesign-design.md``.
"""
import datetime as _dt
import math as _math

from pages import fmt as _fmt    # the ONE numeric vocabulary (pages/fmt.py)

from .theme import THEME as _THEME   # config only - theme.py imports no widget code

NO_READING = _fmt.NO_READING
# What the summary strip says when no price was read - words, never $0.00.
PRICE_UNAVAILABLE = "Price unavailable"

# The seven build groups ``swing_scan`` stamps on each candidate as ``group``, in
# the order the chips render.
GROUPS = [
    ("DIRECTIONAL", "Directional"),
    ("VERTICAL", "Spreads"),
    ("NEUTRAL", "Neutral"),
    ("STRADDLE", "Straddles & strangles"),
    ("BUTTERFLY", "Butterflies & condors"),
    ("CALENDAR", "Calendars"),
    ("STOCK", "Stock + options"),
]
_GROUP_LABEL = dict(GROUPS)

# Month abbreviations spelled out rather than ``strftime("%b")``, which follows
# the process locale.
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _half_up(x):
    """Round to the nearest whole number, halves UP.

    Python's ``round`` is banker's rounding (``round(42.5) == 42``), which would
    make the page round one half down and the next up. Every whole number this
    page shows - a percent, Vol Rank, a bar's 5% step - goes through here.
    """
    return int(_math.floor(x + 0.5))


# --------------------------------------------------------------------------- text

def money(v):
    """Dollars for a card or cell: ``$54,058`` at |v| >= 100, ``$4.50`` below.

    Whole dollars at scale because the page puts a $54,000 collar beside a $196
    butterfly, and cents on the first only add noise. A negative keeps a leading
    minus (``-$300``). No reading (None, NaN, a bool) is the em-dash.
    """
    f = _fmt.num(v)
    if f is None:
        return NO_READING
    a = abs(f)
    # Compare the ROUNDED cents, so 99.996 reads "$100", never "$100.00".
    body = f"{a:,.0f}" if round(a, 2) >= 100 else f"{a:,.2f}"
    # The sign is decided AFTER rounding: -0.001 is "$0.00", never "-$0.00".
    sign = "-" if f < 0 and body.strip("0.,") else ""
    return f"{sign}${body}"


def _share_count(legs):
    """Shares held across the stock legs (``qty`` counts 100-share LOTS; a missing
    or malformed qty reads as one lot, as ``strategy_table.legs_summary`` does)."""
    total = 0
    for leg in legs or []:
        if (leg or {}).get("kind") != "stock":
            continue
        n = _fmt.num(leg.get("qty"))
        total += 100 * (int(n) if n is not None and n >= 1 else 1)
    return total


def cost_text(sig):
    """``"$195 debit"`` / ``"$804 credit"`` / ``"$54,058 debit for 100 shares"``.

    The word carries the direction, so the amount is shown unsigned.
    """
    s = sig or {}
    debit = _fmt.num(s.get("net_debit"))
    credit = _fmt.num(s.get("net_credit"))
    if debit is not None:
        text = f"{money(abs(debit))} debit"
    elif credit is not None:
        text = f"{money(abs(credit))} credit"
    else:
        return NO_READING
    shares = _share_count(s.get("legs"))
    if shares:
        text += f" for {shares} shares"
    return text


def expiry_text(sig):
    """``"Oct 16 · 8d"`` - the front expiry and its days to expiry."""
    s = sig or {}
    try:
        d = _dt.date.fromisoformat(str(s.get("expiration"))[:10])
    except (TypeError, ValueError):
        return NO_READING
    text = f"{_MONTHS[d.month - 1]} {d.day}"
    dte = _fmt.num(s.get("dte"))
    if dte is not None:
        text += f" · {int(dte)}d"
    return text


def earnings_text(sig, today=None):
    """``"Earnings Nov 19"`` for a candidate the service stamped as holding through
    an earnings report, else None.

    Keyed on ``spans_earnings`` alone - ``earnings_status`` rides on EVERY row (it
    names the calendar coverage the scan got, not a conflict). The year is added
    only when the report falls in another calendar year than ``today``
    (``"Earnings Jan 22, 2027"``). A stamp with no readable date is no tag: a
    warning that cannot say when is not one.
    """
    s = sig or {}
    if not s.get("spans_earnings"):
        return None
    raw = s.get("earnings_date")
    if not isinstance(raw, str):
        return None
    try:
        d = _dt.date.fromisoformat(raw[:10])
    except ValueError:
        return None
    text = f"Earnings {_MONTHS[d.month - 1]} {d.day}"
    if d.year != (today or _dt.date.today()).year:
        text += f", {d.year}"
    return text


# ------------------------------------------------------------------------ presets

# (label, DTE min, DTE max). A max of None is no upper limit: the scan command
# carries ``dte_max: null`` and the service fetches every listed expiry.
EXPIRY_PRESETS = [
    ("1–2 wk", 7, 14),
    ("2–6 wk", 14, 42),
    ("1–3 mo", 30, 90),
    ("3–12 mo", 90, 365),
    ("1 yr+", 365, None),
    ("All", 0, None),
]


def expiry_range_for(label):
    """``(DTE min, DTE max)`` for a preset label; None for anything else (a cleared
    toggle, a hand-edited range)."""
    for p_label, p_lo, p_hi in EXPIRY_PRESETS:
        if label == p_label:
            return p_lo, p_hi
    return None


def expiry_preset_for(lo, hi):
    """The preset label a DTE range matches, or None when it is hand-edited.

    Only a real ``None`` upper bound matches a no-limit preset: junk in the field
    (NaN, text) is not "no limit" and matches nothing.
    """
    a = _fmt.num(lo)
    b = None if hi is None else _fmt.num(hi)
    if a is None or (hi is not None and b is None):
        return None
    for label, p_lo, p_hi in EXPIRY_PRESETS:
        if a == p_lo and b == p_hi:
            return label
    return None


# Short-leg |delta| bands, applied to both sides. Balanced is TODAY's default scan
# (put -0.20..-0.10, call 0.10..0.20) - the design's first draft would have
# relabelled it Custom and silently moved the default.
RISK_STYLES = {
    "Conservative": (0.05, 0.10),
    "Balanced": (0.10, 0.20),
    "Aggressive": (0.20, 0.30),
}
RISK_DEFAULT = "Balanced"
RISK_CUSTOM = "Custom"
_BAND_TOL = 1e-9


def risk_bands(name):
    """The four scan keyword values for a risk style (put side negative)."""
    lo, hi = RISK_STYLES[name]
    return {"put_d_min": -hi, "put_d_max": -lo, "call_d_min": lo, "call_d_max": hi}


def risk_style_for(put_d_min, put_d_max, call_d_min, call_d_max):
    """The style whose bands these four values are, else ``"Custom"``."""
    got = [_fmt.num(v) for v in (put_d_min, put_d_max, call_d_min, call_d_max)]
    if any(v is None for v in got):
        return RISK_CUSTOM
    for name in RISK_STYLES:
        want = risk_bands(name)
        exp = [want["put_d_min"], want["put_d_max"], want["call_d_min"], want["call_d_max"]]
        if all(abs(g - e) <= _BAND_TOL for g, e in zip(got, exp)):
            return name
    return RISK_CUSTOM


def bands_for_choice(choice):
    """The bands a risk-style TOGGLE value selects, or None.

    The page's handler goes through this rather than :func:`risk_bands`, which
    raises on ``"Custom"``: Custom is a read-only state (the fields match no
    style), never a choice that writes four fields.
    """
    return risk_bands(choice) if choice in RISK_STYLES else None


DEFAULT_DTE = expiry_range_for("All")
DEFAULT_MIN_CREDIT_PCT = 10.0
BAND_KEYS = ("put_d_min", "put_d_max", "call_d_min", "call_d_max")


def scan_controls_from(payload):
    """The scan bar's starting values: the params the cached result was scanned
    with (the handler echoes the command args as ``params``), so the bar never
    describes a different scan than the ideas under it.

    Three groups, each falling back to the page's own default AS A GROUP: a DTE
    pair must be two whole days, ``0 <= min <= max`` and ``max >= 1`` - or a whole
    ``min >= 0`` with ``dte_max`` explicitly None, which is no upper limit (a
    MISSING ``dte_max`` is not, and falls back like any bad pair); the four
    delta bands must all be real numbers; the credit floor must be a
    non-negative fraction. A missing key falls back to the PAGE default, not the
    service's - every scan the page sends carries every key, so only a scan
    enqueued from outside the page can lack one.

    Returns ``dte_min``, ``dte_max``, the four band keys and ``min_credit_pct``
    (percent, as the field shows it).
    """
    params = (payload or {}).get("params")
    params = params if isinstance(params, dict) else {}
    out = {}

    lo, hi = _fmt.num(params.get("dte_min")), _fmt.num(params.get("dte_max"))
    lo_ok = lo is not None and lo == int(lo) and lo >= 0
    if lo_ok and "dte_max" in params and params["dte_max"] is None:
        out["dte_min"], out["dte_max"] = int(lo), None
    elif lo_ok and hi is not None and hi == int(hi) and lo <= hi and hi >= 1:
        out["dte_min"], out["dte_max"] = int(lo), int(hi)
    else:
        out["dte_min"], out["dte_max"] = DEFAULT_DTE

    bands = {k: _fmt.num(params.get(k)) for k in BAND_KEYS}
    out.update(bands if all(v is not None for v in bands.values())
               else risk_bands(RISK_DEFAULT))

    frac = _fmt.num(params.get("min_cr_fraction"))
    out["min_credit_pct"] = (round(frac * 100.0, 6) if frac is not None and frac >= 0
                             else DEFAULT_MIN_CREDIT_PCT)
    return out


def risk_toggle_value(put_d_min, put_d_max, call_d_min, call_d_max):
    """The toggle's value for four band fields: a style name, or None for Custom.

    The toggle offers only the three styles, so a hand-edited band shows no
    selection (plus the page's read-only Custom marker).
    """
    style = risk_style_for(put_d_min, put_d_max, call_d_min, call_d_max)
    return style if style in RISK_STYLES else None


# --------------------------------------------------------------- chips and picks

ALL_CHIP = "ALL"


def toggle_chip(active, code):
    """The active chip set after clicking ``code``. ``None`` means All.

    All resets; a chip toggles its membership; un-choosing the last chip goes
    back to All rather than to an empty page. Never mutates ``active``.
    """
    if code == ALL_CHIP:
        return None
    if active is None:
        return {code}
    out = set(active)
    if code in out:
        out.discard(code)
    else:
        out.add(code)
    return out or None


def chip_is_active(active, code):
    """Whether chip ``code`` renders highlighted for the active set."""
    if code == ALL_CHIP:
        return active is None
    return active is not None and code in active


def carry_chips(active, prev_symbol, symbol, signals):
    """Chip state across a repaint: kept for the same symbol, All for a new one.

    Groups the new payload did not produce are dropped, and if nothing chosen
    survives the page goes back to All.
    """
    if active is None or prev_symbol != symbol:
        return None
    present = {code for code, _label, _n in chip_counts(signals)}
    kept = set(active) & present
    return kept or None

def chip_counts(signals):
    """``[(code, label, n), ...]`` in :data:`GROUPS` order, only groups with rows."""
    counts = {}
    for s in signals or []:
        g = (s or {}).get("group")
        if g in _GROUP_LABEL:
            counts[g] = counts.get(g, 0) + 1
    return [(code, label, counts[code]) for code, label in GROUPS if counts.get(code)]


def filter_groups(signals, active):
    """The signals whose group is in ``active``; ``None`` means every group.

    An EMPTY set is a real selection (nothing chosen) and yields nothing.
    """
    rows = list(signals or [])
    if active is None:
        return rows
    return [s for s in rows if (s or {}).get("group") in active]


def _rank_key(sig):
    score = _fmt.num(sig.get("composite_score"))
    # Unscored rows last; ties by id so the cards do not reshuffle between paints.
    return (score is None, -(score or 0.0), str(sig.get("id") or ""))


def ranked(signals):
    """Signals best score first (unscored last, ties by id)."""
    return sorted((s for s in signals or [] if s), key=_rank_key)


def top_picks(signals, k=4):
    """Up to ``k`` cards: the best of each DIFFERENT group first, then the rest.

    Four spreads in a row would be one idea shown four times, so each group's
    best idea takes a card before any group gets a second. When the visible rows
    span fewer than ``k`` groups - a single chip clicked, say - the remaining
    slots FILL with the next-best ideas in score order, so a one-group filter
    still shows up to ``k`` cards rather than one.
    """
    if k <= 0:
        return []
    order = ranked(signals)
    picks, taken, rest = [], set(), []
    for s in order:
        g = s.get("group")
        if g in taken:
            rest.append(s)
            continue
        taken.add(g)
        picks.append(s)
        if len(picks) >= k:
            return picks
    return picks + rest[:k - len(picks)]


# ------------------------------------------------------------------ summary strip

def _conviction_word(c):
    if c < 0.34:
        return "low"
    if c < 0.67:
        return "medium"
    return "high"


def payload_answers_scan(scan, payload):
    """Whether a newly published payload is the answer to the scan in progress.

    ``cache:options:swing`` is ONE slot: scan AAPL then quickly MSFT and AAPL's
    result still lands, as does any other tab's scan - and so does SPY 1-2 wk
    while SPY 1-3 mo is waiting. ``scan`` is the request being waited on (the
    ``swing_scan`` args) or None when nothing waits, in which case anything
    paints. The symbol must match (a blank request names none, so any answer is
    its answer), and every request field the payload echoes back in ``params``
    (the handler stores the args there) must match too, numbers as numbers. A
    payload that echoes no params can only be matched on its symbol.
    """
    if scan is None:
        return True
    want = str(scan.get("symbol") or "").strip().upper()
    if not want:
        return True
    p = payload or {}
    got = str(p.get("symbol") or "").strip().upper()
    if got != want:
        return False
    echoed = p.get("params")
    if not isinstance(echoed, dict):
        return True
    for key, value in scan.items():
        if key == "symbol" or key not in echoed:
            continue
        a, b = _fmt.num(value), _fmt.num(echoed[key])
        if a is None or b is None:
            if value != echoed[key]:
                return False
        elif abs(a - b) > _BAND_TOL:
            return False
    return True


def _spot(payload):
    """The answer's price: the payload's ``spot``, else the first row's
    ``underlying_price`` (a payload written before ``spot`` existed), else None."""
    p = payload or {}
    spot = _fmt.num(p.get("spot"))
    if spot is None:
        first = next((s for s in (p.get("signals") or []) if s), {})
        spot = _fmt.num(first.get("underlying_price"))
    return spot


def _price_text(spot):
    """A read price as ``$764.48``; :data:`PRICE_UNAVAILABLE` for no reading."""
    f = _fmt.num(spot)
    return PRICE_UNAVAILABLE if f is None else f"${f:,.2f}"


# ----------------------------------------------------------- large-chain chooser

# The four ways to limit a large chain, in the service's order
# (``options_svc.compute.EXPIRY_CHOICES``, which Tier 1 cannot import). The
# payload carries its own labels; this map is the fallback when one is unusable.
_CHOICE_LABELS = {
    "next_30": "Next 30 days",
    "next_90": "Next 90 days",
    "monthly": "Monthlies only",
    "all": "Everything",
}


def choice_label(key):
    """The label for one of the four expiry choices, or None for anything else."""
    return _CHOICE_LABELS.get(key) if isinstance(key, str) else None


def _whole_count(v):
    """A count as an int when it was READ as a whole number >= 0, else None - so
    ``True``, NaN, ``"lots"`` and ``2.5`` are all "not read", never a number. A
    numeric string such as ``"56"`` IS read, through ``_fmt.num``, exactly as the
    module's other counts are."""
    f = _fmt.num(v)
    return int(f) if f is not None and f >= 0 and f == int(f) else None


def _expirations(n):
    return f"{n:,} expiration{'' if n == 1 else 's'}"


def _symbol_of(payload):
    return str((payload or {}).get("symbol") or "").strip().upper()


def _label_for(payload_choices, key):
    """``key``'s label from the payload's own choices, else the fixed map."""
    for entry in payload_choices if isinstance(payload_choices, list) else []:
        if isinstance(entry, dict) and entry.get("key") == key:
            label = entry.get("label")
            if isinstance(label, str) and label.strip():
                return label.strip()
            break
    return choice_label(key)


def chooser_facts(payload):
    """The chooser card for an answer that ASKED instead of scanning, else None.

    A scan over more than 30 expirations answers with ``needs_choice`` and four
    ``choices`` before fetching any chain. ``{"title", "prompt", "buttons"}``,
    each button ``{"key", "text", "enabled"}`` in the payload's order, its text
    ``"Next 30 days · 23 · ~17 s"``. A part that was not read (the count, the
    estimate) is dropped rather than printed as 0, and a button is enabled only
    when its count was read and is above zero; a choice holding nothing drops its
    estimate too, since there is no wait to quote. A failed scan wins; entries
    with no known key are skipped, and a list with none left is no chooser - and
    then :func:`summary_facts` and :func:`no_data_label` do not ask either, but
    fall through to their ordinary lines, so no sentence points at a card the
    page cannot draw. An answer with no symbol is no chooser either: a pick
    scans the symbol the card describes, and there would be none.
    """
    p = payload or {}
    if p.get("error") or not p.get("needs_choice"):
        return None
    symbol = _symbol_of(p)
    if not symbol:
        return None                 # a pick would have no chain to scan
    choices = p.get("choices")
    if not isinstance(choices, list):
        return None
    buttons = []
    for entry in choices:
        if not isinstance(entry, dict) or choice_label(entry.get("key")) is None:
            continue
        key = entry["key"]
        parts = [_label_for(choices, key)]
        count = _whole_count(entry.get("count"))
        if count is not None:
            parts.append(f"{count:,}")
        est = _fmt.num(entry.get("est_seconds"))
        if est is not None and est >= 0 and count != 0:
            parts.append(f"~{_half_up(est):,} s")
        buttons.append({"key": key, "text": " · ".join(parts),
                        "enabled": count is not None and count > 0})
    if not buttons:
        return None
    listed = _whole_count(p.get("expiration_count"))
    # "many" rather than a number nobody read.
    how_many = "many expirations" if listed is None else _expirations(listed)
    return {"title": f"{symbol} lists {how_many} in this range.",
            "prompt": "Choose what to scan:", "buttons": buttons}


def no_data_label(payload):
    """What the empty list says after a scan that returned no rows - the reason,
    in the page's voice, rather than Quasar's "No data available".

    It names the symbol and the price, so an empty list reads as an answer about
    THIS symbol and not a page that failed to load. Precedence: a failed scan
    (``error`` is the exception's class name - tested for truthiness) · a large
    chain that asked which expirations to scan · a choice that held no
    expirations · no chain came back · no expirations in the range · the quality
    cut · premium too cheap to sell · nothing could be built.

    A remembered choice can hold nothing in the range the bar now asks for (Next
    30 days on $SPX with DTE min at 31): the service answers
    ``no_expiries_in_range`` with ``expirations_scanned`` 0 while 33 expirations
    are in range, so "no expirations in this expiry range" would be false beside
    "Scanned 0 of 33 expirations". That
    line names the choice instead, and points at Change only when the page draws
    it - a known choice, both counts read, and choices to reopen.

    One subject throughout: ``SPY at $764.48``, ``SPY`` without a price, and
    ``this symbol`` with no symbol at all. Two exceptions, both deliberate: a
    failed scan names the symbol but no price (the failure is the news), and the
    no-symbol "too cheap" sentence keeps its original wording, which the uniform
    form would have reworded.
    """
    p = payload or {}
    symbol = str(p.get("symbol") or "").strip().upper()
    spot = _spot(p)
    at = "" if spot is None else f" at {_price_text(spot)}"
    subject = f"{symbol}{at}" if symbol else "this symbol"

    if p.get("error"):
        failed = f"The scan for {symbol} failed." if symbol else "The scan failed."
        return f"{failed} Check System Status and scan again."
    if chooser_facts(p) is not None:
        tail = f" for {symbol}" if symbol else ""
        return f"Choose which expirations to scan{tail}."
    choice = p.get("expiry_choice")
    if choice_label(choice) is not None and _whole_count(p.get("expirations_scanned")) == 0:
        tail = f" for {symbol}" if symbol else ""
        head = f"{_label_for(p.get('choices'), choice)} holds no expirations in this range{tail}"
        offers_change = (summary_facts(p) or {}).get("can_change") and \
            chooser_facts({**p, "needs_choice": True}) is not None
        return f"{head} — use Change to pick another." if offers_change else f"{head}."
    if p.get("chain_missing"):
        return f"No option chain came back for {subject}."
    if p.get("no_expiries_in_range"):
        return f"{subject[0].upper()}{subject[1:]} has no expirations in this expiry range."
    if _fmt.num(p.get("filtered_out")):
        return f"No strategies cleared the quality bar for {subject}."
    if _fmt.num(p.get("vol_filtered")):
        if not symbol:
            return "No strategies to show — premium is too cheap to sell for this symbol."
        return f"No strategies for {subject} — premium is too cheap to sell."
    return f"No strategies could be built for {subject} in this expiry range."


def scan_timeout_text(symbol, seconds):
    """The spinner's running count while a scan runs: ``"Scanning SPY… 12 s"``.

    Whole seconds, rounded down. A whole-chain scan legitimately takes 40 s for
    $SPX and 26 s for SPY (measured live), so the count is what keeps a long wait
    from reading as a hang. An
    unreadable count drops the number rather than printing one it did not read.
    """
    sym = str(symbol or "").strip().upper()
    head = f"Scanning {sym}…" if sym else "Scanning…"
    sec = _fmt.num(seconds)
    if sec is None:
        return head
    return f"{head} {max(0, int(_math.floor(sec)))} s"


def summary_facts(payload):
    """The summary strip's facts, or None before any scan has published.

    ``{"symbol", "price", "pills", "vol_rank", "counts", "can_change"}``. Vol Rank
    lives here rather than in the list because a scan is one symbol, so every row
    carried the same value.

    A large chain that ASKED (``needs_choice``, with a choice to offer) counts
    nothing yet: its line is
    ``"56 expirations — choose what to scan"``. A scan limited by a choice adds
    ``"Scanned 19 of 56 expirations · Monthlies only"`` - only when the choice is
    one of the four and both counts were read - and ``can_change`` is True
    exactly then, so the page offers Change beside the line it explains.

    ⚠ **Two drops, two sentences, never merged** (moved here from the old
    ``swing.status_text``). The service's quality cut and the volatility gate (gap
    assessment B2) both remove candidates, but the gate refuses to SELL premium
    when IV rank sits below the floor - a statement about today's environment, not
    the candidate - so borrowing "below the quality bar" for it would print
    something untrue on exactly the scan where the reader most needs the reason.
    Both read with a falsy default, so a payload written before ``vol_filtered``
    existed (Redis keeps this view across a restart) renders as it always did.
    """
    p = payload or {}
    symbol = p.get("symbol")
    if not symbol:
        return None
    signals = [s for s in (p.get("signals") or []) if s]
    first = signals[0] if signals else {}

    # Every answer shows a price - an empty one too, so it reads as an answer -
    # and a missing reading says so in words, never as $0.00.
    price = _price_text(_spot(p))

    view = p.get("view") or {}
    pills = []
    if view.get("direction"):
        pills.append(str(view["direction"]).title())
    conviction = _fmt.num(view.get("conviction"))
    if conviction is not None:
        pills.append(f"Conviction {_conviction_word(conviction)}")
    if view.get("vol_regime"):
        pills.append(f"Volatility {view['vol_regime']}")

    rank = _fmt.num(first.get("iv_rank"))
    vol_rank = None if rank is None else f"Vol Rank {_half_up(rank)}"

    if p.get("error"):
        # A failed scan read no ideas and no cuts: "0 ideas" would be a count
        # nobody read. ``error`` is the exception's class name - truthiness only.
        return {"symbol": symbol, "price": price, "pills": pills,
                "vol_rank": vol_rank, "counts": "Scan failed", "can_change": False}

    if chooser_facts(p) is not None:
        listed = _whole_count(p.get("expiration_count"))
        counts = ("Choose what to scan" if listed is None
                  else f"{_expirations(listed)} — choose what to scan")
        return {"symbol": symbol, "price": price, "pills": pills,
                "vol_rank": vol_rank, "counts": counts, "can_change": False}

    n = len(signals)
    parts = [f"{n:,} idea" if n == 1 else f"{n:,} ideas"]
    below = _fmt.num(p.get("filtered_out"))
    if below:
        parts.append(f"{int(below):,} below the quality bar")
    cheap = _fmt.num(p.get("vol_filtered"))
    if cheap:
        parts.append(f"{int(cheap):,} where premium is too cheap to sell")
    # The service keeps the best 25 of each strategy type; the rest are counted.
    hidden = _fmt.num(p.get("not_shown"))
    if hidden:
        parts.append(f"{int(hidden):,} lower-scoring idea{'' if hidden == 1 else 's'} "
                     "not shown")
    # None is "not counted", which is not zero - both add no line.
    failed = _fmt.num(p.get("expiries_failed"))
    if failed:
        parts.append(f"{int(failed):,} expiration{'' if failed == 1 else 's'} "
                     "could not be loaded")
    # Last, so it sits beside the Change link the page draws after the line.
    choice = p.get("expiry_choice")
    scanned = _whole_count(p.get("expirations_scanned"))
    listed = _whole_count(p.get("expiration_count"))
    can_change = (choice_label(choice) is not None
                  and scanned is not None and listed is not None)
    if can_change:
        parts.append(f"Scanned {scanned:,} of {_expirations(listed)} · "
                     f"{_label_for(p.get('choices'), choice)}")

    return {"symbol": symbol, "price": price, "pills": pills,
            "vol_rank": vol_rank, "counts": " · ".join(parts),
            "can_change": can_change}


# ------------------------------------------------------------------------- bars

# Width classes snap to 5% steps: a FIXED, finite class set (the Tailwind
# finite-palette rule), never a runtime ``w-[43.7%]``.
_WIDTH = {p: ("w-0" if p == 0 else "w-full" if p == 100 else f"w-[{p}%]")
          for p in range(0, 101, 5)}


def _snap(pct):
    """``pct`` to the nearest 5 in [0, 100]. A positive value never snaps to 0 -
    a real $1,960 profit beside a $53,817 loss must still show a sliver."""
    f = _fmt.num(pct)
    if f is None or f <= 0:
        return 0
    return max(5, min(100, _half_up(f / 5.0) * 5))


def _width(pct):
    return _WIDTH[_snap(pct)]


# Which side of a payoff is unbounded. Restated from strategy_table's
# ``_profit_is_unbounded`` / ``_loss_is_unbounded`` (that module cannot be imported
# here, see the docstring): the explicit engine flags first, then the legacy
# ``unbounded`` partitioned by ``max_profit`` for a row cached before them.
def _profit_unbounded(sig):
    if sig.get("unbounded_profit"):
        return True
    return bool(sig.get("unbounded")) and sig.get("max_profit") is None


def _loss_unbounded(sig):
    if sig.get("unbounded_loss"):
        return True
    return bool(sig.get("unbounded")) and sig.get("max_profit") is not None


_INFINITY = "∞"


def risk_reward_bar(sig):
    """One split bar: red max loss left, green max profit right.

    Both halves scale to the LARGER of the two, so the bar shows the shape of the
    bet (a butterfly mostly green, a covered call mostly red). A single scale
    across rows would let one covered call flatten every other bar. An unbounded
    side draws full and is labelled ``∞`` - for a naked short, whose ``max_loss``
    is a margin proxy, drawing that number to scale would read as a cap.

    ``{"loss_class", "profit_class", "loss_label", "profit_label"}``, or None when
    neither side has a usable number.
    """
    s = sig or {}
    p_inf, l_inf = _profit_unbounded(s), _loss_unbounded(s)
    profit = None if p_inf else _fmt.num(s.get("max_profit"))
    loss = None if l_inf else _fmt.num(s.get("max_loss"))
    profit = None if profit is None else abs(profit)
    loss = None if loss is None else abs(loss)
    if not (p_inf or l_inf) and profit is None and loss is None:
        return None

    def _half(value, inf, other_inf):
        if inf:
            return "w-full", _INFINITY
        if value is None:
            return "w-0", NO_READING
        if other_inf:
            return _width(0 if value == 0 else 1), money(value)   # a sliver
        top = max(v for v in (profit, loss) if v is not None)
        return _width(100.0 * value / top if top > 0 else 0), money(value)

    loss_class, loss_label = _half(loss, l_inf, p_inf)
    profit_class, profit_label = _half(profit, p_inf, l_inf)
    return {"loss_class": loss_class, "profit_class": profit_class,
            "loss_label": loss_label, "profit_label": profit_label}


def pop_bar(pop):
    """Probability-of-profit bar, 0-100%: amber below 40, green above 60.

    ``pop`` is a PERCENT (the engine's ``pop_pct``), not a fraction.
    ``{"class", "tone": "warn" | "neutral" | "pos", "label"}``, or None.
    """
    f = _fmt.num(pop)
    if f is None:
        return None
    # The band is decided on the ROUNDED percent, so the colour always agrees
    # with the label beside it (39.6 reads "40%" and is not amber).
    whole = _half_up(f)
    tone = "warn" if whole < 40 else "pos" if whole > 60 else "neutral"
    return {"class": _width(f), "tone": tone, "label": f"{whole}%"}


# The odds bar's fill per tone - a fixed class set, bound through a table slot's
# ``:class``: amber below 40, the theme's accent blue between (grey read as "no
# data"), the app's profit green above 60.
POP_FILL = {"warn": "bg-[#fbbf24]",
            "neutral": f"bg-[{_THEME['palette']['primary']}]",
            "pos": "bg-[#34d399]"}


def _pop_fill(bar):
    return POP_FILL.get((bar or {}).get("tone"), "")


# ------------------------------------------------------------------- payoff SVG

# The app's existing P/L colours (simulator.whatif_figure). A hand-drawn chart's
# stroke colours are chart config, outside the Tailwind class rule.
PROFIT_STROKE = "#34d399"
LOSS_STROKE = "#f87171"
ZERO_STROKE = "#3a4a6b"
SPOT_STROKE = "#8794b4"
_PAD = 2
_SPOT_TICK = 4          # half-height of today's-price tick, px


def _n(v):
    """A coordinate as short text: one decimal, no trailing ``.0``."""
    t = f"{v:.1f}"
    return t[:-2] if t.endswith(".0") else t


def _line(x1, y1, x2, y2, stroke, extra=""):
    return (f'<line x1="{_n(x1)}" y1="{_n(y1)}" x2="{_n(x2)}" y2="{_n(y2)}" '
            f'stroke="{stroke}"{extra}/>')


def _sign_stroke(pnl):
    return PROFIT_STROKE if pnl > 0 else LOSS_STROKE if pnl < 0 else ZERO_STROKE


def payoff_svg(curve, spot, width=120, height=32):
    """A small payoff shape: profit green, loss red, a dashed zero line, a tick at
    today's price.

    ``curve`` is the service's ``payoff_curve`` - ``[[price, pnl], ...]``. The SVG
    is a FIXED pixel size with a matching ``viewBox`` and no
    ``preserveAspectRatio``: stretching a viewBox needs ``vector-effect`` to keep
    strokes even, and DOMPurify strips that attribute (CLAUDE.md). One ``<line>``
    per segment, coloured by its sign - a polyline cannot change colour part-way
    - and a segment that CROSSES zero is split at the interpolated break-even so
    the colour changes exactly there. Returns ``""`` when there is nothing to draw
    (fewer than two usable points, or no price range).
    """
    pts = []
    for p in curve or []:
        try:
            x, y = _fmt.num(p[0]), _fmt.num(p[1])
        except (TypeError, IndexError, KeyError):
            continue
        if x is not None and y is not None:
            pts.append((x, y))
    if len(pts) < 2:
        return ""
    pts.sort(key=lambda q: q[0])
    x_lo, x_hi = pts[0][0], pts[-1][0]
    if x_hi <= x_lo:
        return ""
    y_lo = min(0.0, min(q[1] for q in pts))
    y_hi = max(0.0, max(q[1] for q in pts))
    w, h = int(width), int(height)
    inner_w, inner_h = w - 2 * _PAD, h - 2 * _PAD

    def sx(x):
        return _PAD + (x - x_lo) / (x_hi - x_lo) * inner_w

    def sy(y):
        if y_hi <= y_lo:                      # a flat-zero payoff: centre it
            return h / 2.0
        return _PAD + (y_hi - y) / (y_hi - y_lo) * inner_h

    zero_y = sy(0.0)
    parts = [_line(_PAD, zero_y, w - _PAD, zero_y, ZERO_STROKE,
                   ' stroke-width="1" stroke-dasharray="2 2"')]
    seg = ' stroke-width="1.5" stroke-linecap="round"'
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        if y1 * y2 < 0:
            # Crosses break-even: split at the interpolated zero so red and green
            # meet exactly there, instead of one colour spilling past it.
            xc = x1 + (0.0 - y1) / (y2 - y1) * (x2 - x1)
            parts.append(_line(sx(x1), sy(y1), sx(xc), zero_y, _sign_stroke(y1), seg))
            parts.append(_line(sx(xc), zero_y, sx(x2), sy(y2), _sign_stroke(y2), seg))
        else:
            # Same sign, or touching zero at one end: the non-zero end decides.
            parts.append(_line(sx(x1), sy(y1), sx(x2), sy(y2),
                               _sign_stroke(y1 if y1 != 0 else y2), seg))
    s = _fmt.num(spot)
    if s is not None and x_lo <= s <= x_hi:
        x = sx(s)
        parts.append(_line(x, max(0.0, zero_y - _SPOT_TICK), x,
                           min(float(h), zero_y + _SPOT_TICK), SPOT_STROKE,
                           ' stroke-width="1"'))
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
            f'viewBox="0 0 {w} {h}">' + "".join(parts) + "</svg>")


# ------------------------------------------------------------ list and cards

# An unbounded side sorts past every real figure. float("inf") cannot cross the
# JSON wire to the table, so a large finite stand-in.
_UNBOUNDED_SORT = 1e12


# The two long-text cells may wrap (at their spaces), each with a floor so the
# browser does not squeeze them to a word per line: room for two legs, and for
# "$54,058 debit". Every header may wrap too - a label, not its numbers, was
# setting the widest columns. Measured in the local harness at 1372 px.
_WRAP_CELL = {"strikes": "whitespace-normal min-w-[10.5rem]",
              "cost": "whitespace-normal min-w-[7rem]"}
_WRAP_HEADER = "whitespace-normal"


def finder_columns():
    """``ui.table`` columns for the slim ranked list.

    Breakevens, bias and Vol Rank are not in the list (the detail panel and the
    summary strip carry them). Strikes ARE, beside Expiry, so a row names its
    contracts without a click - a later-expiring leg carries its own date in that
    text, the way the top-pick cards print it. The money, odds and expiry columns sort on
    a NUMERIC twin field - their shown text ("$1,234", "Oct 16 · 30d") would sort
    as a string - and the page's slots render the text.
    """
    spec = [
        ("strategy", "Strategy", "strategy", True),
        ("composite_score", "Score", "composite_score", True),
        ("strikes", "Strikes", "strikes", False),
        ("expiry", "Expiry", "_dte", True),
        ("cost", "Cost", "cost", False),
        ("max_profit", "Max profit", "_max_profit_n", True),
        ("max_loss", "Max loss", "_max_loss_n", True),
        ("pop", "Probability of profit", "_pop_n", True),
        ("grade", "Grade", "grade", False),
    ]
    cols = [{"name": name, "label": label, "field": field, "sortable": sortable,
             "align": "left"} for name, label, field, sortable in spec]
    # Unwrapped, a condor's legs and a collar's "for 100 shares" push the actions
    # off-screen at 1440 px.
    cols.append({"name": "actions", "label": "", "field": "actions",
                 "sortable": False, "align": "center"})
    for col in cols:
        col["headerClasses"] = _WRAP_HEADER
        if col["name"] in _WRAP_CELL:
            col["classes"] = _WRAP_CELL[col["name"]]
    return cols


def _score_text(score):
    """A score as its whole number (halves up), or the em-dash."""
    f = _fmt.num(score)
    return NO_READING if f is None else str(_half_up(f))


def _max_profit_cell(sig):
    if _profit_unbounded(sig):
        return _INFINITY, _UNBOUNDED_SORT
    v = _fmt.num(sig.get("max_profit"))
    return (NO_READING, None) if v is None else (money(abs(v)), abs(v))


def _max_loss_cell(sig):
    if _loss_unbounded(sig):
        return _INFINITY, _UNBOUNDED_SORT
    v = _fmt.num(sig.get("max_loss"))
    return (NO_READING, None) if v is None else (money(abs(v)), abs(v))


_LEG_SEP = " / "
_NBSP = " "


def _unbroken_legs(text):
    """A legs line whose wrapping cell may break only BETWEEN legs, after the
    slash: the spaces inside a leg ("S 530P", "L 100 shares") and the one before
    each slash become non-breaking."""
    parts = (p.replace(" ", _NBSP) for p in str(text).split(_LEG_SEP))
    return (_NBSP + "/ ").join(parts)


def finder_rows(signals, *, score_class, grade_class, paper_types, legs_text):
    """Rows for the ranked list, best score first.

    The four hooks are INJECTED because their homes (``scanner.score_zone_class``,
    ``strategy_table.grade_class``, ``strategy_table._PAPER_TYPES`` and
    ``strategy_table.legs_summary``) import the widget library;
    ``swing.finder_rows`` passes the real ones, so the paper gate stays one set
    and the Strikes cell is the same legs line the top-pick card prints.
    """
    rows = []
    for s in ranked(signals):
        profit_text, profit_n = _max_profit_cell(s)
        loss_text, loss_n = _max_loss_cell(s)
        pop = pop_bar(s.get("pop_pct"))
        dte = _fmt.num(s.get("dte"))
        score = s.get("composite_score")
        rows.append({
            "id": s.get("id"),
            "strategy": s.get("strategy_label") or "",
            "composite_score": score,
            "strikes": _unbroken_legs(legs_text(s.get("legs"))),
            "expiry": expiry_text(s),
            "earnings": earnings_text(s),
            "cost": cost_text(s),
            "max_profit": profit_text,
            "max_loss": loss_text,
            "pop": pop["label"] if pop else NO_READING,
            "grade": s.get("grade") or "",
            "grade_reason": s.get("grade_reason") or "",
            # The badge text - the same whole number the card shows; the column
            # still sorts on the raw composite_score.
            "score_text": _score_text(score),
            "_dte": None if dte is None else int(dte),
            "_max_profit_n": profit_n,
            "_max_loss_n": loss_n,
            "_pop_n": _fmt.num(s.get("pop_pct")),
            "_score_class": score_class(score),
            "_grade_class": grade_class(s.get("grade")),
            "_payoff_svg": payoff_svg(s.get("payoff_curve"), s.get("underlying_price"),
                                      width=72, height=20),
            "_pop": pop,
            "_pop_fill": _pop_fill(pop),
            "_allow_paper": s.get("type") in paper_types,
            "_undefined_risk": _loss_unbounded(s),
        })
    return rows


CARD_SHAPE_W, CARD_SHAPE_H = 280, 56


def card_facts(sig):
    """What a top-pick card says. The legs line and the paper gate are added by
    the page (their helpers live in widget-importing modules)."""
    s = sig or {}
    score = _fmt.num(s.get("composite_score"))
    pop = pop_bar(s.get("pop_pct"))
    return {
        "title": s.get("strategy_label") or NO_READING,
        "score": score,
        "score_text": _score_text(score),
        "grade": s.get("grade") or "",
        "expiry": expiry_text(s),
        "earnings": earnings_text(s),
        "cost": cost_text(s),
        # A card is ~300px wide; the list keeps its 72x20 shape.
        "payoff_svg": payoff_svg(s.get("payoff_curve"), s.get("underlying_price"),
                                 width=CARD_SHAPE_W, height=CARD_SHAPE_H),
        "rr": risk_reward_bar(s),
        "pop": pop,
        "pop_fill": _pop_fill(pop),
    }

"""``config/scalp.toml``: the Opening Scalp's tunables.

Design: docs/plans/2026-10-18-opening-scalp-design.md. Read per call through the
mtime-cached loader, so a saved change applies with no restart. Every accessor
returns its built-in default for a missing or unusable value and never raises.
The per-symbol tables (``width``, ``strike_step``, ``inputs``) are keyed by the
symbol: an unknown symbol reads as the SPY-like default for a width or a strike
step (1), and as ABSENT (``None`` / empty) for an input, never borrowed from
another symbol and never zero. Stdlib, ``shared.config_toml`` and ``repo_paths``
only, so Tier 1 may read it as well. The one other import is ``shared.symbols``
(itself ``repo_paths`` + ``config_toml``), whose ``big10()`` the bar store adds.

The intraday calls (design 2026-10-20-scalp-intraday-calls) add the slot
schedule, its three blocks and the confirmation's weights and cuts. Slot names
are zero-padded ``"HH:MM"`` Central strings, so string order is time order.
"""
import math
import re
from datetime import date, datetime
from zoneinfo import ZoneInfo

from repo_paths import SCALP_TOML
from shared import symbols as _symbols
from shared.config_toml import toml_loader

_CT = ZoneInfo("America/Chicago")
_HHMM = re.compile(r"\d\d:\d\d")

#: What an unlisted symbol's width and strike step read as: the SPY-like 1.00.
SPY_LIKE_STEP = 1.0

_BUCKETS = [0.4, 0.7]
_IV_SCALES = [0.8, 1.0, 1.25, 1.5]

DEFAULTS = {
    "universe": {"symbols": ["$SPX", "SPY", "QQQ"]},
    "bars": {"symbols": ["$SPX", "SPY", "QQQ", "/ES", "/NQ", "IGV", "SMH", "EWY",
                         "XLE", "XLK", "XLV", "$VIX", "$VXN"]},
    "strike_step": {"$SPX": 5, "SPY": 1, "QQQ": 1},
    "width": {"$SPX": 5, "SPY": 1, "QQQ": 1},
    "inputs": {
        "$SPX": {"futures": "/ES", "etfs": ["XLE", "XLK", "XLV"], "vol": "$VIX"},
        "SPY": {"futures": "/ES", "etfs": ["XLE", "XLK", "XLV"], "vol": "$VIX"},
        "QQQ": {"futures": "/NQ", "etfs": ["IGV", "SMH", "EWY", "XLK"], "vol": "$VXN"},
    },
    "trade": {"otm_steps": 0, "target_pct": 50, "stop_pct": 50, "time_stop_min": 60,
              "iv_scale": 1.0, "min_premium": 0.05, "chain_strikes": 10},
    "direction": {"dead_zone_pct": 0.05, "dead_zone_frac": 0.1,
                  "open_dead_zone_pct": 0.03, "buckets": _BUCKETS},
    "study": {"iv_scales": _IV_SCALES, "min_samples": 20, "scorecard_days": 20},
    "news": {"items": 5},
    "context": {"report_url": "https://neuralstrike.co/report.html"},
    "intraday": {
        "start": "08:51", "end": "13:51", "interval_min": 15, "grace_min": 5,
        "dead_zone_pct": 0.03, "breadth_agree": 0.6, "promotion_sessions": 40,
        "vwap_max_missing": 0.25, "min_brier_margin": 0.005,
        "early_close_days": ["2026-11-27", "2026-12-24"], "early_close_time": "12:00",
        "blocks": {"morning": {"start": "08:51", "end": "09:51"},
                   "midday": {"start": "10:06", "end": "12:21"},
                   "afternoon": {"start": "12:36", "end": "13:51"}},
    },
    "confirm": {
        "flow_window_min": 30, "flow_margin": 2, "netprem_margin": 0.1,
        "max_age_min": 5, "sides_majority": 0.6, "breadth_min_members": 5,
        "min_sources": 2, "cuts": [-0.34, 0.34],
        "weights": {"flow": 1.0, "netprem": 1.0, "dealer": 1.0, "sentiment": 0.5},
    },
    # The fitted chance of target (design 2026-10-22-scalp-weighted-probability).
    "prob": {
        "ema_period": 20, "slope_bars": 3, "rsi_period": 14,
        "timeframes": [5, 15, 60], "lookback_sessions": 10,
        "cap": 3.0, "room_cap": 3.0, "flow_cap": 3,
        "priors": {"slope_5": 0.15, "slope_15": 0.10, "slope_60": 0.05,
                   "rsi_5": 0.10, "rsi_15": 0.07, "rsi_60": 0.04,
                   "room": 0.15, "breadth_room": 0.05, "flip_side": 0.0,
                   "netprem": 0.10, "flow": 0.10, "sentiment": 0.05},
        "prior_sd": {"wide": 1.0, "tight": 0.10},
        "min_train_weeks": 8, "min_brier_margin": 0.005, "refit_weekday": 5,
        "show": True, "assumed_share": 0.5,
    },
}

load, reset_cache = toml_loader(SCALP_TOML, DEFAULTS, label="scalp.toml")


def _section(name) -> dict:
    cfg = load()
    sec = cfg.get(name) if isinstance(cfg, dict) else None
    return sec if isinstance(sec, dict) else {}


def _real(raw) -> bool:
    """A finite number that is not a bool."""
    return (not isinstance(raw, bool) and isinstance(raw, (int, float))
            and math.isfinite(raw))


def _number(section, key, *, floor=0.0, strict=True) -> float:
    """A finite number above ``floor`` (``strict``) or at it, else the default."""
    raw = _section(section).get(key)
    default = float(DEFAULTS[section][key])
    if not _real(raw) or (raw <= floor if strict else raw < floor):
        return default
    return float(raw)


def _fraction(section, key, *, allow_zero) -> float:
    """A share of a whole, above zero (or at it) and at most 1, else the default."""
    raw = _section(section).get(key)
    if not _real(raw) or not (0 <= raw <= 1) or (raw == 0 and not allow_zero):
        return float(DEFAULTS[section][key])
    return float(raw)


def _count(section, key, *, minimum=1) -> int:
    """A whole number of at least ``minimum``, else the default."""
    raw = _section(section).get(key)
    if not _real(raw) or raw != int(raw) or raw < minimum:
        return DEFAULTS[section][key]
    return int(raw)


def _clean(symbol) -> str:
    return symbol.strip().upper() if isinstance(symbol, str) else ""


def _symbol_list(raw) -> list:
    """Upper-cased, de-duplicated tickers from a list; non-strings and empties
    are skipped. Not a list gives an empty one."""
    out: list = []
    for item in raw if isinstance(raw, list) else []:
        sym = _clean(item)
        if sym and sym not in out:
            out.append(sym)
    return out


# ── the universe ─────────────────────────────────────────────────────────────
def symbols() -> list:
    """The symbols the page calls: ``["$SPX", "SPY", "QQQ"]``."""
    return _symbol_list(_section("universe").get("symbols")) \
        or list(DEFAULTS["universe"]["symbols"])


def inputs_for(symbol) -> dict:
    """What the direction call reads for ``symbol``:
    ``{"futures": str | None, "etfs": [str], "vol": str | None}``.

    An unknown symbol, or an entry field that is not usable, is ABSENT (``None``
    / empty). Returns a fresh dict each call."""
    entry = _section("inputs").get(_clean(symbol))
    entry = entry if isinstance(entry, dict) else {}
    return {"futures": _clean(entry.get("futures")) or None,
            "etfs": _symbol_list(entry.get("etfs")),
            "vol": _clean(entry.get("vol")) or None}


def bar_symbols() -> list:
    """Every symbol whose bars are stored: ``[bars] symbols``, then any symbol or
    input the other tables name that list forgot, so a changed input is always
    fetched, then the big ten (the intraday calls read their breadth)."""
    out = _symbol_list(_section("bars").get("symbols")) \
        or list(DEFAULTS["bars"]["symbols"])
    for sym in symbols():
        wanted = [sym]
        got = inputs_for(sym)
        wanted += [got["futures"], *got["etfs"], got["vol"]]
        for w in wanted:
            if w and w not in out:
                out.append(w)
    for sym in _symbol_list(list(_symbols.big10())):
        if sym not in out:
            out.append(sym)
    return out


# ── per-symbol sizes ─────────────────────────────────────────────────────────
def _per_symbol(table, symbol) -> float:
    raw = _section(table).get(_clean(symbol))
    if _real(raw) and raw > 0:
        return float(raw)
    default = DEFAULTS[table].get(_clean(symbol))
    return float(default) if default is not None else SPY_LIKE_STEP


def strike_step(symbol) -> float:
    """Dollars between listed strikes ($SPX 5, SPY 1, QQQ 1)."""
    return _per_symbol("strike_step", symbol)


def width(symbol) -> float:
    """Credit-spread width in dollars ($SPX 5, SPY 1, QQQ 1)."""
    return _per_symbol("width", symbol)


# ── the trade ────────────────────────────────────────────────────────────────
def otm_steps() -> int:
    """Strikes out of the money for the long option; 0 is at the money."""
    return _count("trade", "otm_steps", minimum=0)


def target_pct() -> float:
    return _number("trade", "target_pct")


def stop_pct() -> float:
    return _number("trade", "stop_pct")


def time_stop_min() -> int:
    return _count("trade", "time_stop_min")


def iv_scale() -> float:
    return _number("trade", "iv_scale")


def min_premium() -> float:
    return _number("trade", "min_premium", floor=0.0, strict=False)


def chain_strikes() -> int:
    return _count("trade", "chain_strikes")


# ── the direction call ───────────────────────────────────────────────────────
def dead_zone_pct() -> float:
    """Returns (percent) below this are flat."""
    return _number("direction", "dead_zone_pct", strict=False)


def dead_zone_frac() -> float:
    """A 0..1 position within this of the middle is neutral."""
    return _number("direction", "dead_zone_frac", strict=False)


def open_dead_zone_pct() -> float:
    return _number("direction", "open_dead_zone_pct", strict=False)


def buckets() -> list:
    """Two ascending cut points in (0, 1); anything else is ``[0.4, 0.7]``."""
    raw = _section("direction").get("buckets")
    if (isinstance(raw, list) and len(raw) == 2 and all(_real(v) for v in raw)
            and 0 < raw[0] < raw[1] < 1):
        return [float(raw[0]), float(raw[1])]
    return list(_BUCKETS)


# ── the study and the page ───────────────────────────────────────────────────
def iv_scales() -> list:
    """A non-empty list of positive finite numbers, else the default."""
    raw = _section("study").get("iv_scales")
    if isinstance(raw, list) and raw and all(_real(v) and v > 0 for v in raw):
        return [float(v) for v in raw]
    return list(_IV_SCALES)


def min_samples() -> int:
    return _count("study", "min_samples")


def scorecard_days() -> int:
    return _count("study", "scorecard_days")


def news_items() -> int:
    return _count("news", "items", minimum=0)


def report_url() -> str:
    """The reports link shown beside the call: an ``http(s)://`` address, else the
    default."""
    raw = _section("context").get("report_url")
    if isinstance(raw, str) and raw.strip().lower().startswith(("https://", "http://")):
        return raw.strip()
    return DEFAULTS["context"]["report_url"]


# ── the intraday calls: the 15-minute slots ──────────────────────────────────
def _minutes(raw):
    """Minutes after midnight for a zero-padded ``"HH:MM"``, else ``None``."""
    if not isinstance(raw, str) or not _HHMM.fullmatch(raw):
        return None
    hh, mm = int(raw[:2]), int(raw[3:])
    return hh * 60 + mm if hh < 24 and mm < 60 else None


def _slot_name(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _slot_edge(key):
    """One of the schedule's ends, as minutes, else its default's."""
    got = _minutes(_section("intraday").get(key))
    return got if got is not None else _minutes(DEFAULTS["intraday"][key])


#: The 08:36 call's slot name. An intraday slot of the same name would share its
#: recorded row (``intraday_readings`` is keyed by day, slot and symbol).
CALL_SLOT = "08:36"
#: The earliest first slot: the intraday reading needs the last 15 minutes of
#: the regular session, and the 08:36 call owns the opening minutes.
EARLIEST_START = "08:45"
#: The regular session's close, Central. Fixed by the exchange, not tunable.
REGULAR_CLOSE = "15:00"


def schedule_problem(start, end, interval_min, time_stop_min):
    """Why a slot schedule (each end as minutes after midnight) is not usable, or
    ``None``: the first slot must not be after the last nor before
    ``EARLIEST_START``, no slot may be named ``CALL_SLOT``, and the last slot's
    entry + ``time_stop_min`` must not pass ``REGULAR_CLOSE``."""
    if start is None or end is None or start > end:
        return "the first call is after the last"
    call = _minutes(CALL_SLOT)
    if start <= call <= end and (call - start) % interval_min == 0:
        return f"a call would be made at {CALL_SLOT}"
    if start < _minutes(EARLIEST_START):
        return f"the first call is before {EARLIEST_START}"
    last = start + (end - start) // interval_min * interval_min
    if last - last % 5 + time_stop_min > _minutes(REGULAR_CLOSE):
        return f"the last call's trade would run past the {REGULAR_CLOSE} close"
    return None


def intraday_slots(day) -> list:
    """The call slots as ``"HH:MM"`` Central names, from ``start`` to ``end``
    every ``interval_min`` (08:51 .. 13:51 by 15: 21 slots). A schedule that
    ``schedule_problem`` refuses (a start after the end or before 08:45, a slot
    at 08:36, a last trade running past 15:00) reads as the defaults whole.
    ``day`` is accepted but every session has the same slots: an early close
    cuts slots from the CALLS (``intraday_slot_cut``), not from the schedule. A
    fresh list each call."""
    start, end = _slot_edge("start"), _slot_edge("end")
    step = _count("intraday", "interval_min")
    if schedule_problem(start, end, step, time_stop_min()) is not None:
        start, end = _minutes(DEFAULTS["intraday"]["start"]), \
            _minutes(DEFAULTS["intraday"]["end"])
        step = DEFAULTS["intraday"]["interval_min"]
    return [_slot_name(m) for m in range(start, end + 1, step)]


def early_close_days() -> set:
    """The sessions that close early (``[intraday] early_close_days``, ISO
    dates). A value that is not a list of ``YYYY-MM-DD`` strings reads as the
    defaults whole; an empty list is no early closes."""
    raw = _section("intraday").get("early_close_days")
    try:
        if isinstance(raw, list) and all(isinstance(d, str) and len(d) == 10 for d in raw):
            return {date.fromisoformat(d) for d in raw}
    except ValueError:
        pass
    return {date.fromisoformat(d) for d in DEFAULTS["intraday"]["early_close_days"]}


def early_close_time() -> str:
    """The Central time an early session closes (``"12:00"``): an ``HH:MM`` after
    the 08:30 open and not after ``REGULAR_CLOSE``, else the default."""
    raw = _section("intraday").get("early_close_time")
    got = _minutes(raw)
    if got is None or not (_minutes("08:30") < got <= _minutes(REGULAR_CLOSE)):
        return DEFAULTS["intraday"]["early_close_time"]
    return raw


def early_close_on(day):
    """The aware Central close of ``day`` when it closes early, else ``None``."""
    if not isinstance(day, date) or day not in early_close_days():
        return None
    return intraday_slot_time(day, early_close_time())


def intraday_slot_cut(day, slot, stop_min=None) -> bool:
    """True when ``day`` closes early and ``slot``'s trade (its entry +
    ``stop_min``, default the configured time stop) would run past that close:
    no call is made at such a slot, and the study leaves it out."""
    close = early_close_on(day)
    if close is None:
        return False
    stop = time_stop_min() if stop_min is None else stop_min
    return intraday_entry_ts(day, slot) + stop * 60 > close.timestamp()


def intraday_slot_time(day, slot):
    """The slot's wall-clock instant on ``day`` as an aware Central datetime.
    A ``slot`` that is not ``"HH:MM"`` is the caller's bug and raises."""
    minutes = _minutes(slot)
    if minutes is None:
        raise ValueError(f"not an HH:MM slot: {slot!r}")
    return datetime(day.year, day.month, day.day, minutes // 60, minutes % 60,
                    tzinfo=_CT)


def intraday_entry_ts(day, slot) -> int:
    """Epoch seconds of the 5-minute bar a slot enters on: the slot minute
    floored to the last 5-minute boundary (08:51 -> the 08:50:00 bar, which has
    closed by 08:55 and holds the price the call was about)."""
    at = intraday_slot_time(day, slot)
    return int(at.replace(minute=at.minute - at.minute % 5).timestamp())


def _blocks() -> dict:
    """``{name: (start_min, end_min)}`` for morning, midday and afternoon. A
    block that is missing, malformed, backwards or overlapping another makes the
    WHOLE table the defaults: half a partition would leave slots in no block."""
    default = {name: (_minutes(b["start"]), _minutes(b["end"]))
               for name, b in DEFAULTS["intraday"]["blocks"].items()}
    raw = _section("intraday").get("blocks")
    if not isinstance(raw, dict):
        return default
    out = {}
    for name in default:
        entry = raw.get(name)
        entry = entry if isinstance(entry, dict) else {}
        lo, hi = _minutes(entry.get("start")), _minutes(entry.get("end"))
        if lo is None or hi is None or lo > hi:
            return default
        out[name] = (lo, hi)
    spans = list(out.values())
    if any(a[1] >= b[0] for a, b in zip(spans, spans[1:])):
        return default
    return out


def intraday_block(slot):
    """``"morning"``, ``"midday"`` or ``"afternoon"`` for the block holding
    ``slot``, else ``None`` (a time outside every block, or not a slot)."""
    at = _minutes(slot)
    if at is None:
        return None
    for name, (lo, hi) in _blocks().items():
        if lo <= at <= hi:
            return name
    return None


# ── the intraday calls: the call and its confirmation ────────────────────────
def intraday_grace_min() -> int:
    """How many minutes late a slot may still fire."""
    return _count("intraday", "grace_min")


def intraday_dead_zone_pct() -> float:
    """Returns (percent) below this are flat, for the intraday move."""
    return _number("intraday", "dead_zone_pct", strict=False)


def breadth_agree() -> float:
    """The share (0..1] of the big ten that must lean one way to count as
    breadth."""
    return _fraction("intraday", "breadth_agree", allow_zero=False)


def vwap_max_missing() -> float:
    """The share of the session's 5-minute bars (08:30 to the entry bar) that
    may be missing before the VWAP side is absent: at least 0 and below 1, any
    other value reads as the default."""
    raw = _section("intraday").get("vwap_max_missing")
    if not _real(raw) or not (0 <= raw < 1):
        return float(DEFAULTS["intraday"]["vwap_max_missing"])
    return float(raw)


def min_brier_margin() -> float:
    """How far below the base rate's Brier a bucket's must sit, at every study
    scale, for an INTRADAY edge (the 08:36 study does not read it): at least 0
    and below 0.25, any other value reads as the default."""
    raw = _section("intraday").get("min_brier_margin")
    if not _real(raw) or not (0 <= raw < 0.25):
        return float(DEFAULTS["intraday"]["min_brier_margin"])
    return float(raw)


def promotion_sessions() -> int:
    """Sessions an intraday group needs before it can be promoted."""
    return _count("intraday", "promotion_sessions")


def flow_window_min() -> int:
    return _count("confirm", "flow_window_min")


def flow_margin() -> float:
    return _number("confirm", "flow_margin")


def netprem_margin() -> float:
    """The share (0..1) of total net premium a side must lead by."""
    return _fraction("confirm", "netprem_margin", allow_zero=True)


def sides_majority() -> float:
    """The share of an option alert's labelled (bought + sold) volume that must
    be on one side, above 0.5 and at most 1, for the alert to be directional;
    any other value reads as the default."""
    raw = _section("confirm").get("sides_majority")
    if not _real(raw) or not (0.5 < raw <= 1):
        return float(DEFAULTS["confirm"]["sides_majority"])
    return float(raw)


def breadth_min_members() -> int:
    """How many of the big ten must have a usable reading before a breadth is
    read at all (a share of two names is not a breadth). It governs BOTH the
    dealer read's wall breadth (names with a usable wall) and the intraday price
    breadth (names moving outside the dead zone over their last 15 minutes). A
    whole number of at least 1."""
    return _count("confirm", "breadth_min_members")


def confirm_min_sources() -> int:
    """How many of the four confirmation sources must give a reading (agree,
    disagree or neutral) before the reading is put in one word: one source alone
    is that source, not a confirmation. A whole number from 1 to 4, else the
    default."""
    got = _count("confirm", "min_sources")
    return got if got <= 4 else DEFAULTS["confirm"]["min_sources"]


def confirm_max_age_min() -> int:
    """The oldest a confirming reading may be, in minutes, before it is absent."""
    return _count("confirm", "max_age_min")


def confirm_cuts() -> tuple:
    """``(contradicts_at, confirms_at)``: a score at or below the first
    contradicts the call, at or above the second confirms it. Two numbers in
    [-1, 1] with the first below zero and the second above, else the defaults."""
    raw = _section("confirm").get("cuts")
    if (isinstance(raw, (list, tuple)) and len(raw) == 2 and all(_real(v) for v in raw)
            and -1 <= raw[0] < 0 < raw[1] <= 1):
        return float(raw[0]), float(raw[1])
    return tuple(float(v) for v in DEFAULTS["confirm"]["cuts"])


def confirm_weights() -> dict:
    """What each input counts for in the confirmation score: ``flow``,
    ``netprem``, ``dealer``, ``sentiment``. A weight that is not a finite number
    at or above zero reads as its default, and a set that sums to zero (nothing
    would count) is the defaults whole. A fresh dict each call."""
    default = DEFAULTS["confirm"]["weights"]
    raw = _section("confirm").get("weights")
    raw = raw if isinstance(raw, dict) else {}
    out = {key: float(raw[key]) if _real(raw.get(key)) and raw[key] >= 0 else float(dflt)
           for key, dflt in default.items()}
    return out if sum(out.values()) > 0 else {k: float(v) for k, v in default.items()}


# ── the fitted chance of target ([prob]) ─────────────────────────────────────
def prob_ema_period() -> int:
    """Bars in the moving average whose slope is a factor."""
    return _count("prob", "ema_period")


def prob_slope_bars() -> int:
    """Bars over which that moving average's slope is measured."""
    return _count("prob", "slope_bars")


def prob_rsi_period() -> int:
    """Bars in the RSI factor."""
    return _count("prob", "rsi_period")


def prob_timeframes() -> list:
    """The bar sizes, in minutes, the slope and RSI factors are read on: a
    non-empty strictly ascending list of whole multiples of 5, else
    ``[5, 15, 60]``. A fresh list each call."""
    raw = _section("prob").get("timeframes")
    if (isinstance(raw, list) and raw and all(_real(v) and v == int(v) and v >= 5
                                             and int(v) % 5 == 0 for v in raw)):
        out = [int(v) for v in raw]
        if all(a < b for a, b in zip(out, out[1:])):
            return out
    return list(DEFAULTS["prob"]["timeframes"])


def prob_lookback_sessions() -> int:
    """Sessions of bars read before an entry to warm up the EMA and RSI."""
    return _count("prob", "lookback_sessions")


def prob_cap() -> float:
    """The cap on a scaled factor, in scaled units (above zero)."""
    return _number("prob", "cap")


def prob_room_cap() -> float:
    """The cap on room divided by the target's points (above zero)."""
    return _number("prob", "room_cap")


def prob_flow_cap() -> int:
    """The cap on the flow factor's count of alerts (a whole number, at least 1)."""
    return _count("prob", "flow_cap")


def prob_priors() -> dict:
    """The ASSUMED weight of each factor before its history has moved it:
    exactly the twelve factors the defaults name (any other TOML key is kept
    out), each any finite number, a bad one reading as its default. A factor
    not listed has a prior of 0.0 for whoever asks. A fresh dict each call."""
    default = DEFAULTS["prob"]["priors"]
    raw = _section("prob").get("priors")
    raw = raw if isinstance(raw, dict) else {}
    return {key: float(raw[key]) if _real(raw.get(key)) else float(dflt)
            for key, dflt in default.items()}


def prob_prior_sd() -> dict:
    """How tightly each prior holds: ``wide`` for the factors with long history
    (the price inputs, slopes, momentum and time of day: their ~180 days decide
    the weight) and ``tight`` for the thin sources (dealer read, net premium,
    flow, sentiment: they stay near their assumed value). Both must be finite
    and above zero, or the pair is the defaults whole. A fresh dict each call."""
    default = DEFAULTS["prob"]["prior_sd"]
    raw = _section("prob").get("prior_sd")
    if (isinstance(raw, dict) and all(_real(raw.get(k)) and raw[k] > 0 for k in default)):
        return {k: float(raw[k]) for k in default}
    return {k: float(v) for k, v in default.items()}


def prob_min_train_weeks() -> int:
    """Weeks of history before the refit's walk-forward check is run."""
    return _count("prob", "min_train_weeks")


def prob_min_brier_margin() -> float:
    """How far below the usual rate's Brier a fit must score for its
    "beats the usual rate on unseen days" line to read yes (the chance is shown
    either way): at least 0 and below 0.25, any other value reads as the
    default."""
    raw = _section("prob").get("min_brier_margin")
    if not _real(raw) or not (0 <= raw < 0.25):
        return float(DEFAULTS["prob"]["min_brier_margin"])
    return float(raw)


def prob_refit_weekday() -> int:
    """The weekday the weekly refit runs, Monday 0 to Sunday 6 (Saturday 5): a
    whole number in that range, else the default."""
    raw = _section("prob").get("refit_weekday")
    if not _real(raw) or raw != int(raw) or not (0 <= raw <= 6):
        return DEFAULTS["prob"]["refit_weekday"]
    return int(raw)


def prob_show() -> bool:
    """Whether the page shows the fitted chance. Only a real ``true`` / ``false``
    counts: anything else reads as the default (shown)."""
    raw = _section("prob").get("show")
    return raw if isinstance(raw, bool) else DEFAULTS["prob"]["show"]


def prob_assumed_share() -> float:
    """The share of a weight's posterior precision that must come from the data
    for it to read as learned: under it, the weight still rests mostly on its
    starting assumption and is shown as "assumed". A number strictly between 0
    and 1, else the default (0.5)."""
    raw = _section("prob").get("assumed_share")
    if not _real(raw) or not (0 < raw < 1):
        return float(DEFAULTS["prob"]["assumed_share"])
    return float(raw)

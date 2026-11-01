"""``config/futures_chain.toml``: the /ES and /NQ 0-DTE chain feed's tunables.

Read by the capture on the PC (through the plan the server builds), by options_svc
(the ingest) and by the page. Stdlib, ``shared.config_toml`` and ``repo_paths``
only: Tier 1 imports it, and ``shared/tests/test_futures_chain_config.py`` pins
that set. Design: docs/plans/2026-10-31-futures-0dte-chain-feed-design.md

Missing file / bad TOML / bad value -> the built-in defaults, never a raise.
"""
from repo_paths import FUTURES_CHAIN_TOML
from shared.config_toml import toml_loader

_ES = {
    "symbol": "FOP.ES", "label": "/ES", "multiplier": 50, "exchange": "XCME",
    "strike_step": 5, "strike_width_pct": 5, "parity_limit_pts": 15,
    "weekday_roots": ["E{w}A", "E{w}B", "E{w}C", "E{w}D"],
    "friday_root": "EW{w}", "eom_root": "EW", "quarterly_root": "ES",
}
_NQ = {
    "symbol": "FOP.NQ", "label": "/NQ", "multiplier": 20, "exchange": "XCME",
    "strike_step": 10, "strike_width_pct": 5, "parity_limit_pts": 60,
    "weekday_roots": ["Q{w}A", "Q{w}B", "Q{w}C", "Q{w}D"],
    "friday_root": "QN{w}", "eom_root": "QNE", "quarterly_root": "NQ",
}

DEFAULTS = {
    "capture": {
        # Seconds between snapshots. One value; nothing assumes five minutes.
        "interval_sec": 300,
        # The server drops a snapshot older than this.
        "max_age_sec": 900,
        # The page calls a symbol stale past this, during the session.
        "stale_after_sec": 660,
        # A snapshot stamped further ahead than this is refused.
        "max_future_skew_sec": 30,
        # A capture reading fewer contracts than this share of the most read lately
        # is not used: the PC measures against the most its ladder has read, the
        # server against the capture before. Not a share of the contracts tried.
        "min_fill_fraction": 0.8,
        # The floor against the contracts tried, for a first read with nothing to
        # compare with. Low: /NQ's uneven spacing lists 416 of 572 tried (0.73).
        "min_read_fraction": 0.25,
        # Captures in a row with the price, volume and open interest unchanged.
        "frozen_repeats": 3,
        # The page draws one column per row and repeats a row to this step, up to
        # one interval. At the interval itself nothing is repeated.
        "fill_step_sec": 300,
        # Two captures further apart than this have an outage between them. Below
        # the interval it reads as the interval.
        "outage_after_sec": 450,
        # The most blank columns one jump can add.
        "max_outage_columns": 288,
        # Between sessions: the last capture at least this long before expiry.
        "frozen_before_expiry_min": 60,
        # The stopped-capture alert repeats at most this often.
        "alert_repeat_min": 240,
        # A snapshot larger than this is refused.
        "max_snapshot_kb": 512,
        # The put-call check looks at this many strikes, nearest the price first,
        # for a call and a put that both have a mark.
        "parity_strikes": 5,
        # The rest are the PC's own: how it waits for ThinkorSwim and looks after
        # itself. They reach it in the plan.
        # How long a sheet's count of filled cells must hold, and the most it waits.
        "fill_settle_sec": 5,
        "fill_wait_max_sec": 20,
        # Where to look for a series: round strikes this many steps apart, this many
        # each side of the price.
        "probe_round_steps": 10,
        "probe_reach": 3,
        # The ladder is rewritten once the price has moved this share of the way to
        # its end.
        "recentre_share": 0.5,
        # A hung ssh is killed after this.
        "ssh_timeout_sec": 60,
        # The first retry after a failed plan fetch, doubling up to the second value.
        "no_plan_retry_sec": 60,
        "plan_retry_max_sec": 900,
        # No sign of life from the capture's loop for this long: its Excel is stopped.
        "hang_limit_sec": 600,
        # Offered in the Dealer Positioning list. Ships off.
        "show_in_dropdown": False,
    },
    "session": {"open_ct": "17:00", "close_ct": "15:00"},
    # Exactly these two. The symbol, the keys and the page follow code that knows
    # them, so a table for another root is not a feed and is ignored.
    "instruments": {"ES": _ES, "NQ": _NQ},
}

load, reset_cache = toml_loader(FUTURES_CHAIN_TOML, DEFAULTS, label="futures_chain.toml")


def _is_number(raw) -> bool:
    return isinstance(raw, (int, float)) and not isinstance(raw, bool)


def _table(section):
    table = load().get(section)
    return table if isinstance(table, dict) else {}


def _in_range(raw, default, minimum, maximum):
    """``raw`` as the default's own type when it is a number inside the range
    (a bool is refused: ``True`` is an int), else ``default``. A whole-number
    default also refuses a fraction: it counts something."""
    if not _is_number(raw):
        return default
    if isinstance(default, int) and raw != int(raw):
        return default
    value = type(default)(raw)
    return value if minimum <= value <= maximum else default


# Each capture value: (minimum, maximum), inclusive.
_CAPTURE_BOUNDS = {
    "interval_sec": (30, 3600),
    "max_age_sec": (60, 86400),
    "stale_after_sec": (60, 86400),
    "max_future_skew_sec": (0, 600),
    "min_fill_fraction": (0.05, 1.0),
    "frozen_repeats": (2, 100),
    "fill_step_sec": (10, 600),
    "outage_after_sec": (30, 86400),
    "max_outage_columns": (1, 2000),
    "frozen_before_expiry_min": (0, 1440),
    "alert_repeat_min": (5, 1440),
    "max_snapshot_kb": (16, 4096),
    "parity_strikes": (1, 50),
    "min_read_fraction": (0.01, 1.0),
    "fill_settle_sec": (1, 60),
    "fill_wait_max_sec": (5, 300),
    "probe_round_steps": (1, 200),
    "probe_reach": (1, 20),
    "recentre_share": (0.05, 1.0),
    "ssh_timeout_sec": (10, 600),
    "no_plan_retry_sec": (5, 3600),
    "plan_retry_max_sec": (30, 7200),
    "hang_limit_sec": (120, 7200),
}


def capture() -> dict:
    """The capture's numbers and its one switch. ``show_in_dropdown`` must be a
    real bool: a 1 or a "true" is the default."""
    table = _table("capture")
    out = {key: _in_range(table.get(key), DEFAULTS["capture"][key], lo, hi)
           for key, (lo, hi) in _CAPTURE_BOUNDS.items()}
    raw = table.get("show_in_dropdown")
    out["show_in_dropdown"] = (raw if isinstance(raw, bool)
                               else DEFAULTS["capture"]["show_in_dropdown"])
    return out


def _clock(raw, default) -> str:
    """``"H:MM"`` or ``"HH:MM"`` as ``"HH:MM"``, else ``default``."""
    if not isinstance(raw, str):
        return default
    hour, sep, minute = raw.strip().partition(":")
    if (not sep or not hour.isascii() or not hour.isdigit() or len(hour) > 2
            or not minute.isascii() or not minute.isdigit() or len(minute) != 2):
        return default
    if int(hour) > 23 or int(minute) > 59:
        return default
    return f"{int(hour):02d}:{minute}"


def session() -> dict:
    """``{"open_ct", "close_ct"}``, each ``"HH:MM"`` Central: when trading opens
    for an expiry and when that expiry stops."""
    table = _table("session")
    return {key: _clock(table.get(key), DEFAULTS["session"][key])
            for key in ("open_ct", "close_ct")}


def _word(raw, default, *, allow_week=False, longest=12):
    """A plain ASCII letters-and-digits word (and, for a series root, the ``{w}``
    week placeholder), else ``default``."""
    if not isinstance(raw, str):
        return default
    text = raw.strip()
    bare = text.replace("{w}", "") if allow_week else text
    if (not text or len(text) > longest or not bare.isascii()
            or not bare.isalnum()):
        return default
    return text


def _symbol(raw, default):
    """``FOP.<root>``: the app's ticker rule allows 8 characters, and the symbol
    becomes part of a Redis key name, so nothing else is read."""
    if not isinstance(raw, str):
        return default
    text = raw.strip()
    tail = text[4:]
    if (not text.startswith("FOP.") or len(text) > 8 or not tail
            or not tail.isascii() or not tail.isalnum() or tail != tail.upper()):
        return default
    return text


def _label(raw, default):
    if not isinstance(raw, str) or not raw.strip() or len(raw.strip()) > 12:
        return default
    return raw.strip()


def _step(raw, default, maximum):
    """A positive number of points (a whole number stays whole), else default."""
    if not _is_number(raw) or not 0 < raw <= maximum:
        return default
    return raw


def _weekday_roots(raw, default):
    """Exactly four roots, Monday to Thursday."""
    if not isinstance(raw, list) or len(raw) != 4:
        return list(default)
    roots = [_word(item, None, allow_week=True) for item in raw]
    return roots if None not in roots else list(default)


def _instrument(raw, default) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    return {
        "symbol": _symbol(raw.get("symbol"), default["symbol"]),
        "label": _label(raw.get("label"), default["label"]),
        "multiplier": _in_range(raw.get("multiplier"), default["multiplier"], 1, 10000),
        "exchange": _word(raw.get("exchange"), default["exchange"], longest=8),
        "strike_step": _step(raw.get("strike_step"), default["strike_step"], 1000),
        "strike_width_pct": _step(raw.get("strike_width_pct"),
                                  default["strike_width_pct"], 50),
        "parity_limit_pts": _step(raw.get("parity_limit_pts"),
                                  default["parity_limit_pts"], 100000),
        "weekday_roots": _weekday_roots(raw.get("weekday_roots"),
                                        default["weekday_roots"]),
        "friday_root": _word(raw.get("friday_root"), default["friday_root"],
                             allow_week=True),
        "eom_root": _word(raw.get("eom_root"), default["eom_root"], allow_week=True),
        "quarterly_root": _word(raw.get("quarterly_root"), default["quarterly_root"],
                                allow_week=True),
    }


def instruments() -> dict:
    """``{"ES": {...}, "NQ": {...}}``: each instrument's internal ``symbol``
    (``FOP.ES``), the page's ``label`` (``/ES``), its ``multiplier`` (dollars a
    point), ``exchange``, the ``strike_step`` and ``strike_width_pct`` the
    capture tries, the ``parity_limit_pts`` a call-minus-put check may miss by,
    and the series roots (``weekday_roots`` Monday to Thursday, ``friday_root``,
    ``eom_root``, ``quarterly_root``). A fresh copy: callers may edit it."""
    table = _table("instruments")
    return {name: _instrument(table.get(name), default)
            for name, default in DEFAULTS["instruments"].items()}

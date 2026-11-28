"""Trade-management stop/target rules, from ``config/trade_mgmt.toml``.

These are the trader's own risk rules - when to take profit, when to cut, how far
a short delta may drift - so they belong in a file, not in two Python modules.

**They were mirrored BY HAND across folders that cannot import each other.**
``services/options_svc/rescue.py`` opened with a comment reading "Mirror
signal_recommender stop constants so detection stays consistent with the
auto-close manage cycle", and then restated four of them:

    rescue delta_critical      0.45  <-  DELTA_HARD_CEILING
    rescue delta_drift         0.12  <-  DELTA_DRIFT
    rescue dte_urgent          2     <-  CUT_DTE
    rescue money_tested_mult   2.0   <-  STOP_MULT

A silent disagreement there means the at-risk board flags a position the manage
cycle will not act on, or worse, stays quiet about one it will close. So
``rescue_thresholds()`` here DERIVES those four from ``[stops]`` rather than
listing them again - the mirror is now structural instead of clerical, and the
escalation bands rescue owns alone (warn levels, proximity, dte_manage) stay in
their own section.

``[structures.*]`` is the SECOND half of the same argument. The rules above are
written for a credit spread, and for the Income Window's two single-leg
structures the loss-side ones are not merely unproven but inverted - a covered
call losing 2x its credit is the stock rallying, and a cash-secured put's delta
stop fires exactly when assignment, which is the wheel's plan, becomes likely.
A2 shipped that as a hardcoded tuple in ``signal_recommender``; it is config now,
because it is a trading rule and not a fact about the code. See
``structure_rules()`` and docs/plans/2026-09-11-income-exit-rules-design.md.

Missing file / bad TOML / missing key -> the built-in defaults, never a raise.
"""
from repo_paths import TRADE_MGMT_TOML
from shared import structures as _structures
from shared.config_toml import toml_loader

DEFAULTS = {
    "stops": {
        # >= 50% of the credit captured -> ARM the break-even stop. Note this
        # arms a stop; it is not an immediate close.
        "tp_frac": 0.50,
        "stop_mult": 2.0,              # cut at >= 2x credit loss
        "delta_drift": 0.12,           # cut when short delta drifts this far past entry
        "delta_hard_ceiling": 0.45,    # ...but never hold past this, whatever the entry
        "delta_abs_fallback": 0.35,    # absolute breach when entry delta is unknown
        "cut_dte": 2,                  # cut when DTE <= this and underwater
        # ...but not in a trade's first this-many minutes, for a caller that
        # says how long it has been held (captured signals). A fresh spread is
        # "underwater" by its bid/ask alone, so without this a capture entered
        # at DTE <= cut_dte was closed on its first mark (2026-11-13: 47 of 78
        # Sep-Nov time stops within 15 minutes, median hold 6 minutes).
        "time_stop_min_hold_min": 30,
        "recovery_dte_min": 5,         # min DTE to DEFER a soft delta stop
        "recovery_min_cushion": 0.015,  # min spot<->short-strike cushion to defer
    },
    "trail": {
        # Which ladder is in force: "default" (a single break-even rung - today's
        # behaviour) or "ratchet". See active_trail_ladder() for the measurement.
        "active": "ratchet",
        # Peak-driven profit-lock ladder for the armed break-even stop. Each rung
        # is [peak_frac, lock_frac]: once PEAK profit reaches peak_frac of the
        # credit, the stop ratchets to lock in lock_frac of it.
        #
        # The default is a single break-even rung (lock 0.0) - i.e. exactly the
        # plain break-even stop - so the ratchet is INERT until a caller passes a
        # richer ladder plus peak_pnl_frac in ctx.
        "default_ladder": [[0.50, 0.0]],
        "ratchet_ladder": [[0.50, 0.0], [0.65, 0.25], [0.80, 0.50]],
    },
    "protect": {
        # The profit-protecting trail (docs/plans/2026-11-15-profit-trail-design.md).
        # Once a trade's BEST profit reaches trail_start of its base (the credit;
        # for a debit, the base its target is measured on), it is closed when its
        # profit falls trail_giveback of that base below the best. Never below
        # break-even plus commissions. 0 for trail_start turns the trail off.
        "trail_start": 0.25,
        "trail_giveback": 0.10,
        # Central-time windows in which the PROTECTIVE stops (break-even, the
        # ladder's lock, the trail) wait: the opening and closing quotes are wide,
        # and a mark priced off them read as a loss the stock never made. The
        # money, time and delta stops are not affected.
        "quiet_windows": ["08:30-08:45", "14:45-15:00"],
        # What a captured signal does on reaching [stops] tp_frac: "close" takes
        # the profit, "ride" arms break-even and rides under the [trail] ladder.
        "captured_at_target": "close",
        # Which books the trail and the quiet windows apply to.
        "captured": True,
        "account": True,
        "ledger": True,
        # The Ledger's self-managed rows (trade ideas, the daily condor, shadow
        # ideas), apart from the operator's own Ledger trades.
        "ledger_self_managed": True,
        # The operator's own Ledger CREDIT trades also take the profit at the
        # structure's tp_frac (+50%). They still have no money, time or delta stop.
        "ledger_take_profit": True,
    },
    "rescue": {
        # Escalation bands the rescue board owns on its own. The four values it
        # SHARES with [stops] are not repeated here - see rescue_thresholds().
        "delta_warn": 0.30,
        "money_warn_mult": 1.0,          # x entry credit (loss)
        "money_critical_mult": 3.0,
        "dte_manage": 21,
        "proximity_watch_pct": 0.03,     # underlying within 3% of the short strike
        "proximity_tested_pct": 0.01,
        # How far a roll out moves the expiry: the LISTED expiry nearest this
        # many days past the position's own (rescue._roll_expiry).
        "roll_out_days": 30,
    },
    "structures": {
        # Per-structure OVERLAYS on [stops]. Each table names only what differs;
        # everything else is inherited, so an unlisted structure gets the global
        # rules with every loss-side stop on - which is what every spread
        # already did.
        #
        # Keyed on structures.canonical(), so the short put's two spellings
        # (SHORT_PUT / NAKED_PUT) cannot be given different rules.
        "SHORT_PUT": {"loss_rules": False, "manage_dte": 21},
        "COVERED_CALL": {"loss_rules": False, "manage_dte": 21},
        # The ledger's four DEBIT structures (D3). ``exit_dte`` is the time exit;
        # the loss side is governed by ``debit_stop_frac``, which is OFF, and NOT
        # by ``loss_rules`` (a credit-side key the debit path never reads).
        "LONG_CALL": {"exit_dte": 21},
        "LONG_PUT": {"exit_dte": 21},
        "BULL_CALL": {"exit_dte": 21},
        "BEAR_PUT": {"exit_dte": 21},
        # The Strategy Finder's long butterflies and condors are paper-tradeable
        # but deliberately have NO table - no time exit (operator decision
        # 2026-09-13). They gain most of their value in the last two weeks: a
        # 95/100/105 call fly at spot 100 is $1.20 at 30 DTE, $1.42 at 21, and
        # reaches its ~$3.10 target only near 3 DTE, so a 21-DTE exit would close
        # every 22-30 DTE entry flat. They keep the tp_frac target on max profit
        # and settle at expiry. See config/trade_mgmt.toml. No straddle/strangle
        # either - analysis only (D1).
        #
        # Tracked only (structure_marks). A backspread entered for a credit
        # sits in a small, DEFINED loss until the large move it is for, so the
        # 2x-credit money stop would close every one of them in that valley.
        "CALL_BACKSPREAD": {"loss_rules": False},
        "PUT_BACKSPREAD": {"loss_rules": False},
    },
    # The TRACKED structures' manage loop (services/options_svc/tracked.py):
    # rows recorded to be measured, never traded. See config/trade_mgmt.toml.
    "tracked": {
        "mark_interval_min": 15,
        "mark_offset_min": 10,
        "front_expiry_close": "14:00",
    },
}

# Rules that exist ONLY per structure - they have no [stops] counterpart, so the
# overlay needs its own defaults for them.
STRUCTURE_DEFAULTS = {
    # Do the loss-side rules (money stop, time stop, delta stop) apply at all?
    "loss_rules": True,
    # Close a PROFITABLE position at or below this DTE (playbook X5: manage
    # premium selling at 21 days, where gamma starts to dominate). None = off,
    # which is every spread: they already have cut_dte + the delta stops, and a
    # 21-DTE close for spreads is a separate, measurable change.
    "manage_dte": None,
    # ── DEBIT-only keys (D3) ────────────────────────────────────────────────
    # Time exit for a long option / debit vertical: close at or below this DTE
    # whether the trade is up or down. ⚠ Unlike manage_dte it is NOT
    # profit-conditional (the sourced rule is to be out before the final weeks
    # either way) and it fires ONLY when dte_at_entry was greater - see
    # signal_recommender._recommend_debit. None = off, which is every credit
    # structure.
    "exit_dte": None,
    # OPTIONAL percent-of-debit stop: cut once this fraction of the debit paid
    # is gone. None = OFF, and that is the SOURCED default - the practitioner
    # sources close debit spreads before expiry rather than stopping them out,
    # so any level here would be invention. This is the debit path's ONLY
    # loss-side knob; `loss_rules` and `stop_mult` are credit-denominated and
    # are not read for a debit (2x a debit is a loss that cannot happen).
    "debit_stop_frac": None,
}

load, reset_cache = toml_loader(TRADE_MGMT_TOML, DEFAULTS, label="trade_mgmt.toml")


def _section(name):
    sec = load().get(name)
    return sec if isinstance(sec, dict) else DEFAULTS[name]


def stops() -> dict:
    return _section("stops")


def _ladder(key):
    """A TOML array-of-arrays -> the list[tuple] the recommender expects. A
    malformed rung is dropped rather than crashing the manage cycle."""
    raw = _section("trail").get(key) or DEFAULTS["trail"][key]
    out = []
    for rung in raw:
        try:
            peak, lock = rung
            out.append((float(peak), float(lock)))
        except Exception:
            continue
    return out or [(float(r[0]), float(r[1])) for r in DEFAULTS["trail"][key]]


#: Ladder names ``[trail].active`` may take. A NAME rather than a fourth array,
#: so both ladders stay side by side in the TOML and reverting is one word.
_LADDER_KEYS = {"default": "default_ladder", "ratchet": "ratchet_ladder"}


def active_trail_ladder():
    """The profit-lock ladder in force, from ``[trail].active``.

    ⚠ **An unknown or malformed name falls back to the INERT default ladder**, not
    to the richer one. A typo in a risk config must never silently switch on an
    untried policy - the same direction ``structure_rules`` takes when a
    ``[structures.*]`` table is junk.

    Measured over 281 closed captured signals replayed on their own mark series,
    the ratchet beats the plain break-even stop it replaces by +$383 (+4.7%) at a
    10% slippage haircut, and wins in every month and both scanner types. It
    reverses only at an implausible 50% haircut. Structurally it cannot increase
    loss exposure: ``signal_recommender`` rule 3 takes
    ``max(be_level, _locked_profit_level(...))``, so a lock only ever raises a stop
    that is already above break-even.
    """
    name = _section("trail").get("active")
    key = _LADDER_KEYS.get(name) if isinstance(name, str) else None
    return _ladder(key or "default_ladder")


def default_trail_ladder():
    return _ladder("default_ladder")


def ratchet_trail_ladder():
    return _ladder("ratchet_ladder")


#: The books ``[protect]`` can switch on or off by name.
PROTECT_BOOKS = ("captured", "account", "ledger", "ledger_self_managed")

#: What ``captured_at_target`` may say.
_AT_TARGET = ("close", "ride")


def _parse_window(text):
    """``"08:30-08:45"`` -> ``(time(8, 30), time(8, 45))``, or None."""
    import datetime as _dt
    try:
        a, b = str(text).split("-")
        ha, ma = a.strip().split(":")
        hb, mb = b.strip().split(":")
        start, end = _dt.time(int(ha), int(ma)), _dt.time(int(hb), int(mb))
    except (ValueError, TypeError):
        return None
    return (start, end) if start < end else None


def protect() -> dict:
    """The profit-protecting trail's settings, closed over the defaults.

    ``{"trail_start", "trail_giveback", "quiet_windows", "captured_at_target",
    "captured", "account", "ledger", "ledger_self_managed",
    "ledger_take_profit"}``. ``quiet_windows``
    is a list of ``(start, end)`` Central ``datetime.time`` pairs.

    A bad value is the SHIPPED one, never a looser one: a fraction outside 0..1,
    a give-back of 0 or more than the start (which would put the stop at or above
    the best profit and close every trade the mark after it armed), an unknown
    ``captured_at_target``, or a non-boolean book switch.
    """
    d = DEFAULTS["protect"]
    sec = load().get("protect")
    sec = sec if isinstance(sec, dict) else {}

    def _frac(key):
        v = sec.get(key, d[key])
        ok = (isinstance(v, (int, float)) and not isinstance(v, bool)
              and 0.0 <= float(v) <= 1.0)
        return float(v) if ok else float(d[key])

    start, give = _frac("trail_start"), _frac("trail_giveback")
    if start > 0 and not (0 < give < start):
        start, give = float(d["trail_start"]), float(d["trail_giveback"])

    raw = sec.get("quiet_windows", d["quiet_windows"])
    if not isinstance(raw, list):
        raw = d["quiet_windows"]
    windows = [w for w in (_parse_window(x) for x in raw) if w]
    if raw and len(windows) != len(raw):
        windows = [_parse_window(x) for x in d["quiet_windows"]]

    at = sec.get("captured_at_target", d["captured_at_target"])
    out = {"trail_start": start, "trail_giveback": give, "quiet_windows": windows,
           "captured_at_target": at if at in _AT_TARGET else d["captured_at_target"]}
    for book in PROTECT_BOOKS + ("ledger_take_profit",):
        v = sec.get(book, d[book])
        out[book] = v if isinstance(v, bool) else d[book]
    return out


def in_quiet_window(when, windows=None) -> bool:
    """Is ``when`` (a datetime; naive means Central) inside a quiet window?

    ``None`` is never inside one: a caller that does not say when the mark was
    taken gets the stops it always had.
    """
    if when is None:
        return False
    try:
        from zoneinfo import ZoneInfo
        ct = ZoneInfo("America/Chicago")
        local = when.astimezone(ct) if when.tzinfo else when
        t = local.time().replace(tzinfo=None)
    except (AttributeError, ValueError, TypeError):
        return False
    wins = protect()["quiet_windows"] if windows is None else windows
    return any(a <= t < b for a, b in wins)


def tracked() -> dict:
    """``{"mark_interval_min", "mark_offset_min", "front_expiry_close"}`` for the
    tracked structures' manage loop; ``front_expiry_close`` is a
    ``datetime.time`` (Central).

    Closed over the defaults: an unusable value is the shipped one. The interval
    is 1..60 minutes and the offset is taken modulo it, so no setting can make
    the loop never run.
    """
    import datetime as _dt
    d = DEFAULTS["tracked"]
    sec = load().get("tracked")
    sec = sec if isinstance(sec, dict) else {}

    def _int(key, lo, hi):
        v = sec.get(key, d[key])
        ok = isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi
        return v if ok else d[key]

    interval = _int("mark_interval_min", 1, 60)
    offset = _int("mark_offset_min", 0, 59) % interval

    def _time(text):
        try:
            hh, mm = str(text).split(":")
            return _dt.time(int(hh), int(mm))
        except (ValueError, TypeError):
            return None

    close = _time(sec.get("front_expiry_close", d["front_expiry_close"]))
    return {"mark_interval_min": interval, "mark_offset_min": offset,
            "front_expiry_close": close or _time(d["front_expiry_close"])}


def structure_rules(strategy) -> dict:
    """The effective rule set for ONE structure: ``[stops]`` overlaid by
    ``[structures.<canonical name>]``, plus the two per-structure-only keys
    ``loss_rules`` and ``manage_dte``.

    An unlisted structure - and a caller with no ``strategy`` at all - resolves
    to ``[stops]`` unchanged with every loss-side rule ON, so the table is purely
    additive and the pre-B1 callers are unaffected.

    Not memoised into a module constant on purpose: unlike every other accessor
    here the answer DEPENDS ON THE POSITION, so ``recommend()`` resolves it per
    call. ``load()`` is mtime-cached, so that costs a dict merge.
    """
    base = {**STRUCTURE_DEFAULTS, **stops()}
    name = _structures.canonical(strategy)
    over = _section("structures").get(name)
    if not isinstance(over, dict):
        # A junk table falls back to the SHIPPED one rather than to the global
        # rules: degrading the other way would re-arm the money stop on a covered
        # call because of a typo, which is the rule this table exists to remove.
        over = DEFAULTS["structures"].get(name)
    if not isinstance(over, dict):
        return base
    return {**base, **over}


def rescue_thresholds() -> dict:
    """The rescue board's escalation map.

    The four values shared with the manage cycle are READ FROM ``[stops]``, so
    the two can no longer drift; the rest are rescue's own.
    """
    st, rs = stops(), _section("rescue")
    return {
        "delta_warn": rs["delta_warn"],
        "delta_critical": st["delta_hard_ceiling"],   # <- shared
        "delta_drift": st["delta_drift"],             # <- shared
        "money_warn_mult": rs["money_warn_mult"],
        "money_tested_mult": st["stop_mult"],         # <- shared
        "money_critical_mult": rs["money_critical_mult"],
        "dte_manage": rs["dte_manage"],
        "dte_urgent": st["cut_dte"],                  # <- shared
        "proximity_watch_pct": rs["proximity_watch_pct"],
        "proximity_tested_pct": rs["proximity_tested_pct"],
    }


def rescue_roll_out_days() -> int:
    """Days a Rescue roll out aims past the position's expiry. The roll lands on
    the LISTED expiry nearest that date, so this is a target, not the gap. A
    value that is not a positive whole number falls back to the default."""
    raw = _section("rescue").get("roll_out_days", DEFAULTS["rescue"]["roll_out_days"])
    try:
        days = int(raw)
    except (TypeError, ValueError):
        return DEFAULTS["rescue"]["roll_out_days"]
    return days if days > 0 and not isinstance(raw, bool) else DEFAULTS["rescue"]["roll_out_days"]

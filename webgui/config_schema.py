"""The catalogue behind Settings -> Configuration.

Every key in the editable ``config/*.toml`` files is described here once: a
plain-English label, a sentence of help, its unit, the range the editor accepts,
and which services must restart before a change takes effect. The page
(``pages/config_editor.py``) renders from this and nothing else, so adding a
setting means adding its TOML key AND an entry here -- ``tests/test_config_schema``
fails on a key that has no entry, which is how the standing "configurable by
default" rule (CLAUDE.md) stays visible in the app rather than only in a file.

PURE DATA + pure helpers. No NiceGUI, no I/O. Tier 1 allow-list: nothing new.

Kinds
    int / float     a plain number (``unit`` is only a suffix)
    money           dollars
    fraction        stored as a fraction (0.12), shown and typed as a percent (12)
    bool            on / off
    time            "HH:MM", Central time unless the section says otherwise
    date            "YYYY-MM-DD"
    choice          one of ``choices``
    text            a short piece of text (a tab name)
    symbols         a list of tickers
    pair            two numbers [low, high]
    ladder          a list of [peak, lock] rungs (fractions, shown as percents)
    sector          one of the sector names (the sector-map editor)
"""
from __future__ import annotations

from dataclasses import dataclass

# Unit names of the systemd user units a change needs restarted, as the Status
# page spells them (``trading-<env>-<name>``). "timers" is not a unit: it means
# "regenerate the scheduled timers" (deploy.systemd.generate_units --install).
OPTIONS = "options_svc"
SENTIMENT = "sentiment_svc"
MARKET = "market_svc"
NEWS = "news_svc"
TRADE = "trade_svc"
WEBGUI = "webgui"
TIMERS = "timers"

RESTART_LABELS = {
    OPTIONS: "Options service",
    SENTIMENT: "Sentiment service",
    MARKET: "Market service",
    NEWS: "News service",
    TRADE: "Trade service",
    WEBGUI: "Web app (this page reloads)",
    TIMERS: "Scheduled timers (regenerated, no restart)",
}

SECTORS = (
    "Communication Services", "Consumer Discretionary", "Consumer Staples",
    "Energy", "Financials", "Health Care", "Industrials",
    "Information Technology", "Materials", "Real Estate", "Utilities", "INDEX",
)


@dataclass(frozen=True)
class Field:
    key: str                  # dotted path in the file; one "*" segment allowed
    label: str
    help: str = ""
    kind: str = "float"
    unit: str = ""
    min: float | None = None
    max: float | None = None
    step: float | None = None
    choices: tuple = ()
    optional: bool = False    # may be absent from the file: unset = off / inherited
    restart: tuple | None = None   # overrides the section's / file's restart list


@dataclass(frozen=True)
class Section:
    title: str
    help: str
    fields: tuple
    restart: tuple | None = None   # overrides the file's restart list
    # Shown, never edited: the page draws the value with no input, and the save
    # path never writes it (see ``is_readonly`` and
    # ``pages/config_editor.overrides_to_save``). A hand-written override of it
    # in config/local is carried through a save untouched.
    readonly: bool = False


@dataclass(frozen=True)
class ConfigFile:
    name: str                 # "scanner.toml"
    title: str                # the category name the operator sees
    icon: str
    summary: str
    restart: tuple
    sections: tuple
    editable: bool = True
    editor: str = "fields"    # "fields" | "sectors" | "readonly"
    caution: str = ""


def _pct(key, label, help, lo=0.0, hi=100.0, step=0.5, **kw):
    return Field(key, label, help, kind="fraction", unit="%", min=lo, max=hi,
                 step=step, **kw)


# ─────────────────────────────────────────────────────────────────────────────
# Trade selection — config/scanner.toml
# ─────────────────────────────────────────────────────────────────────────────
_SCANNER = ConfigFile(
    name="scanner.toml", title="Trade selection", icon="filter_alt",
    summary="Which signals the scanner lets through at all: volatility floors, "
            "minimum credit, and the score a signal needs to be recorded.",
    restart=(OPTIONS,),
    caution="Loosening these produces more signals, including cheaper premium. "
            "The IV-rank floors are the usual reason index names show no signals.",
    sections=(
        Section("Volatility floor (IV rank)",
                "A trade that SELLS premium is refused when the symbol's IV rank "
                "is below this. 0 turns the floor off for that trade type.", (
            Field('iv_rank."0-DTE"', "0-DTE trades (0–4 days)",
                  "Minimum IV rank to sell premium in the 0–4 day window.",
                  kind="int", min=0, max=100, step=1),
            Field("iv_rank.SWING", "Swing trades (5–15 days)",
                  "Minimum IV rank to sell premium in the swing window.",
                  kind="int", min=0, max=100, step=1),
            Field("iv_rank.INCOME", "Income window (30–45 days)",
                  "Minimum IV rank for the Income board's credit spreads and "
                  "cash-secured puts.", kind="int", min=0, max=100, step=1),
        )),
        Section("Volatility ceiling (IV rank)",
                "A trade that BUYS premium is refused when IV rank is above this "
                "— don't buy expensive volatility. 0 means off (shipped off).", (
            Field('iv_rank_ceiling."0-DTE"', "0-DTE trades", "", kind="int",
                  min=0, max=100, step=1),
            Field("iv_rank_ceiling.SWING", "Swing trades", "", kind="int",
                  min=0, max=100, step=1),
            Field("iv_rank_ceiling.INCOME", "Income window", "", kind="int",
                  min=0, max=100, step=1),
        )),
        Section("Minimum credit",
                "The smallest credit accepted, as a percent of the spread's "
                "width. The 0-DTE floor rises with the VIX regime.", (
            _pct("credit.swing", "Swing spreads", "Credit ÷ width for 1–15 day spreads.",
                 hi=60),
            _pct("credit.zero_dte.LOW", "0-DTE, VIX low", "", hi=60),
            _pct("credit.zero_dte.NORMAL", "0-DTE, VIX normal", "", hi=60),
            _pct("credit.zero_dte.ELEVATED", "0-DTE, VIX elevated", "", hi=60),
            _pct("credit.zero_dte.HIGH", "0-DTE, VIX high", "", hi=60),
        )),
        Section("Directional credit spreads",
                "Spreads placed closer to the price because the scanner has a "
                "directional view: they pay more and carry a higher floor.", (
            _pct("directional.min_credit_pct", "Minimum credit",
                 "Credit ÷ width.", hi=80),
            _pct("directional.max_risk_pct", "Account risk per trade",
                 "Maximum loss per directional trade, as a percent of the account.",
                 hi=20, step=0.25),
            Field("directional.max_per_symbol_bucket", "Maximum per symbol",
                  "How many directional spreads one symbol may show per window.",
                  kind="int", min=0, max=20, step=1),
            Field("directional.pcs_delta", "Put credit spread — short delta band",
                  "Short-put delta range, low to high (negative numbers).",
                  kind="pair", min=-1.0, max=0.0, step=0.01),
            Field("directional.ccs_delta", "Call credit spread — short delta band",
                  "Short-call delta range, low to high.",
                  kind="pair", min=0.0, max=1.0, step=0.01),
        )),
        Section("Single options (long and short calls and puts)",
                "The Directional tab's single-leg trades.", (
            Field("single_leg.max_per_symbol", "Maximum per symbol",
                  "Four structures × two windows is 8.", kind="int", min=0,
                  max=40, step=1),
            Field("single_leg.min_score", "Minimum score",
                  "Fit + Quality score a single-leg trade needs (0–100).",
                  kind="float", min=0, max=100, step=1),
            Field("single_leg.excluded_grades", "Grades never shown",
                  "Grades removed from the list outright.", kind="symbols"),
        )),
        Section("Score thresholds",
                "Scores run 0–100. A signal must clear the capture score to be "
                "recorded (and so to be traded by the paper account).", (
            Field("scores.capture_min", "Capture score (0-DTE and swing)",
                  "Minimum score to record a signal.", kind="int", min=0,
                  max=100, step=1),
            Field("scores.capture_min_income", "Capture score (income window)",
                  "0 records whatever the Income board offered; the board already "
                  "cuts below the Strategy Finder score.", kind="int", min=0,
                  max=100, step=1),
            Field("scores.neg_gex_min", "Index score in a negative-gamma market",
                  "Index premium spreads need this higher score when dealer gamma "
                  "is mildly negative.", kind="int", min=0, max=100, step=1),
            Field("scores.gex_strong_neg", "Strongly negative gamma cut-off",
                  "Below this dealer-gamma reading, index premium is skipped.",
                  kind="float", min=-5, max=0, step=0.05),
            Field("scores.swing_min", "Strategy Finder minimum score",
                  "Candidates below this are not shown.", kind="float", min=0,
                  max=100, step=1),
        )),
        Section("Captured signals", "", (
            Field("capture.max_open_per_symbol", "Open captured signals per symbol",
                  "Across 0-DTE, swing and income together. 0 turns the cap off.",
                  kind="int", min=0, max=50, step=1),
        )),
    ),
)

# ─────────────────────────────────────────────────────────────────────────────
# Exits and risk management — config/trade_mgmt.toml
# ─────────────────────────────────────────────────────────────────────────────
_STRUCT_LABELS = {
    "SHORT_PUT": "Cash-secured put", "COVERED_CALL": "Covered call",
    "LONG_CALL": "Long call", "LONG_PUT": "Long put",
    "BULL_CALL": "Bull call (debit) spread", "BEAR_PUT": "Bear put (debit) spread",
}

_TRADE_MGMT = ConfigFile(
    name="trade_mgmt.toml", title="Exits & trade management", icon="logout",
    summary="When a paper position takes its profit, when it is cut, and when the "
            "Rescue board starts flagging it.",
    restart=(OPTIONS,),
    caution="These close real positions in the paper books on the next manage "
            "cycle.",
    sections=(
        Section("Profit and loss rules (credit spreads)",
                "Apply to every credit structure unless a per-structure rule "
                "below overrides them.", (
            _pct("stops.tp_frac", "Take profit at",
                 "Percent of the credit captured. Captured signals arm a break-even "
                 "stop here; the manual book closes outright.",
                 lo=5, hi=100, step=5),
            Field("stops.stop_mult", "Stop loss at",
                  "Cut when the loss reaches this multiple of the credit received.",
                  kind="float", unit="× credit", min=0.25, max=10, step=0.25),
            Field("stops.cut_dte", "Time stop",
                  "Cut a LOSING position at or below this many days to expiry.",
                  kind="int", unit="days", min=0, max=30, step=1),
        )),
        Section("Delta stops (the short leg)", "", (
            Field("stops.delta_drift", "Delta drift allowed",
                  "Cut when the short delta has risen this much since entry.",
                  kind="float", min=0.01, max=1, step=0.01),
            Field("stops.delta_hard_ceiling", "Hard delta ceiling",
                  "Cut at this absolute short delta, whatever the entry was.",
                  kind="float", min=0.05, max=1, step=0.01),
            Field("stops.delta_abs_fallback", "Fallback ceiling",
                  "Used when a position's entry delta was never recorded.",
                  kind="float", min=0.05, max=1, step=0.01),
            Field("stops.recovery_dte_min", "Defer a delta stop if at least",
                  "…this many days remain (and the cushion below holds).",
                  kind="int", unit="days", min=0, max=60, step=1),
            _pct("stops.recovery_min_cushion", "…and the price is at least",
                 "…this far from the short strike.", hi=20, step=0.1),
        )),
        Section("Profit-lock ladder",
                "After take-profit arms a break-even stop, the ladder can ratchet "
                "the stop up as the peak profit grows.", (
            Field("trail.active", "Ladder in force",
                  "\"ratchet\" locks profit in steps; \"default\" is a plain "
                  "break-even stop.", kind="choice", choices=("ratchet", "default")),
            Field("trail.ratchet_ladder", "Ratchet rungs",
                  "Each rung: once PEAK profit reaches the first percent of the "
                  "credit, lock in the second.", kind="ladder"),
            Field("trail.default_ladder", "Default rungs",
                  "The plain break-even stop, as a ladder.", kind="ladder"),
        )),
        Section("Per-structure rules",
                "Override the rules above for one structure. Blank means "
                "inherited or off.", tuple(
            f for s, lbl in _STRUCT_LABELS.items() for f in (
                Field(f"structures.{s}.loss_rules", f"{lbl} — loss stops on",
                      "Off = no money, time or delta stop (the wheel's plan for "
                      "income structures).", kind="bool", optional=True),
                Field(f"structures.{s}.manage_dte", f"{lbl} — close winners at",
                      "Close a PROFITABLE position at or below this many days.",
                      kind="int", unit="days", min=0, max=90, step=1, optional=True),
                Field(f"structures.{s}.exit_dte", f"{lbl} — time exit at",
                      "Debit trades: close at this many days to expiry, win or lose.",
                      kind="int", unit="days", min=0, max=90, step=1, optional=True),
                Field(f"structures.{s}.debit_stop_frac", f"{lbl} — debit stop",
                      "Debit trades: cut once this percent of the debit paid is gone.",
                      kind="fraction", unit="%", min=5, max=100, step=5,
                      optional=True),
                _pct(f"structures.{s}.tp_frac", f"{lbl} — take profit at",
                     "Overrides the global take-profit for this structure.",
                     lo=5, hi=100, step=5, optional=True),
            ))),
        Section("Rescue board warnings",
                "When an open position is flagged at risk. These only colour the "
                "board; they close nothing.", (
            Field("rescue.delta_warn", "Warn at short delta", "",
                  kind="float", min=0.05, max=1, step=0.01),
            Field("rescue.money_warn_mult", "Warn at a loss of", "",
                  kind="float", unit="× credit", min=0.1, max=10, step=0.1),
            Field("rescue.money_critical_mult", "Critical at a loss of", "",
                  kind="float", unit="× credit", min=0.1, max=20, step=0.1),
            Field("rescue.dte_manage", "Manage window",
                  "Days to expiry at which a position enters the manage band.",
                  kind="int", unit="days", min=0, max=90, step=1),
            _pct("rescue.proximity_watch_pct", "Watch when price is within",
                 "…this far of the short strike.", hi=20, step=0.1),
            _pct("rescue.proximity_tested_pct", "Tested when price is within",
                 "…this far of the short strike.", hi=20, step=0.1),
        )),
    ),
)

# ─────────────────────────────────────────────────────────────────────────────
# Flow alerts — config/flow_alerts.toml
# ─────────────────────────────────────────────────────────────────────────────
_FLOW = ConfigFile(
    name="flow_alerts.toml", title="Flow alerts", icon="notifications_active",
    summary="The options-flow detectors behind Flow Alerts and the phone pushes.",
    restart=(OPTIONS,),
    sections=(
        Section("Master switch", "", (
            Field("enabled", "Flow alerts on",
                  "Off stops every detector and every flow push.", kind="bool"),
        )),
        Section("Premium crossover",
                "Fires when call premium overtakes put premium (or the reverse).", (
            _pct("crossover.band", "Lead required",
                 "The crossing side must lead by this percent of the larger side.",
                 hi=50, step=0.5),
            Field("crossover.cooldown_min", "Quiet period per symbol", "",
                  kind="int", unit="min", min=0, max=1440, step=5),
            Field("crossover.min_premium", "Ignore until premium reaches", "",
                  kind="money", min=0, max=1e9, step=1000),
        )),
        Section("Unusual activity", "Volume far above open interest.", (
            Field("uoa.k", "Volume at least", "", kind="float",
                  unit="× open interest", min=0.5, max=100, step=0.5),
            Field("uoa.vol_floor", "Minimum volume", "", kind="int",
                  unit="contracts", min=0, max=1e7, step=100),
            Field("uoa.premium_floor", "Minimum premium", "", kind="money",
                  min=0, max=1e10, step=100000),
            Field("uoa.top_n", "Contracts reported per symbol", "", kind="int",
                  min=1, max=50, step=1),
        )),
        Section("Gamma flip", "When price crosses the dealer gamma flip level.", (
            Field("gamma_flip.enabled", "Gamma-flip alerts on", "", kind="bool"),
            _pct("gamma_flip.band_pct", "Dead zone around the flip",
                 "Price must clear the flip by this much to switch regime.",
                 hi=5, step=0.01),
            Field("gamma_flip.cooldown_min", "Quiet period per symbol", "",
                  kind="int", unit="min", min=0, max=1440, step=5),
            Field("gamma_flip.symbols", "Symbols watched",
                  "Leave empty to watch the whole flow universe.", kind="symbols"),
        )),
        Section("Big delta", "One contract carrying a large share of a symbol's "
                "delta-dollars.", (
            Field("big_delta.enabled", "Big-delta detector on", "", kind="bool"),
            Field("big_delta.push", "Send big-delta pushes to the phone",
                  "Off = Flow screen only.", kind="bool"),
            _pct("big_delta.push_threshold", "Push when share of symbol is at least",
                 "A separate, higher bar for the phone.", hi=100, step=1),
            _pct("big_delta.rel_threshold", "Show when share of symbol is at least",
                 "The bar for appearing on the Flow screen.", hi=100, step=1),
            Field("big_delta.min_contract_notional", "…and delta-dollars at least",
                  "Filters out a big share of a thin name.", kind="money", min=0,
                  max=1e11, step=1000000),
            Field("big_delta.delta_lo", "Ignore delta below", "", kind="float",
                  min=0, max=1, step=0.01),
            Field("big_delta.delta_hi", "Ignore delta above", "", kind="float",
                  min=0, max=1, step=0.01),
            Field("big_delta.delta_max", "Reject delta above (bad data)", "",
                  kind="float", min=0, max=2, step=0.05),
            Field("big_delta.top_n", "Contracts reported per symbol", "",
                  kind="int", min=1, max=50, step=1),
        )),
    ),
)

# ─────────────────────────────────────────────────────────────────────────────
# Market hours & schedules — config/sessions.toml
# ─────────────────────────────────────────────────────────────────────────────
_ALL_SVC = (OPTIONS, SENTIMENT, MARKET, WEBGUI)


def _window(name, title, help, restart, *, tz_note="", extra=()):
    return Section(title, help + tz_note, (
        Field(f"windows.{name}.start", "Starts", "", kind="time"),
        Field(f"windows.{name}.end", "Ends", "", kind="time"),
    ) + tuple(extra), restart=restart)


def _after_hours(name, what):
    return Field(f"windows.{name}.after_hours", "Also run outside these hours",
                 f"On: {what} still run outside the hours, and the page warns "
                 "that bid, ask and mark may be stale or wrong. Off: they are "
                 "refused until the hours open.", kind="bool")


_SESSIONS = ConfigFile(
    name="sessions.toml", title="Market hours & schedules", icon="schedule",
    summary="Session times, the operating windows each service works in, and the "
            "clock times of every scheduled job. All times are Central (CT).",
    restart=_ALL_SVC,
    caution="Scheduled Claude briefings are paid calls, and the income scan is the "
            "largest scheduled Schwab spend: moving or adding slots changes cost.",
    sections=(
        Section("Extended hours",
                "Cboe extended trading hours for multi-listed equity options.", (
            Field("activation.extended_hours_from", "Extended hours begin on",
                  "Every extended-hours behaviour is off before this date.",
                  kind="date"),
            Field("alerts.fire_in_extended_hours", "Alert during extended hours",
                  "Phone pushes and toasts outside the regular session.",
                  kind="bool", restart=(OPTIONS,)),
        )),
        Section("Sessions", "The market's own sessions (CT).", (
            Field("sessions.gth.start", "Global trading hours start", "", kind="time"),
            Field("sessions.gth.end", "Global trading hours end", "", kind="time"),
            Field("sessions.regular.start", "Regular session opens", "", kind="time"),
            Field("sessions.regular.end", "Regular session closes", "", kind="time"),
            Field("sessions.curb.start", "Curb session start", "", kind="time"),
            Field("sessions.curb.end", "Curb session end", "", kind="time"),
        )),
        _window("scan", "Auto-scan window",
                "When the options service runs its 15-minute rescans.",
                (OPTIONS, WEBGUI)),
        Section("Gamma collection window",
                "When the 1-minute GEX collector runs. Every minute costs one "
                "chain call per symbol.", (
            Field("windows.collection.start", "Starts", "", kind="time"),
            Field("windows.collection.eth_start", "Starts (extended-hours symbols)",
                  "", kind="time"),
            Field("windows.collection.stop", "Stops", "", kind="time"),
            Field("windows.session_flip.at", "Charts switch to today at",
                  "When Dealer Positioning stops showing the prior session.",
                  kind="time"),
        ), restart=(OPTIONS, WEBGUI)),
        _window("market_snapshot", "Market snapshot pushes", "",
                (OPTIONS, SENTIMENT)),
        _window("live_capture", "Public screenshot captures",
                "Keep this after the close: the capture is CPU-heavy and must "
                "not compete with the collection tick.", ()),
        _window("gamma_public", "Public Gamma live symbols",
                "When the public Gamma page keeps a visitor's symbol live. "
                "Keep it inside the GEX collection window: only collection "
                "updates a live symbol.", ()),
        _window("finder_public", "Public Strategy Finder scans",
                "When the public site will scan a symbol a visitor types. "
                "Outside it the page shows the last scan.", ()),
        _window("rescue_public", "Public Rescue form",
                "When the public site loads strikes and computes rescue options "
                "against live prices.", (),
                extra=(_after_hours("rescue_public", "rescues"),)),
        _window("tools_public", "Public Calculator and Simulator",
                "When the public site loads a chain, rates a trade or loads a "
                "Simulator snapshot against live prices. Pricing a loaded "
                "position works at any time.", (),
                extra=(_after_hours("tools_public", "those requests"),)),
        Section("Claude briefings (paid)",
                "Scheduled Dealer Positioning briefings. Each one is a paid call.", (
            Field("slots.analyze.grace_min", "Fire if late by at most", "",
                  kind="int", unit="min", min=1, max=120, step=1),
            Field("slots.analyze.*", "", "", kind="time"),
        ), restart=(OPTIONS, WEBGUI)),
        Section("Action digests (phone)",
                "The \"trades needing action\" push.", (
            Field("slots.action_alert.grace_min", "Fire if late by at most", "",
                  kind="int", unit="min", min=1, max=120, step=1),
            Field("slots.action_alert.*", "", "", kind="time"),
        ), restart=(OPTIONS,)),
        Section("Public Strategy Finder warm-up",
                "Queues the warm-up symbols through the public scan worker.", (
            Field("slots.finder_public.grace_min", "Fire if late by at most", "",
                  kind="int", unit="min", min=1, max=120, step=1),
            Field("slots.finder_public.*", "", "", kind="time"),
        ), restart=(OPTIONS,)),
        Section("Income scan", "The once-a-day 30–45 day scan.", (
            Field("slots.income.grace_min", "Fire if late by at most", "",
                  kind="int", unit="min", min=1, max=120, step=1),
            Field("slots.income.*", "", "", kind="time"),
        ), restart=(OPTIONS,)),
        Section("Hourly trade idea (Discord and Telegram)", "", (
            Field("slots.trade_idea.grace_min", "Fire if late by at most",
                  "Keep under 60 so a late post can't overlap the next hour.",
                  kind="int", unit="min", min=1, max=59, step=1),
            Field("slots.trade_idea.*", "", "", kind="time"),
        ), restart=(OPTIONS,)),
        Section("After-close jobs", "", (
            Field("slots.eod_report.at", "End-of-day report", "", kind="time",
                  restart=(TIMERS,)),
            Field("slots.flow_delta.at", "Flow instrumentation report", "",
                  kind="time", restart=(TIMERS,)),
            Field("slots.momentum.at", "Momentum recompute",
                  "Earlier than ~80 minutes after the close scores stale bars.",
                  kind="time", restart=(SENTIMENT, TRADE, WEBGUI)),
            Field("slots.calibration.at", "Signal calibration rebuild", "",
                  kind="time", restart=(OPTIONS, TRADE, WEBGUI)),
            Field("slots.gallery_capture.at", "Website gallery capture",
                  "Runs in session on purpose (index open interest zeroes after "
                  "hours).", kind="time", restart=(TIMERS,)),
        )),
    ),
)

# ─────────────────────────────────────────────────────────────────────────────
# Symbols & watchlists — config/symbols.toml
# ─────────────────────────────────────────────────────────────────────────────
_SYMBOLS = ConfigFile(
    name="symbols.toml", title="Symbols & watchlists", icon="list_alt",
    summary="Which symbols the 1-minute gamma collector polls, and the groups on "
            "the Net Prem view.",
    restart=(OPTIONS, MARKET, WEBGUI),
    caution="Each symbol added to collection costs about 440 extra Schwab chain "
            "calls a day.",
    sections=(
        Section("Always collected", "Unioned with the watchlist workbook.", (
            Field("collection.base", "Core symbols", "", kind="symbols"),
            Field("collection.broad", "Broad ETFs", "", kind="symbols"),
            Field("collection.sectors", "Sector ETFs", "", kind="symbols"),
            Field("collection.megacaps", "Mega-caps", "", kind="symbols"),
        )),
        Section("Baskets", "", (
            Field("baskets.big10", "BIG10 basket",
                  "Summed into one pseudo-symbol by the market and net-premium "
                  "views.", kind="symbols"),
        )),
        Section("Net Prem groups", "The groups on the Dealer Positioning Net Prem "
                "view, in display order. Every symbol needs a chart colour.", (
            Field("netprem_groups.*.label", "Tab name", "", kind="text"),
            Field("netprem_groups.*.symbols", "", "", kind="symbols"),
        )),
    ),
)

# ─────────────────────────────────────────────────────────────────────────────
# Sector map — config/sectors.toml
# ─────────────────────────────────────────────────────────────────────────────
_SECTORS = ConfigFile(
    name="sectors.toml", title="Sector map", icon="category",
    summary="Which sector each symbol belongs to, for the paper book's sector cap "
            "(at most 5 positions and $1,500 of risk per sector).",
    restart=(OPTIONS,), editor="sectors",
    sections=(Section("Symbol → sector", "", (
        Field("sectors.*", "", "", kind="sector", choices=SECTORS),
    )),),
)

# ─────────────────────────────────────────────────────────────────────────────
# Public Strategy Finder — config/finder_public.toml
# ─────────────────────────────────────────────────────────────────────────────
# Read per request by options_svc and the public site, through the mtime-cached
# loader, so a saved change applies to the next scan with no restart.
_FINDER_PUBLIC = ConfigFile(
    name="finder_public.toml", title="Public Strategy Finder", icon="travel_explore",
    summary="The Strategy Finder on the public site: the fixed filters every "
            "visitor's scan runs, how results are reused, and the daily limit.",
    restart=(),
    caution="Each public scan costs about six Schwab calls. The daily limit is "
            "the cap on that spend across every visitor.",
    sections=(
        Section("Filters", "The one filter set every public scan uses. Visitors "
                "cannot change these.", (
            Field("scan.dte_min", "Shortest expiration", "", kind="int",
                  unit="days", min=0, max=365, step=1),
            Field("scan.dte_max", "Longest expiration",
                  "Longer ranges make each scan slower, especially for symbols "
                  "with daily expirations.", kind="int",
                  unit="days", min=1, max=730, step=1),
            Field("scan.put_d_min", "Put short-leg delta, low", "", min=-1,
                  max=0, step=0.01),
            Field("scan.put_d_max", "Put short-leg delta, high", "", min=-1,
                  max=0, step=0.01),
            Field("scan.call_d_min", "Call short-leg delta, low", "", min=0,
                  max=1, step=0.01),
            Field("scan.call_d_max", "Call short-leg delta, high", "", min=0,
                  max=1, step=0.01),
            _pct("scan.min_cr_fraction", "Minimum credit",
                 "As a share of the spread width."),
        )),
        Section("Limits", "", (
            Field("limits.result_ttl_min", "Reuse a result for", "", kind="int",
                  unit="min", min=1, max=240, step=1),
            Field("limits.dedup_sec", "Ignore a repeat request for", "",
                  kind="int", unit="s", min=0, max=3600, step=5),
            Field("limits.negative_ttl_min", "Remember a symbol with no options for",
                  "Stops repeated requests for made-up tickers from using up the "
                  "daily limit.", kind="int", unit="min", min=1, max=1440, step=10),
            Field("limits.max_wait_sec", "Drop a request that waited", "",
                  kind="int", unit="s", min=10, max=3600, step=10),
            Field("limits.daily_budget", "Scans per day",
                  "Across all visitors together.", kind="int", min=1,
                  max=5000, step=10),
            Field("limits.result_keep_hours", "Keep a symbol's result for", "",
                  kind="int", unit="h", min=1, max=168, step=1),
        )),
        Section("Visitors", "", (
            Field("visitor.scans_per_hour", "Scans per visitor per hour",
                  "Counted by address in memory; no address is stored.",
                  kind="int", min=1, max=200, step=1),
        )),
        Section("Display", "", (
            Field("display.show_leg_quotes", "Show per-leg bid and ask",
                  "Off until Schwab's terms on republishing quotes are settled.",
                  kind="bool"),
            Field("display.rows_per_type", "Ideas kept per strategy type",
                  "The rest are counted as not shown.", kind="int", min=1,
                  max=25, step=1),
        )),
        Section("Morning warm-up", "Scanned once each morning so the page's "
                "default symbol has a fresh result. Each uses one scan of the "
                "daily limit.", (
            Field("warm.symbols", "Symbols", "", kind="symbols"),
        ), restart=(OPTIONS,)),
    ),
)

# ─────────────────────────────────────────────────────────────────────────────
# Public Gamma page — config/gamma_public.toml
# ─────────────────────────────────────────────────────────────────────────────
# Read per request by options_svc and the public site through the mtime-cached
# loader, so a saved change applies with no restart.
_GAMMA_PUBLIC = ConfigFile(
    name="gamma_public.toml", title="Public Gamma page", icon="stacked_line_chart",
    summary="The Gamma page on the public site: how many visitor-picked symbols "
            "are kept live at once, and for how long.",
    restart=(),
    caution="Each live symbol adds one snapshot to the one-minute GEX update, "
            "which already runs past its minute a few times a day. It costs no "
            "Schwab call.",
    sections=(
        Section("Live symbols", "Beyond $SPX, SPY and QQQ, which are always live.", (
            Field("hot.cap", "Symbols live at once", "0 turns this off.",
                  kind="int", min=0, max=40, step=1),
            Field("hot.lease_min", "Keep a symbol live for",
                  "After the last visitor asked for it.", kind="int",
                  unit="min", min=1, max=240, step=1),
            Field("hot.keep_min", "Keep its data after that for", "",
                  kind="int", unit="min", min=1, max=1440, step=5),
        )),
        Section("Page", "", (
            Field("page.renew_min", "An open page renews its symbol every",
                  "Keep this shorter than the time a symbol stays live.",
                  kind="int", unit="min", min=1, max=60, step=1),
            Field("limits.max_wait_sec", "Drop a request that waited", "",
                  kind="int", unit="s", min=10, max=3600, step=10),
        )),
        Section("Visitors", "", (
            Field("visitor.picks_per_hour", "Symbol changes per visitor per hour",
                  "Counted by address in memory; no address is stored.",
                  kind="int", min=1, max=500, step=1),
        )),
    ),
)

# ─────────────────────────────────────────────────────────────────────────────
# Public edge — config/edge.toml
# ─────────────────────────────────────────────────────────────────────────────
# Read by deploy/caddy/generate_caddyfile.py only. No service restart applies it:
# the Caddyfile has to be regenerated and Caddy reloaded, as root (see caution).
_EDGE = ConfigFile(
    name="edge.toml", title="Public site rate limit", icon="speed",
    summary="How many pages one visitor may load on the public site per window.",
    restart=(),
    caution="Needs a Caddy built with the rate-limit module. Saving here changes "
            "nothing until the Caddyfile is regenerated and Caddy reloaded, as "
            "root - see the runbook's Edge rate limit section.",
    sections=(
        Section("Page loads per visitor", "Counts pages only, not the images, "
                "scripts and live connection each page uses.", (
            Field("live_rate_limit.enabled", "Limit page loads", "", kind="bool"),
            Field("live_rate_limit.events", "Pages allowed", "", kind="int",
                  min=1, max=100000, step=1),
            Field("live_rate_limit.window_sec", "Per", "", kind="int", unit="s",
                  min=1, max=86400, step=10),
            Field("live_rate_limit.ipv6_prefix", "Group IPv6 visitors by",
                  "Prefix bits. One IPv6 visitor can use a whole /64, so smaller "
                  "numbers group more loosely.", kind="int", unit="bits", min=1,
                  max=128, step=1),
        )),
    ),
)

# ─────────────────────────────────────────────────────────────────────────────
# Commissions — config/commissions.toml
# ─────────────────────────────────────────────────────────────────────────────
_COMMISSIONS = ConfigFile(
    name="commissions.toml", title="Commissions", icon="payments",
    summary="Schwab's rates, folded into every P&L and ranking.",
    restart=(OPTIONS,),
    sections=(
        Section("Options", "Per contract, per leg, charged on open and on close.", (
            Field("options.equity", "Stock and ETF options", "", kind="money",
                  min=0, max=10, step=0.01),
            Field("options.index", "Index options", "", kind="money", min=0,
                  max=10, step=0.01),
            Field("options.index_exchange_fee", "Index exchange fee",
                  "Added per index contract.", kind="money", min=0, max=10,
                  step=0.01),
        )),
        Section("Futures", "Per contract, per side.", (
            Field("futures.standard", "Futures commission", "", kind="money",
                  min=0, max=20, step=0.01),
            Field("futures.exchange_fee", "Futures exchange fee", "", kind="money",
                  min=0, max=20, step=0.01),
        )),
    ),
)

# ─────────────────────────────────────────────────────────────────────────────
# Public Calculator and Simulator — config/tools_public.toml
# ─────────────────────────────────────────────────────────────────────────────
# Read per request by options_svc and the public site, through the mtime-cached
# loader, so a saved change applies to the next request with no restart. The
# daily Schwab budget these requests spend is the shared one in
# rescue_public.toml's [budget].
_TOOLS_PUBLIC = ConfigFile(
    name="tools_public.toml", title="Public Calculator and Simulator",
    icon="calculate",
    summary="The Calculator and Simulator on the public site: how results are "
            "reused, how much is held in memory, and how much one visitor may "
            "ask for.",
    restart=(),
    caution="Loading a chain, a Simulator snapshot or a trade rating spends "
            "Schwab calls from the shared daily public budget (Public Rescue "
            "form → Budget). Pricing spends none.",
    sections=(
        Section("Reuse and waiting", "", (
            Field("limits.result_ttl_min", "Reuse a pricing result for", "",
                  kind="int", unit="min", min=1, max=60, step=1),
            Field("limits.rate_ttl_min", "Reuse a trade rating for", "",
                  kind="int", unit="min", min=1, max=240, step=1),
            Field("limits.dedup_sec", "Ignore a repeat request for", "",
                  kind="int", unit="s", min=0, max=3600, step=5),
            Field("limits.structure_runs", "Ratings of one trade per reuse window",
                  "The same strikes with different prices. Stops one trade being "
                  "rated again for every price typed.",
                  kind="int", min=1, max=50, step=1),
            Field("limits.max_wait_sec", "Drop a request that waited", "",
                  kind="int", unit="s", min=10, max=3600, step=10),
            Field("limits.result_keep_min", "Keep a result for", "",
                  kind="int", unit="min", min=1, max=1440, step=5),
            Field("limits.answer_keep_min", "Keep a request's answer for", "",
                  kind="int", unit="min", min=1, max=240, step=1),
        )),
        Section("Held in memory", "What the options service keeps between "
                "visitors' requests.", (
            Field("limits.snapshot_limit", "Simulator snapshots held",
                  "One per symbol, shared by every visitor on it.",
                  kind="int", min=1, max=64, step=1),
            Field("limits.snapshot_ttl_min", "Keep a Simulator snapshot for", "",
                  kind="int", unit="min", min=1, max=240, step=1),
            Field("limits.chain_hold_limit", "Quoted chains held",
                  "Used to rate trades and estimate volatility; never published "
                  "while quotes are off.",
                  kind="int", min=1, max=128, step=1),
        )),
        Section("Visitors", "Counted by address in memory; no address is stored.", (
            Field("visitor.tools_per_hour", "Loads and ratings per visitor per hour",
                  "", kind="int", min=1, max=1000, step=1),
            Field("visitor.math_per_hour", "Pricing requests per visitor per hour",
                  "The Calculator reprices on every edit, so this is generous.",
                  kind="int", min=1, max=10000, step=10),
            Field("visitor.handoff_keep_min",
                  "Keep a tab's Calculator position for",
                  "How long a browser tab keeps the position the Calculator "
                  "hands to the Simulator, after it last changed. Held in the "
                  "public site's memory only. Read when the public site "
                  "starts, so a change needs a restart of the public site "
                  "(webgui_live on the System Status page).",
                  kind="int", unit="min", min=1, max=1440, step=5),
        )),
    ),
)

# ─────────────────────────────────────────────────────────────────────────────
# Public Rescue form — config/rescue_public.toml
# ─────────────────────────────────────────────────────────────────────────────
# Read per request by options_svc and the public site, through the mtime-cached
# loader, so a saved change applies to the next request with no restart.
_RESCUE_PUBLIC = ConfigFile(
    name="rescue_public.toml", title="Public Rescue form", icon="healing",
    summary="The Rescue form on the public site: how results are reused, the "
            "daily limits, and how much one visitor may ask for.",
    restart=(),
    caution="Each public rescue reprices the trade and fetches its roll "
            "candidates from Schwab. The daily public budget caps that spend "
            "across every visitor and every public tool.",
    sections=(
        Section("Limits", "", (
            Field("limits.result_ttl_min", "Reuse a rescue menu for",
                  "For the same trade. Short, because the menu reprices live.",
                  kind="int", unit="min", min=1, max=60, step=1),
            Field("limits.ladder_ttl_min", "Reuse a symbol's strikes for", "",
                  kind="int", unit="min", min=1, max=480, step=5),
            Field("limits.dedup_sec", "Ignore a repeat request for", "",
                  kind="int", unit="s", min=0, max=3600, step=5),
            Field("limits.structure_runs", "Runs of one trade per reuse window",
                  "The same strikes and expiration with different prices. "
                  "Stops one trade being rerun for every price typed.",
                  kind="int", min=1, max=50, step=1),
            Field("limits.max_wait_sec", "Drop a request that waited", "",
                  kind="int", unit="s", min=10, max=3600, step=10),
            Field("limits.result_keep_min", "Keep a rescue menu for", "",
                  kind="int", unit="min", min=1, max=1440, step=5),
            Field("limits.ladder_keep_min", "Keep a symbol's strikes for", "",
                  kind="int", unit="min", min=1, max=1440, step=5),
            Field("limits.answer_keep_min", "Keep a request's answer for", "",
                  kind="int", unit="min", min=1, max=240, step=1),
        )),
        Section("Visitors", "Counted by address in memory; no address is stored.", (
            Field("visitor.computes_per_hour", "Rescues per visitor per hour", "",
                  kind="int", min=1, max=500, step=1),
            Field("visitor.ladders_per_hour", "Strike loads per visitor per hour",
                  "", kind="int", min=1, max=1000, step=1),
        )),
        Section("Budget", "One Schwab allowance behind every public tool, so "
                          "one daily budget shared by all of them.", (
            Field("budget.daily_budget", "Daily public budget",
                  "Requests that reach Schwab per trading day, across the Rescue "
                  "form, the Calculator and the Simulator and every visitor "
                  "together. A request refused for any other reason never counts.",
                  kind="int", min=1, max=20000, step=10),
        )),
    ),
)

# ─────────────────────────────────────────────────────────────────────────────
# System (read-only) — ports + environments
# ─────────────────────────────────────────────────────────────────────────────
_READONLY_NOTE = ("Shown for reference. Changing a port or an environment profile "
                  "means regenerating the systemd units and restarting the whole "
                  "stack, so it is done in the file, not here.")
_PORTS = ConfigFile(name="ports.toml", title="Ports", icon="lan",
                    summary=_READONLY_NOTE, restart=(), sections=(),
                    editable=False, editor="readonly")
_ENVS = ConfigFile(name="environments.toml", title="Environments", icon="dns",
                   summary=_READONLY_NOTE, restart=(), sections=(),
                   editable=False, editor="readonly")

# ─────────────────────────────────────────────────────────────────────────────
# Paper books — config/paper.toml
# ─────────────────────────────────────────────────────────────────────────────
_PAPER = ConfigFile(
    name="paper.toml", title="Paper books", icon="account_balance_wallet",
    summary="The largest loss one trade may carry in each of your two paper books.",
    restart=(OPTIONS,),
    caution="The Account's cap also decides which spread widths the scanner "
            "offers: a width whose one contract would lose more is never shown.",
    sections=(
        Section("Per-trade loss caps", "The most one trade may lose, in dollars.", (
            Field("risk.max_risk_per_trade", "Paper Account (automatic trades)",
                  "Also the budget the Market Scanner sizes spread widths against.",
                  kind="money", min=1, max=100000, step=50),
            Field("risk.ledger_max_risk_per_trade", "Paper Ledger (the Paper button)",
                  "Also the budget the Strategy Finder and Income Window size "
                  "credit spreads against, since their trades land here.",
                  kind="money", min=1, max=100000, step=50),
        )),
    ),
)

# ─────────────────────────────────────────────────────────────────────────────
# Market news — config/news.toml
# ─────────────────────────────────────────────────────────────────────────────
_NEWS = ConfigFile(
    name="news.toml", title="Market news", icon="newspaper",
    summary="Which public feeds the news collector reads, how often, and which "
            "of them the public site may show.",
    restart=(NEWS,),
    caution="Every feed is a public RSS, Google News or SEC feed. A feed marked "
            "not public stays in the app and never reaches live.neuralstrike.co. "
            "The feed list itself is read-only here: add or change a feed in "
            "config/news.toml.",
    sections=(
        Section("Polling", "How often every feed is read. Faster costs nothing "
                "in API budget but is discourteous to the publishers.", (
            Field("collector.rth_poll_min", "During market hours",
                  "Minutes between polls, 08:30–15:00 CT.", kind="int", unit="min",
                  min=3, max=60, step=1),
            Field("collector.offhours_poll_min", "Outside market hours",
                  "Minutes between polls on a trading day outside 08:30–15:00 CT.",
                  kind="int", unit="min", min=1, max=240, step=1),
            Field("collector.weekend_poll_min", "Weekends and holidays",
                  "Minutes between polls on Saturday, Sunday and market holidays.",
                  kind="int", unit="min", min=5, max=720, step=5),
            Field("collector.keep_days", "Keep items for",
                  "Older rows are pruned from the store.", kind="int", unit="days",
                  min=1, max=90, step=1),
            Field("collector.view_items", "Rows published",
                  "The newest rows the page, the Desk and the Symbol page read.",
                  kind="int", min=50, max=1000, step=50),
            Field("collector.request_timeout_s", "Request timeout",
                  "How long one feed may take to answer before it is skipped "
                  "until the next poll.", kind="int", unit="s", min=5, max=120, step=5),
            Field("collector.sec_user_agent", "SEC User-Agent",
                  "The SEC requires a contact address in every request.", kind="text"),
        )),
        Section("Tickers", "The per-ticker feeds and the watchlist filter use the "
                "gamma collection list (Symbols & watchlists) plus these.", (
            Field("tickers.extras", "Extra tickers",
                  "Followed by the news feeds but not scanned for trades.",
                  kind="symbols"),
        )),
        Section("Trending", "", (
            Field("trending.window_h", "Trending window",
                  "The Trending chips count ticker mentions in this window.",
                  kind="int", unit="h", min=1, max=48, step=1),
        )),
        Section("Feed switches", "One pair per feed, named by the feed. A switch "
                "is a table entry, so changing one leaves every other feed alone.", (
            Field("feed_flags.*.enabled", "Enabled",
                  "Off stops polling this feed; its items age out of the store.",
                  kind="bool"),
            Field("feed_flags.*.public", "Show on the public site",
                  "Off keeps this feed's headlines in the app only.", kind="bool"),
        )),
        Section("Feeds", "One entry per source, in display order. Read-only here: "
                "a list in config/local would replace the whole shipped list, so "
                "feeds are added and changed in config/news.toml.", (
            Field("feeds.*.name", "Name", "", kind="text"),
            Field("feeds.*.kind", "Kind", "rss · yahoo_ticker · google_news · "
                  "edgar_form4 · edgar_filings", kind="choice",
                  choices=("rss", "yahoo_ticker", "google_news", "edgar_form4",
                           "edgar_filings")),
            Field("feeds.*.url", "Feed URL", "For rss and yahoo_ticker ({symbol} "
                  "expands over the ticker set).", kind="text", optional=True),
            Field("feeds.*.query", "Google News search", "", kind="text",
                  optional=True),
            Field("feeds.*.forms", "SEC form types", "", kind="symbols",
                  optional=True),
            Field("feeds.*.min_value_usd", "Insider buy floor",
                  "A buy on an untracked ticker is shown only at or above this.",
                  kind="money", min=0, optional=True),
        ), readonly=True),
    ),
)

FILES = (_SCANNER, _PAPER, _TRADE_MGMT, _FLOW, _SESSIONS, _SYMBOLS, _NEWS, _SECTORS,
         _FINDER_PUBLIC, _RESCUE_PUBLIC, _TOOLS_PUBLIC, _GAMMA_PUBLIC, _EDGE, _COMMISSIONS, _PORTS, _ENVS)
EDITABLE = tuple(f for f in FILES if f.editable)
BY_NAME = {f.name: f for f in FILES}

# Appearance lives in its own editor (Settings -> Appearance).
NOT_HERE = {"theme.toml": "Settings → Appearance",
            "env.local.example.toml": "a template, not a setting"}


# ── pure helpers ─────────────────────────────────────────────────────────────
def split_key(key):
    """``'iv_rank."0-DTE"'`` -> ``['iv_rank', '0-DTE']``; quotes protect dots."""
    out, cur, quoted = [], "", False
    for ch in key:
        if ch == '"':
            quoted = not quoted
        elif ch == "." and not quoted:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    out.append(cur)
    return out


def _matches(pattern, parts):
    pp = split_key(pattern)
    return len(pp) == len(parts) and all(a == "*" or a == b for a, b in zip(pp, parts))


def locate(cfg: ConfigFile, parts):
    """``(section, field)`` for a key path, exact entries before wildcards."""
    wild = None
    for sec in cfg.sections:
        for f in sec.fields:
            if split_key(f.key) == list(parts):
                return sec, f
            if wild is None and "*" in f.key and _matches(f.key, parts):
                wild = (sec, f)
    return wild or (None, None)


def is_readonly(cfg: ConfigFile, parts) -> bool:
    """True when the key path ``parts`` may be shown but never written.

    A path the catalogue locates is read-only when its section is. A path it
    does NOT locate (a hand-written key with no field) is read-only when its
    top-level table belongs to read-only sections only - so an unknown key under
    ``feeds`` cannot become writable by falling outside the catalogue."""
    parts = list(parts)
    sec, fld = locate(cfg, parts)
    if fld is not None:
        return bool(sec.readonly)
    tops = {}
    for s in cfg.sections:
        for f in s.fields:
            tops.setdefault(split_key(f.key)[0], set()).add(bool(s.readonly))
    return bool(parts) and tops.get(parts[0]) == {True}


def restart_for(cfg: ConfigFile, section: Section | None, fld: Field | None):
    if fld is not None and fld.restart is not None:
        return fld.restart
    if section is not None and section.restart is not None:
        return section.restart
    return cfg.restart


def humanize(name):
    """A slot or group name for display: ``h0835`` -> ``08:35``, ``premarket``
    -> ``Premarket``."""
    s = str(name)
    if len(s) == 5 and s[0] == "h" and s[1:].isdigit():
        return f"{s[1:3]}:{s[3:]}"
    return s.replace("_", " ").capitalize()


def to_display(fld: Field, value):
    """The number shown in the editor (a fraction becomes a percent)."""
    if fld.kind == "fraction" and isinstance(value, (int, float)) and not isinstance(value, bool):
        return round(value * 100.0, 6)
    if fld.kind == "ladder" and isinstance(value, list):
        return [[round(a * 100.0, 6), round(b * 100.0, 6)] for a, b in value]
    return value


def _num(raw):
    if isinstance(raw, bool):
        raise ValueError("not a number")
    v = float(raw)
    if v != v or v in (float("inf"), float("-inf")):
        raise ValueError("not a finite number")
    return v


def _bounded(fld, v):
    if fld.min is not None and v < fld.min:
        raise ValueError(f"must be at least {fld.min:g}{_u(fld)}")
    if fld.max is not None and v > fld.max:
        raise ValueError(f"must be at most {fld.max:g}{_u(fld)}")
    return v


def _u(fld):
    return "" if not fld.unit else (fld.unit if fld.unit == "%" else f" {fld.unit}")


def parse(fld: Field, raw, *, shipped=None):
    """Editor value -> the value stored in TOML, or ``ValueError`` with a
    sentence the page shows beside the field. ``shipped`` keeps an int an int."""
    k = fld.kind
    if raw is None or (isinstance(raw, str) and raw.strip() == ""):
        if fld.optional:
            return None
        raise ValueError("a value is required")
    if k == "bool":
        return bool(raw)
    if k in ("int",):
        v = _bounded(fld, _num(raw))
        if v != int(v):
            raise ValueError("must be a whole number")
        return int(v)
    if k in ("float", "money"):
        v = _bounded(fld, _num(raw))
        if isinstance(shipped, int) and not isinstance(shipped, bool) and v == int(v):
            return int(v)
        return float(v)
    if k == "fraction":
        v = _bounded(fld, _num(raw))
        return round(v / 100.0, 10)
    if k == "time":
        s = str(raw).strip()
        try:
            hh, mm = s.split(":")
            h, m = int(hh), int(mm)
        except Exception:
            raise ValueError("use HH:MM, e.g. 08:30") from None
        if not (0 <= h <= 23 and 0 <= m <= 59):
            raise ValueError("not a clock time")
        return f"{h:02d}:{m:02d}"
    if k == "date":
        import datetime as _dt
        s = str(raw).strip()
        try:
            _dt.date.fromisoformat(s)
        except Exception:
            raise ValueError("use YYYY-MM-DD") from None
        return s
    if k == "text":
        s = str(raw).strip()
        if not s:
            raise ValueError("a value is required")
        return s
    if k in ("choice", "sector"):
        if raw not in fld.choices:
            raise ValueError("pick one of the listed values")
        return raw
    if k == "symbols":
        items = raw if isinstance(raw, (list, tuple)) else str(raw).replace(",", " ").split()
        out = []
        for it in items:
            t = str(it).strip().upper() if fld.key != "single_leg.excluded_grades" \
                else str(it).strip().capitalize()
            if t and t not in out:
                out.append(t)
        return out
    if k == "pair":
        try:
            lo, hi = (_num(x) for x in raw)
        except Exception:
            raise ValueError("enter two numbers") from None
        for v in (lo, hi):
            _bounded(fld, v)
        if lo >= hi:
            raise ValueError("the low end must be below the high end")
        return [lo, hi]
    if k == "ladder":
        rungs = []
        for r in raw:
            a, b = (_num(x) for x in r)
            if not (0 <= a <= 100 and 0 <= b <= 100):
                raise ValueError("rungs are percents between 0 and 100")
            if b >= a:
                raise ValueError("a rung must lock LESS than the peak that arms it")
            rungs.append([round(a / 100.0, 10), round(b / 100.0, 10)])
        if not rungs:
            raise ValueError("a ladder needs at least one rung")
        peaks = [r[0] for r in rungs]
        if peaks != sorted(peaks) or len(set(peaks)) != len(peaks):
            raise ValueError("rungs must rise, one per peak")
        return rungs
    return raw


# ── cross-field checks: (file, message) pairs over a flat {path-tuple: value} ─
def cross_check(name, values):
    """Sentences for combinations that each pass alone but not together."""
    g = lambda *p: values.get(tuple(p))  # noqa: E731
    errs = []
    if name == "sessions.toml":
        pairs = [(("windows", w, "start"), ("windows", w, "end"))
                 for w in ("scan", "market_snapshot", "live_capture")]
        pairs += [(("sessions", s, "start"), ("sessions", s, "end"))
                  for s in ("gth", "regular", "curb")]
        pairs.append((("windows", "collection", "start"),
                      ("windows", "collection", "stop")))
        for a, b in pairs:
            va, vb = values.get(a), values.get(b)
            if isinstance(va, str) and isinstance(vb, str) and va >= vb:
                errs.append(f"{humanize(a[1])}: the start must be before the end.")
    if name == "trade_mgmt.toml":
        w, c = g("rescue", "money_warn_mult"), g("rescue", "money_critical_mult")
        if None not in (w, c) and w >= c:
            errs.append("The rescue warning loss must be smaller than the critical "
                        "loss.")
        w, t = g("rescue", "proximity_watch_pct"), g("rescue", "proximity_tested_pct")
        if None not in (w, t) and t >= w:
            errs.append("\"Tested\" must be closer to the strike than \"watch\".")
    if name == "flow_alerts.toml":
        lo, hi = g("big_delta", "delta_lo"), g("big_delta", "delta_hi")
        if None not in (lo, hi) and lo >= hi:
            errs.append("Big delta: the low delta bound must be below the high one.")
    return errs

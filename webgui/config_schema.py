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
    text            a short piece of text (a tab name); ``blank_ok`` lets "" stand
    symbols         a list of tickers
    phrases         a list of keyword phrases: case and inner spaces kept,
                    split on commas only (each list item too), deduplicated
                    ignoring case; non-string items are skipped
    pair            two numbers [low, high]
    numbers         a list of numbers (``min``/``max`` bound each), at least one
    ladder          a list of [peak, lock] rungs (fractions, shown as percents)
    sector          one of the sector names (the sector-map editor)
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Unit names of the systemd user units a change needs restarted, as the Status
# page spells them (``trading-<env>-<name>``). "timers" is not a unit: it means
# "regenerate the scheduled timers" (deploy.systemd.generate_units --install).
OPTIONS = "options_svc"
SENTIMENT = "sentiment_svc"
MARKET = "market_svc"
NEWS = "news_svc"
BLOG = "blog_svc"
TRADE = "trade_svc"
WEBGUI = "webgui"
TIMERS = "timers"

RESTART_LABELS = {
    OPTIONS: "Options service",
    SENTIMENT: "Sentiment service",
    MARKET: "Market service",
    NEWS: "News service",
    BLOG: "Blog service",
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
    # text only: "" is a real value (e.g. "use the default User-Agent"), stored
    # as "" rather than refused or read as unset.
    blank_ok: bool = False


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
        Section("Which strikes may be sold",
                "The scanner's strike rules. A short leg must pass every one.", (
            Field("selection.max_entry_short_delta", "Highest short delta at entry",
                  "A short leg above this delta is not sold. Lower means further "
                  "from the money: fewer signals, each more likely to expire "
                  "worthless.",
                  kind="float", min=0.05, max=0.5, step=0.01),
            Field("selection.delta_sanity_max", "Delta treated as a data fault",
                  "A contract whose delta is above this is skipped as bad data. "
                  "Keep it above the entry limit.",
                  kind="float", min=0.1, max=1.0, step=0.01),
            Field("selection.momentum_veto", "Move that stops the offside spread",
                  "Once a symbol has moved more than this fraction of its daily "
                  "expected move, the spread on the side it is moving toward is "
                  "not offered.",
                  kind="float", unit="of the expected move", min=0.0, max=3.0, step=0.05),
            Field("selection.edge_margin", "Credit required above break-even",
                  "A spread must pay credit ÷ width of at least its short delta "
                  "plus this. 0 is break-even on the model. A larger value "
                  "removes most spreads.",
                  kind="float", min=0.0, max=0.2, step=0.005),
            Field("selection.min_abs_credit", "Smallest credit worth taking",
                  "Per share: 0.25 is $25 a contract.",
                  kind="float", unit="$ per share", min=0.0, max=5.0, step=0.05),
            Field("selection.min_abs_spread", "Quote always accepted as tight",
                  "A bid-ask gap this small in dollars is accepted however large "
                  "it is in percent.",
                  kind="float", unit="$", min=0.0, max=1.0, step=0.01),
            Field("selection.max_width_dollars", "Widest spread considered",
                  "The width search stops at this many dollars between strikes.",
                  kind="int", unit="$", min=1, max=1000, step=5),
            Field("selection.zero_dte_min_mult", "Same-day: nearest short strike",
                  "As a multiple of the session's remaining expected move. The "
                  "1–4 day window uses the same minimum.",
                  kind="float", unit="× expected move", min=0.0, max=5.0, step=0.05),
            Field("selection.zero_dte_max_mult", "Same-day: furthest short strike",
                  "As a multiple of the session's remaining expected move.",
                  kind="float", unit="× expected move", min=0.1, max=10.0, step=0.1),
            Field("selection.directional_min_mult", "Directional: nearest short strike",
                  "As a multiple of the expected move.",
                  kind="float", unit="× expected move", min=0.0, max=5.0, step=0.05),
            Field("selection.directional_max_mult", "Directional: furthest short strike",
                  "As a multiple of the expected move. Keep it at or below the "
                  "same-day minimum so the two bands do not overlap.",
                  kind="float", unit="× expected move", min=0.0, max=5.0, step=0.05),
        )),
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
            Field("single_leg.long_excluded_grades",
                  "Grades never shown for a bought call or put",
                  "Removed for long calls and long puts on top of the list "
                  "above. Weak and Marginal leaves only Good and Strong.",
                  kind="symbols"),
        )),
        Section("Other structures (0-DTE and Swing tabs)",
                "Debit spreads, straddles and strangles, butterflies and condors, "
                "calendars and diagonals. Scored separately from the credit "
                "spreads and never ranked against them.", (
            Field("structures.enabled", "Build other structures",
                  "Off removes the Other structures tables' contents; the credit "
                  "spreads are not affected.", kind="bool"),
            Field("structures.families", "Families built",
                  "VERTICAL (debit spreads) · STRADDLE (straddles and strangles) "
                  "· BUTTERFLY (butterflies and condors) · CALENDAR (calendars "
                  "and diagonals, Swing tab only) · RATIO (ratio backspreads).",
                  kind="symbols"),
            Field("structures.min_score", "Minimum score",
                  "Fit + Quality score a structure needs to be shown (0–100).",
                  kind="float", min=0, max=100, step=1),
            Field("structures.excluded_grades", "Grades never shown",
                  "Grades removed from the list outright.", kind="symbols"),
            Field("structures.max_per_family", "Maximum per family",
                  "Most rows of one family one symbol may show in one tab. "
                  "0 is no limit.", kind="int", min=0, max=10, step=1),
            Field("structures.short_delta_min", "Short strangle — lowest delta",
                  "A short strangle sells between this delta and the highest "
                  "short delta at entry, aiming at the midpoint.",
                  kind="float", min=0.0, max=0.5, step=0.01),
            Field("structures.backspread_max_debit_frac",
                  "Ratio backspread — highest cost",
                  "The most a backspread may cost, as a fraction of the distance "
                  "between its two strikes. 0 allows only one entered for a "
                  "credit or at even money.",
                  kind="float", min=0.0, max=1.0, step=0.05),
            Field("structures.earnings_long_premium", "Bought premium through earnings",
                  "\"flag\" keeps a trade that buys premium and would be held "
                  "through an earnings report, and marks the row; \"drop\" removes "
                  "it. A trade that sells premium is always removed.",
                  kind="choice", choices=("flag", "drop")),
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
            Field("scores.capture_min_tracked", "Capture score (tracked structures)",
                  "0 records whatever the Other structures tables showed; their "
                  "own minimum score already decided that.", kind="int", min=0,
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
            Field("capture.tracked", "Record other structures for study",
                  "Records the Market Scanner's debit spreads, straddles, "
                  "butterflies, calendars, backspreads and single options so "
                  "each gets an outcome. They are measured, never traded.",
                  kind="bool"),
            Field("capture.max_open_per_symbol_tracked",
                  "Open tracked structures per symbol, in one family",
                  "Across both tabs. A separate count from the captured signals "
                  "above. 0 turns the cap off.", kind="int", min=0, max=50, step=1),
            Field("capture.max_open_per_family_tracked",
                  "Open tracked structures per family, in all",
                  "Across every symbol. Each open row is priced every 15 "
                  "minutes, so this is what bounds the cost. 0 turns the cap "
                  "off.", kind="int", min=0, max=500, step=1),
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
    "CALL_BACKSPREAD": "Call backspread (tracked)",
    "PUT_BACKSPREAD": "Put backspread (tracked)",
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
        Section("Tracked structures",
                "The Market Scanner's structures that are not credit spreads are "
                "recorded to be measured, never traded. This is how often they "
                "are marked.", (
            Field("tracked.mark_interval_min", "Minutes between marks",
                  "Each mark fetches one chain per open symbol and expiration.",
                  kind="int", unit="minutes", min=1, max=60, step=1),
            Field("tracked.mark_offset_min", "Minutes after each boundary",
                  "10 with an interval of 15 marks at :10, :25, :40 and :55, clear "
                  "of the quarter-hour scan.", kind="int", unit="minutes", min=0,
                  max=59, step=1),
            Field("tracked.front_expiry_close", "Close calendars from",
                  "Central time on a calendar's front expiry day from which it is "
                  "closed on its mark.", kind="time"),
        )),
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
        Section("Hedging flow (HIRO model)",
                "A model of dealer hedging built from the 1-minute chain poll. "
                "Schwab has no trade tape, so each contract gets one buy/sell label "
                "per minute.", (
            Field("hiro.enabled", "Hedging-flow alerts on", "", kind="bool"),
            Field("hiro.push", "Send hedging-flow pushes to the phone",
                  "Off = on screen only, never pushed, and the Desk does not speak "
                  "them.", kind="bool"),
            Field("hiro.public", "Show hedging-flow alerts on the public screens",
                  "Off = they appear in this app only, never on the public live "
                  "screens. Turning it off later leaves alerts already published "
                  "visible there until the list resets overnight.", kind="bool"),
            Field("hiro.symbols", "Symbols watched",
                  "Only symbols in the GEX collection list are measured.",
                  kind="symbols"),
            Field("hiro.window_min", "Surge window", "", kind="int", unit="min",
                  min=5, max=60, step=1),
            Field("hiro.k", "Surge at", "Multiples of the symbol's normal window size.",
                  kind="float", unit="× normal", min=1, max=20, step=0.5),
            Field("hiro.push_k", "Push surges at", "A separate, higher bar for the phone.",
                  kind="float", unit="× normal", min=1, max=20, step=0.5),
            Field("hiro.min_notional", "…and at least", "Ignores a dead tape.",
                  kind="money", min=0, max=1e11, step=1000000),
            _pct("hiro.max_unclassified", "Skip when unlabelled volume exceeds",
                 "Volume with no buy/sell label: a print at the exact midpoint, or "
                 "a quote or delta that cannot be read.", hi=100, step=5),
            Field("hiro.cooldown_min", "Surge quiet period", "Per symbol and direction.",
                  kind="int", unit="min", min=0, max=1440, step=5),
            Field("hiro.baseline_sessions", "Normal size from the last", "",
                  kind="int", unit="sessions", min=1, max=20, step=1),
            Field("hiro.min_minutes", "Until then, wait for", "Minutes of today's data.",
                  kind="int", unit="min", min=15, max=390, step=5),
            Field("hiro.flip_enabled", "Reversal alerts on", "", kind="bool"),
            Field("hiro.flip_band", "Reversal dead zone", "Multiples of normal window size.",
                  kind="float", unit="× normal", min=0.25, max=10, step=0.25),
            Field("hiro.flip_not_before", "No reversals before", "Central time.",
                  kind="time"),
            Field("hiro.flip_cooldown_min", "Reversal quiet period", "",
                  kind="int", unit="min", min=0, max=1440, step=5),
            Field("hiro.keep_sessions", "Keep minute history for", "",
                  kind="int", unit="sessions", min=6, max=120, step=1),
        # No restart: options_svc re-reads [hiro] (mtime-cached) on every
        # 1-minute tick -- the measurement, the rules and the purge alike.
        ), restart=()),
        Section("Bought or sold (estimate)",
                "An estimated bought / sold / unlabelled share on each unusual-volume "
                "and outsized-bet alert. Schwab publishes no trade tape, so new volume "
                "is labelled from where the latest trade price sits against the bid "
                "and ask. Volume that printed while the service was not watching is "
                "counted as unlabelled.", (
            Field("sides.enabled", "Estimate bought and sold on flow alerts",
                  "Off stops the measurement and removes the figures from every "
                  "screen.", kind="bool"),
            Field("sides.public", "Show the estimate on the public screens",
                  "Off = the figures appear in this app only.", kind="bool"),
            Field("sides.stream", "Stream a contract after its alert",
                  "A finer reading of what trades after the alert. Off = the "
                  "once-a-minute reading only.", kind="bool"),
            Field("sides.stream_max_contracts", "Most contracts streamed in a day",
                  "Alerts past this number keep the once-a-minute reading only. "
                  "Schwab allows 3,000 streamed option contracts in all, shared "
                  "with paper-trade tracking.",
                  # 500 = flow_sides_tick.STREAM_HARD_MAX (one request line).
                  kind="int", unit="contracts", min=0, max=500, step=10),
        # No restart: options_svc re-reads [sides] (mtime-cached) every minute.
        ), restart=()),
        Section("Opened or closed (next day)",
                "The next session's open interest for each flagged contract. The "
                "change in open interest, divided by that day's volume, says "
                "whether the volume mostly opened new positions or closed old "
                "ones. A contract that expired on its alert day has no reading.", (
            Field("followup.enabled", "Read next-day open interest", "", kind="bool"),
            Field("followup.opened_ratio", "\"Mostly opened\" at",
                  "Open interest rose by at least this share of the day's volume.",
                  kind="float", unit="× volume", min=0, max=1, step=0.05),
            Field("followup.closed_ratio", "\"Mostly closed\" at",
                  "Open interest fell by at least this share of the day's volume "
                  "(a negative number).",
                  kind="float", unit="× volume", min=-1, max=0, step=0.05),
            Field("followup.keep_sessions", "Keep flagged contracts for", "",
                  kind="int", unit="sessions", min=1, max=250, step=1),
        ), restart=()),
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
                (OPTIONS, WEBGUI), extra=(
            Field("windows.scan.offset_min", "Minutes after the quarter hour",
                  "Each scan starts this many minutes after its quarter hour: "
                  "2 means 9:02, 9:17, 9:32 and 9:47. Schwab refuses calls in "
                  "the first minute after the hour and half hour, so starting "
                  "on the quarter hour loses data. 0 starts on the quarter "
                  "hour. The same scans run either way. Takes effect at the "
                  "next scan, no restart.",
                  kind="int", unit="minutes", min=0, max=10),
        )),
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
        Section("Paper expiry settlement",
                "Settles the Paper Account's and Paper Ledger's expiring "
                "positions just after the close. Keep the time after 15:00.", (
            Field("slots.paper_settle.grace_min", "Fire if late by at most",
                  "Long on purpose: a settlement gives the same answer an hour "
                  "later, so a late start should still run it.",
                  kind="int", unit="min", min=1, max=480, step=1),
            Field("slots.paper_settle.*", "", "", kind="time"),
        ), restart=(OPTIONS,)),
        Section("Income scan", "The once-a-day 30–45 day scan.", (
            Field("slots.income.grace_min", "Fire if late by at most", "",
                  kind="int", unit="min", min=1, max=120, step=1),
            Field("slots.income.*", "", "", kind="time"),
        ), restart=(OPTIONS,)),
        Section("Opening Scalp morning call", "When the morning call is made: "
                "today's bars and each scalp symbol's 0DTE chain are fetched and "
                "the call is published. Not 08:35: the autoscan fetches "
                ":32 to :35 and Schwab refuses the first minute after the half hour.", (
            Field("slots.scalp.grace_min", "Fire if late by at most",
                  "A capture is only worth taking near the open, so keep this short.",
                  kind="int", unit="min", min=1, max=120, step=1),
            Field("slots.scalp.*", "", "", kind="time"),
        ), restart=(OPTIONS,)),
        Section("Opening Scalp bar top-up", "The nightly price-bar fetch the "
                "Opening Scalp study reads. Keep it after the 16:00 flow report.", (
            Field("slots.scalp_bars.grace_min", "Fire if late by at most",
                  "A missed night is not lost: the next top-up starts from the "
                  "latest stored bar.",
                  kind="int", unit="min", min=1, max=480, step=1),
            Field("slots.scalp_bars.*", "", "", kind="time"),
        ), restart=(OPTIONS,)),
        Section("Opening Scalp weekly refit", "When the chance-of-target weights "
                "are refitted. The weekday is set under Opening Scalp, Chance of "
                "target; this is the time of day, Central.", (
            Field("slots.scalp_prob.grace_min", "Fire if late by at most",
                  "A refit missed is not lost: the weights stay one week older.",
                  kind="int", unit="min", min=1, max=480, step=1),
            Field("slots.scalp_prob.*", "", "", kind="time"),
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
            Field("slots.idea_review.at", "Weekly trade-idea review",
                  "Once a week, on the week's last trading day. Only the time is "
                  "set here. Must be after the 15:05 settlement pass.",
                  kind="time", restart=(TIMERS,)),
            Field("slots.token_watch.at", "Schwab sign-in check",
                  "Once a day, every day: sends a Server alert when the 7-day "
                  "Schwab sign-in is close to running out.", kind="time",
                  restart=(TIMERS,)),
            Field("slots.flow_delta.at", "Flow instrumentation report", "",
                  kind="time", restart=(TIMERS,)),
            Field("slots.hiro_report.at", "Hedging-flow validation report",
                  "Scores the day's hedging-flow alerts against the price move "
                  "after each. Must be after the close.",
                  kind="time", restart=(TIMERS,)),
            Field("slots.label_journal.at", "Trade Analyzer outcome labelling",
                  "Fills in how each recommendation actually did once its "
                  "20-day horizon has passed. Must be after the close.",
                  kind="time", restart=(TIMERS,)),
            Field("slots.swing_refit.at", "Swing model refit (1st of the month)",
                  "Refits the Short Term model. It replaces the live model only "
                  "if it passes the checks under Swing model refit.",
                  kind="time", restart=(TIMERS,)),
            Field("slots.stock_rank.at", "Listed-stocks popularity re-rank (Saturday)",
                  "Refreshes the ranks in the listed-stocks classification file. "
                  "Only the time is set here; the day is always Saturday.",
                  kind="time", restart=(TIMERS,)),
            Field("slots.momentum.at", "Momentum recompute",
                  "Earlier than ~80 minutes after the close scores stale bars.",
                  kind="time", restart=(SENTIMENT, TRADE, WEBGUI)),
            Field("slots.calibration.at", "Signal calibration rebuild", "",
                  kind="time", restart=(OPTIONS, TRADE, WEBGUI)),
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
        Section("Index futures", "The /ES and /NQ tiles on the Macro Board "
                "follow the front-month contract (March, June, September, "
                "December; each expires on the third Friday of its month).", (
            Field("futures.roll_days_before_expiry",
                  "Switch to the next contract this many days before expiry",
                  "8 is the usual roll, the Thursday of the week before expiry. "
                  "0 keeps the expiring contract until expiry day.",
                  kind="int", unit="days", min=0, max=30),
        ), restart=()),
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
# Dealer Positioning heatmap — config/gamma_heat.toml
# ─────────────────────────────────────────────────────────────────────────────
# Read per paint by the page through the mtime-cached loader, so a saved change
# applies with no restart.
_GAMMA_HEAT = ConfigFile(
    name="gamma_heat.toml", title="Dealer Positioning heatmap", icon="grid_on",
    summary="What the heatmap on Dealer Positioning marks on its strike axis, "
            "and how its locked colour scale is set.",
    restart=(),
    sections=(
        Section("Balanced strikes",
                "A strike holding large calls and large puts that nearly cancel. "
                "It looks empty when the heatmap shows Net, so it is marked.", (
            _pct("balanced.max_polarity", "Lean at most",
                 "Net as a share of calls plus puts. Lower marks fewer strikes.",
                 lo=0, hi=100, step=1),
            _pct("balanced.min_size_quantile", "Larger than this share of strikes",
                 "Only the largest strikes on screen are marked.",
                 lo=0, hi=100, step=5),
            Field("balanced.max_marks", "Markers at most", "0 turns them off.",
                  kind="int", min=0, max=10, step=1),
        )),
        Section("Locked colour scale",
                "The Locked scale keeps one colour meaning one amount all "
                "session. The options service sets it from the start of the "
                "session and reads these each minute, so no restart is needed. "
                "Changing one during a session moves that session's scale "
                "within a minute.", (
            Field("lock.minutes", "Set it from the first",
                  "Until these have passed the scale adapts to what is on screen.",
                  kind="int", unit="min", min=5, max=390, step=5),
            _pct("lock.quantile", "Take this rank of the cells",
                 "95 ignores the few largest cells, so they do not set the scale.",
                 lo=50, hi=100, step=1),
            Field("lock.headroom", "Multiply by",
                  "Room above the first minutes' largest cells.",
                  kind="float", min=1, max=5, step=0.1),
        )),
        Section("Change view",
                "The heat map's Show picker can draw how each strike has moved "
                "instead of its level.", (
            Field("show.change_window_min", "Change over",
                  "The shorter of the two Change views looks back this far. "
                  "The other one looks back to the open.",
                  kind="int", unit="min", min=5, max=240, step=5),
        )),
        Section("Strikes shown",
                "Read by the page and the options service each time they draw "
                "or publish, so no restart is needed.", (
            Field("window.n_side", "Each side of the price",
                  "On the bars and the heat map.",
                  kind="int", unit="strikes", min=4, max=60, step=1),
            Field("window.spot_side", "Each side of the price, measured from spot",
                  "For the From spot frame. The service keeps this many extra "
                  "strikes around the day's low and high, in every view, every "
                  "minute: 10 costs about an eighth more history on a wide "
                  "day, 20 about two fifths. 0 keeps none.",
                  kind="int", unit="strikes", min=0, max=60, step=1),
        )),
        Section("Contour lines",
                "The heat map's Contours switch draws lines of equal value "
                "over the cells. Read each time the page draws.", (
            Field("contours.steps", "Levels each side of zero",
                  "The top of the colour scale, then each level half the one "
                  "above. 3 draws the full scale, a half and a quarter.",
                  kind="int", min=1, max=5, step=1),
            Field("contours.min_columns", "Shortest line drawn",
                  "A line spanning fewer minutes than this is a speck and is "
                  "left out. 0 draws everything.",
                  kind="int", unit="min", min=0, max=60, step=1),
            Field("contours.max_points", "Most points drawn",
                  "For each of the two signs. Past it the shortest lines are "
                  "dropped, so a busy session cannot slow the page.",
                  kind="int", unit="points", min=500, max=40000, step=500),
        )),
        Section("Smoothing",
                "The heat map's Smooth picker averages each cell with its "
                "neighbours before it is coloured. These are the widths at "
                "Medium; Light is half of them and Strong twice.", (
            Field("smooth.minutes", "Along time",
                  "How many minutes either side a cell is mostly averaged "
                  "over. Higher is smoother and lags further behind the "
                  "newest readings.", kind="float", unit="min",
                  min=0, max=30, step=0.5),
            Field("smooth.strikes", "Across strikes",
                  "How many strikes either side. Keep it under about 1, or "
                  "two neighbouring walls merge into one. 0 leaves strikes "
                  "alone.", kind="float", unit="strikes", min=0, max=5, step=0.1),
        )),
        Section("Projected afternoon",
                "From the open the heat maps run to the close. Right of the "
                "\"now\" line the cells are projected with price, open interest "
                "and volatility held where they are, on all four views, and the "
                "contour lines carry on through them, dashed. The options service "
                "reads these each minute, so no restart is needed.", (
            Field("forward.enabled", "Show the projected afternoon",
                  "Off shows the collected minutes only.", kind="bool"),
            Field("forward.step_min", "Price it every",
                  "The page fills the minutes between. Smaller is finer and "
                  "costs the service more each minute.",
                  kind="int", unit="min", min=1, max=30, step=1),
        )),
        Section("Gravity well",
                "The Gamma view's Gravity well switch draws net gamma by strike "
                "as ground, with the price as a ball on it.", (
            Field("well.height", "Height",
                  "Root draws each strike at the square root of its net gamma, "
                  "so the one largest strike does not flatten the rest. Linear "
                  "draws it in proportion. A strike keeps its side of zero "
                  "either way.", kind="choice", choices=("root", "linear")),
        )),
        Section("Ridge plot",
                "The Ridge plot switch draws one by-strike profile every so "
                "many minutes, stacked front to back, the earliest on top.", (
            Field("ridge.every_min", "A profile every",
                  "The latest reading is always drawn as well. Reload the page "
                  "after changing this: it changes how many profiles the chart "
                  "holds.", kind="int", unit="min", min=5, max=120, step=5),
            Field("ridge.overlap", "Typical peak spans",
                  "In rows, for the middle profile of those drawn. Higher "
                  "overlaps the profiles more. A profile several times the "
                  "rest is drawn in proportion.", kind="float", unit="rows",
                  min=0.5, max=8, step=0.5),
            Field("ridge.height", "Height",
                  "Root draws the square root of each value's size, so the "
                  "largest strike does not flatten the rest. Linear draws it "
                  "in proportion.", kind="choice", choices=("root", "linear")),
        )),
    ),
)

# ─────────────────────────────────────────────────────────────────────────────
# Raw feed and catch-up — config/feed.toml
# ─────────────────────────────────────────────────────────────────────────────
# Read per call through the mtime-cached loader (shared/feed_config.py), so a
# saved change applies with no restart.
_FEED = ConfigFile(
    name="feed.toml", title="Raw feed and catch-up", icon="storage",
    summary="Whether each minute's option chains are kept on disk and checked: "
            "the first step towards catching up after a restart with no gap "
            "in the day's data.",
    restart=(),
    sections=(
        Section("Raw feed",
                "Off is how the app ran before the feed: nothing is kept. Tee "
                "keeps each minute and checks that working from the kept copy "
                "gives the same numbers, with no extra Schwab calls.", (
            Field("feed.mode", "Mode",
                  "Off, or tee (keep each minute and check it).",
                  kind="choice", choices=("off", "tee")),
            Field("feed.keep_sessions", "Sessions kept",
                  "About 1 GB each. They are never backed up.",
                  kind="int", min=1, max=30, step=1),
            Field("feed.zlib_level", "Compression",
                  "1 is fastest, 9 is smallest. Saving runs during collection "
                  "every minute, so a lower number delays collection less.",
                  kind="int", min=1, max=9, step=1),
        )),
    ),
)

# ─────────────────────────────────────────────────────────────────────────────
# Opening Scalp — config/scalp.toml
# ─────────────────────────────────────────────────────────────────────────────
# Read per call through the mtime-cached loader (shared/scalp_config.py), so a
# saved change applies with no restart. The call and bar-fetch TIMES are slots in
# sessions.toml, not here.
_SCALP = ConfigFile(
    name="scalp.toml", title="Opening Scalp", icon="bolt",
    summary="The same-day option scalp entered just after the first five minutes "
            "of the session: which symbols it covers, how the option is chosen "
            "and closed, and how the direction call and its study are scored.",
    restart=(),
    sections=(
        Section("Symbols",
                "The symbols the page calls and the bars that are kept for them "
                "and for what the call reads. An input named below that is "
                "missing from the bar list is added for you.", (
            Field("universe.symbols", "Symbols called", "", kind="symbols"),
            Field("bars.symbols", "Bars kept", "", kind="symbols"),
        )),
        Section("Sizes",
                "Per symbol, in dollars. A symbol not listed reads as 1.00.", (
            Field("strike_step.*", "Strike step",
                  "The gap between listed strikes. The scalp's strike is the "
                  "nearest multiple of this to the open.",
                  kind="float", unit="$", min=0.01, max=100, step=0.5),
            Field("width.*", "Spread width",
                  "The credit-spread width the exit rule compares with.",
                  kind="float", unit="$", min=0.01, max=100, step=0.5),
        )),
        Section("What the call reads",
                "Per symbol: the overnight futures, the sector funds that lead "
                "it, and the volatility index. A blank one is left out of the "
                "call, never counted as flat.", (
            Field("inputs.*.futures", "Futures",
                  "The overnight futures contract. Blank leaves it out.",
                  kind="text", blank_ok=True),
            Field("inputs.*.etfs", "Sector funds",
                  "Funds whose opening move votes on the direction.",
                  kind="symbols"),
            Field("inputs.*.vol", "Volatility index",
                  "The index whose overnight move votes against a rise. Blank "
                  "leaves it out.", kind="text", blank_ok=True),
        )),
        Section("The scalp",
                "How the long option is picked and when it is closed.", (
            Field("trade.otm_steps", "Strikes out of the money",
                  "How many strikes beyond the open the long option sits. 0 is "
                  "at the money.", kind="int", min=0, max=20, step=1),
            Field("trade.target_pct", "Profit target",
                  "Close when the option has gained this much of its entry price.",
                  kind="float", unit="%", min=1, max=1000, step=5),
            Field("trade.stop_pct", "Loss stop",
                  "Close when the option has lost this much of its entry price.",
                  kind="float", unit="%", min=1, max=100, step=5),
            Field("trade.time_stop_min", "Time stop",
                  "Close this long after entry whatever the price is.",
                  kind="int", unit="min", min=1, max=390, step=5),
            Field("trade.iv_scale", "Volatility multiplier",
                  "Multiplies the implied volatility the model prices with. 1 "
                  "uses the volatility as observed.",
                  kind="float", min=0.1, max=5, step=0.05),
            Field("trade.min_premium", "Smallest premium",
                  "An option cheaper than this per share is not tradeable and the "
                  "trade is skipped, not scored.",
                  kind="float", unit="$", min=0, max=50, step=0.01),
            Field("trade.chain_strikes", "Strikes saved",
                  "Strikes either side of the open kept when the morning's chain "
                  "is saved.", kind="int", min=1, max=100, step=1),
        )),
        Section("Direction",
                "How far a move has to be before the call counts it.", (
            Field("direction.dead_zone_pct", "Flat return band",
                  "A return smaller than this is flat, not up or down.",
                  kind="float", unit="%", min=0, max=5, step=0.01),
            Field("direction.dead_zone_frac", "Neutral band",
                  "A reading's place in its range (0 to 1) within this of the "
                  "middle is neutral.", kind="float", min=0, max=0.5, step=0.01),
            Field("direction.open_dead_zone_pct", "Flat opening band",
                  "The same flat band for the first five minutes' move.",
                  kind="float", unit="%", min=0, max=5, step=0.01),
            Field("direction.buckets", "Confidence cut points",
                  "Two shares of inputs agreeing with the call, low then high, "
                  "that split it into low, medium and high confidence.",
                  kind="pair", min=0.01, max=0.99, step=0.05),
        )),
        Section("Study and page", "", (
            Field("study.iv_scales", "Volatility multipliers tried",
                  "The study re-prices every trade at each of these, to show how "
                  "much the answer leans on the volatility assumption.",
                  kind="numbers", min=0.1, max=5),
            Field("study.min_samples", "Smallest sample",
                  "The fewest scored mornings a group needs before it can be "
                  "marked as an edge. It gates only that verdict: every group's "
                  "rate is still shown, with its count.",
                  kind="int", min=1, max=1000, step=1),
            Field("study.scorecard_days", "Scorecard window",
                  "Trading days the scorecard looks back over.",
                  kind="int", unit="days", min=1, max=250, step=1),
            Field("news.items", "Headlines per call",
                  "Headlines shown beside each morning call.",
                  kind="int", min=0, max=20, step=1),
            Field("context.report_url", "Reports link",
                  "The premarket and prior-close reports the page links to. "
                  "Shown beside the call, not part of its probability.",
                  kind="text"),
        )),
        Section("Intraday calls",
                "A new call every few minutes through the session, Central "
                "time. A slot is entered on the last 5-minute bar that has "
                "closed.", (
            Field("intraday.start", "First call",
                  "The first slot of the day: not before 08:45, and no slot may "
                  "fall at 08:36, the morning call's time.", kind="time"),
            Field("intraday.end", "Last call",
                  "The last slot of the day. It must be after the first, and the "
                  "last call's entry plus the time stop must not pass the 15:00 "
                  "close.", kind="time"),
            Field("intraday.interval_min", "Minutes between calls",
                  "The gap from one slot to the next.",
                  kind="int", unit="min", min=1, max=390, step=1),
            Field("intraday.grace_min", "Fire if late by at most",
                  "How many minutes late a slot may still fire.",
                  kind="int", unit="min", min=1, max=60, step=1),
            Field("intraday.dead_zone_pct", "Flat intraday band",
                  "A move smaller than this is flat, not up or down.",
                  kind="float", unit="%", min=0, max=5, step=0.01),
            Field("intraday.breadth_agree", "Breadth agreement",
                  "The share of the ten largest stocks that must lean one way "
                  "for the breadth to count (0 to 1).",
                  kind="float", min=0.01, max=1, step=0.05),
            Field("intraday.vwap_max_missing", "Missing bars allowed for VWAP",
                  "The share of the session's 5-minute bars, from the open to the "
                  "entry bar, that may be missing before the side of VWAP is "
                  "left out (0 up to, not including, 1).",
                  kind="float", min=0, max=0.99, step=0.05),
            Field("intraday.min_brier_margin", "Edge margin over the base rate",
                  "How much lower than the base rate's a bucket's Brier score must "
                  "be, at every volatility multiplier, before an intraday call "
                  "counts as an edge. 0.005 is about a 7-point win-rate difference "
                  "near a 50% win rate (0 up to, not including, 0.25).",
                  kind="float", min=0, max=0.249, step=0.001),
            Field("intraday.promotion_sessions", "Sessions to promote",
                  "Sessions of calls a group needs before it can be promoted.",
                  kind="int", min=1, max=1000, step=1),
            Field("intraday.early_close_days", "Early-close days",
                  "Sessions that close early (YYYY-MM-DD). On such a day no call "
                  "is made whose entry plus the time stop would pass the early "
                  "close, and the study leaves those slots out. Add next year's "
                  "half days here.", kind="dates"),
            Field("intraday.early_close_time", "Early close",
                  "When an early-close session ends, Central time: after 08:30 "
                  "and not after 15:00.", kind="time"),
        )),
        Section("Intraday blocks",
                "The three parts of the day the study keeps apart. Every slot "
                "belongs to one block and blocks may not overlap, or the "
                "defaults apply to all three.", (
            Field("intraday.blocks.morning.start", "Morning starts",
                  "The first slot in the morning block.", kind="time"),
            Field("intraday.blocks.morning.end", "Morning ends",
                  "The last slot in the morning block.", kind="time"),
            Field("intraday.blocks.midday.start", "Midday starts",
                  "The first slot in the midday block.", kind="time"),
            Field("intraday.blocks.midday.end", "Midday ends",
                  "The last slot in the midday block.", kind="time"),
            Field("intraday.blocks.afternoon.start", "Afternoon starts",
                  "The first slot in the afternoon block.", kind="time"),
            Field("intraday.blocks.afternoon.end", "Afternoon ends",
                  "The last slot in the afternoon block.", kind="time"),
        )),
        Section("Confirmation",
                "The check scored beside each intraday call. It never changes "
                "the call.", (
            Field("confirm.flow_window_min", "Flow look-back",
                  "Minutes of flow alerts read.",
                  kind="int", unit="min", min=1, max=390, step=5),
            Field("confirm.flow_margin", "Flow lead needed",
                  "How many more alerts one side needs than the other to lean.",
                  kind="float", min=0.5, max=100, step=0.5),
            Field("confirm.netprem_margin", "Net premium lead needed",
                  "The share of total net premium one side must lead by to "
                  "lean (0 to 1).", kind="float", min=0, max=1, step=0.01),
            Field("confirm.sides_majority", "Bought or sold majority needed",
                  "The share of an option alert's labelled volume that must be "
                  "bought (or sold) before the alert counts as bullish or "
                  "bearish. Above 0.5, up to 1.", kind="float", min=0.51,
                  max=1, step=0.01),
            Field("confirm.breadth_min_members", "Big names needed for breadth",
                  "How many of the ten biggest stocks must have a usable reading "
                  "before a breadth is read: a usable dealer wall for the dealer "
                  "read's breadth of room, and a move outside the flat band over "
                  "their last 15 minutes for the intraday price breadth.",
                  kind="int", min=1, max=50, step=1),
            Field("confirm.min_sources", "Sources needed for a word",
                  "How many of the four sources (flow, net premium, dealer "
                  "positioning, sentiment) must give a reading before they are "
                  "summed up as Confirms, Mixed or Contradicts. One source alone "
                  "is not a confirmation.", kind="int", min=1, max=4, step=1),
            Field("confirm.max_age_min", "Oldest reading used",
                  "A confirming reading older than this is treated as absent.",
                  kind="int", unit="min", min=1, max=60, step=1),
            Field("confirm.cuts", "Mixed band",
                  "Contradicts at or below the low number, Confirms at or above "
                  "the high one, Mixed between. Low must be below zero and high "
                  "above it.", kind="pair", min=-1, max=1, step=0.01),
            Field("confirm.weights.flow", "Weight: flow",
                  "What flow counts for in the score. 0 leaves it out.",
                  kind="float", min=0, max=10, step=0.1),
            Field("confirm.weights.netprem", "Weight: net premium",
                  "What net premium counts for in the score.",
                  kind="float", min=0, max=10, step=0.1),
            Field("confirm.weights.dealer", "Weight: dealer positioning",
                  "What dealer positioning counts for in the score.",
                  kind="float", min=0, max=10, step=0.1),
            Field("confirm.weights.sentiment", "Weight: sentiment",
                  "What the sentiment read counts for in the score.",
                  kind="float", min=0, max=10, step=0.1),
        )),
        Section("Chance of target",
                "The fitted chance a call reaches its target: each factor is "
                "scaled, pushes the chance by a weight, and the weights are "
                "refitted weekly from the stored history. Until a factor has "
                "history its weight is the assumed one below.", (
            Field("prob.show", "Show the chance",
                  "Show the fitted chance beside each call.", kind="bool"),
            Field("prob.ema_period", "Moving average bars",
                  "Bars in the moving average whose slope is a factor.",
                  kind="int", min=2, max=200, step=1),
            Field("prob.slope_bars", "Slope bars",
                  "Bars the moving average's slope is measured over.",
                  kind="int", min=1, max=50, step=1),
            Field("prob.rsi_period", "RSI bars",
                  "Bars in the RSI factor.", kind="int", min=2, max=100, step=1),
            Field("prob.timeframes", "Bar sizes",
                  "Minutes per bar the slope and RSI factors are read on. Whole "
                  "multiples of 5, rising.", kind="numbers", unit="min",
                  min=5, max=390),
            Field("prob.lookback_sessions", "Warm-up sessions",
                  "Sessions of bars read before an entry to warm up the moving "
                  "average and RSI.", kind="int", min=1, max=60, step=1),
            Field("prob.cap", "Factor cap",
                  "The most one scaled factor may count, in scaled units, so one "
                  "extreme reading cannot dominate.",
                  kind="float", min=0.1, max=10, step=0.1),
            Field("prob.room_cap", "Room cap",
                  "The most the room to the nearest wall counts, as a multiple "
                  "of the target's points.",
                  kind="float", min=0.1, max=10, step=0.1),
            Field("prob.flow_cap", "Flow alert cap",
                  "The most flow alerts counted on one side.",
                  kind="int", min=1, max=50, step=1),
            Field("prob.assumed_share", "Learned share before a weight is trusted",
                  "A factor's weight is shown as assumed while less than this "
                  "share of it was learned from data (the rest is its starting "
                  "assumption). Between 0 and 1, not either end.",
                  kind="float", min=0.01, max=0.99, step=0.05),
        )),
        Section("Chance of target: assumed weights",
                "What each factor is assumed to push the chance by, before its "
                "own history has moved it. Starting guesses, not measurements: "
                "the weekly refit replaces them as history grows. 0 assumes no "
                "push.", (
            Field("prob.priors.slope_5", "Slope, 5-minute",
                  "Assumed push from the 5-minute moving average "
                  "sloping with the call.",
                  kind="float", min=-2, max=2, step=0.01),
            Field("prob.priors.slope_15", "Slope, 15-minute",
                  "The same on 15-minute bars.",
                  kind="float", min=-2, max=2, step=0.01),
            Field("prob.priors.slope_60", "Slope, 60-minute",
                  "The same on 60-minute bars.",
                  kind="float", min=-2, max=2, step=0.01),
            Field("prob.priors.rsi_5", "RSI, 5-minute",
                  "Assumed push from the 5-minute RSI reading with the "
                  "call.",
                  kind="float", min=-2, max=2, step=0.01),
            Field("prob.priors.rsi_15", "RSI, 15-minute",
                  "The same on 15-minute bars.",
                  kind="float", min=-2, max=2, step=0.01),
            Field("prob.priors.rsi_60", "RSI, 60-minute",
                  "The same on 60-minute bars.",
                  kind="float", min=-2, max=2, step=0.01),
            Field("prob.priors.room", "Room to the nearest wall",
                  "Assumed push from the room between the entry and the "
                  "nearest dealer wall in the call's direction.",
                  kind="float", min=-2, max=2, step=0.01),
            Field("prob.priors.breadth_room", "Room across the big ten",
                  "Assumed push from the same room across the ten "
                  "biggest stocks.",
                  kind="float", min=-2, max=2, step=0.01),
            Field("prob.priors.flip_side", "Side of the gamma flip",
                  "Assumed push from the price being on the call's side "
                  "of the gamma flip.",
                  kind="float", min=-2, max=2, step=0.01),
            Field("prob.priors.netprem", "Net premium",
                  "Assumed push from net premium leaning with the call.",
                  kind="float", min=-2, max=2, step=0.01),
            Field("prob.priors.flow", "Flow alerts",
                  "Assumed push from flow alerts leaning with the call.",
                  kind="float", min=-2, max=2, step=0.01),
            Field("prob.priors.sentiment", "Sentiment",
                  "Assumed push from the sentiment read agreeing with "
                  "the call.",
                  kind="float", min=-2, max=2, step=0.01),
            Field("prob.prior_sd.wide", "Prior spread: loose",
                  "How far a weight may move from its assumed value for the "
                  "factors with long history (price inputs, slopes, momentum, "
                  "time of day), so their history decides them (the spread of "
                  "the pull; smaller holds harder). Above 0.",
                  kind="float", min=0.01, max=10, step=0.05),
            Field("prob.prior_sd.tight", "Prior spread: firm",
                  "The same for the thin sources (dealer read, net premium, "
                  "flow, sentiment), held near their assumed value until their "
                  "history grows. Above 0.",
                  kind="float", min=0.01, max=10, step=0.01),
        )),
        Section("Chance of target: weekly refit",
                "The refit's honesty check, and which day it runs. The time of "
                "day is under the schedule, Opening Scalp weekly refit.", (
            Field("prob.refit_weekday", "Refit day",
                  "0 is Monday, 5 is Saturday, 6 is Sunday.",
                  kind="int", min=0, max=6, step=1),
            Field("prob.min_train_weeks", "Weeks before the check",
                  "Weeks of history the walk-forward check needs before it is "
                  "run.", kind="int", unit="weeks", min=1, max=104, step=1),
            Field("prob.min_brier_margin", "Margin over the usual rate",
                  "How far below the usual rate's Brier score a fit must land "
                  "for the \"beats the usual rate on unseen days\" line to read "
                  "yes. It decides only that line: the chance is shown either "
                  "way (0 up to, not including, 0.25).",
                  kind="float", min=0, max=0.249, step=0.001),
        )),
        Section("Gamma regime study",
                "The declared test of whether the dealer gamma regime changes how "
                "often the 15-minute calls' long options reach their target. "
                "Declared 2026-10-25: change a value only to declare a new "
                "study. Shown, not edited here: these were declared on "
                "2026-10-25.", (
            Field("regime.start_day", "First session counted",
                  "The first session of the test window. Earlier sessions are "
                  "never counted.", kind="date"),
            Field("regime.min_sessions", "Sessions before the study is read",
                  "Usable sessions the afternoon needs graded before the result "
                  "is read, once.",
                  kind="int", unit="sessions", min=1, max=1000, step=1),
            Field("regime.alpha", "Significance level",
                  "The p-value a block's one-sided permutation test must reach "
                  "(0 to 1, not either end).",
                  kind="float", min=0.001, max=0.5, step=0.001),
            Field("regime.min_effect_pp", "Smallest effect",
                  "The smallest gap between the negative and positive regimes' "
                  "win rates that counts as an effect.",
                  kind="float", unit="points", min=0, max=50, step=0.5),
            Field("regime.order", "Order the blocks are tested",
                  "Each of morning, midday and afternoon once. A block is "
                  "tested only if the one before it passed.", kind="phrases"),
            Field("regime.permutations", "Permutations",
                  "Random relabelings the permutation test draws.",
                  kind="int", min=100, max=100000, step=100),
            Field("regime.seed", "Random seed",
                  "Fixed, so a re-run gives the same p-value.",
                  kind="int", min=0, max=2147483647, step=1),
            Field("regime.min_sign_agreement", "Backfill agreement needed",
                  "The share of slots, backfilled against live, whose net-gamma "
                  "sign must agree before the study runs.",
                  kind="fraction", unit="%", min=1, max=100, step=1),
            Field("regime.agree_min_sessions", "Sessions the agreement covers",
                  "Test-window sessions that agreement check must cover.",
                  kind="int", unit="sessions", min=1, max=100, step=1),
        ), readonly=True),
    ),
)

# ─────────────────────────────────────────────────────────────────────────────
# Public edge — config/edge.toml
# ─────────────────────────────────────────────────────────────────────────────
# Read by deploy/caddy/generate_caddyfile.py only. No service restart applies it:
# the Caddyfile has to be regenerated and Caddy reloaded, as root (see caution).
_EDGE = ConfigFile(
    name="edge.toml", title="Public edge limits", icon="speed",
    summary="How many pages one visitor may load on the public site per window, "
            "and the largest sign-in request the edge passes on.",
    restart=(),
    caution="Needs a Caddy built with the rate-limit module. Saving here changes "
            "nothing until the Caddyfile is regenerated and Caddy reloaded, as "
            "root - see the runbook's Edge rate limit section.",
    sections=(
        Section("Sign-in request size", "", (
            Field("limits.login_body_kb", "Largest sign-in request",
                  "A real sign-in is a few hundred bytes. Larger requests to the "
                  "sign-in address are refused before they reach the app.",
                  kind="int", unit="KB", min=1, max=1024, step=1),
        )),
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
# Swing model refit — config/swing_model.toml
# ─────────────────────────────────────────────────────────────────────────────
# Read by trade-analyzer/fit_swing_model.py at fit time: nothing to restart.
_SWING_MODEL = ConfigFile(
    name="swing_model.toml", title="Swing model refit", icon="model_training",
    summary="When a monthly refit of the Short Term model may replace the live one.",
    restart=(),
    sections=(
        Section("Replace the live model only if", "A fit that fails either check "
                "is kept beside the live model as a rejected copy, and the live "
                "model stays as it was.", (
            Field("refit.min_coverage", "Share of symbols that loaded",
                  "Of the 78 symbols the model is fitted on. A symbol whose "
                  "history fails to load is dropped from the fit.",
                  kind="fraction", min=50, max=100, step=1),
            Field("refit.min_oos_ic", "Out-of-sample score above",
                  "How well the new fit ranked stocks on data it was not fitted "
                  "on (information coefficient). 0 means any real edge at all.",
                  kind="float", min=-0.1, max=0.2, step=0.005),
        )),
    ),
)

# ─────────────────────────────────────────────────────────────────────────────
# Listed-stocks re-rank — config/listed_stocks.toml
# ─────────────────────────────────────────────────────────────────────────────
# Read by tools/rank_listed_stocks.py at run time, and [display] by
# services/trade_svc/listed_stocks.py on every analysis: nothing to restart.
_LISTED_STOCKS = ConfigFile(
    name="listed_stocks.toml", title="Listed-stocks re-rank", icon="leaderboard",
    summary="How the weekly re-rank scores the listed-stocks classification "
            "file, and how much of it the Trade Analyzer shows.",
    restart=(),
    sections=(
        Section("Trade Analyzer", "The Overview lists the top-ranked stocks of "
                "the industry the symbol on screen belongs to.", (
            Field("display.industry_top", "Stocks listed per industry",
                  "The next analysis after a change uses the new count.",
                  kind="int", min=1, max=20, step=1),
        )),
        Section("Popularity score", "Each stock's average dollar volume and its "
                "market cap are each turned into a percentile across the whole "
                "file, then blended.", (
            Field("blend.dollar_volume_weight", "Dollar volume's share of the score",
                  "Market cap gets the rest. 50% is an even blend; 100% ranks on "
                  "dollar volume alone.", kind="fraction", min=0, max=100, step=5),
            Field("inputs.market_cap", "Market cap comes from",
                  "schwab: Schwab's current figure, with the file's own column "
                  "where Schwab has none. file: the file's column first, which "
                  "never changes from week to week.",
                  kind="choice", choices=("schwab", "file")),
        )),
        Section("Schwab calls", "The run asks for every symbol in the file, in "
                "batches, through the proxy every service shares.", (
            Field("fetch.fundamentals_batch", "Symbols per volume and market cap call",
                  "", kind="int", min=1, max=300, step=1),
            Field("fetch.quotes_batch", "Symbols per price call", "", kind="int",
                  min=1, max=300, step=1),
            Field("fetch.pause_sec", "Wait between calls",
                  "The proxy allows 5 calls a second across everything; 2 seconds "
                  "uses a tenth of it.", kind="float", unit="s", min=0.2, max=30,
                  step=0.1),
        )),
        Section("Keep last week's ranks if", "", (
            Field("safety.min_coverage", "Schwab answered for fewer than",
                  "Of the file's symbols, on volume and on price. Below this the "
                  "run changes nothing and reports a failure.",
                  kind="fraction", min=50, max=100, step=1),
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
        Section("Marking positions", "", (
            Field("marks.chain_max_age_sec", "Oldest chain a mark may be priced from",
                  "Applies when the proxy answers from its own stored chains. "
                  "An entry or a close is always priced from a chain fetched at "
                  "that moment. Applies at once.",
                  kind="int", unit="seconds", min=0, max=300),
            Field("marks.zero_bid_max_ask", "Largest offer for a leg with no bid",
                  "A leg nobody is bidding for is still priced when its offer is "
                  "at or under this, in dollars a share: a nearly worthless "
                  "option, such as the long leg of a spread that has won. Above "
                  "it the quote is treated as broken and the position is not "
                  "priced that cycle. 0 never prices a leg with no bid. Applies "
                  "at once.",
                  kind="money", min=0, max=5, step=0.05, restart=()),
        )),
        Section("Trade ideas in the Paper Ledger",
                "Every hourly trade idea that is posted is also booked into the "
                "Paper Ledger, so it is measured like any other trade. A weekly "
                "review reads how they did.", (
            Field("idea_trades.enabled", "Book the posted ideas",
                  "Off stops new bookings. Ideas already booked are still "
                  "marked and settled.", kind="bool"),
            Field("idea_trades.quantity", "Contracts per idea",
                  "How many contracts each booked idea trades.",
                  kind="int", min=1, max=100, step=1),
            Field("idea_trades.mark_interval_min", "Minutes between marks",
                  "How often the open idea trades are priced.",
                  kind="int", unit="min", min=1, max=60, step=1),
            Field("idea_trades.debit_stop_frac", "Debit idea stop-out",
                  "A debit idea is closed when it has lost this fraction of what "
                  "it cost. 0.60 is a 60% loss of the debit paid.",
                  kind="float", min=0.05, max=1.0, step=0.05),
            Field("idea_trades.clean_mae_pct", "Largest drawdown of a clean win",
                  "The weekly review calls a winner clean when its worst "
                  "drawdown before the win stayed within this percent of the "
                  "risk.", kind="float", unit="%", min=1, max=100, step=1),
            Field("idea_trades.min_n", "Fewest ideas before a conclusion",
                  "The weekly review draws no conclusion from a group with "
                  "fewer booked ideas than this.",
                  kind="int", min=1, max=100, step=1),
            Field("idea_trades.review_model", "Weekly review model",
                  "The Claude model tier that writes the weekly judgment. Use a "
                  "tier name such as sonnet, not a version. Applies at the "
                  "next weekly run.", kind="text", restart=()),
        ), restart=(OPTIONS,)),
    ),
)

# ─────────────────────────────────────────────────────────────────────────────
# Momentum / Bull-Bear Map — config/momentum.toml
# ─────────────────────────────────────────────────────────────────────────────
_MOMENTUM = ConfigFile(
    name="momentum.toml", title="Momentum & Bull / Bear Map", icon="account_tree",
    summary="How GICS sub-industries are scored, and how the Bull / Bear Map "
            "fetches its live day moves.",
    restart=(SENTIMENT,),
    sections=(
        Section("Sub-industry baskets",
                "A GICS sub-industry has no ETF, so it is scored as an equal-weight "
                "basket of its listed stocks.", (
            Field("subindustry.min_members", "Fewest stocks in a basket",
                  "Below this many usable stocks the sub-industry is left out and "
                  "its stocks are scored on their own. At 1, a basket can be one "
                  "stock under another name.",
                  kind="int", min=1, max=5, step=1),
        )),
        Section("Live quotes", "The Bull / Bear Map's day-move column.", (
            Field("bullbear.quote_batch", "Symbols per quote request",
                  "The map asks Schwab for every symbol's day move in batches of "
                  "this size. 375 is the largest batch measured to come back in "
                  "one call.",
                  kind="int", min=50, max=500, step=25),
        )),
    ),
)

# ─────────────────────────────────────────────────────────────────────────────
# Market news — config/news.toml
# ─────────────────────────────────────────────────────────────────────────────
def _pts(key, label, help=""):
    """A points value in the impact score: whole points, either sign."""
    return Field(key, label, help, kind="int", unit="pts", min=-10, max=10, step=1)


# Mirrors shared.news_config.TRANSFORMS / SCHEDULES (pinned equal by
# tests/test_config_schema.py; this module stays import-free).
INDICATOR_TRANSFORMS = ("pct_mom", "change_k", "level_pct", "level_k", "pct_saar")
INDICATOR_SCHEDULES = ("bls", "bea", "fred")

# How the calendar's sources read on screen (``calendar.sources.<name>``).
CALENDAR_SOURCE_NAMES = {
    "fed": "Federal Reserve calendar", "bls": "BLS", "bea": "BEA",
    "fred_calendar": "FRED release calendar", "fred_api": "FRED API",
    "fredgraph": "FRED graph download", "nasdaq_ipo": "Nasdaq IPOs",
}

_NEWS_IMPACT_SECTIONS = (
    Section("Impact", "Every headline gets a High, Med or Low rank from points: "
            "keywords, the feed, insider-buy size and filing type add up to a "
            "score, and these cut it into the three ranks.", (
        Field("impact.high_at", "High at", "A score at or above this is High.",
              kind="int", unit="pts", min=1, max=30, step=1),
        Field("impact.med_at", "Med at", "A score at or above this (and below High) "
              "is Med; anything lower is Low.", kind="int", unit="pts", min=0,
              max=30, step=1),
        Field("impact.stale_after_h", "High fades after",
              "A High item older than this shows as Med.", kind="int", unit="h",
              min=1, max=168, step=1),
        _pts("impact.multi_source", "Carried by two or more feeds",
             "Added when more than one feed ran the same story."),
        _pts("impact.watchlist", "About a followed ticker",
             "Added when the item is tagged with a ticker you follow."),
        Field("impact.match_teaser", "Match keywords in the summary too",
              "Off matches the headline only, which is less noisy.", kind="bool"),
    )),
    Section("Impact keywords", "Tiers of words and phrases. A tier adds its "
            "points once, however many of its words a headline contains. "
            "Matching ignores case.", (
        _pts("impact.keywords.*.points", "Points",
             "Added when any word of this tier matches."),
        Field("impact.keywords.*.words", "Words",
              "Type a word or phrase and press Enter; each chip is matched as a "
              "whole phrase.", kind="phrases"),
    )),
    Section("Impact by feed", "Points added for the feed an item came from. A "
            "story on several feeds takes the highest; a feed not listed adds "
            "nothing.", (
        _pts("impact.source_points.*", ""),
    )),
    Section("Impact: insider buys", "Points for an SEC Form 4 open-market buy, by "
            "the total dollars bought.", (
        Field("impact.form4.small_usd", "Small buy from", "", kind="money",
              min=0, max=1e10, step=50000),
        _pts("impact.form4.small", "Small buy points"),
        Field("impact.form4.large_usd", "Large buy from", "", kind="money",
              min=0, max=1e10, step=100000),
        _pts("impact.form4.large", "Large buy points"),
        Field("impact.form4.huge_usd", "Very large buy from", "", kind="money",
              min=0, max=1e11, step=1000000),
        _pts("impact.form4.huge", "Very large buy points"),
        _pts("impact.form4.officer", "Bought by an officer or director",
             "Added on top of the size points."),
    )),
    Section("Impact: SEC filings", "Points for an offering filing, by its exact "
            "form type.", (
        _pts("impact.filings.untracked", "On a ticker you do not follow",
             "Usually a micro-cap; a negative value pushes these down."),
        _pts("impact.filings.*", ""),
    )),
)

_NEWS_CALENDAR_SECTIONS = (
    Section("Calendar", "The economic calendar: Fed events, data release dates "
            "and the latest values, IPOs and dividends.", (
        Field("calendar.enabled", "Calendar on", "Off stops every calendar fetch.",
              kind="bool"),
        Field("calendar.refresh_min", "Refresh schedules every",
              "Release schedules, Fed events and dividends. A source may set its "
              "own.", kind="int", unit="min", min=5, max=1440, step=5),
        Field("calendar.values_refresh_min", "Refresh values every",
              "Latest indicator values, outside a release watch.", kind="int",
              unit="min", min=15, max=1440, step=15),
        Field("calendar.release_poll_min", "During a release, check every",
              "How often a just-due release is checked for its new value.",
              kind="int", unit="min", min=1, max=30, step=1),
        Field("calendar.release_watch_min", "Watch a release for",
              "How long after its scheduled time a release keeps being checked.",
              kind="int", unit="min", min=5, max=480, step=5),
        Field("calendar.actual_fresh_h", "Mark a new value as released for",
              "", kind="int", unit="h", min=1, max=168, step=1),
    )),
    Section("Calendar sources", "Where each part of the calendar comes from.", (
        Field("calendar.sources.*.enabled", "Enabled", "", kind="bool"),
        Field("calendar.sources.*.url", "Address",
              "Words in {braces} are filled in by the collector.", kind="text"),
        Field("calendar.sources.*.user_agent", "User-Agent",
              "Empty sends the feed User-Agent. BLS refuses a browser one; "
              "Nasdaq requires it.", kind="text", optional=True, blank_ok=True),
        Field("calendar.sources.*.refresh_min", "Refresh every",
              "Unset uses the calendar's own refresh.", kind="int", unit="min",
              min=5, max=1440, step=5, optional=True),
        Field("calendar.sources.*.accept", "Accept header",
              "The response type the source is asked for.", kind="text",
              optional=True),
    )),
    Section("Fed events", "From the Federal Reserve's own calendar.", (
        Field("calendar.fed.types", "Event types shown",
              "Fed calendar types, e.g. FOMC, Beige, Speeches, Testimony.",
              kind="phrases"),
        Field("calendar.fed.horizon_days", "Look ahead",
              "An FOMC meeting can be six weeks out.", kind="int", unit="days",
              min=1, max=180, step=1),
        Field("calendar.fed.speech_horizon_days", "Look ahead for speeches",
              "Board speeches are frequent, so they get a shorter window.",
              kind="int", unit="days", min=1, max=90, step=1),
    )),
    Section("Other releases", "", (
        Field("calendar.events.extra_releases", "Extra releases shown",
              "Release names, as the BLS or BEA schedule spells them, shown as "
              "dated events without a value tile.", kind="phrases"),
        Field("calendar.events.high_impact", "Highlight events whose title contains",
              "An event whose title contains one of these, in any case, is drawn "
              "highlighted on the calendar. The Fed calendar names an FOMC meeting "
              "\"FOMC statement\" and its press conference \"Press conference\"; "
              "\"- Chair\" matches the Chair's own speeches and testimony and never "
              "a Vice Chair's. Leave it empty to highlight no event.",
              kind="phrases"),
    )),
    Section("IPOs", "From Nasdaq's IPO calendar.", (
        Field("calendar.ipo.min_offer_usd", "Smallest deal shown",
              "About half of the rows are SPACs and tiny deals.", kind="money",
              min=0, max=1e11, step=10000000),
        Field("calendar.ipo.lookback_days", "Keep priced deals for", "",
              kind="int", unit="days", min=0, max=60, step=1),
    )),
    Section("Dividends", "Collected by the trade service for the followed "
            "tickers.", (
        Field("calendar.dividends.enabled", "Dividends on", "", kind="bool"),
        Field("calendar.dividends.refresh_at", "Refresh at",
              "Central time, trading days.", kind="time"),
        Field("calendar.dividends.horizon_days", "Look ahead", "", kind="int",
              unit="days", min=1, max=120, step=1),
        Field("calendar.dividends.lookback_days", "Keep past dates for", "",
              kind="int", unit="days", min=0, max=30, step=1),
        Field("calendar.dividends.retry_min", "Retry a failed pull after",
              "How long the trade service waits before trying a failed dividend "
              "pull again.", kind="int", unit="min", min=1, max=1440, step=1,
              restart=(TRADE,)),
    ), restart=(TRADE, NEWS)),
    Section("Economic indicators", "One entry per indicator, in tile order. "
            "Indicators sharing a tile name are shown together.", (
        Field("calendar.indicators.*.enabled", "Enabled", "", kind="bool"),
        Field("calendar.indicators.*.label", "Name", "", kind="text"),
        Field("calendar.indicators.*.series", "FRED series",
              "The FRED series id the value is read from.", kind="text"),
        Field("calendar.indicators.*.transform", "Shown as",
              "pct_mom: % change on the month · change_k: change in thousands · "
              "level_pct: the level, as a percent · level_k: the level in "
              "thousands · pct_saar: annualised % rate.", kind="choice",
              choices=INDICATOR_TRANSFORMS),
        Field("calendar.indicators.*.schedule", "Release dates from",
              "bls or bea: matched by release name · fred: by release number.",
              kind="choice", choices=INDICATOR_SCHEDULES),
        Field("calendar.indicators.*.match", "Release name contains",
              "For bls and bea schedules. Surrounding spaces are trimmed, and it "
              "is a prefix match on the release name: \"GDP (\" picks only the "
              "releases starting with that, where \"GDP\" also picks \"GDP by "
              "Industry\".", kind="text", optional=True),
        Field("calendar.indicators.*.release_id", "FRED release number",
              "For the fred schedule.", kind="int", min=1, max=100000, step=1,
              optional=True),
        Field("calendar.indicators.*.time_ct", "Release time",
              "Central time; used only when the date source gives no time.",
              kind="time", optional=True),
        Field("calendar.indicators.*.tile", "Tile", "", kind="text"),
        Field("calendar.indicators.*.high", "High impact",
              "On draws this indicator's tile highlighted on the calendar. Off for "
              "a routine release.", kind="bool", optional=True),
    )),
)

_NEWS = ConfigFile(
    name="news.toml", title="Market news", icon="newspaper",
    summary="Which public feeds the news collector reads, how often, and which "
            "of them the public site may show.",
    restart=(NEWS,),
    caution="Every feed is a public RSS, Google News or SEC feed. A feed marked "
            "not public stays in the app and never reaches live.neuralstrike.co. "
            "The feed list itself is read-only here: add or change a feed in "
            "config/news.toml. The FRED API key is read from FRED_API_KEY in the "
            "stack .env and is never stored here.",
    sections=(
        Section("Polling", "How often every feed is read. Faster costs nothing "
                "in API budget but is discourteous to the publishers.", (
            Field("collector.rth_poll_min", "During market hours",
                  "Minutes between polls, 08:30–15:00 CT. The service never polls "
                  "faster than once a minute.", kind="int", unit="min",
                  min=1, max=60, step=1),
            Field("collector.eth_poll_min", "During extended hours",
                  "Minutes between polls in the early and late sessions, 06:30–08:25 "
                  "and 15:00–15:15 CT.", kind="int", unit="min", min=1, max=120, step=1),
            Field("collector.offhours_poll_min", "Outside market hours",
                  "Minutes between polls on a trading day outside every session.",
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
            Field("collector.sec_view_items", "SEC rows published",
                  "The newest SEC filings and insider buys the SEC panel reads.",
                  kind="int", min=10, max=1000, step=10),
            Field("collector.request_timeout_s", "Request timeout",
                  "How long one feed may take to answer before it is skipped "
                  "until the next poll.", kind="int", unit="s", min=5, max=120, step=5),
            Field("collector.sec_user_agent", "SEC User-Agent",
                  "The SEC requires a contact address in every request.", kind="text"),
            Field("collector.feed_user_agent", "Feed User-Agent",
                  "Sent to every feed that is not the SEC. Keep the leading "
                  "Mozilla/5.0: Yahoo answers a missing-page error to a client "
                  "without it.", kind="text"),
            Field("collector.max_body_bytes", "Largest response read",
                  "A feed answering more than this is skipped until the next poll "
                  "instead of being read into memory.", kind="int", unit="bytes",
                  min=100000, max=50000000, step=100000),
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
        Section("Desk", "", (
            Field("desk.headlines", "Headlines on the Desk",
                  "How many of the newest stories the Desk's Headlines panel "
                  "shows. Nine matches the Flow alerts panel beside it.",
                  kind="int", min=1, max=30, step=1),
        ), restart=(WEBGUI,)),
        Section("Duplicates", "Two different feeds with one headline within a "
                "day are always shown as one story.", (
            Field("dedupe.same_feed_merge_h", "One feed's repeated headline",
                  "When ONE feed publishes the same headline twice within this "
                  "many hours, it is shown once. Longer apart, both are kept, so a "
                  "daily column with a fixed title is not folded into yesterday's. "
                  "SEC filing feeds are never folded this way: their headlines "
                  "follow a template, so two different filings can share one. "
                  "0 turns this off.", kind="int", unit="h", min=0, max=24, step=1),
        )),
        *_NEWS_IMPACT_SECTIONS,
        *_NEWS_CALENDAR_SECTIONS,
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

# ─────────────────────────────────────────────────────────────────────────────
# The site Blog — config/blog.toml
# ─────────────────────────────────────────────────────────────────────────────
# Every min / max below mirrors shared.blog_inbox.BOUNDS, which is what the
# loader enforces: outside it a value reads as the shipped one with no sign on
# this page. Pinned equal, both ways, by tests/test_config_schema.py; this
# module stays import-free.
#
# Restart: the blog service only, never this app. shared.blog_inbox's accessors
# re-read the file (mtime-cached) on every call, and a page calls them as it is
# built, so the web GUI picks a change up on the next page load. The service is
# restarted because that is the flow a service's settings follow here, and
# nothing is lost by it: it holds no market data.
_BLOG = ConfigFile(
    name="blog.toml", title="Site blog", icon="article",
    summary="What the Blog accepts as a draft, how large a document and its "
            "details may be, and whether its typefaces are copied onto this server.",
    restart=(BLOG,),
    caution="Nothing here publishes anything: an entry reaches the public site "
            "only when you press Publish on the Blog page.",
    sections=(
        Section("The public site", "", (
            Field("site.enabled", "Write entries to the site",
                  "Off, drafts still arrive and can be previewed, but Publish "
                  "and Unpublish change nothing on the public site and the list "
                  "of entries there stays as it is: an entry you unpublish "
                  "stays on the site. Switched back on, the site catches up at "
                  "the next refresh below.", kind="bool"),
            Field("site.republish_min", "Refresh the Blog page's lists every",
                  "The lists of drafts and published entries are sent to this "
                  "app whenever one changes. They are also sent again this "
                  "often, so the page recovers by itself if the server's cache "
                  "is ever emptied. The public site's blog pages are checked "
                  "against what is published on the same timer, and only what "
                  "differs is rewritten.", kind="int", unit="min",
                  min=1, max=1440, step=1),
        )),
        Section("Drafts", "What is accepted as a draft from an upload on the "
                "Blog page.", (
            Field("limits.max_html_kb", "Largest document",
                  "A document larger than this is refused before it is read. "
                  "Measured as the file arrives, before anything is removed "
                  "from it. The first entry written for the site was 35 KB.",
                  kind="int", unit="KB", min=1, max=4096, step=1),
            Field("limits.max_drafts", "Drafts waiting at once",
                  "With this many drafts waiting, a new one is refused until "
                  "you publish or discard one.",
                  kind="int", unit="drafts", min=1, max=200, step=1),
            Field("limits.clean_sec", "Time to clean one document",
                  "Each document is cleaned in a separate process, stopped if it "
                  "takes longer than this; a document that times out is refused "
                  "and the Blog service moves on. A badly built document can take "
                  "minutes to parse, so this is the guard. The Blog service does "
                  "one thing at a time, so every other Blog request waits while "
                  "a document is cleaned, and a request that has waited longer "
                  "than \"Every other command\" under Services (900 seconds as "
                  "shipped) when its turn comes is not carried out. Keep this "
                  "well under that.",
                  kind="int", unit="seconds", min=2, max=120, step=1),
            Field("limits.clean_mem_mb", "Memory for cleaning one document",
                  "The memory that cleaning process may use before it is stopped "
                  "(Linux only; on Windows the time limit above is the whole "
                  "guard). It stops a malformed document exhausting memory.",
                  kind="int", unit="MB", min=128, max=4096, step=64),
        )),
        Section("An entry's details", "The title, summary, tags and address shown "
                "with an entry. A longer title, summary or tag is cut to fit; a "
                "longer address is refused.", (
            Field("limits.title_chars", "Longest title",
                  "A longer title is cut here.",
                  kind="int", unit="characters", min=1, max=300, step=1),
            Field("limits.summary_chars", "Longest summary",
                  "The summary is the line under the title in the list of "
                  "entries and in a shared link's preview. A longer one is cut "
                  "here.", kind="int", unit="characters", min=1, max=1000, step=1),
            Field("limits.max_tags", "Most tags on one entry",
                  "Tags past this number are dropped.",
                  kind="int", unit="tags", min=1, max=24, step=1),
            Field("limits.tag_chars", "Longest tag",
                  "A longer tag is cut here.",
                  kind="int", unit="characters", min=1, max=64, step=1),
            Field("limits.slug_chars", "Longest address",
                  "The address is the last part of an entry's link: lower-case "
                  "letters, digits and hyphens. A longer one is refused, not "
                  "cut, because half an address is a different address.",
                  kind="int", unit="characters", min=16, max=120, step=1),
        )),
        Section("Typefaces", "An entry written in Claude Chat asks Google Fonts "
                "for its typefaces. The public site loads nothing from another "
                "website, so they are copied onto this server when the draft "
                "arrives.", (
            Field("fonts.enabled", "Copy an entry's typefaces",
                  "Off, nothing is fetched and an entry is shown in the "
                  "fallback fonts its own design names.", kind="bool"),
            Field("fonts.subsets", "Character sets copied",
                  "Google's names for them, in lower case: latin, latin-ext, "
                  "cyrillic, greek, vietnamese. Each one is another file for "
                  "every weight of every typeface. Icon fonts are filed under "
                  "a name of their own, fallback: add it to copy them. A name "
                  "is lower-case letters, digits and hyphens; one not written "
                  "that way is dropped, and with none left latin and latin-ext "
                  "are copied. A name written that way which Google does not "
                  "use is kept and matches nothing, so nothing is copied for "
                  "it: check the spelling.", kind="phrases"),
            Field("fonts.max_links", "Most typeface stylesheets for one entry",
                  "An entry names its typefaces in one or more links to Google "
                  "Fonts. Links past this number are not followed.",
                  kind="int", unit="links", min=1, max=16, step=1),
            Field("fonts.max_css_kb", "Largest typeface stylesheet",
                  "What Google Fonts sends back for one link is the list of "
                  "files to copy. A larger list than this is not read.",
                  kind="int", unit="KB", min=16, max=2048, step=16),
            Field("fonts.max_files", "Most typeface files for one entry",
                  "Files past this number are not copied, and the text they "
                  "would have styled uses a fallback font. It counts the "
                  "files asked for, so one that fails to arrive still uses "
                  "one up.",
                  kind="int", unit="files", min=1, max=64, step=1),
            Field("fonts.max_file_kb", "Largest typeface file",
                  "A larger file is not stored.",
                  kind="int", unit="KB", min=16, max=1024, step=16),
            Field("fonts.max_total_mb", "Most typeface bytes for one entry",
                  "All of one entry's typeface files together. They are held "
                  "in memory at once while a draft is being filed, so this is "
                  "what one draft can cost. Once it is reached no more are "
                  "fetched, and the draft says how many were left out. As it "
                  "ships it is above what the two settings before it allow, so "
                  "it only matters once one of them is raised.",
                  kind="int", unit="MB", min=1, max=64, step=1),
            Field("fonts.max_rules", "Most typeface rules written into one entry",
                  "Each weight and style of a typeface, in each character set, "
                  "is one rule added to the entry, and several rules can share "
                  "one file. Rules past this number are left out and the draft "
                  "says how many. It keeps what is added to an entry small: at "
                  "most about 3 KB for each rule, usually a tenth of that.",
                  kind="int", unit="rules", min=1, max=1000, step=1),
            Field("fonts.timeout_sec", "Longest wait for Google Fonts",
                  "For each request. A typeface that does not arrive in time is "
                  "left out and the draft says so; it never holds a draft up.",
                  kind="int", unit="seconds", min=1, max=60, step=1),
            Field("fonts.total_sec", "Longest spent copying one entry's typefaces",
                  "For all of one entry's requests together. Once it has "
                  "passed, the typefaces not copied yet are left out and the "
                  "draft says so. Keep it well under \"Every other command\" "
                  "under Services (900 seconds as shipped): the next Blog "
                  "request is not read until this one's typefaces are done, "
                  "and one that has waited longer than that expires: it is "
                  "not carried out.",
                  kind="int", unit="seconds", min=1, max=600, step=1),
            Field("fonts.user_agent", "Browser named to Google Fonts",
                  "Google Fonts sends woff2 files, split by character set, "
                  "only to a browser it knows can read them. This is the "
                  "browser the Blog service says it is. Change it when drafts "
                  "start saying their typeface rules were not usable while "
                  "Google Fonts itself is working: that is Google no longer "
                  "answering this one with woff2. Paste the User-Agent of a "
                  "current desktop Chrome. Plain letters, digits and "
                  "punctuation only, 20 to 300 characters.", kind="text"),
        )),
    ),
)

# The push categories' plain-English names (config/notify.toml [channels.*]),
# shared by the Configuration editor's row labels and Settings -> General's grid.
NOTIFY_CATEGORY_NAMES = {
    "signals": "New scanner signals",
    "flow_uoa": "Unusual options activity",
    "flow_crossover": "Put/call premium crossover",
    "flow_gamma_flip": "Gamma flip",
    "flow_hiro": "Hedging flow",
    "action_alert": "Open-position action digest",
    "eod_summary": "End-of-day summary",
    "gamma_briefing": "Dealer Positioning briefings",
    "market_snapshot": "Market snapshot",
    "market_state": "Market state change",
    "trade_idea": "Hourly trade idea",
    "system": "Server alerts",
}

# ─────────────────────────────────────────────────────────────────────────────
# Push notifications — config/notify.toml (also Settings -> General's grid)
# ─────────────────────────────────────────────────────────────────────────────
_NOTIFY = ConfigFile(
    name="notify.toml", title="Push notifications", icon="notifications",
    summary="Which channels each push category is sent to, and the trade idea's "
            "Google Calendar event.",
    restart=(),       # read at send time (shared/notify/switches.py)
    sections=(
        Section("Channels", "One entry per category. The same switches are on "
                "Settings → General.", (
            Field("channels.*.discord", "Discord", "Off stops this category's "
                  "Discord posts.", kind="bool"),
            Field("channels.*.telegram", "Telegram", "Off stops this category's "
                  "Telegram posts.", kind="bool"),
            Field("channels.*.calendar", "Google Calendar event",
                  "Trade idea only: also create a short event in the calendar "
                  "below.", kind="bool", optional=True),
        )),
        Section("Google Calendar", "The trade idea's event. Needs the "
                "service-account key at shared/google_calendar_sa.json on the "
                "server, and the calendar shared with that account. The popup "
                "is the calendar's own default notification — set it to \"at "
                "time of event\".", (
            Field("calendar.calendar_id", "Calendar ID",
                  "Google Calendar → the calendar's Settings → Integrate "
                  "calendar → Calendar ID. Blank = no event.",
                  kind="text", blank_ok=True),
            Field("calendar.lead_min", "Event starts after the post",
                  "The popup fires when the event starts. Under about 4 minutes "
                  "your phone may not have synced the event in time, and no "
                  "popup arrives.",
                  kind="int", unit="min", min=0, max=60, step=1),
            Field("calendar.duration_min", "Event length", "",
                  kind="int", unit="min", min=1, max=120, step=1),
        )),
        Section("Public site", "Each posted trade idea also appears on "
                "neuralstrike.co: the home page shows the newest three, and "
                "ideas.html shows every card of the days kept.", (
            Field("site.trade_ideas", "Publish trade ideas to the website",
                  "Off still posts to Discord, Telegram and X, but adds nothing "
                  "to the site. Cards already there stay.", kind="bool"),
            Field("site.keep_days", "Days kept on the site",
                  "Counts days on which an idea was posted, so weekends and "
                  "holidays do not use one up. Older cards are deleted.",
                  kind="int", unit="days", min=1, max=30, step=1),
            Field("site.refresh_min", "Result refresh",
                  "How often each open idea's result is recomputed from the stock "
                  "price during the session. One quote call per refresh.",
                  kind="int", unit="min", min=5, max=60, step=5),
        )),
        Section("Server alerts", "Sent when a part of the system has stopped "
                "and stayed down, when the nightly backup fails, and when the "
                "Schwab sign-in is about to run out. Switch the category on or "
                "off under Channels above.", (
            Field("system.token_warn_hours", "Warn before the Schwab sign-in expires",
                  "The sign-in lasts 7 days and is renewed only by signing in "
                  "again on the proxy's /auth page. The warning is sent once a "
                  "day while this many hours or fewer remain.",
                  kind="int", unit="hours", min=1, max=168, step=1),
            Field("system.failure_repeat_hours", "Repeat a failure alert after",
                  "One alert per failing part per this many hours, so a job "
                  "that fails every 15 minutes does not send four an hour.",
                  kind="int", unit="hours", min=1, max=168, step=1),
        )),
    ),
)

# ─────────────────────────────────────────────────────────────────────────────
# Local market data — config/marketdata.toml
# ─────────────────────────────────────────────────────────────────────────────
_SEC = dict(kind="int", unit="seconds", min=0)

_MARKETDATA = ConfigFile(
    name="marketdata.toml", title="Local market data", icon="storage",
    summary="How the Schwab proxy reuses data it has already fetched, how "
            "often watchlist symbols are fetched, and how its paper-trade "
            "tracker retries. Changes apply at once.",
    restart=(),
    caution="Longer time limits save Schwab calls and show older data. Turn the "
            "mode to off to return to fetching everything.",
    sections=(
        Section("Mode", "", (
            Field("mode", "Mode",
                  "Off fetches everything from Schwab. Shadow still fetches "
                  "everything and counts what could have been reused. On answers "
                  "repeat requests locally.",
                  kind="choice", choices=("off", "shadow", "on")),
        )),
        Section("Option chains", "", (
            Field("chains.enabled", "Reuse option chains", "", kind="bool"),
            Field("chains.max_age_sec", "Oldest chain to reuse while a session is open",
                  "Applies to every caller that sends no limit of its own. The "
                  "autoscan is one of them. Never more than five minutes: a "
                  "higher value in the file is read as 300.", **_SEC, max=300),
            Field("chains.closed_max_age_sec", "Oldest chain to reuse while markets are closed",
                  "Never more than an hour.", **_SEC, max=3600),
            Field("chains.max_entries", "Most chains kept at once", "", kind="int",
                  min=50, max=5000),
            Field("chains.shadow_compare_max_age_sec",
                  "Oldest chain compared in shadow mode", "", **_SEC, max=300),
            Field("chains.wide_refetch_max_days",
                  "Longest held window refetched in place of a shorter one",
                  "When a request just misses, the proxy fetches a wider window "
                  "it already holds for that symbol and cuts the answer from it. "
                  "This should equal the collector's window, 7 days. Any higher "
                  "and the collector's own request is replaced by a longer "
                  "window that something else holds.",
                  kind="int", unit="days", min=1, max=14),
        )),
        Section("Quotes", "", (
            Field("quotes.enabled", "Reuse quotes", "", kind="bool"),
            Field("quotes.max_age_sec", "Oldest quote to reuse", "", **_SEC, max=60),
            Field("quotes.max_symbols", "Most symbols kept at once",
                  "Past this the symbols stored longest ago are dropped.",
                  kind="int", min=100, max=50000),
        )),
        Section("Daily price bars", "", (
            Field("bars.enabled", "Reuse daily price bars", "", kind="bool"),
            Field("bars.today_bar", "Today's bar",
                  "ttl re-serves the last fetched series. quote builds today's "
                  "bar from the live quote; use it only once the shadow counts "
                  "show the two agree.",
                  kind="choice", choices=("ttl", "quote")),
            Field("bars.session_ttl_sec", "Oldest series to reuse during the session",
                  "Used when today's bar is set to ttl, and in quote mode "
                  "whenever no usable quote is held.", **_SEC, max=7200),
            Field("bars.session_spread", "Stagger when series are refetched",
                  "Each series gets its own reuse window inside the limit "
                  "above, so the series one scan fetched together are not all "
                  "refetched by the same later scan. A series is then reused "
                  "for up to the limit, on average about half of it the first "
                  "time. Off: every series lives exactly the limit.",
                  kind="bool"),
            Field("bars.today_quote_max_age_sec", "Oldest quote used to build today's bar",
                  "", **_SEC, max=600),
            Field("bars.settle_min", "Minutes after the close before bars are refetched",
                  "", kind="int", unit="minutes", min=0, max=120),
            Field("bars.max_entries", "Most price series kept at once",
                  "One series per symbol and range. Past this the series stored "
                  "longest ago are dropped.",
                  kind="int", min=100, max=50000),
        )),
        Section("Autoscan", "", (
            Field("scan.wide_fetch", "Fetch one wide chain per symbol",
                  "One fetch out to 45 days in place of three.", kind="bool"),
            Field("scan.wide_fetch_exclude", "Symbols that keep three separate fetches",
                  "Their 45-day chain is too large for one request.", kind="symbols"),
        )),
        Section("Request order",
                "Every Schwab market-data call is sent 0.2 seconds after the "
                "one before it. The one-minute collection poll's calls go "
                "ahead of other calls that are waiting.", (
            Field("limiter.priority_run",
                  "Collection calls sent before one other call",
                  "At 4, while the collection poll is fetching, 4 of every 5 "
                  "calls are the poll's and 1 is everything else's, such as a "
                  "scan or a page load. 0 sends calls in the order they arrive.",
                  kind="int", unit="calls", min=0, max=20),
        )),
        Section("Paper-trade tracker",
                "The proxy streams the legs of open paper credit spreads. A "
                "trade it could not start tracking is tried again after 30 "
                "seconds, then 60, 120 and so on, up to these limits.", (
            Field("tracker.retry_max_sec", "Longest wait before trying again",
                  "For a failure Schwab recovering will not fix, such as a "
                  "strike that is not in the chain.",
                  **{**_SEC, "min": 30}, max=86400),
            Field("tracker.fetch_retry_max_sec",
                  "Longest wait after Schwab did not send the chain",
                  "Kept short so tracking resumes soon after an outage or a "
                  "Schwab re-authorization.",
                  **{**_SEC, "min": 30}, max=3600),
        )),
        Section("Collector", "", (
            Field("collection.tail_interval_min",
                  "Minutes between real fetches for watchlist symbols",
                  "1 fetches every symbol every minute. At 3, symbols that are "
                  "collected only because they are on the watchlist are fetched "
                  "every third minute and carried forward in between. Use 3 or "
                  "5: the Opportunity Board's flow acceleration reads a "
                  "15-minute window, and an interval that does not divide 15 "
                  "puts an uneven number of real fetches in each window. Takes "
                  "effect only while the mode is on and option-chain reuse is on.",
                  kind="int", unit="minutes", min=1, max=5),
            Field("collection.fresh_max_age_sec",
                  "Oldest chain the collector treats as new",
                  "Does two jobs. An answer older than this is treated as "
                  "carried forward. While the store is on, it is also the age "
                  "limit the collector sends for every symbol fetched every "
                  "minute, and for the others on the minute they are due. Above "
                  "30 seconds, such a symbol is regularly answered with the "
                  "previous minute's chain and treated as new.", **_SEC, max=30),
            Field("collection.max_gamma_ratio",
                  "Most a carried gamma may grow",
                  "Caps how far a carried contract's gamma may move above "
                  "Schwab's value between fetches, as a multiple of that value.",
                  kind="float", unit="times", min=1, max=1000, step=1),
            Field("collection.cap_refetch_max",
                  "Most symbols refetched when the gamma cap binds",
                  "When the cap above holds a carried gamma down, that symbol "
                  "is fetched for real in the same minute instead of being "
                  "written capped. This is the most symbols one minute "
                  "refetches that way. 0 writes the capped chain.",
                  kind="int", unit="symbols", min=0, max=40),
            Field("collection.empty_retry_min",
                  "Minutes before retrying a symbol with nothing listed",
                  "A symbol with monthly options only has no expiration inside "
                  "the collector's seven-day window for most of the month. "
                  "Schwab answers with an empty chain, nothing is charted, and "
                  "the collector waits this long before asking again. 0 asks "
                  "every minute. Works whatever the mode above is.",
                  kind="int", unit="minutes", min=0, max=720),
            Field("collection.carry_slack_sec",
                  "Slack when asking for a stored chain",
                  "Seconds added to the interval when the collector asks for a "
                  "stored chain, so one fetched a little late still counts.",
                  **_SEC, max=60),
            Field("collection.traded_premium",
                  "Record traded premium as it trades",
                  "Stores, per strike, a running total in which each minute's "
                  "new volume is priced once, at that minute's mark. Unlike "
                  "the premium the Flow view shows, it cannot fall when prices "
                  "fall. Nothing reads it yet. It adds about an eighth to the "
                  "gamma history database. Turning it off and on again during "
                  "a session leaves the volume traded in between out of the "
                  "total.", kind="bool"),
            Field("collection.traded_premium_late_sec",
                  "Seconds after which new volume counts as priced late",
                  "Used only by the daily check line in the options service's "
                  "log. New volume first seen more than this long after the "
                  "symbol's previous reading is counted as priced late.",
                  **{**_SEC, "min": 30}, max=600),
        )),
    ),
)

# ─────────────────────────────────────────────────────────────────────────────
# Queued commands — config/services.toml
# ─────────────────────────────────────────────────────────────────────────────
_COMMANDS = ConfigFile(
    name="services.toml", title="Services", icon="hourglass_bottom",
    summary="How long a click may wait in a service's queue before the service "
            "refuses to act on it, how much of each queue is kept, and when a "
            "service reports itself unhealthy.",
    restart=(OPTIONS, SENTIMENT, TRADE, MARKET, NEWS, BLOG),
    caution="These stop a restarted service from re-running its queue's history. "
            "Longer limits let stale clicks through; shorter ones drop a click "
            "that waited behind a long scan.",
    sections=(
        Section("How old a command may be", "", (
            Field("age.side_effect_max_sec",
                  "Commands that change a paper book, cost money or post in public",
                  "Opening, closing or deleting a paper trade, resetting the paper "
                  "account, applying a Rescue adjustment, the auto-close switches, "
                  "a Claude briefing, a post to X. Older than this, the command "
                  "is refused.",
                  kind="int", unit="seconds", min=30, max=3600, step=30),
            Field("age.replay_max_sec", "Every other command",
                  "Older than this, a command is treated as the queue's history "
                  "being replayed after a restart and is not run. Commands that "
                  "only re-read a local store run at any age.",
                  kind="int", unit="seconds", min=60, max=86400, step=60),
        )),
        Section("Commands that were never run", "", (
            Field("dead_letters.keep", "How many to keep",
                  "A command a service could not run is kept so you can see what "
                  "was lost; the count shows on its System Status card. Each "
                  "queue keeps the newest this-many.",
                  kind="int", unit="commands", min=1, max=100000, step=10),
            # Bounds mirror shared.service_limits.DEAD_FIELD_KB_MIN / _MAX
            # (pinned by tests/test_config_schema.py). No restart: the bus reads
            # it each time it keeps a command.
            Field("dead_letters.max_field_kb", "How much of each one to keep",
                  "The list is for you to see what was lost, not a copy to run "
                  "again. Most commands are a few hundred bytes and are kept "
                  "whole. One that carries a whole document, such as a blog "
                  "draft, is cut at this size, ends with a note of how large it "
                  "was, and is marked as cut. A change applies to the next "
                  "command that is kept.",
                  kind="int", unit="KB", min=1, max=4096, step=16, restart=()),
        )),
        # Bounds mirror shared.service_limits.stream_keep_bounds(<stream>):
        # 10..100000, and 10..500 for the one queue whose commands carry a
        # document (pinned per stream by tests/test_config_schema.py). No
        # restart: the bus reads the cap at every enqueue through the
        # mtime-cached loader, in whichever process is sending. The named
        # queue has an entry of its own so it can say why it is small;
        # the wildcard catches any other stream given a cap by hand in
        # config/local, which would otherwise be in force and shown nowhere.
        Section("How many commands each queue holds",
                "A queue holds its newest commands, whether or not they have "
                "been run. Each new command trims the queue to about this many, "
                "oldest first. So this is how far back you can look at what was "
                "asked, and also the most a service can fall behind: if a "
                "service is stopped or busy while more than this many commands "
                "arrive, the oldest ones still waiting are lost. They are never "
                "run and nothing reports it. A change applies to the next "
                "command.", (
            Field("stream_keep.default", "Every queue not named below",
                  "Commands are small, so a thousand of them is little memory. "
                  "A queue of small commands can run up to about a hundred "
                  "over this number before it is trimmed. Keep it well above "
                  "the number of commands that could arrive while a service "
                  "restarts.",
                  kind="int", unit="commands", min=10, max=100000, step=50),
            Field("stream_keep.cmd:blog", "The Blog page's uploads and Publish",
                  "Kept small on purpose: an upload carries a whole document, "
                  "up to the largest one the Site blog settings allow. At 50 "
                  "that is about 25 MB of the server's memory; at 500, the "
                  "most allowed here, about 250 MB. More than this many sent "
                  "while the blog service is stopped, and the oldest are lost.",
                  kind="int", unit="commands", min=10, max=500, step=10),
            Field("stream_keep.*", "",
                  "A queue given its own number by hand in the settings file, "
                  "shown under the queue's own name.",
                  kind="int", unit="commands", min=10, max=100000, step=10),
        ), restart=()),
        Section("Threads", "", (
            Field("pool.workers", "Threads for scheduled work",
                  "How many scheduled jobs (scans, the one-minute collection, "
                  "refreshes) one service can run at the same moment. Each "
                  "command queue has its own thread outside this number, so a "
                  "click is never waiting for one of these.",
                  kind="int", unit="threads", min=2, max=256, step=1),
        )),
        Section("When a service is unhealthy", "", (
            Field("health.tick_stale_sec", "Longest a scheduler may be silent",
                  "A service whose scheduler has not run for this long shows as "
                  "offline on System Status and counts on the status badge, even "
                  "though its process still answers. Keep it well above 120.",
                  kind="int", unit="seconds", min=150, max=86400, step=30),
            Field("health.restart_reset_sec", "Healthy run that restores the restart budget",
                  "A scheduler that fails is restarted up to ten times. After "
                  "running this long without failing it gets all ten back.",
                  kind="int", unit="seconds", min=60, max=604800, step=60),
        )),
    ),
)

# ─────────────────────────────────────────────────────────────────────────────
# The Desk's Market read scorecard — config/market_read.toml
# ─────────────────────────────────────────────────────────────────────────────
_MARKET_READ = ConfigFile(
    name="market_read.toml", title="Market read", icon="fact_check",
    summary="The Desk's Market read: six readings, each marked a tailwind, a "
            "headwind or neutral for stocks. Changes apply at the next reading.",
    # The market service reads this file on every reading.
    restart=(),
    caution="Every threshold here is a starting guess. None has been measured "
            "against what the market did next.",
    sections=(
        Section("Schedule", "", (
            Field("enabled", "Market read on",
                  "Off stops new readings and takes the panel off the Desk "
                  "within a few seconds.", kind="bool"),
            Field("public", "Show it on the public Desk",
                  "Off = it appears in this app only. Applies within a few "
                  "seconds. The Flow row also needs Flow Alerts' own public "
                  "switch for the estimate.", kind="bool"),
            Field("interval_min", "A new reading every",
                  "On the clock, from the first one after the 08:30 Central open "
                  "to the 15:00 close.", kind="choice", choices=(15, 30), unit="min"),
            Field("stale_after_sec", "Ignore a source older than",
                  "A row whose source is older than this reads No reading.",
                  kind="int", unit="sec", min=60, max=3600, step=30),
            Field("dashboard_stale_after_sec", "Ignore Market Dashboard tiles older than",
                  "The tiles are republished every 3 seconds in the session, so "
                  "this can be much shorter.",
                  kind="int", unit="sec", min=10, max=3600, step=10),
            Field("retry_sec", "After a failed reading, retry after", "",
                  kind="int", unit="sec", min=5, max=600, step=5),
            Field("glow_min", "Glow the Desk's buttons for",
                  "The Market read and Market report buttons glow for this long "
                  "after a new reading or report. 0 = never. Applies at the next "
                  "reading.", kind="int", unit="min", min=0, max=60, step=1),
        )),
        Section("Direction", "The S&P 500 and Nasdaq 100 indexes, on the day.", (
            Field("direction.move_pct", "Both up or both down by at least", "",
                  kind="float", unit="%", min=0, max=5, step=0.05),
        )),
        Section("Breadth", "Advancing and declining funds and stocks on the Market "
                "Dashboard's equity frames.", (
            _pct("breadth.strong_share", "Tailwind when advancing at least", ""),
            _pct("breadth.weak_share", "Headwind when advancing at most", ""),
            Field("breadth.min_tiles", "Needs at least this many tiles with a price",
                  "Fewer reads No reading, so a quote outage cannot pass for a tape.",
                  kind="int", min=1, max=80, step=1),
        )),
        Section("Structure", "Price against the dealer gamma flip and the ceiling.", (
            Field("structure.symbols", "Symbols read",
                  "They must agree for the row to lean either way.", kind="symbols"),
            Field("structure.room_pct", "Tailwind with at least this room to the ceiling",
                  "While price is above the flip.",
                  kind="float", unit="%", min=0, max=5, step=0.05),
            Field("structure.near_pct", "Headwind within this of the ceiling",
                  "While dealers are long gamma. Below the flip is always a "
                  "headwind. Must be below the room above, or both revert.",
                  kind="float", unit="%", min=0, max=5, step=0.05),
            Field("structure.stale_after_sec", "Ignore dealer levels older than",
                  "By the collector's last snapshot. The Desk's dealer panel "
                  "greys its levels at the same age.",
                  kind="int", unit="sec", min=60, max=3600, step=30),
        )),
        Section("Volatility", "The VIX and its one-day and three-month versions.", (
            Field("volatility.vix_move_pct", "A VIX move of at least",
                  "Down by this, and below the three-month, is a tailwind. Up by "
                  "this on a day stocks are up is a headwind.",
                  kind="float", unit="%", min=0, max=20, step=0.25),
        )),
        Section("Flow", "The bought and sold estimate, pooled over today's flagged "
                "contracts. An estimate.", (
            Field("flow.lean_pts", "A lean of at least",
                  "Bought minus sold, in points of volume. Calls leaning bought "
                  "(and puts not) is a tailwind; the reverse is a headwind.",
                  kind="float", unit="points", min=0, max=50, step=0.5),
            Field("flow.min_contracts", "Needs at least this many flagged contracts",
                  "Fewer reads No reading.", kind="int", min=1, max=200, step=1),
        )),
    ),
)

# ─────────────────────────────────────────────────────────────────────────────
# Approve a sign-in from the phone — config/login_push.toml
# ─────────────────────────────────────────────────────────────────────────────
_LOGIN_PUSH = ConfigFile(
    name="login_push.toml", title="Sign-in from the phone", icon="phonelink_lock",
    summary="Leave the code empty at sign-in and approve it on your phone "
            "instead, through the ntfy app. Needs NTFY_TOPIC in the server's "
            ".env; without it the code is required as before.",
    restart=(WEBGUI,),
    sections=(
        Section("Approval push", "The push goes to the ntfy topic named by "
                "NTFY_TOPIC. Approving happens in the app on a phone that is "
                "already signed in.", (
            Field("enabled", "Approve sign-ins from the phone",
                  "Off makes the authenticator code required again.", kind="bool"),
            Field("server", "ntfy server",
                  "Where the push is published. https only.", kind="text"),
            Field("ttl_sec", "Approval waits for",
                  "After this the waiting sign-in lapses and must be started "
                  "again.", kind="int", unit="s", min=10, max=3600, step=10),
            Field("max_pending", "Approvals waiting at once",
                  "A sign-in past this many is refused, so a password holder "
                  "cannot flood your phone.", kind="int", min=1, max=50, step=1),
            Field("publish_timeout_sec", "Push send timeout",
                  "How long the app waits for ntfy before falling back to the "
                  "code.", kind="float", unit="s", min=0.5, max=30, step=0.5),
            Field("priority", "Push priority",
                  "ntfy priority, 1 (lowest) to 5 (highest). 4 makes the phone "
                  "sound and show it at the top.", kind="int", min=1, max=5, step=1),
        )),
    ),
)

FILES = (_COMMANDS, _SCANNER, _PAPER, _TRADE_MGMT, _FLOW, _NOTIFY, _LOGIN_PUSH, _SESSIONS, _SYMBOLS, _NEWS, _BLOG, _SECTORS, _MOMENTUM,
         _FINDER_PUBLIC, _RESCUE_PUBLIC, _TOOLS_PUBLIC, _GAMMA_PUBLIC, _GAMMA_HEAT, _FEED, _SCALP, _EDGE, _SWING_MODEL, _LISTED_STOCKS, _COMMISSIONS, _MARKETDATA,
         _MARKET_READ, _PORTS, _ENVS)
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


_API_KEY_RE = re.compile(r"(?i)api_key")


def _refuse_api_key(fld, text):
    """A calendar source's address or headers must never carry an API key:
    whatever is saved here lands in config/local and its changes.jsonl."""
    if fld.key.startswith("calendar.sources.") and _API_KEY_RE.search(text):
        raise ValueError("no API key here: the FRED key belongs in FRED_API_KEY "
                         "in the stack .env, never in this file")


# Mirrors shared.blog_inbox.USER_AGENT_CHARS and its printable-ASCII rule (this
# module stays import-free; tests/test_config_schema.py pins the two together).
_BLOG_USER_AGENT_CHARS = (20, 300)


def _refuse_unusable_user_agent(fld, text):
    """The blog service reads a User-Agent that could not be a request header
    as the shipped one, with no sign on this page. Refused here instead."""
    if fld.key != "fonts.user_agent":
        return
    low, high = _BLOG_USER_AGENT_CHARS
    if not low <= len(text) <= high:
        raise ValueError(f"must be {low} to {high} characters")
    if not all(" " <= ch <= "~" for ch in text):
        raise ValueError("plain letters, digits and punctuation only, on one line")


# Lists of GRADES ("Weak", "Marginal") among the symbols-kind fields: a grade
# is capitalised the way the scorer writes it, where a symbol is upper-cased.
_GRADE_LISTS = ("single_leg.excluded_grades", "single_leg.long_excluded_grades",
                "structures.excluded_grades")


def parse(fld: Field, raw, *, shipped=None):
    """Editor value -> the value stored in TOML, or ``ValueError`` with a
    sentence the page shows beside the field. ``shipped`` keeps an int an int."""
    k = fld.kind
    if k == "text" and fld.blank_ok and (raw is None or str(raw).strip() == ""):
        return ""
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
    if k == "dates":
        # A list of YYYY-MM-DD, de-duplicated and in order; empty is allowed.
        import datetime as _dt
        items = raw if isinstance(raw, (list, tuple)) else str(raw).replace(",", " ").split()
        out = set()
        for it in items:
            s = str(it).strip()
            try:
                if len(s) != 10:
                    raise ValueError
                _dt.date.fromisoformat(s)
            except ValueError:
                raise ValueError(f"{s!r}: use YYYY-MM-DD") from None
            out.add(s)
        return sorted(out)
    if k == "text":
        s = str(raw).strip()
        if not s:
            raise ValueError("a value is required")
        _refuse_api_key(fld, s)
        _refuse_unusable_user_agent(fld, s)
        return s
    if k in ("choice", "sector"):
        if raw not in fld.choices:
            raise ValueError("pick one of the listed values")
        return raw
    if k == "symbols":
        items = raw if isinstance(raw, (list, tuple)) else str(raw).replace(",", " ").split()
        out = []
        for it in items:
            t = str(it).strip().upper() if fld.key not in _GRADE_LISTS \
                else str(it).strip().capitalize()
            if t and t not in out:
                out.append(t)
        return out
    if k == "phrases":
        # Only strings survive, ints included in the skip: the loader
        # (news_config._tiers) keeps only strings, so a str()-ed 11 or None
        # would be a phrase the editor shows and the service never matches.
        # Each item is split on commas too, so a pasted "a, b" is two phrases.
        items = raw if isinstance(raw, (list, tuple)) else [str(raw)]
        out, seen = [], set()
        for it in items:
            if not isinstance(it, str):
                continue
            for part in it.split(","):
                t = " ".join(part.split())
                if t and t.casefold() not in seen:
                    seen.add(t.casefold())
                    out.append(t)
        return out
    if k == "numbers":
        items = raw if isinstance(raw, (list, tuple)) else str(raw).replace(",", " ").split()
        vals = []
        for x in items:
            try:
                v = _num(x)
            except (TypeError, ValueError):
                raise ValueError("enter numbers only") from None
            vals.append(_bounded(fld, v))
        if not vals:
            raise ValueError("at least one number is needed")
        return vals
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


def _real(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _hhmm(s):
    """Minutes after midnight for ``"HH:MM"``, else None."""
    try:
        hh, mm = s.split(":")
        return int(hh) * 60 + int(mm)
    except (AttributeError, ValueError):
        return None


def _scalp_schedule_errors(first, last, step, time_stop):
    """The intraday slot schedule's own rules, as ``shared.scalp_config.
    schedule_problem`` applies them (Tier 1 does not import that module; the
    loader would read a schedule breaking them as its defaults, with no sign
    here): not before 08:45, no slot at 08:36, and the last slot's entry (its
    minute floored to 5) plus the time stop not past the 15:00 close."""
    start, end = _hhmm(first), _hhmm(last)
    if start is None or end is None or start > end:
        return []
    out = []
    step = int(step) if _real(step) and step >= 1 else None
    if step and start <= 8 * 60 + 36 <= end and (8 * 60 + 36 - start) % step == 0:
        out.append("Intraday calls: no call may fall at 08:36, the morning call's "
                   "time.")
    if start < 8 * 60 + 45:
        out.append("Intraday calls: the first call may not be before 08:45.")
    if step and _real(time_stop):
        final = start + (end - start) // step * step
        if final - final % 5 + time_stop > 15 * 60:
            out.append("Intraday calls: the last call's entry plus the time stop "
                       "runs past the 15:00 close. End earlier or shorten the time "
                       "stop.")
    return out


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
    if name == "news.toml":
        hi, med = g("impact", "high_at"), g("impact", "med_at")
        if _real(hi) and _real(med) and med >= hi:
            # the loader would drop BOTH to its defaults, with no sign here
            errs.append("Impact: High must be above Med.")
        bands = [g("impact", "form4", k) for k in ("small_usd", "large_usd",
                                                     "huge_usd")]
        if all(_real(b) for b in bands) and not (bands[0] < bands[1] < bands[2]):
            errs.append("Impact: insider buy sizes must rise: small below large "
                        "below very large.")
    if name == "marketdata.toml":
        fresh = g("collection", "fresh_max_age_sec")
        slack = g("collection", "carry_slack_sec")
        if _real(fresh) and _real(slack) and fresh + slack > 60:
            # the collector would clamp the first, with no sign here
            errs.append("Collector: the oldest chain treated as new plus the "
                        "slack must not exceed 60 seconds.")
    if name == "scalp.toml":
        cuts = g("confirm", "cuts")
        if (isinstance(cuts, (list, tuple)) and len(cuts) == 2
                and all(_real(c) for c in cuts) and not cuts[0] < 0 < cuts[1]):
            # the loader would read the defaults, with no sign here
            errs.append("Confirmation: the low cut must be below zero and the "
                        "high one above it.")
        first, last = g("intraday", "start"), g("intraday", "end")
        if isinstance(first, str) and isinstance(last, str) and first > last:
            errs.append("Intraday calls: the first call must not be after the last.")
        errs += _scalp_schedule_errors(first, last, g("intraday", "interval_min"),
                                       g("trade", "time_stop_min"))
        close = g("intraday", "early_close_time")
        if isinstance(close, str) and not "08:30" < close <= "15:00":
            errs.append("Intraday calls: the early close must be after 08:30 and "
                        "not after 15:00.")
        spans = [(g("intraday", "blocks", b, "start"), g("intraday", "blocks", b, "end"))
                 for b in ("morning", "midday", "afternoon")]
        if all(isinstance(t, str) for s in spans for t in s) and (
                any(lo > hi for lo, hi in spans)
                or any(a[1] >= b[0] for a, b in zip(spans, spans[1:]))):
            errs.append("Intraday blocks: each must start before it ends, and "
                        "morning, midday and afternoon must follow in order "
                        "without overlapping.")
    if name == "flow_alerts.toml":
        lo, hi = g("big_delta", "delta_lo"), g("big_delta", "delta_hi")
        if None not in (lo, hi) and lo >= hi:
            errs.append("Big delta: the low delta bound must be below the high one.")
    return errs

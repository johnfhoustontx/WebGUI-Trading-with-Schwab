"""The Signal Desk's PURE display builders — a library, not a page.

Everything a ``TradeAnalysis`` payload has to become before a screen can draw
it: verdict and bias colouring, the momentum / breakdown / alignment / dealer /
plan / clearance rows, the swing-model sentences and the model-staleness read.
No widget, no bus read, no CSS — the four screens (``trade_overview``,
``trade_evidence``, ``trade_board``, ``trade_plan_screen``) and their shared
``trade_shell`` / ``trade_terminal`` import sixteen of these names between
them, and that is the whole of this module's job.

⚠ It was the Trade PAGE until 2026-09-20 and its ``render`` was never routed —
see the note at the foot of the file. The orchestration was never here either:
fetching MTF data, computing indicators and scoring the Position / Investor
engines all live in ``services/trade_svc/compute`` (``analyze``), which the
shell enqueues and the screens read back off ``cache:trade:analysis``.

Fundamentals ARE wired: ``trade_svc.compute`` fetches them from the proxy's
``/instruments?projection=fundamental`` endpoint, so the Investor verdict scores
real P/E, PEG, revenue/EPS growth, ROE and margin trend whenever at least three
of its four core fields arrive. It degrades to "Insufficient fundamental data →
HOLD" only on a failed/thin fetch — a note flags that case.

⚠ Four Investor/Position inputs are structurally absent from that payload — verified
live 2026-08-22, where ``fundamental`` returned 56 keys and none of these:

* ``epsSurprises`` — **now supplied by Alpha Vantage** via
  ``trade_svc.earnings_history``, so ``earnings_traj`` scores properly rather
  than sitting at a permanent 0. ``guidanceDirection`` is still absent from
  every source here, so the component scores on the surprise record alone
  instead of averaging in a structural zero that halved it.
* ``freeCashFlow`` — scores nothing either way, but its HOLD gate cannot fire.
* ``nextEarningsDate`` — which is why the Position earnings gate never fires.

Do not read a low Investor score as a verdict on the company without checking which
components could contribute.
"""
from pages import fmt

# Tailwind text-[...] classes for the verdict/bias palette (LOCAL — these hexes
# are DARKER than theme's TXT_* semantic colors, so they are not reused). The
# verdict cards refill in place, so reactive labels swap via .classes(remove=
# VERDICT_TEXT_CLASSES, add=…) to avoid stacking conflicting text-[…] classes.
_BUY_TEXT = "text-[#2e7d32]"
_HOLD_TEXT = "text-[#f9a825]"
_SELL_TEXT = "text-[#c62828]"
VERDICT_TEXT_CLASSES = f"{_BUY_TEXT} {_HOLD_TEXT} {_SELL_TEXT}"


def verdict_text_class(verdict):
    """Tailwind text-[…] class for a BUY/HOLD/SELL verdict (amber HOLD default)."""
    v = (verdict or "").upper()
    if v == "BUY":
        return _BUY_TEXT
    if v == "SELL":
        return _SELL_TEXT
    return _HOLD_TEXT


def bias_text_class(bias):
    """Tailwind text-[…] class for a BULLISH/BEARISH/NEUTRAL bias (amber default)."""
    b = (bias or "").upper()
    if b == "BULLISH":
        return _BUY_TEXT
    if b == "BEARISH":
        return _SELL_TEXT
    return _HOLD_TEXT


def _fmt(v, nd=1):
    return "—" if v is None else f"{v:.{nd}f}"


def _pct(v):
    return "—" if v is None else f"{v * 100:.1f}%"


def _days(n):
    """'1 day' / 'N days' (whole words, not the compact 'Nd')."""
    return f"{n} day" if n == 1 else f"{n} days"


# Snake_case engine factor keys → readable labels (Position / Investor / validated
# swing model). Standard trader acronyms are kept verbatim (RSI/MACD/ADX/VWAP/EMA);
# everything else is spelled out. An unknown key falls back to underscores→spaces.
_FACTOR_LABELS = {
    # Position (1–8 week) factors
    "ema_alignment": "EMA alignment",
    "adx": "ADX",
    "rsi": "RSI",
    "macd": "MACD",
    "rel_volume": "Relative volume",
    "vwap": "VWAP",
    "volume_profile": "Volume profile",
    "rs_3m": "Relative strength (3-month)",
    "rs_6m": "Relative strength (6-month)",
    "dist_52wk": "Distance from 52-week high",
    "sector": "Sector strength",
    # Investor (months+) factors
    "valuation": "Valuation",
    "growth_quality": "Growth quality",
    "earnings_traj": "Earnings trajectory",
    "rs_vs_spy": "Relative strength vs SPY",
    "rs_vs_sector": "Relative strength vs sector",
    # Validated swing-model factors
    "mom_12_1": "12-1 momentum",
    "mom_6_1": "6-1 momentum",
    "pth": "Price-to-52-week-high",
    "str_5d": "5-day reversal",
    "vol_adj_mom": "Volatility-adjusted momentum",
    "trend_quality": "Trend quality",
    "low_vol": "Low volatility",
    "rs_spy": "Relative strength vs SPY",
    "rs_sector": "Relative strength vs sector",
    "turnover": "Turnover",
}


def humanize_factor(key):
    """Readable label for a snake_case engine factor key.

    Known keys map via ``_FACTOR_LABELS`` (standard trader acronyms kept as-is); an
    unknown key degrades to underscores→spaces with the first letter capitalized, so a
    new engine factor still renders legibly instead of as a raw identifier."""
    if not key:
        return ""
    label = _FACTOR_LABELS.get(key)
    if label:
        return label
    s = str(key).replace("_", " ").strip()
    return (s[:1].upper() + s[1:]) if s else str(key)


def humanize_reason(reason):
    """Humanize the leading factor key in an engine reason string.

    The verdict engines format reasons as ``"<factor_key> (+score)"``; this swaps the
    key for its readable label and keeps the score annotation. A reason that is not in
    that shape (e.g. "Insufficient fundamental data") is returned unchanged."""
    if not reason:
        return reason
    text = str(reason)
    if " (" in text and text.endswith(")"):
        key, _sep, rest = text.partition(" (")
        return f"{humanize_factor(key)} ({rest}"
    return text


def fundamentals_rows(f):
    """(label, value) pairs for the fundamentals card; '—' for missing values.

    Growth/ROE are stored as fractions and shown as percents; margin trend and
    days-to-earnings render only when present.
    """
    if not f:
        return []
    rows = [
        ("P/E", _fmt(f.get("pe_ratio"), 1)),
        ("PEG", _fmt(f.get("peg_ratio"), 2)),
        ("Revenue growth", _pct(f.get("rev_growth_ttm"))),
        ("EPS growth", _pct(f.get("eps_growth_ttm"))),
        ("ROE", _pct(f.get("roe"))),
    ]
    me = f.get("margin_expanding")
    rows.append(("Margins", "expanding" if me else "contracting" if me is False else "—"))
    dte = f.get("days_to_earnings")
    if dte is not None:
        rows.append(("Earnings in", _days(dte)))
    return rows


def momentum_rows(m):
    """(label, value) pairs for the momentum strip; '—' for missing values."""
    if not m:
        return []
    return [
        ("RSI", _fmt(m.get("rsi"))),
        ("ADX", _fmt(m.get("adx"))),
        ("MACD histogram", _fmt(m.get("macd_hist"), 3)),
        ("VWAP", _fmt(m.get("vwap"), 2)),
        ("Relative Volume", _fmt(m.get("relative_volume"), 2)),
    ]


def breakdown_rows(verdict):
    """Factor-breakdown rows for a verdict's contribution table."""
    rows = []
    for b in (verdict or {}).get("breakdown", []):
        rows.append({
            "factor": humanize_factor(b.get("factor", "")),
            "weight": b.get("weight", 0),
            "raw_score": b.get("raw_score", 0),
            "contribution": round(float(b.get("contribution", 0.0)), 1),
        })
    return rows


def alignment_rows(ema):
    """(timeframe, status) rows for the multi-timeframe EMA-alignment table."""
    rows = []
    for tf in (ema or {}).get("timeframes", []):
        rows.append({"timeframe": tf.get("timeframe", ""),
                     "status": tf.get("status", "")})
    return rows


def seed_symbol(result):
    """The symbol to pre-fill the input on (re)build: the last analyzed symbol from
    the persisted cache result, else the AAPL default (so revisiting the page shows
    the symbol that matches the displayed analysis)."""
    sym = (result or {}).get("symbol")
    return sym if sym else "AAPL"


def should_request(symbol, last_requested, since_seconds):
    """True when an analyze request should fire for ``symbol``.

    Non-empty, AND (the symbol changed since the last request OR enough time has
    passed). The time guard collapses the blur-then-click double fire — clicking
    Analyze blurs the field first, so blur + click would otherwise enqueue twice —
    while still allowing a deliberate same-symbol refresh seconds later."""
    s = (symbol or "").strip().upper()
    if not s:
        return False
    return s != (last_requested or "") or since_seconds >= 1.0


def should_open_tab(pending, version, baseline):
    """Open the report/query tab only when a click is pending AND the cache version
    advanced past the baseline captured at click time (so a page-load with a stale
    cached result never auto-opens a tab)."""
    return bool(pending) and version is not None and version != baseline


# Tone → text class for the validated swing TILT (reuses the verdict palette, but the
# card renders it small/plain rather than a bold BUY — see swing_tilt on why).
_TONE_TEXT = {"pos": _BUY_TEXT, "neg": _SELL_TEXT, "neutral": _HOLD_TEXT}


def tilt_text_class(tone):
    """Tailwind text-[…] class for a swing tilt tone (amber neutral default)."""
    return _TONE_TEXT.get(tone, _HOLD_TEXT)


def swing_tilt(sm):
    """``(headline, tone)`` for the validated swing read — a ranked TILT, not a verdict.

    The measured edge is THIN (top band ≈52% beat-SPY, OOS IC ≈+0.04), so the model's
    BUY/HOLD/SELL is rendered as a cross-sectional RANK plus a mild directional tilt
    rather than a trade call — a coin-flip-plus-2% edge shown as a bold green "BUY"
    invites over-reading. Presentation only: the Tier-2 ``swing_model`` contract still
    carries BUY/HOLD/SELL."""
    sm = sm or {}
    pct = sm.get("percentile")
    rank = f"{pct}th percentile" if pct is not None else "unranked"
    v = (sm.get("verdict") or "").upper()
    if v == "BUY":
        return f"{rank} · slight bullish tilt", "pos"
    if v == "SELL":
        return f"{rank} · slight bearish tilt", "neg"
    return f"{rank} · no clear edge", "neutral"


def swing_headline(sm):
    """Ranked-tilt headline + calibrated outcome line for the Short Term card, or None.

    ``tilt``/``tone`` are the ranked read (see :func:`swing_tilt`); ``line`` summarizes
    the calibrated outcome (expected forward EXCESS return vs SPY over the horizon +
    beat-SPY hit rate). Missing fields are omitted."""
    if not sm:
        return None
    exp = sm.get("expected_fwd")
    hit = sm.get("hit_rate")
    hzn = sm.get("horizon_days", 20)
    tilt, tone = swing_tilt(sm)
    parts = []
    if exp is not None:
        parts.append(f"{exp:+.1%} excess / {_days(hzn)}")
    if hit is not None:
        parts.append(f"{hit:.0%} beat-SPY")
    return {"tilt": tilt, "tone": tone, "line": " · ".join(parts)}


def swing_contrib_rows(sm):
    """Factor-evidence rows (factor / z / weight / contribution / IC) for the expander.

    Already sorted by |contribution| desc upstream; z/weight/contribution are signed,
    and a None IC renders as an em dash."""
    if not sm:
        return []
    rows = []
    for c in sm.get("contributions", []):
        ic = c.get("ic")
        rows.append({
            "factor": humanize_factor(c.get("factor", "")),
            "z": f"{c.get('z', 0):+.2f}",
            "weight": f"{c.get('weight', 0):+.3f}",
            "contribution": f"{c.get('contribution', 0):+.3f}",
            "ic": f"{ic:+.3f}" if isinstance(ic, (int, float)) else "—",
        })
    return rows


def swing_model_meta(sm):
    """Model track-record line (version + OOS IC), or None."""
    if not sm:
        return None
    oos = sm.get("oos_ic")
    return {"version": sm.get("model_version", "?"),
            "oos_ic": f"{oos:+.4f}" if isinstance(oos, (int, float)) else "—"}


_REGIME_WORDS = {"trend": "a trending tape", "chop": "a rangebound tape",
                 "highvol": "an elevated-volatility tape"}


def swing_regime_note(sm):
    """Which weight set scored this symbol, in words. '' when unknown.

    Worth its own line because the two cases are different claims: regime
    weights say "this is what has worked in tapes like today's", the pooled fit
    says "this is what has worked on average". A reader looking at the
    contributions table cannot otherwise account for the weights it shows.

    ``"all"`` is an artifact key, not a market condition — printed raw it would
    read as a regime called "all"."""
    key = (sm or {}).get("regime_key")
    if not key:
        return ""
    if key == "all":
        return "scored on the pooled fit (no regime-specific weights)"
    return f"scored on weights fitted for {_REGIME_WORDS.get(key, key)}"


# A third of the model's weight on one directional exposure is material by any
# reading; below that the number is worth stating but does not change how you
# would hold the position.
_EXPOSURE_CAVEAT_AT = 0.30


def swing_exposure_note(sm):
    """How much of this score is a bet on volatility. '' when unknown.

    Phase 4 measured the composite at cross-sectional IC **+0.16 when the
    market's forward 20 days were up and -0.11 when they were down**, with the
    whole asymmetry carried by the volatility factors. So a high share here
    means the verdict is substantially a directional bet on the market — which
    reverses in exactly the drawdown a 1-8 week position cannot sit through.

    A `risk_share` of None means the factor registry was unreachable, NOT that
    the model has no exposure; printing "0%" there would be a confident wrong
    answer, so the line is omitted instead."""
    share = (sm or {}).get("risk_share")
    if not isinstance(share, (int, float)) or isinstance(share, bool):
        return ""
    pct = f"{share:.0%}"
    base = f"{pct} of this score's weight sits on volatility factors"
    if share < _EXPOSURE_CAVEAT_AT:
        return base + "."
    return (base + " — historically that ranks high-beta names top, and it "
            "reverses when the market falls.")


def live_ic_line(lic):
    """Is the live edge holding? '' when there is no monitor block.

    ⚠ The live number is a POOLED rank correlation over every labelled reading.
    The artifact's OOS IC is the mean of per-DATE cross-sectional correlations.
    They are different statistics, and printing them adjacent without saying so
    would manufacture a decay finding out of a units mismatch — so the line says
    it every time."""
    lic = lic or {}
    if not lic.get("status"):
        return ""
    if lic["status"] != "ok":
        n, need = lic.get("n_labelled", 0), lic.get("min_required", 20)
        return (f"Live tracking: {n} labelled reading(s) so far, {need} needed "
                "before an edge can be measured at all.")
    ic = fmt.num(lic.get("pooled_ic"))
    n = lic.get("n_labelled", 0)
    if ic is None:
        return f"Live tracking: {n} readings, not yet correlatable."
    return (f"Live pooled IC {ic:+.3f} over {n} readings — a pooled statistic, "
            "not the per-date one the model's OOS IC reports.")


def live_ic_split_line(lic):
    """The up-market / down-market split, and the beta-neutral reading.

    This is the line that matters most: Phase 4 showed the model's measured edge
    IS beta, so a single live IC would read healthy through any rising market."""
    lic = lic or {}
    if lic.get("status") != "ok":
        return ""
    up, down = fmt.num(lic.get("ic_market_up")), fmt.num(lic.get("ic_market_down"))
    ba = fmt.num(lic.get("pooled_ic_beta_adj"))
    bits = []
    if up is not None or down is not None:
        bits.append("market up " + (f"{up:+.2f}" if up is not None else "—")
                    + " · down " + (f"{down:+.2f}" if down is not None else "—"))
    if ba is not None:
        bits.append(f"beta-neutral {ba:+.3f}")
    return " · ".join(bits)


def live_ic_decay_note(lic):
    """A decay claim, ONLY from the statistic that is actually comparable.

    '' the rest of the time — which is most of the time, because live readings
    are too sparse for a per-date cross-sectional IC. Absent is the honest state
    here, not a gap to fill with the pooled number."""
    lic = lic or {}
    if not lic.get("comparable_to_artifact"):
        return ""
    decay = fmt.num(lic.get("decay"))
    live = fmt.num(lic.get("by_date_ic"))
    if decay is None or live is None:
        return ""
    word = "decay" if decay < 0 else "improvement"
    return (f"Live per-date IC {live:+.4f} vs the fit's "
            f"{fmt.num(lic.get('artifact_oos_ic')) or 0:+.4f} — "
            f"{word} of {abs(decay):.4f}.")


def model_staleness(version, today=None, threshold_days=60):
    """A staleness nudge for the swing-model artifact, or '' when fresh/unparseable.

    ``version`` is the fit date (YYYY-MM-DD). Older than ``threshold_days`` → a
    're-run fit_swing_model.py' warning: the validated edge decays as the market regime
    drifts from the fit window (esp. the regime-dependent ``low_vol`` sign). A
    missing/unparseable version → '' (no false warning)."""
    import datetime as _dt
    try:
        fit = _dt.date.fromisoformat(str(version)[:10])
    except (ValueError, TypeError):
        return ""
    today = today or _dt.date.today()
    age = (today - fit).days
    if age < threshold_days:
        return ""
    return (f"⚠ Model is {age} days old (fit {fit.isoformat()}) — re-run "
            f"fit_swing_model.py to refresh the weights.")


# ── two-sided reads (Phase 2) ───────────────────────────────────────────────
# Pure builders for the three additive blocks `trade_svc` now publishes. Each
# no-ops on an absent block, so a payload cached before they existed renders
# exactly as it did.

# Data-driven colour maps to a FIXED finite set of static Tailwind classes —
# never a runtime-built arbitrary value (the Tailwind-first house rule).
CLEARANCE_TEXT_CLASSES = (_BUY_TEXT, _HOLD_TEXT, _SELL_TEXT)
_CLEARANCE_TEXT = {"cleared": _BUY_TEXT,
                   "relative_only": _HOLD_TEXT,
                   "blocked": _SELL_TEXT}


def clearance_text_class(state):
    """Tailwind text-[…] class for a clearance state.

    An unknown state falls back to the amber HOLD colour rather than a green
    that would read as permission."""
    return _CLEARANCE_TEXT.get(state, _HOLD_TEXT)


def clearance_rows(clearance):
    """One row per SIDE — always both, each with its reasons.

    Rendering only the permitted side would make the reader infer the absence;
    a blocked side WITH its reasons is a research finding."""
    if not clearance:
        return []
    rows = []
    for key, label in (("long", "Long"), ("short", "Short")):
        side = (clearance or {}).get(key)
        if not side:
            continue
        state = side.get("state") or ""
        rows.append({
            "side": label,
            "state": state.replace("_", " "),
            "state_key": state,
            "text_class": clearance_text_class(state),
            "reasons": list(side.get("reasons") or []),
        })
    return rows


def _wall_value(level, pct):
    if level is None:
        return None
    return "%g" % level if pct is None else "%g (%+.1f%%)" % (level, pct)


def dealer_rows(context):
    """Label/value rows for the dealer-positioning block, or [].

    A withheld wall is simply ABSENT rather than rendered as ``None`` — a
    printed None reads as a level. Off-hours (index OI zero) and stale
    payloads both land there by design."""
    if not context or not context.get("collected"):
        return []
    rows = []
    if context.get("regime_words"):
        rows.append({"label": "Gamma regime", "value": context["regime_words"]})
    if context.get("setup_words"):
        rows.append({"label": "Setup", "value": context["setup_words"]})
    if context.get("flip") is not None:
        rows.append({"label": "Flip", "value": "%g" % context["flip"]})
    cw = _wall_value(context.get("call_wall"), context.get("call_wall_pct"))
    if cw:
        rows.append({"label": "Call wall", "value": cw})
    pw = _wall_value(context.get("put_wall"), context.get("put_wall_pct"))
    if pw:
        rows.append({"label": "Put wall", "value": pw})
    if context.get("atm_iv") is not None:
        iv = "%.1f%%" % context["atm_iv"]
        state = context.get("iv_state")
        if state and state != "na":
            iv += " (%s)" % state
        rows.append({"label": "ATM IV", "value": iv})
    return rows


def _ordinal(n):
    if n is None:
        return ""
    if 10 <= (n % 100) <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def peer_line(peers, symbol):
    """"3rd of 5 in Technology" — where this name sits among its peers.

    This answers the question single-stock research should end on: is this the
    best vehicle for the thesis? Empty when there is no peer set."""
    if not peers or not peers.get("ranked"):
        return ""
    rank, n, sector = peers.get("rank"), peers.get("n"), peers.get("sector")
    if not rank or not n or not sector:
        return ""
    return f"{_ordinal(rank)} of {n} in {sector}"


def peer_chips(peers, symbol):
    """The named peers — strongest, immediate neighbours, weakest — as chips."""
    if not peers or not peers.get("ranked"):
        return []
    sym = (symbol or "").strip().upper()
    out, seen = [], set()
    for key, role in (("strongest", "strongest"), ("above", "just above"),
                      ("below", "just below"), ("weakest", "weakest")):
        p = peers.get(key)
        if not p or p.get("symbol") in seen or p.get("symbol") == sym:
            continue
        seen.add(p["symbol"])
        out.append({"symbol": p["symbol"], "role": role,
                    "score": p.get("score")})
    return out


def plan_headline(plan):
    """``(text, kind)`` for the Trade Plan header.

    ``kind`` is one of debit / credit / relative / none — a finite set that maps
    to a fixed palette class, never a runtime-built one."""
    if not plan:
        return ("", "none")
    action = (plan.get("action") or "none").lower()
    side = (plan.get("side") or "").lower()
    structure = plan.get("structure") or ""
    if action == "none":
        return ("No trade — the tape does not support this side today", "none")
    if action == "relative":
        return (f"{side.title()} · {structure}", "relative")
    return (f"{side.title()} · {structure} ({action})", action)


def plan_rows(plan):
    """Label/value rows for the Trade Plan block, or [].

    A field the analysis could not produce is OMITTED rather than rendered as
    None — a printed None in a stop row reads as a level."""
    if not plan or (plan.get("action") or "none") == "none":
        return []
    rows = []
    structure = plan.get("structure")
    if structure:
        tenor = ""
        if plan.get("dte_min") and plan.get("dte_max"):
            tenor = f" · {plan['dte_min']}–{plan['dte_max']} DTE"
        rows.append({"label": "Structure", "value": f"{structure}{tenor}",
                     "note": plan.get("rationale") or ""})
    if plan.get("short_strike_guidance"):
        rows.append({"label": "Short strike",
                     "value": plan["short_strike_guidance"], "note": ""})
    if plan.get("entry_zone"):
        rows.append({"label": "Entry zone", "value": plan["entry_zone"],
                     "note": ""})
    if plan.get("stop") is not None:
        rows.append({"label": "Stop", "value": f"{plan['stop']:g}",
                     "note": plan.get("stop_note") or ""})
    if plan.get("target"):
        rows.append({"label": "Target", "value": plan["target"], "note": ""})
    days = plan.get("time_stop_trading_days")
    if days:
        date = plan.get("time_stop_date") or ""
        rows.append({"label": "Time stop",
                     "value": f"{days} trading days — {date}".strip(" —"),
                     "note": plan.get("time_stop_note") or ""})
    if plan.get("events"):
        rows.append({"label": "Events", "value": plan["events"], "note": ""})
    return rows


_PLAN_TEXT = {"debit": _BUY_TEXT, "credit": _BUY_TEXT,
              "relative": _HOLD_TEXT, "none": _HOLD_TEXT}


def plan_text_class(kind):
    """Tailwind class for a plan kind (amber default — never green by
    accident)."""
    return _PLAN_TEXT.get(kind, _HOLD_TEXT)


def earnings_note(coverage, days):
    """One line about the next earnings report — including when we don't know.

    The vendor's coverage is measurably patchy (MSFT/AMZN/META absent while
    AAPL and GOOGL are listed on the same reporting cycle), so an unlisted
    symbol must SAY the date is unknown. Letting the gate's silence read as an
    all-clear is the fail-open case: a hold walks into a report under the
    appearance of protection."""
    if coverage == "upcoming" and days is not None:
        return f"Next earnings in {days} days"
    if coverage == "none_scheduled":
        return "Next earnings: none scheduled in the calendar"
    if coverage == "not_listed":
        return ("Next earnings: unknown — this symbol is not in the earnings "
                "calendar, so the earnings gate cannot speak for it")
    return ""


def gate_rows(verdict):
    """Long-side gates — "why isn't this a BUY?"."""
    return list((verdict or {}).get("gates_triggered") or [])


def short_gate_rows(verdict):
    """Short-side gates, kept SEPARATE from the long ones.

    A short-only constraint in the shared list prints "cannot be SELL" on every
    strong BUY, which is noise rather than a reason."""
    return list((verdict or {}).get("short_gates") or [])


# (``render`` lived here until 2026-09-20, with ``_BREAKDOWN_COLS`` and
# ``_SWING_COLS``, the two ``ui.table`` column sets only it used. NOTHING ever
# routed it: ``main.py`` sends ``/trade`` to ``trade_overview`` and the other
# three Signal Desk screens own the rest of the family, so the only reference
# to ``trade.render`` left in the repo was a test asserting it was callable.
# It also carried the LAST ``calc-v2`` element in the app, and with it the last
# injection of ``theme.QUASAR_INTERNAL_CSS`` - the same block, rule for rule,
# that both entrypoints already inject app-wide as ``APP_FIELD_CSS`` under
# ``ns-app``. ``TYPICAL_ANALYZE_SEC`` / ``ANALYZE_TIMEOUT_SEC`` /
# ``analyzing_label`` had already moved to ``pages/trade_shell.py``.
#
# What the module is FOR is everything above: pure transforms, sixteen of them
# imported by five siblings and the rest pinned by this file's tests.)

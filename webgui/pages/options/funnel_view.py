"""Why was there no trade on this symbol today? - PURE (no UI, no bus, no I/O).

Design 2026-09-15, Part 3. Renders ``cache:options:scan_funnel`` - the per-symbol
account ``scanner_engine.run_full_scan`` keeps of what each scan window did - as
a headline sentence plus a stage list with the survivors remaining at each step.

**The rule this module exists to serve is the repo's hardest one: never print a
zero you did not read.** A funnel counter that is absent, junk, or was never
written because the pass returned early is NOT a zero - it is silence, and a
stage list of confident zeroes is the exact shape that sends a reader hunting
for a market condition when the truth is that nothing looked. So every path that
cannot support a count returns ``stages == []`` and says what happened in words:
a symbol the scan never reached, a symbol Schwab would not quote, a window with
no usable chain, a bucket that is not in the payload, a crashed directional
build. ``bucket_card`` reads a counter through :func:`_count`, which answers
``None`` for anything that is not a real integer, and a stage whose counter is
``None`` is DROPPED rather than rendered as 0.

**The binding stage is the answer.** It is the first stage whose remaining count
is 0 - the wall the symbol actually hit - and the headline is that stage's own
sentence, naming the number that reached it and the reason they stopped. Every
stage and every ``WIDTH_STAGES`` reason has a sentence of its own; a raw counter
name must never reach the screen, and ``test_funnel_view.py`` iterates both sets
to prove none falls back to one.

⚠ The stage ARITHMETIC mirrors the engine, not intuition. ``kept_after_cap`` is
ABSOLUTE and already carries the iron condors BUILT from the survivors, so the
count can RISE across that stage; ``regime_pass_added`` is an addition, not a
removal; and ``emitted`` is read straight off the finished list rather than
accumulated, so it is the terminal truth even where the chain above it does not
add up. Rendering any of those as a subtraction would show a number the engine
never computed.
"""
from ..fmt import num

# ── the vocabulary ──────────────────────────────────────────────────────────

# The stages a candidate width passes through, in SEARCH order - a byte mirror
# of ``scanner_engine.WIDTH_STAGES``, pinned by test against that file's source.
# Tier 1 cannot import the engine, and the ORDER is the meaning: it is what
# breaks a tie between two reasons with the same count, and reporting the later
# one would send the reader past the real wall.
WIDTH_STAGES = ("increment_over_cap",
                "long_leg_missing", "long_leg_unpriced", "long_leg_illiquid",
                "no_credit", "sanity_cap", "credit_floor", "edge_floor",
                "over_trade_cap", "no_contracts", "no_positive_ev")

# The counters this module reads out of each section, mirrored by test against
# the engine's own seed literals.
STRIKE_COUNTERS = ("expiration_sides_in_window",
                   "expiration_sides_skipped_earnings",
                   "delta_reject", "delta_pass", "mark_fail", "delta_ceiling",
                   "em_fail", "liq_fail_short", "width_found",
                   "strikes_dropped_no_delta", "strikes_dropped_off_increment")

SPREAD_COUNTERS = ("built", "momentum_veto", "iron_condors", "kept_after_cap",
                   "regime_pass_added", "regime_filter", "below_iv_floor",
                   "no_iv_history", "gamma_gate", "emitted")

DIRECTIONAL_COUNTERS = ("windows_without_candidates", "built", "vol_gate",
                        "score_cut", "capped", "emitted", "build_failed")

# Display names. The bucket keys are the engine's own ``0DTE``/``SWING`` - the
# spelling ``signal_recorder`` records and ``shared.calibration`` buckets on -
# so the label is where the reader's "0-DTE" lives.
BUCKET_LABELS = {"0DTE": "0-DTE", "SWING": "Swing",
                 "DIRECTIONAL": "Directional"}

# Stage key -> the label the page shows. Keys are this module's own; the page
# and its tests address a stage through ``LABELS[...]`` so a wording change is
# one edit.
LABELS = {
    # short strikes, in the order ``screen_spreads`` examines them
    "delta_band": "In the delta band",
    "priced": "Priced",
    "delta_ceiling": "Under the delta ceiling",
    "em_window": "Inside the expected-move window",
    "liquid": "Short leg liquid",
    "width_found": "Width found",
    # spreads, in the order ``run_full_scan`` gates them
    "built": "Spreads built",
    "momentum_veto": "Past the momentum veto",
    "kept_after_cap": "Kept by the per-symbol cap",
    "regime_pass_added": "With the regime pass",
    "regime_filter": "Past the regime filter",
    "iv_floor": "Past the volatility floor",
    "gamma_gate": "Past the dealer-gamma gate",
    # single-leg directional, which has no strike-level tally of its own
    "dir_built": "Candidates built",
    "dir_vol_gate": "Past the volatility gate",
    "dir_score_cut": "Above the quality bar",
    "dir_capped": "Kept by the per-symbol cap",
    # terminal, shared by all three buckets
    "emitted": "Reached the board",
}

# Whole-symbol stops. ``no_data`` is the defensive other half of ``no_quote``
# and is unreachable on today's fetch path; it is worded anyway, because the
# alternative to a sentence is a screen full of zeroes.
STOP_SENTENCES = {
    "no_quote": "Schwab returned no quote for this symbol.",
    "no_data": "The scan could not load this symbol's price history or chains.",
}
STOP_UNKNOWN = "The scan stopped on this symbol before it reached a chain."

NOT_SCANNED = "This symbol was not in the last scan."
NO_BUCKET = "The scan recorded no account of this window for this symbol."
NO_CHAIN = "The scan could not read an options chain for this window."
STALE = "From an earlier scan."


# ── reading the payload ─────────────────────────────────────────────────────

def _count(d, key):
    """A counter as a non-negative int, or None when there is no reading.

    ⚠ ``num`` rejects bool and NaN, which is what keeps ``build_failed`` and a
    corrupted counter out of the arithmetic. None here means the stage is
    DROPPED - never rendered as 0.
    """
    if not isinstance(d, dict):
        return None
    f = num(d.get(key))
    if f is None or f != int(f) or f < 0:
        return None
    return int(f)


def _sub(remaining, n):
    """``remaining - n``, floored at 0, and None if either side is unknown."""
    if remaining is None or n is None:
        return None
    return max(remaining - n, 0)


def _money(v):
    """``$250`` for a real dollar reading, else "" - never a hardcoded figure."""
    f = num(v)
    if f is None or f <= 0:
        return ""
    return f"${f:,.0f}" if f == int(f) else f"${f:,.2f}"


def _plural(n, one, many=None):
    return one if n == 1 else (many or one + "s")


def _top(counter, order):
    """The largest entry in ``counter``, ties broken by ``order``.

    The funnel is PUBLISHED, so ``width_reasons`` routinely arrives back from
    ``json.loads`` as a plain dict rather than the Counter the engine wrote -
    hence no ``most_common`` here.
    """
    if not isinstance(counter, dict):
        return None
    best, best_n = None, 0
    for name in order:
        n = _count(counter, name)
        if n and n > best_n:
            best, best_n = name, n
    return best


# ── the sentences ───────────────────────────────────────────────────────────

# One clause per width stage. The subject is always "every width" or "no width":
# the search attributes a strike to the FURTHEST stage its BEST width reached,
# so the reason describes the whole ladder of widths tried for that strike, not
# one of them.
WIDTH_SENTENCES = {
    "increment_over_cap":
        "this chain's strike increment is wider than the widest spread the "
        "scanner builds, so no width was ever tried.",
    "long_leg_missing":
        "no long strike was listed at any width the scanner builds.",
    "long_leg_unpriced":
        "the long leg had no price at any width.",
    "long_leg_illiquid":
        "the long leg failed its liquidity gate at every width.",
    "no_credit":
        "no width came back with a net credit to collect.",
    "sanity_cap":
        "every width's quoted credit sat implausibly close to its full width, "
        "which is a quote fault rather than a spread.",
    "credit_floor":
        "every width paid less than the credit floor this volatility regime "
        "asks for.",
    "edge_floor":
        "every width paid less than the short strike's own assignment "
        "probability plus the edge margin.",
    "over_trade_cap":
        "every width that cleared the credit and edge floors cost more than "
        "the {cap}per-trade risk cap.",
    "no_contracts":
        "the risk cap left no room for a single contract at any width.",
    # ⚠ UNREACHABLE at the shipped EDGE_MARGIN (recorded in 65e1612): the edge
    # floor above already demands credit/width > |delta| + margin, which IS the
    # zero-margin E[PnL] > 0 test with the margin made explicit, so nothing
    # survives that floor to fail this one. Worded anyway - a margin change must
    # not be the thing that ships a raw counter name to the screen.
    "no_positive_ev":
        "every width that cleared the floors carried a negative expected P&L.",
}
WIDTH_UNRECORDED = "no width was found and the scan recorded no reason."


def _s_delta_band(ctx):
    """The first strike stage, so there is no "N entered" to name - the reason
    has to come from the counters that sit OUTSIDE the partition."""
    st = ctx["strikes"]
    in_window = _count(st, "expiration_sides_in_window")
    skipped = _count(st, "expiration_sides_skipped_earnings")
    if in_window == 0:
        return "no expiration in this window was listed in the chain."
    if in_window and skipped and skipped >= in_window:
        when = ctx["entry"].get("earnings_date")
        when = f" on {when}" if isinstance(when, str) and when else ""
        return ("every expiration in this window was skipped for the earnings "
                f"report{when}.")
    rejected = _count(st, "delta_reject")
    if rejected:
        return (f"all {rejected} strikes examined sat outside the delta band.")
    off = _count(st, "strikes_dropped_off_increment")
    if off:
        return (f"{off} {_plural(off, 'strike')} were dropped as off the "
                "chain's standard increment, and nothing else reached the "
                "delta band.")
    no_delta = _count(st, "strikes_dropped_no_delta")
    if no_delta:
        return (f"{no_delta} {_plural(no_delta, 'strike')} carried no delta, "
                "and nothing else reached the delta band.")
    return "no strike in this window reached the delta band."


def _s_width_found(ctx):
    n = ctx["entered"]
    lead = f"{n} short {_plural(n, 'strike')} priced, and "
    reason = _top(ctx["strikes"].get("width_reasons"), WIDTH_STAGES)
    if reason is None:
        return lead + WIDTH_UNRECORDED
    return lead + WIDTH_SENTENCES[reason].format(cap=ctx["cap"])


def _s_iv_floor(ctx):
    sp = ctx["spreads"]
    below = _count(sp, "below_iv_floor") or 0
    none_ = _count(sp, "no_iv_history") or 0
    entered = ctx["entered"]
    if none_ > below:
        return (f"{entered} {_plural(entered, 'spread')} reached the "
                "volatility floor, and every one was refused because there is "
                "no volatility history for this symbol to price against.")
    return (f"{entered} {_plural(entered, 'spread')} reached the volatility "
            "floor, and every one sat below the IV-rank floor - the "
            "volatility here is too cheap to sell.")


def _s_dir_built(ctx):
    d = ctx["bucket"]
    windows = _count(d, "windows_without_candidates")
    if windows:
        return (f"{windows} scan {_plural(windows, 'window')} were read and "
                "offered no single-leg candidate.")
    return "neither scan window had a chain to read."


def _entered_strikes(ctx, noun="short strikes"):
    n = ctx["entered"]
    return f"{n} {noun if n != 1 else noun.rstrip('s')}"


# stage label -> the binding sentence, as a string or a callable over the
# context. ``{n}`` is the count that ENTERED the stage - the previous stage's
# remaining, which is what makes "38 short strikes priced, and ..." a fact
# rather than a total.
STAGE_SENTENCES = {
    LABELS["delta_band"]: _s_delta_band,
    LABELS["priced"]:
        "{n} short strikes sat inside the delta band, and none of them had a "
        "usable mark.",
    LABELS["delta_ceiling"]:
        "{n} short strikes were priced, and every one sat past the short-delta "
        "ceiling.",
    LABELS["em_window"]:
        "{n} short strikes were under the delta ceiling, and every one sat "
        "outside the expected-move window.",
    LABELS["liquid"]:
        "{n} short strikes sat inside the expected-move window, and every one "
        "failed the short-leg liquidity gate.",
    LABELS["width_found"]: _s_width_found,
    LABELS["built"]:
        "{n} short strikes found a width, and none of them became a spread.",
    LABELS["momentum_veto"]:
        "{n} spreads were built, and the intraday momentum veto dropped every "
        "one - the underlying had already moved more than its expected day.",
    LABELS["kept_after_cap"]:
        "{n} spreads survived the momentum veto, and the per-symbol cap kept "
        "none of them.",
    # ⚠ Cannot bind: this stage ADDS, so it is only zero when the stage before
    # it is already zero and has bound first. Worded so a future reordering
    # cannot ship a raw counter name.
    LABELS["regime_pass_added"]:
        "{n} spreads were kept, and the regime-gated directional pass added "
        "none.",
    LABELS["regime_filter"]:
        "{n} spreads reached the regime filter, and the sentiment and trend "
        "filter dropped every one as being on the structurally-doomed side.",
    LABELS["iv_floor"]: _s_iv_floor,
    LABELS["gamma_gate"]:
        "{n} spreads cleared the volatility floor, and the dealer-gamma regime "
        "gate removed every one.",
    LABELS["dir_built"]: _s_dir_built,
    LABELS["dir_vol_gate"]:
        "{n} single-leg candidates were built, and the volatility gate refused "
        "every one.",
    LABELS["dir_score_cut"]:
        "{n} single-leg candidates cleared the volatility gate, and every one "
        "scored below the quality bar.",
    LABELS["dir_capped"]:
        "{n} single-leg candidates cleared the quality bar, and the per-symbol "
        "cap kept none of them.",
    LABELS["emitted"]:
        "{n} candidates survived every gate, and none of them reached the "
        "final list.",
}


# ── the public surface ──────────────────────────────────────────────────────

def empty_symbols(payload, bucket):
    """Symbols whose ``bucket`` emitted nothing, sorted.

    ⚠ A symbol whose bucket is ABSENT, or whose bucket carries no ``emitted``
    count, is left OUT. It has not emitted zero - it has said nothing, and
    listing it under "produced nothing" is the same claim as printing a 0 for
    it. A symbol the scan STOPPED on is included: it really did produce no
    signal, and its card says why.
    """
    symbols = payload.get("symbols") if isinstance(payload, dict) else None
    if not isinstance(symbols, dict):
        return []
    out = []
    for sym, entry in symbols.items():
        b = _bucket_of(entry, bucket)
        if b is None:
            continue
        target = b if bucket == "DIRECTIONAL" else b.get("spreads")
        if _count(target, "emitted") == 0:
            out.append(sym)
    return sorted(out)


def stale_note(payload, scan_timestamp):
    """:data:`STALE` when the funnel's stamp differs from the live scan's.

    ⚠ Two stamps are needed to say one is older than the other. With either
    missing this returns None rather than guessing - a note that appears
    whenever a caller has not wired a timestamp is a note nobody reads.
    """
    if not isinstance(payload, dict):
        return None
    own = payload.get("timestamp")
    if not own or not scan_timestamp:
        return None
    return STALE if own != scan_timestamp else None


def bucket_card(entry, bucket, symbol=None, note=None):
    """One card for one symbol's ``bucket``: headline, stages, note.

    ``entry`` is the per-symbol account out of ``payload["symbols"]``, or None
    for a symbol the scan never reached. ``symbol`` names it in the headline -
    the engine's funnel keys entries BY symbol and stores no ``symbol`` field,
    so the caller supplies it (an entry that carries one is read as a fallback).
    ``note`` is passed through from :func:`stale_note`.

    Returns ``{"headline", "stages", "note"}`` where each stage is
    ``{"label", "remaining", "binding"}``. ``stages`` is EMPTY whenever the
    payload cannot support a count - see the module docstring.
    """
    def card(headline, stages=()):
        return {"headline": _prefix(entry, bucket, symbol) + headline,
                "stages": list(stages), "note": note}

    if entry is None:
        return card(NOT_SCANNED)
    if not isinstance(entry, dict):
        return card(NOT_SCANNED)

    stop = entry.get("stop")
    if stop:
        return card(STOP_SENTENCES.get(stop, STOP_UNKNOWN))

    b = _bucket_of(entry, bucket)
    if b is None:
        return card(NO_BUCKET)

    if bucket == "DIRECTIONAL":
        if b.get("build_failed"):
            return card("the single-leg build failed for this symbol; the "
                        "scan logged the error.")
        stages = _directional_stages(b)
    else:
        if not b.get("chain"):
            return card(NO_CHAIN)
        stages = _strike_stages(b.get("strikes")) + \
            _spread_stages(b.get("spreads"))

    if not stages:
        return card(NO_BUCKET)

    ctx = {"entry": entry, "bucket": b,
           "strikes": b.get("strikes") if isinstance(b.get("strikes"), dict)
           else {},
           "spreads": b.get("spreads") if isinstance(b.get("spreads"), dict)
           else {},
           "cap": _cap_phrase(entry)}
    return card(_headline(stages, ctx), stages)


# ── internals ───────────────────────────────────────────────────────────────

def _prefix(entry, bucket, symbol):
    label = BUCKET_LABELS.get(bucket, str(bucket))
    if not symbol and isinstance(entry, dict):
        symbol = entry.get("symbol")
    return f"{symbol} · {label}: " if symbol else f"{label}: "


def _cap_phrase(entry):
    """``"$250 "`` when the funnel names the per-trade cap, else ``""``.

    ⚠ The figure is NOT hardcoded. The two paper books disagree about it - the
    Ledger's per-trade limit is $750 and the Account's $250, and the scanner's
    own ``DEFAULT_MAX_RISK_DOLLARS`` follows the Account's constant - so a
    number written here would be wrong for at least one reader. The funnel does
    not carry it today; the sentence simply says "the per-trade risk cap", and
    gains the figure automatically if a publisher ever stamps one.
    """
    m = _money(entry.get("max_risk_dollars")) if isinstance(entry, dict) else ""
    return f"{m} " if m else ""


def _bucket_of(entry, bucket):
    if not isinstance(entry, dict):
        return None
    buckets = entry.get("buckets")
    if not isinstance(buckets, dict):
        return None
    b = buckets.get(bucket)
    return b if isinstance(b, dict) else None


def _stage(label, remaining):
    return {"label": label, "remaining": remaining, "binding": False}


def _strike_stages(st):
    """The six short-strike stages, or [] when the tally was never written.

    ``screen_spreads`` returns early on an empty chain or a zero underlying
    without touching the funnel, so an empty tally means NOTHING LOOKED - which
    is not the same fact as "nothing passed" and must not render as six zeroes.
    """
    entered = _count(st, "delta_pass")
    if entered is None:
        return []
    out = [_stage(LABELS["delta_band"], entered)]
    for key, counter in (("priced", "mark_fail"),
                         ("delta_ceiling", "delta_ceiling"),
                         ("em_window", "em_fail"),
                         ("liquid", "liq_fail_short")):
        entered = _sub(entered, _count(st, counter))
        if entered is None:
            return out
        out.append(_stage(LABELS[key], entered))
    found = _count(st, "width_found")
    if found is not None:
        # ABSOLUTE, from the tally - the strikes that found a width, which the
        # partition guarantees equals the previous remaining minus the width
        # reasons. Read rather than derived so a dropped reason cannot inflate
        # it.
        out.append(_stage(LABELS["width_found"], found))
    return out


def _spread_stages(sp):
    built = _count(sp, "built")
    if built is None:
        return []
    out = [_stage(LABELS["built"], built)]
    remaining = _sub(built, _count(sp, "momentum_veto"))
    if remaining is None:
        return out
    out.append(_stage(LABELS["momentum_veto"], remaining))
    # ABSOLUTE and already carrying the condors BUILT from the survivors, which
    # is why this can exceed built - momentum_veto. A subtraction here would
    # render a number the engine never computed.
    kept = _count(sp, "kept_after_cap")
    if kept is None:
        return out
    out.append(_stage(LABELS["kept_after_cap"], kept))
    added = _count(sp, "regime_pass_added")
    if added is None:
        return out
    remaining = kept + added
    out.append(_stage(LABELS["regime_pass_added"], remaining))
    for key, counters in (("regime_filter", ("regime_filter",)),
                          ("iv_floor", ("below_iv_floor", "no_iv_history")),
                          ("gamma_gate", ("gamma_gate",))):
        for c in counters:
            remaining = _sub(remaining, _count(sp, c))
        if remaining is None:
            return out
        out.append(_stage(LABELS[key], remaining))
    emitted = _count(sp, "emitted")
    if emitted is not None:
        # Terminal, ASSIGNED off the finished list rather than accumulated - so
        # it is the truth even where the chain above it does not add up.
        out.append(_stage(LABELS["emitted"], emitted))
    return out


def _directional_stages(d):
    built = _count(d, "built")
    if built is None:
        return []
    out = [_stage(LABELS["dir_built"], built)]
    remaining = built
    for key, counter in (("dir_vol_gate", "vol_gate"),
                         ("dir_score_cut", "score_cut"),
                         ("dir_capped", "capped")):
        remaining = _sub(remaining, _count(d, counter))
        if remaining is None:
            return out
        out.append(_stage(LABELS[key], remaining))
    emitted = _count(d, "emitted")
    if emitted is not None:
        out.append(_stage(LABELS["emitted"], emitted))
    return out


def _headline(stages, ctx):
    """The binding stage's own sentence, or what survived when none binds."""
    for i, s in enumerate(stages):
        if s["remaining"] != 0:
            continue
        s["binding"] = True
        ctx["entered"] = stages[i - 1]["remaining"] if i else 0
        sentence = STAGE_SENTENCES.get(s["label"])
        if sentence is None:                       # defensive; pinned by test
            return f"nothing survived {s['label'].lower()}."
        if callable(sentence):
            return sentence(ctx)
        return sentence.format(n=ctx["entered"])
    n = stages[-1]["remaining"]
    return f"{n} {_plural(n, 'signal')} reached the board."

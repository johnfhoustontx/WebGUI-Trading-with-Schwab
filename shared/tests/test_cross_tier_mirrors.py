"""Constants that are DUPLICATED across tiers on purpose, pinned by source text.

Some values genuinely cannot be shared by import: the webgui takes no engine
imports (Tier 1), and two services cannot import each other or the
`sentiment-dashboard/scoring` package (the documented cross-app `scoring`
module-name collision). Where a config file is not the right home - display
WORDS are not operator-tunable - the copies stay, and the only thing keeping them
in step is discipline.

This module replaces that discipline with a test. It reads the files as TEXT and
AST-parses the constants out, importing nothing, so it is safe to run from
anywhere and cannot itself trigger a collision.
"""
import ast
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _const(rel_path, name):
    """A module-level literal constant, read WITHOUT importing the module."""
    path = ROOT / rel_path
    assert path.exists(), f"mirror source moved: {rel_path}"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                getattr(t, "id", None) == name for t in node.targets):
            return ast.literal_eval(node.value)
    pytest.fail(f"{rel_path} no longer defines {name} - "
                "if it moved, this mirror test must move with it")


# --- the five market-regime display words -----------------------------------
# Renamed 2026-08-14 (Mean Reversion -> Balanced, Choppy -> Whipsaw, Volatile ->
# Stressed) while the internal KEYS stayed - they are the RegimeState contract,
# the regime_intraday DB columns and the driver packet. Root CLAUDE.md says
# "keep them in step" across four tiers; one rename in one tier used to drift
# silently until a user noticed a screen disagreeing with another.

REGIME_SOURCE = "sentiment-dashboard/scoring/market_regime.py"
REGIME_MIRRORS = [
    ("services/driver_svc/compute.py", "_REGIME_LABELS"),
    ("services/options_svc/market_console.py", "REGIME_LABELS"),
    ("webgui/pages/regime_mix.py", "REGIME_LABELS"),
]


def test_regime_display_words_agree_across_every_tier():
    source = _const(REGIME_SOURCE, "REGIME_DISPLAY")
    assert source, "the source dict is empty - the pin would be vacuous"
    for rel, name in REGIME_MIRRORS:
        assert _const(rel, name) == source, (
            f"{rel}:{name} has drifted from {REGIME_SOURCE}:REGIME_DISPLAY.\n"
            "The words are duplicated because these tiers cannot import each "
            "other; changing one means changing all of them.")


def test_the_regime_keys_are_the_documented_five():
    """Non-vacuity, and a guard on the OTHER half: the keys are a contract
    (RegimeState, the regime_intraday columns, the driver packet), so a key
    change is a migration, not a rename."""
    assert set(_const(REGIME_SOURCE, "REGIME_DISPLAY")) == {
        "mean_reversion", "trending", "breakout", "choppy", "crisis"}


def test_sentiment_svc_delegates_rather_than_copying():
    """sentiment_svc CAN import the scoring package, so it must not hold a
    fourth copy - it calls market_regime.regime_label. Recorded so nobody
    'helpfully' adds one for symmetry."""
    src = (ROOT / "services/sentiment_svc/compute.py").read_text(encoding="utf-8")
    assert "market_regime.regime_label(" in src
    assert "\"mean_reversion\": \"Balanced\"" not in src


# --- the Regime word's hover --------------------------------------------------
# The service renders the displayed regime word (REGIME_DISPLAY + the
# _DIRECTIONAL adornments, or "Unclear" from regime_label); the webgui holds a
# hover per word. A word the service can print with no hover would render bare.

REGIME_PICTURE_PAGE = "webgui/pages/regime_mix.py"


def _regime_words():
    words = set(_const(REGIME_SOURCE, "REGIME_DISPLAY").values())
    for table in _const(REGIME_SOURCE, "_DIRECTIONAL").values():
        words |= set(table.values())
    # regime_label's own literal for an unknown key.
    return words | {"Unclear"}


def test_every_regime_word_the_service_can_print_has_a_hover():
    words = _regime_words()
    assert len(words) == 11, sorted(words)
    pictures = _const(REGIME_PICTURE_PAGE, "REGIME_PICTURE")
    missing = sorted(w for w in words if not pictures.get(w))
    assert not missing, (
        f"{REGIME_PICTURE_PAGE}:REGIME_PICTURE has no hover for {missing} - the "
        "service can print these words.")


# --- the market-trend pill words --------------------------------------------
# The short word on the Market Trend pill (Climbing / Stalling / Circling /
# Gliding / Diving, plus the 30-day structural words) is drawn by the webgui AND
# by the phone snapshot options_svc renders. Neither tier may import the other,
# so the dict is copied; a rename in one used to leave the phone saying a word
# the screen had stopped using.

TREND_WORDS_SOURCE = "webgui/pages/sentiment.py"
TREND_WORDS_MIRROR = "services/options_svc/market_snapshot.py"


def test_trend_pill_words_agree_between_the_page_and_the_push():
    page = _const(TREND_WORDS_SOURCE, "_TREND_SHORT")
    assert page, "the source dict is empty - the pin would be vacuous"
    assert _const(TREND_WORDS_MIRROR, "_TREND_SHORT") == page, (
        f"{TREND_WORDS_MIRROR}:_TREND_SHORT has drifted from "
        f"{TREND_WORDS_SOURCE}:_TREND_SHORT - the phone snapshot would name the "
        "trend with a word the screen no longer uses.")


TREND_WORDS_SUMMARY = "services/market_svc/compute.py"
_FIVE_STATES = ("bullish", "lack_of_bullishness", "neutral",
                "lack_of_bearishness", "bearish")


def test_the_summary_names_the_trend_with_the_pills_words():
    """market_svc writes the Desk summary with these words; the pill beside it
    uses the page's. A rename in one would have the sentence and the pill name
    one trend two ways."""
    page = _const(TREND_WORDS_SOURCE, "_TREND_SHORT")
    summary = _const(TREND_WORDS_SUMMARY, "_TREND_WORDS")
    assert summary == {k: page[k] for k in _FIVE_STATES}


def test_the_summary_has_a_fact_for_every_word_the_screen_can_show():
    """market_svc writes every factual statement of the Desk summary itself -
    the model only joins them and adds a posture, because every time it
    paraphrased a reading it bent it. A word the screen can show with no
    statement here would silently drop that reading from the sentence."""
    page = _const(TREND_WORDS_SOURCE, "_TREND_SHORT")
    flight = {page[k] for k in _FIVE_STATES}
    assert set(_const(TREND_WORDS_SUMMARY, "_TREND_FACTS")) == flight
    assert set(_const(TREND_WORDS_SUMMARY, "_REGIME_FACTS")) == _regime_words()
    signals = {signal for _t, _size, _bias, signal in _signal_bands()}
    assert set(_const(TREND_WORDS_SUMMARY, "_SENTIMENT_FACTS")) == signals


def test_the_summary_counts_the_bull_bear_maps_own_quadrants():
    """market_svc counts sectors into ``bullbear.quadrant``'s buckets (it cannot
    import Tier 1) and states the sector counts itself from those buckets. A
    bucket added to the map but not here would be miscounted, and the summary's
    sector sentence would disagree with the chips beside it."""
    page = tuple(_const("webgui/pages/bullbear.py", "QUADRANTS"))
    assert tuple(_const(TREND_WORDS_SUMMARY, "_QUADRANTS")) == page


# --- the BIAS / SIGNAL hover sentences --------------------------------------
# Each hover names the composite band its word covers, restating
# live_composite.signal_band's cut-offs and position sizes in Tier-1 prose
# (Tier 1 cannot import the engine). This pins the pairing: every word
# signal_band can publish has a sentence, and each sentence quotes its band's
# threshold and the size that band sets — so moving a cut-off without the
# prose fails here rather than leaving a hover that lies about the number.

BAND_SOURCE = "sentiment-dashboard/live_composite.py"
BAND_PAGE = "webgui/pages/sentiment.py"


def _signal_bands():
    """``[(threshold or None, size, bias, signal)]``, read out of
    ``signal_band``'s ``if total >= N: return (...)`` ladder as text."""
    tree = ast.parse((ROOT / BAND_SOURCE).read_text(encoding="utf-8"))
    fn = next((n for n in tree.body if isinstance(n, ast.FunctionDef)
               and n.name == "signal_band"), None)
    assert fn is not None, f"{BAND_SOURCE} no longer defines signal_band"
    out = []
    for node in fn.body:
        if isinstance(node, ast.If):
            out.append((ast.literal_eval(node.test.comparators[0]),
                        *ast.literal_eval(node.body[0].value)))
        elif isinstance(node, ast.Return):
            out.append((None, *ast.literal_eval(node.value)))
    return out


def test_every_band_word_the_service_publishes_has_a_hover_quoting_its_band():
    bands = _signal_bands()
    assert len(bands) == 5, f"expected signal_band's five bands, read {bands}"
    pics = _const(BAND_PAGE, "BAND_WORD_PICTURE")
    for threshold, size, bias, signal in bands:
        bias_tip = pics["bias"].get(bias.lower(), "")
        signal_tip = pics["signal"].get(signal.lower(), "")
        assert bias_tip and signal_tip, f"no hover for {bias!r} / {signal!r}"
        assert size in bias_tip, f"{bias!r} hover does not quote size {size}"
        if threshold is not None:
            cut = f"{threshold:g}"
            assert cut in bias_tip and cut in signal_tip, (
                f"the {bias!r} / {signal!r} hovers do not quote the {cut} "
                "cut-off signal_band uses")


# --- the covered-call identifier --------------------------------------------
# ONE string, "COVERED_CALL", in three tiers that cannot import each other:
#
#   services/options_svc/compute.py  COVERED_CALL_TYPE       - the scan-row TYPE
#   options-scanner/paper_engine.py  COVERED_CALL_STRATEGIES - the position
#                                                              STRATEGY it settles
#   webgui/pages/options/shares.py   COVERED_CALL_STRATEGIES - the position
#                                                              STRATEGY it displays
#
# ⚠ Those are two DIFFERENT FIELDS, and until ``compute.open_income_position``
# existed the mirror was not real: a scan row's ``type`` and a paper position's
# ``strategy`` merely happened to spell the same word, with nothing carrying one
# into the other. That function is the link — it stores ``strategy = row["type"]``
# — so the three now genuinely have to agree, and this is the test that says so.
# ``shares.py`` claimed to be "pinned by a test on both sides" for a while when
# no such test existed; writing it was cheaper than deleting the claim.

COVERED_CALL_WORD = "COVERED_CALL"


def test_the_covered_call_identifier_is_one_word_in_three_tiers():
    scan_type = _const("services/options_svc/compute.py", "COVERED_CALL_TYPE")
    engine = _const("options-scanner/paper_engine.py", "COVERED_CALL_STRATEGIES")
    page = _const("webgui/pages/options/shares.py", "COVERED_CALL_STRATEGIES")
    assert scan_type == COVERED_CALL_WORD, (
        "the scan-row type changed; the position strategy written by "
        "open_income_position changes with it, so both engine and page must move")
    assert tuple(engine) == (COVERED_CALL_WORD,)
    assert tuple(page) == (COVERED_CALL_WORD,)
    assert scan_type in engine and scan_type in page, (
        "open_income_position stores the scan row's TYPE as the position's "
        "STRATEGY, so a type the engine cannot recognise is a covered call that "
        "is never called away and a lot that can never leave the book.")


def test_the_openable_income_structures_are_the_two_single_leg_products():
    """Non-vacuity for the pin above, and a guard on the page's own gate.

    ``handoff.INCOME_OPENABLE_TYPES`` decides which rows GET an Open button and
    ``compute.INCOME_OPEN_STRUCTURES`` decides which the service will accept. A
    button on a row the service refuses is a dead control; a row the service
    accepts with no button is a feature nobody can reach.
    """
    page = tuple(_const("webgui/pages/options/handoff.py", "INCOME_OPENABLE_TYPES"))
    assert page == ("SHORT_PUT", COVERED_CALL_WORD)
    # The service side names the covered call through COVERED_CALL_TYPE rather
    # than a literal, so its tuple is not a literal AST node - read the source.
    src = (ROOT / "services/options_svc/compute.py").read_text(encoding="utf-8")
    assert 'INCOME_OPEN_STRUCTURES = ("SHORT_PUT", COVERED_CALL_TYPE)' in src


# --- the manuals dual registration ------------------------------------------
# A manual has to be registered in TWO places: docs/manuals/build_docs.py to be
# BUILT, and webgui/pages/manuals.py to be SERVED (that dict is also the path
# whitelist, so an unlisted file is refused rather than served). Root CLAUDE.md
# flags the trap. The existing webgui test checks catalog -> built file; this
# checks the CONVERSE, which was the unguarded half: a manual that is built but
# never listed is silently unreachable in the app.

def _manual_keys(rel_path):
    path = ROOT / rel_path
    assert path.exists(), f"manuals registry moved: {rel_path}"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                getattr(t, "id", None) == "MANUALS" for t in node.targets):
            val = node.value
            if isinstance(val, ast.Dict):
                return {ast.literal_eval(k) for k in val.keys}
            if isinstance(val, (ast.List, ast.Tuple)):
                out = set()
                for el in val.elts:
                    lit = ast.literal_eval(el)
                    out.add(lit[0] if isinstance(lit, (tuple, list)) else lit)
                return out
    pytest.fail(f"{rel_path} no longer defines MANUALS")


def test_every_built_manual_is_also_served():
    built = _manual_keys("docs/manuals/build_docs.py")
    served = _manual_keys("webgui/pages/manuals.py")
    assert built, "no manuals found - the pin would be vacuous"
    unreachable = built - served
    assert not unreachable, (
        f"built but NOT listed in webgui/pages/manuals.py: {sorted(unreachable)}. "
        "That dict is the serving whitelist, so these are unreachable in the app.")


# --- the published Gamma symbols --------------------------------------------
# Four of the public live screens are views of /options/gamma, and three of them
# name a symbol ($SPX, SPY, QQQ). ``cache:options:gamma`` is ONE symbol-agnostic
# key holding whatever the private app last looked at, so options_svc publishes
# a per-symbol snapshot for each named symbol -- and it must know WHICH symbols
# without importing webgui/live_screens.py, because `services` may not import
# `webgui` (Tier 1 / Tier 2). Hence the copy, and hence this pin.
#
# ⚠ Get this wrong in the DROPPING direction and nothing errors: the screen just
# polls a key nobody publishes and shows "no snapshot yet" forever.

GAMMA_SYMBOLS_SOURCE = "services/options_svc/handlers.py"
LIVE_SCREENS = "webgui/live_screens.py"


def _published_gamma_symbols():
    """The symbols options_svc publishes a per-symbol Gamma snapshot for.

    They are the KEYS of ``PUBLISHED_GAMMA_HISTORY_VIEWS`` -- one table, whose
    values say which of each symbol's view histories are worth a key, so a symbol
    cannot be published without an entry saying why."""
    return tuple(_const(GAMMA_SYMBOLS_SOURCE, "PUBLISHED_GAMMA_HISTORY_VIEWS"))


def _gamma_screen_pins(rel_path):
    """``(symbol, view)`` for every ``Screen`` whose module is ``options.gamma``.

    ``view`` is ``None`` when the screen pins none -- which is not the same as
    "no view": an unpinned view means the page BUILDS its picker, so the reader
    can reach all four heatmap views and every one of them needs its history.

    Read as TEXT like everything else here -- importing live_screens.py would put
    webgui on sys.path from a shared test."""
    tree = ast.parse((ROOT / rel_path).read_text(encoding="utf-8"))
    out = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and getattr(node.func, "id", None) == "Screen"):
            continue
        kw = {k.arg: k.value for k in node.keywords}
        module = (node.args[3] if len(node.args) > 3 else kw.get("module"))
        if module is None or ast.literal_eval(module) != "options.gamma":
            continue
        pins = ast.literal_eval(kw["kwargs"]) if "kwargs" in kw else {}
        out.append((pins.get("symbol"), pins.get("view")))
    return out


def _gamma_screen_symbols(rel_path):
    """The ``symbol`` pins alone, in order (a screen without one is skipped)."""
    return [sym for sym, _view in _gamma_screen_pins(rel_path) if sym]


def test_the_published_gamma_symbols_are_the_three_the_screens_name():
    """The pin itself, so it is never vacuous while live_screens.py is pending.

    Changing this table changes which symbols options_svc pays to publish every
    minute -- each one a per-symbol snapshot, plus a history key for each view
    the table lists against it."""
    assert _published_gamma_symbols() == ("$SPX", "SPY", "QQQ")


def test_options_svc_publishes_exactly_the_symbols_the_live_screens_pin():
    """The pairing. Skipped only until webgui/live_screens.py lands (it is a
    later task in the same plan); the moment it exists this starts pinning both
    halves and a screen added with an unpublished symbol fails here."""
    if not (ROOT / LIVE_SCREENS).exists():
        pytest.skip(f"{LIVE_SCREENS} not written yet - the pairing engages when "
                    "it lands; the literal pin above holds until then")
    screens = _gamma_screen_symbols(LIVE_SCREENS)
    assert len(screens) == len(set(screens)), (
        f"two live screens pin the same gamma symbol: {screens}")
    assert set(screens) == set(_published_gamma_symbols()), (
        "a live screen names a gamma symbol options_svc does not publish (it "
        "would poll a key nobody writes and stay empty), or options_svc pays to "
        "publish a symbol no screen reads.")


def test_every_served_manual_is_also_built():
    built = _manual_keys("docs/manuals/build_docs.py")
    served = _manual_keys("webgui/pages/manuals.py")
    orphans = served - built
    assert not orphans, (
        f"served but never BUILT: {sorted(orphans)} - the page would 404.")


def test_every_published_gamma_history_is_one_a_screen_actually_draws():
    """The OTHER half of the pairing above: the views, not just the symbols.

    The symbol test cannot see this. A screen pinned to ``{"symbol": "$SPX",
    "view": "Charm"}`` names a published symbol, so it passes every test in the
    repo -- and renders an EMPTY HEATMAP forever, because ``$SPX`` publishes a
    history for ``GEX`` and nothing else. Silently: a missing key reads as "no
    history yet", which is also what a Sunday looks like.

    The rule is derived, not listed. ``GAMMA_HISTORY_VIEWS`` is the set of views
    that HAVE a history key at all, so:

    * a screen pinned to one of them needs exactly that one;
    * a screen pinned to any other view (Flow, Net Prem, Term) draws from the
      MAIN payload and needs none -- which is why ``SPY`` and ``QQQ`` publish an
      empty tuple;
    * a screen that pins NO view builds the picker, so it can reach all four.

    Asserted as EQUALITY, both directions. Missing is the empty screen above;
    extra is the cost the split table was written to avoid -- each published
    history is a per-symbol grid key rewritten every minute, on a store that has
    already needed a manual ~1 GB VACUUM.
    """
    if not (ROOT / LIVE_SCREENS).exists():
        pytest.skip(f"{LIVE_SCREENS} not written yet")
    history_views = set(_const(GAMMA_SYMBOLS_SOURCE, "GAMMA_HISTORY_VIEWS"))
    assert history_views, "no history views - the pin would be vacuous"
    published = _const(GAMMA_SYMBOLS_SOURCE, "PUBLISHED_GAMMA_HISTORY_VIEWS")

    needed = {sym: set() for sym in published}
    for sym, view in _gamma_screen_pins(LIVE_SCREENS):
        if not sym:
            continue                     # symbol-independent (Net Prem)
        assert sym in needed, f"{sym} is pinned by a screen but never published"
        if view is None:
            needed[sym] |= history_views          # picker built - any view reachable
        elif view in history_views:
            needed[sym].add(view)

    for sym, want in sorted(needed.items()):
        assert set(published[sym]) == want, (
            f"PUBLISHED_GAMMA_HISTORY_VIEWS[{sym!r}] is {tuple(published[sym])!r} "
            f"but the live screens need {tuple(sorted(want))!r}. Too few and the "
            "screen draws an empty heatmap forever; too many and options_svc "
            "rewrites a grid key every minute that nothing reads.")

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
# ONE string, "COVERED_CALL", in three places — now anchored on the one home
# rather than on a chain of peers:
#
#   shared/structures.py             COVERED_CALL      - the TAXONOMY, imported
#                                                        by options-scanner and
#                                                        services alike
#   services/options_svc/compute.py  COVERED_CALL_TYPE - the scan-row TYPE
#   webgui/pages/options/shares.py   COVERED_CALL_STRATEGIES - the position
#                                                        STRATEGY it displays
#
# ⚠ Those are two DIFFERENT FIELDS, and until ``compute.open_income_position``
# existed the mirror was not real: a scan row's ``type`` and a paper position's
# ``strategy`` merely happened to spell the same word, with nothing carrying one
# into the other. That function is the link — it stores ``strategy = row["type"]``
# — so they genuinely have to agree, and this is the test that says so.
# ``shares.py`` claimed to be "pinned by a test on both sides" for a while when
# no such test existed; writing it was cheaper than deleting the claim.
#
# ``paper_engine.COVERED_CALL_STRATEGIES`` used to be the third leg here and is
# GONE (2026-09-11, gap assessment B1) — it reads ``shared.structures`` now, so
# there is nothing left to mirror on that side. The page is the last copy,
# because Tier 1 takes no ``services.*`` import and widening its allow-list was
# out of scope; ``shared/tests/test_structures.py`` fails on a new copy anywhere
# in the other two tiers.

COVERED_CALL_WORD = "COVERED_CALL"


def test_the_covered_call_identifier_is_one_word_in_three_tiers():
    taxonomy = _const("shared/structures.py", "COVERED_CALL")
    scan_type = _const("services/options_svc/compute.py", "COVERED_CALL_TYPE")
    page = _const("webgui/pages/options/shares.py", "COVERED_CALL_STRATEGIES")
    assert scan_type == COVERED_CALL_WORD, (
        "the scan-row type changed; the position strategy written by "
        "open_income_position changes with it, so both the taxonomy and the "
        "page must move")
    assert tuple(taxonomy) == (COVERED_CALL_WORD,)
    assert tuple(page) == (COVERED_CALL_WORD,)
    assert scan_type in taxonomy and scan_type in page, (
        "open_income_position stores the scan row's TYPE as the position's "
        "STRATEGY, so a type the engine cannot recognise is a covered call that "
        "is never called away and a lot that can never leave the book.")


def test_the_short_put_spellings_are_not_restated_in_the_page_tier():
    """The converse, and the one Tier-1 copy worth policing: ``shares.py``
    displays covered calls, so it names only that word. A SHORT_PUT / NAKED_PUT
    pair appearing there would be an eighth copy of the taxonomy in the one tier
    that cannot import it."""
    src = (ROOT / "webgui/pages/options/shares.py").read_text(encoding="utf-8")
    assert "NAKED_PUT" not in src, (
        "shares.py has grown its own short-put spelling pair; the taxonomy lives "
        "in shared/structures.py")


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
    # Several screens may share a symbol (the $SPX GEX, Charm, DEX, Vanna and
    # Term boards all read ONE per-symbol snapshot); two screens pinning the
    # same symbol AND view would be the same page published twice.
    pins = [p for p in _gamma_screen_pins(LIVE_SCREENS) if p[0]]
    assert len(pins) == len(set(pins)), (
        f"two live screens pin the same gamma symbol and view: {pins}")
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


# --- the Paper button and the ledger's debit path ---------------------------

def test_the_pages_paper_button_covers_exactly_the_ledgers_debit_structures():
    """The Paper button (webgui) and the ledger's debit path (options-scanner) are
    two lists in tiers that cannot share one. A type with the button but not the
    debit path is refused by name in create_paper_trade, so the button would fail
    every click; the converse is a structure the ledger supports that nobody can
    send."""
    taxonomy = set(_const("shared/structures.py", "LEDGER_DEBIT"))
    page = set(_const("webgui/pages/options/strategy_table.py", "_PAPER_TYPES"))
    credit = set(_const("shared/structures.py", "LEDGER_CREDIT"))
    assert page == taxonomy | credit


# --- the Strategy Finder's swing-scan defaults ------------------------------
# services/options_svc/handlers._SWING_DEFAULTS fills any key a ``swing_scan``
# command omits. The page's untouched scan bar is a set of module constants in
# webgui/pages/options/finder_view.py. Tier 2 cannot import Tier 1, so for two
# months the two drifted: the page moved to DTE 0 / All while the dict stayed at
# 5-30, which a value test ("dte_min == 5") pinned in place rather than caught.
#
# The 2026-09-05 fix moved the FLOOR the other way (to the service's 5), because
# then the directional family was built on the nearest expiry and em_1sd was
# derived from the floor. Neither holds since the whole-chain Finder:
# compute._build_every_expiry builds each listed expiry on its own and scoring
# judges each candidate against its own expiry's move, so the page's 0 is safe
# and the dict follows the page.

FINDER_VIEW = "webgui/pages/options/finder_view.py"
FINDER_PAGE = "webgui/pages/options/swing.py"
SWING_SERVICE = "services/options_svc/handlers.py"


def _assign_value(rel_path, name):
    """The AST of a module-level assignment's value, unevaluated."""
    tree = ast.parse((ROOT / rel_path).read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                getattr(t, "id", None) == name for t in node.targets):
            return node.value
    pytest.fail(f"{rel_path} no longer defines {name}")


def _finder_page_defaults():
    """The scan the page sends when nobody touches the bar, as swing_scan args."""
    call = _assign_value(FINDER_VIEW, "DEFAULT_DTE")
    assert (isinstance(call, ast.Call) and getattr(call.func, "id", None) == "expiry_range_for"
            and len(call.args) == 1 and isinstance(call.args[0], ast.Constant)), (
        "DEFAULT_DTE is no longer expiry_range_for(<label>) - update this reader")
    presets = {label: (lo, hi) for label, lo, hi in _const(FINDER_VIEW, "EXPIRY_PRESETS")}
    dte_min, dte_max = presets[call.args[0].value]
    lo, hi = _const(FINDER_VIEW, "RISK_STYLES")[_const(FINDER_VIEW, "RISK_DEFAULT")]
    return {
        "dte_min": dte_min, "dte_max": dte_max,
        # finder_view.risk_bands: the put side is the negated band.
        "put_d_min": -hi, "put_d_max": -lo, "call_d_min": lo, "call_d_max": hi,
        "min_cr_fraction": _const(FINDER_VIEW, "DEFAULT_MIN_CREDIT_PCT") / 100.0,
    }


def _scan_params_keys():
    """Every key swing.scan_params can send: its dict literal plus subscripts."""
    tree = ast.parse((ROOT / FINDER_PAGE).read_text(encoding="utf-8"))
    fn = next((n for n in tree.body if isinstance(n, ast.FunctionDef)
               and n.name == "scan_params"), None)
    assert fn is not None, f"{FINDER_PAGE} no longer defines scan_params"
    keys = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Dict):
            keys |= {k.value for k in node.keys if isinstance(k, ast.Constant)}
        elif (isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store)
              and isinstance(node.slice, ast.Constant)):
            keys.add(node.slice.value)
    return keys


def test_swing_service_defaults_are_the_pages_untouched_scan():
    service = _const(SWING_SERVICE, "_SWING_DEFAULTS")
    for key, want in _finder_page_defaults().items():
        assert service[key] == pytest.approx(want) if isinstance(want, float) \
            else service[key] == want, (
                f"_SWING_DEFAULTS[{key!r}] = {service[key]!r} but the Finder's "
                f"untouched scan sends {want!r}. A command that omits the key must "
                "run the same scan the page would. Change both.")


def test_swing_service_defaults_cover_exactly_what_the_page_can_send():
    """Every key the page sends has a fallback, and the only fallback the page
    never sends is ``families`` - whose None means every group, which is what
    the page's scan builds (the chips filter on the page, not in the scan)."""
    service = _const(SWING_SERVICE, "_SWING_DEFAULTS")
    sent = _scan_params_keys()
    assert sent - set(service) == set(), f"no fallback for {sorted(sent - set(service))}"
    assert set(service) - sent == {"families"}
    assert service["families"] is None and service["expiry_choice"] is None


# ── Rate my trade: every Calculator template has a scorer identity ──────────

def _dict_literal_keys(rel_path, name):
    """The string keys of a module-level ``name = {...}`` dict literal."""
    tree = ast.parse((ROOT / rel_path).read_text(encoding="utf-8"))
    for node in tree.body:
        if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict)
                and any(isinstance(t, ast.Name) and t.id == name for t in node.targets)):
            return {k.value for k in node.value.keys if isinstance(k, ast.Constant)}
    raise AssertionError(f"{name} is not a dict literal in {rel_path}")


def test_the_rating_map_covers_every_calculator_template():
    """``calc_rate`` maps the Calculator's shape code to the Strategy Finder's
    type. A template missing from the map would be rated as a CUSTOM structure
    against the debit bars - wrong, and silently so. Neither tier can import the
    other, so the two key sets are compared here."""
    templates = _dict_literal_keys("webgui/pages/options/strategies.py",
                                   "STRATEGY_TEMPLATES")
    mapped = _dict_literal_keys("services/options_svc/rate_trade.py", "CALC_TO_SCORER")
    assert templates, "found no Calculator templates"
    assert templates == mapped, {"unmapped": templates - mapped,
                                 "stale": mapped - templates}


def test_the_public_tools_codes_are_the_ones_the_rating_map_knows():
    """``shared.public_tools`` folds every unknown strategy or structure code to
    CUSTOM, so PCSA and PCSB cannot be two cache keys for one rating. It cannot
    import ``rate_trade``, so its tuple is compared with the map here. (The map
    is itself pinned to the Calculator's templates above.)"""
    codes = set(_const("shared/public_tools.py", "STRUCTURE_CODES"))
    mapped = _dict_literal_keys("services/options_svc/rate_trade.py", "CALC_TO_SCORER")
    assert codes == mapped | {"CUSTOM"}, {"missing": mapped - codes,
                                          "stale": codes - mapped - {"CUSTOM"}}


# ── IV vs HV bands: the Symbol Dossier and the strategy scorer ──────────────
# ``strategy_scoring.infer_market_view`` falls back to the IV/HV ratio when
# iv_rank is missing, with BARE LITERALS (``iv_hv >= 1.2`` -> "high",
# ``iv_hv <= 0.9`` -> "low"). The dossier page (Tier 1) cannot import
# options-scanner, so it restates them as IV_HV_HIGH / IV_HV_LOW. A dossier that
# called a symbol "high" where the scorer said "mid" would be worse than none.

SCORER = "options-scanner/strategy_scoring.py"
DOSSIER_FACTS = "webgui/pages/symbol_facts.py"


def _iv_hv_fallback_bounds():
    """The ``iv_hv >= X`` / ``iv_hv <= Y`` literals in infer_market_view.

    Only the inclusive operators: the iv_rank-primary branch's light nudge uses
    strict ``> 1.3`` / ``< 0.85`` and is a different rule."""
    tree = ast.parse((ROOT / SCORER).read_text(encoding="utf-8"))
    fn = next((n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
               and n.name == "infer_market_view"), None)
    assert fn is not None, f"{SCORER} no longer defines infer_market_view"
    found = {"ge": [], "le": []}
    for node in ast.walk(fn):
        if (isinstance(node, ast.Compare) and isinstance(node.left, ast.Name)
                and node.left.id == "iv_hv" and len(node.ops) == 1
                and isinstance(node.comparators[0], ast.Constant)):
            op = node.ops[0]
            if isinstance(op, ast.GtE):
                found["ge"].append(node.comparators[0].value)
            elif isinstance(op, ast.LtE):
                found["le"].append(node.comparators[0].value)
    assert len(found["ge"]) == 1 and len(found["le"]) == 1, (
        f"expected one `iv_hv >=` and one `iv_hv <=` in infer_market_view, got "
        f"{found} - if the fallback changed shape, this mirror must follow it")
    return found["ge"][0], found["le"][0]


def test_the_dossiers_iv_hv_bands_are_the_scorers():
    high, low = _iv_hv_fallback_bounds()
    assert _const(DOSSIER_FACTS, "IV_HV_HIGH") == high, (
        f"{DOSSIER_FACTS}:IV_HV_HIGH has drifted from {SCORER}'s `iv_hv >= {high}`")
    assert _const(DOSSIER_FACTS, "IV_HV_LOW") == low, (
        f"{DOSSIER_FACTS}:IV_HV_LOW has drifted from {SCORER}'s `iv_hv <= {low}`")


# --- the zero-grid wall rule --------------------------------------------------
# After the close Schwab zeroes index open interest, the GEX grid is all zeros,
# and the wall picker's max/min over an all-zero side returns the FIRST strike -
# a tie-break wearing the authority of a level. The Desk and the matrix refuse
# those walls in Tier 1 (structure.walls_trustworthy, net GEX exactly zero); the
# dossier refuses them at the SOURCE (dossier.zero_grid_walls_ok), because a
# fetched symbol's walls must not depend on the page remembering the rule.
# Services cannot import webgui, so the condition is written twice.

WALL_RULE_PAGE = "webgui/pages/structure.py"
WALL_RULE_SERVICE = "services/options_svc/dossier.py"


def _zero_grid_condition(rel_path, fn_name):
    """The expression of fn_name's LAST return, as an AST dump."""
    path = ROOT / rel_path
    assert path.exists(), f"mirror source moved: {rel_path}"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    fn = next((n for n in tree.body if isinstance(n, ast.FunctionDef)
               and n.name == fn_name), None)
    assert fn is not None, f"{rel_path} no longer defines {fn_name}"
    # The function's own final statement - NOT ast.walk, whose breadth-first
    # order would hand back a nested early return (walls_trustworthy's stale
    # branch) as the "last" one.
    returns = [n for n in fn.body if isinstance(n, ast.Return)]
    assert returns, f"{rel_path}:{fn_name} has no top-level return"
    return ast.dump(returns[-1].value)


def test_the_dossier_refuses_zero_grid_walls_by_the_desks_rule():
    page = _zero_grid_condition(WALL_RULE_PAGE, "walls_trustworthy")
    service = _zero_grid_condition(WALL_RULE_SERVICE, "zero_grid_walls_ok")
    # Non-vacuity: the rule really is the net-GEX-exactly-zero test.
    assert "net_gex" in page and "0.0" in page
    assert service == page, (
        f"{WALL_RULE_SERVICE}:zero_grid_walls_ok has drifted from "
        f"{WALL_RULE_PAGE}:walls_trustworthy's zero-grid condition. A fetched "
        "dossier and the Desk would then disagree about which walls are real.")


# --- the dossier dedup window ------------------------------------------------
# options_svc skips a dossier fetch written under DOSSIER_DEDUP_SEC ago, and the
# Symbol page's Refresh must know that window: pressed inside it, an enqueue is
# a silent no-op server-side, and the page would sit on FETCHING, then report a
# look-up "still queued" that is not queued at all. Tier 1 cannot import the
# service, so the page restates the number.

DEDUP_SOURCE = "services/options_svc/handlers.py"
DEDUP_MIRROR = "webgui/pages/symbol.py"


def test_the_dossier_dedup_window_agrees_across_tiers():
    service = _const(DEDUP_SOURCE, "DOSSIER_DEDUP_SEC")
    page = _const(DEDUP_MIRROR, "DOSSIER_DEDUP_SEC")
    assert isinstance(service, (int, float)) and service > 0, (
        "the service window is not a positive number - the pin would be vacuous")
    assert page == service, (
        f"{DEDUP_MIRROR}:DOSSIER_DEDUP_SEC ({page}) has drifted from "
        f"{DEDUP_SOURCE}:DOSSIER_DEDUP_SEC ({service}). A Refresh inside the "
        "service's window would then enqueue a fetch the service silently drops.")

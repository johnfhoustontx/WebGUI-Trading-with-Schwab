"""The published screen table. Pure data, so the route guard, the capture
script and the static grid all read ONE source."""
import ast
import importlib
import inspect
import pathlib

import pytest

FORBIDDEN = {"/terminate", "/settings", "/status", "/driver", "/manuals",
             "/options/paper", "/options/captured", "/options/portfolio",
             "/options/shares", "/eod", "/trade"}

_LIVE_SCREENS = pathlib.Path(__file__).resolve().parents[1] / "live_screens.py"


def test_there_are_exactly_fourteen_screens():
    import live_screens
    assert len(live_screens.SCREENS) == 14


def test_no_screen_publishes_a_control_surface():
    """THE GUARD THAT MATTERS. Every route here is served unauthenticated on a
    public origin, so a route added carelessly is a control surface on the
    internet -- /terminate stops the whole stack."""
    import live_screens
    routes = {s.route for s in live_screens.SCREENS}
    assert routes & FORBIDDEN == set(), f"published a control surface: {routes & FORBIDDEN}"


def test_routes_and_slugs_are_unique():
    """A duplicate slug would make two screens overwrite one capture file."""
    import live_screens
    routes = [s.route for s in live_screens.SCREENS]
    slugs = [s.slug for s in live_screens.SCREENS]
    assert len(set(routes)) == len(routes)
    assert len(set(slugs)) == len(slugs)


def test_every_route_starts_with_a_slash_and_every_slug_is_url_safe():
    import re
    import live_screens
    for s in live_screens.SCREENS:
        assert s.route.startswith("/")
        assert re.fullmatch(r"[a-z0-9-]+", s.slug), f"{s.slug} is not URL-safe"


def test_the_pins_name_real_settings_keys():
    """A typo'd pin key would silently do nothing and the screen would publish
    the wrong default -- and a typo in an ORIGIN pin is the same failure with a
    worse blast radius, since those are the ones that exist to switch something
    dangerous off."""
    import app_settings
    import live_screens
    for s in live_screens.SCREENS:
        for key in s.settings:
            assert key in app_settings.DEFAULTS, f"{s.slug} pins unknown key {key}"
    for key in live_screens.PUBLIC_PINS:
        assert key in app_settings.DEFAULTS, f"PUBLIC_PINS names unknown key {key}"


def test_the_net_prem_pin_matches_the_requested_screen():
    """SPY, QQQ and BIG10 in Dollars. BIG10 is a SYMBOL inside the `indices`
    group in config/symbols.toml, not a group of its own."""
    import live_screens
    np = next(s for s in live_screens.SCREENS if s.slug == "net-premium")
    assert np.settings["gamma_netprem_group"] == "indices"
    assert np.settings["gamma_netprem_symbols"] == ["SPY", "QQQ", "BIG10"]
    assert np.settings["gamma_netprem_mode"] == "dollars"


def test_no_two_screens_pin_the_same_key_to_different_values():
    """SETTINGS_PINS is process-wide -- app_settings is a module singleton, not
    per-request state. Two screens disagreeing about a key would silently
    last-wins, and one of them would publish the wrong view."""
    import collections
    import live_screens
    seen = collections.defaultdict(set)
    for s in live_screens.SCREENS:
        for k, v in s.settings.items():
            seen[k].add(repr(v))
    clashes = {k: v for k, v in seen.items() if len(v) > 1}
    assert clashes == {}, f"screens disagree on {clashes}"


def test_settings_pins_is_the_union_of_every_screen_s_pin_and_the_origin_s():
    """Non-vacuity for the clash test above: the union is what the entrypoint
    freezes, so a pin that never reaches it is a screen publishing a default.

    ``PUBLIC_PINS`` is applied LAST, so an origin rule wins over a screen that
    named the same key -- and the collision test below refuses that case
    outright, so the ordering is a backstop rather than a policy."""
    import live_screens
    assert live_screens.SETTINGS_PINS == {
        **{k: v for s in live_screens.SCREENS for k, v in s.settings.items()},
        **live_screens.PUBLIC_PINS}
    assert live_screens.SETTINGS_PINS, "no pins at all - the freeze would be vacuous"


def test_no_screen_pin_collides_with_a_public_pin():
    """The screen-vs-screen clash test one level up.

    ``SETTINGS_PINS`` merges two dicts into one process-wide store, so a screen
    naming a ``PUBLIC_PINS`` key would either be silently overridden or -- with
    the merge written the other way round -- silently switch an origin rule back
    on. Neither is visible in a rendered page. A screen that genuinely needs one
    of these keys is a decision about the ORIGIN, so it belongs in
    ``PUBLIC_PINS``."""
    import live_screens
    offenders = {s.slug: sorted(set(s.settings) & set(live_screens.PUBLIC_PINS))
                 for s in live_screens.SCREENS
                 if set(s.settings) & set(live_screens.PUBLIC_PINS)}
    assert offenders == {}, (
        f"these screens pin an origin-level key: {offenders}. Move the decision "
        "to live_screens.PUBLIC_PINS.")


# --- what a public origin may leave on its defaults -------------------------

# Every ``app_settings.DEFAULTS`` key, judged against ONE question: served
# unauthenticated to anyone, does this default SPEND MONEY, MAKE AN OUTBOUND
# NETWORK CALL, or WRITE something the owner owns? Answering "no" for a key is
# the review; the completeness test below is what forces the next key added to
# ``DEFAULTS`` to be reviewed at the only moment anyone would look at it.
PUBLIC_UNSAFE_DEFAULTS = {
    # ``webgui/voice.py`` synthesizes through ``edge_tts`` -- a network call to
    # a Microsoft endpoint -- and writes mp3s into ``webgui/data/voice/``. The
    # Desk takes that path on every new flow alert, and prewarms up to 32 clips
    # on the first build with no market-hours gate.
    "voice_enabled": False,
}

# Reviewed and safe as they stand. The reasoning, grouped:
#   * read only by pages this origin does not publish (``pages/settings.py``,
#     ``pages/ticker.py``) -- and the marquee in particular is mounted by
#     ``main._layout``, which this process does not run, so ``ticker_enabled``
#     never reaches the ~20-minute paid Claude verdict here;
#   * or read by a published page purely to CHOOSE WHAT TO DRAW
#     (``macro_skin``, the three ``gamma_*`` display knobs,
#     ``alert_market_hours_only`` as a gate, the two remaining ``voice_*`` keys
#     which are inert once ``voice_enabled`` is off);
#   * or nav chrome this process has none of (``nav_pinned``).
# Cross-process WRITES are refused a second way regardless: the bus is
# read-only and ``live_main`` calls none of main's ``sync_*`` helpers.
PUBLIC_SAFE_DEFAULTS = {
    "alert_enabled", "alert_sound", "alert_volume", "alert_market_hours_only",
    "alert_min_score", "desktop_notifications", "flow_alerts_enabled",
    "voice_name", "voice_volume", "captured_autoclose_enabled",
    "manual_paper_lifecycle_enabled", "ticker_enabled", "ticker_speed",
    "nav_pinned", "gamma_level_tracks", "gamma_spot_style",
    "gamma_spot_interval", "gamma_netprem_group", "gamma_netprem_mode",
    "gamma_netprem_symbols", "macro_skin",
}


def test_every_app_settings_default_is_reviewed_for_the_public_origin():
    """THE ONE THAT CATCHES THE NEXT ONE.

    ``voice_enabled`` was missed because nothing asked the question: it defaults
    True, and the design doc and plan for this origin never mention voice at
    all. A specific "voice is pinned off" assertion would not have caught it
    before it was written, and will not catch the next default like it.

    So this asserts COMPLETENESS instead -- every key in ``DEFAULTS`` sits in
    exactly one of the two lists above. A new setting fails this test the day it
    is added, and the failure names the question to answer."""
    import app_settings
    reviewed = set(PUBLIC_UNSAFE_DEFAULTS) | PUBLIC_SAFE_DEFAULTS
    unreviewed = set(app_settings.DEFAULTS) - reviewed
    stale = reviewed - set(app_settings.DEFAULTS)
    assert not unreviewed, (
        f"new app_settings default(s) {sorted(unreviewed)}: served "
        "unauthenticated to anyone, does this default spend money, make an "
        "outbound network call, or write something the owner owns? Add it to "
        "PUBLIC_UNSAFE_DEFAULTS (and pin it in live_screens.PUBLIC_PINS) or to "
        "PUBLIC_SAFE_DEFAULTS with the reason.")
    assert not stale, f"these keys no longer exist in DEFAULTS: {sorted(stale)}"
    assert not (set(PUBLIC_UNSAFE_DEFAULTS) & PUBLIC_SAFE_DEFAULTS)


def test_every_public_unsafe_default_is_pinned_to_its_safe_value():
    import live_screens
    for key, safe in PUBLIC_UNSAFE_DEFAULTS.items():
        assert live_screens.SETTINGS_PINS.get(key) == safe, (
            f"{key} is unsafe on a public origin and is not pinned to {safe!r}")


def test_each_unsafe_default_really_is_the_dangerous_one():
    """Non-vacuity: a pin that merely restates the default proves nothing, and
    would go on passing after someone flipped ``DEFAULTS`` the other way."""
    import app_settings
    for key, safe in PUBLIC_UNSAFE_DEFAULTS.items():
        assert app_settings.DEFAULTS[key] != safe, (
            f"{key} already defaults to {safe!r} -- either the classification is "
            "stale or the pin is doing nothing")


def test_the_desk_voice_is_off_on_the_public_origin():
    """The instance, named, because the completeness test above reads as
    bookkeeping and this is the thing that was actually wrong: a public visitor
    opening /desk drove ``edge_tts`` synthesis calls whose audio ``/voice`` is
    not even mounted to serve."""
    import live_screens
    assert live_screens.SETTINGS_PINS["voice_enabled"] is False


def test_every_module_resolves_and_accepts_its_kwargs():
    """A typo'd module path is a 500 on a PUBLIC page, and nothing else in this
    plan catches it -- the route registration only imports lazily at request
    time. Signature-checked rather than called: ``render()`` mounts widgets and
    needs a NiceGUI client."""
    import live_screens
    for s in live_screens.SCREENS:
        mod = importlib.import_module(f"pages.{s.module}")
        render = getattr(mod, "render", None)
        assert callable(render), f"pages.{s.module} has no render()"
        try:
            inspect.signature(render).bind(**s.kwargs)
        except TypeError as exc:            # pragma: no cover - the failure text
            pytest.fail(f"{s.slug}: pages.{s.module}.render{inspect.signature(render)} "
                        f"cannot take {s.kwargs}: {exc}")


def test_live_screens_imports_nothing_from_nicegui():
    """PURE DATA is the whole point: the capture script and the static-site
    build read this table, and neither should drag in the UI stack. Read as
    SOURCE, in the shape of ``test_shell_seam.test_no_page_imports_main``, so an
    import inside a function is caught too."""
    banned = {"nicegui", "pages", "main", "shell"}
    tree = ast.parse(_LIVE_SCREENS.read_text(encoding="utf-8"))
    bad = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            bad += [a.name for a in node.names
                    if a.name.split(".")[0] in banned]
        if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] in banned:
            bad.append(node.module)
    assert bad == [], f"live_screens.py must stay pure data; it imports {bad}"


def test_no_gamma_screen_reads_the_private_snapshot_slot():
    """A gamma screen pinning a VIEW but no SYMBOL reads ``options:gamma``.

    That key is the PRIVATE page's shared slot — whatever the owner last had
    open — so such a screen would publish the owner's current symbol on an
    unauthenticated origin, under a caption naming a view. The Net Prem screen
    is the one that may pin no symbol, and only because it draws nothing from a
    snapshot at all (``gamma.reads_snapshot``); every other view does.

    The cross-tier mirror cannot see this: it pairs SYMBOLS against what
    options_svc publishes, and a screen with no symbol is skipped there."""
    import live_screens
    from pages.options import gamma

    for s in live_screens.SCREENS:
        if s.module != "options.gamma" or s.kwargs.get("symbol"):
            continue
        assert not gamma.reads_snapshot(s.kwargs.get("view")), (
            f"{s.slug} pins no symbol, so it would read "
            f"{gamma.snapshot_view(None)!r} — the private page's slot. Pin a "
            "symbol options_svc publishes (see PUBLISHED_GAMMA_HISTORY_VIEWS).")

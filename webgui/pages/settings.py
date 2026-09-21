"""Settings page — GUI preferences (audio alerts, notifications, appearance),
plus API-usage stats and database maintenance, and the Configuration tab
(``pages/config_editor.py``) that edits every config/*.toml setting.

Thin render(): each control writes through to app_settings; Appearance is its
own tab (``pages/appearance.py``). The API-usage card reads the proxy's
``/stats/api_calls`` off-thread;
the Maintenance card runs ``tools/vacuum_gex.py`` as a subprocess off-thread
(the tool itself refuses to run while the collector is active). Extensible —
add new cards/sections here as more settings arrive.

The three tabs are peers on one page, so all three use ``kit.page()`` (full
width) and title themselves after their TAB: "Settings" is the breadcrumb
already, and a second copy of it under the breadcrumb would read as a heading
for the whole page rather than for this tab.
"""
import app_settings
import bus_client
import proxy as _proxy
import voice
from nicegui import run, ui

from pages import ui_kit as kit
from pages.options import theme
from pages.ui_guard import guard, guard_async

# The Desk's per-section voice switches: (app_settings key, label). The labels
# name the Desk panel and what makes it speak, in the Desk's own order.
VOICE_SECTION_SWITCHES = (
    ("voice_board", "Opportunity Board — a symbol joins the board"),
    ("voice_flow", "Flow Alerts — a new alert"),
    ("voice_positions", "Positions — a newly-opened position"),
)


def apply_ticker_enabled(value) -> None:
    """Persist the ticker toggle. It only shows or hides the marquee.

    Until 2026-09-10 it also stopped market_svc's Claude call. That call was
    retired on 2026-09-16: the ticker and the Desk now quote the latest market
    report, so there is nothing for the toggle to stop."""
    app_settings.set("ticker_enabled", bool(value))


def apply_captured_autoclose(value) -> None:
    """Persist the captured auto-close toggle AND tell options_svc to gate the cycle.

    The scheduled captured-manage cycle (break-even trailing + auto-close) runs in
    options_svc, so the toggle has to reach the service — mirroring the ticker
    write-through. Best-effort: the setting persists even with the bus down (the
    service defaults to ON on a missing key and re-syncs at webgui startup)."""
    enabled = bool(value)
    app_settings.set("captured_autoclose_enabled", enabled)
    try:
        bus_client.request("options", {"type": "set_autoclose",
                                       "args": {"enabled": enabled}})
    except Exception:  # noqa: BLE001 — a bus outage must not break the toggle.
        pass


def apply_manual_paper_lifecycle(value) -> None:
    """Persist the MANUAL paper account's break-even-lifecycle opt-in AND tell
    options_svc to gate it — an inert, opt-in placeholder (flag default OFF)
    mirroring ``apply_captured_autoclose``, but for the manual paper book instead
    of captured signals; the DRIVER's isolated account never reads this flag.
    Best-effort: the setting persists even with the bus down (the service
    defaults OFF on a missing key and re-syncs at webgui startup)."""
    enabled = bool(value)
    app_settings.set("manual_paper_lifecycle_enabled", enabled)
    try:
        bus_client.request("options", {"type": "set_manual_paper_lifecycle",
                                       "args": {"enabled": enabled}})
    except Exception:  # noqa: BLE001 — a bus outage must not break the toggle.
        pass


def api_stats_rows(stats):
    """(label, value-text) rows for the API-usage card — pure/testable.

    ``stats`` is the proxy's ``/stats/api_calls`` dict or None (proxy down /
    old proxy build). Counts are formatted with thousands separators."""
    if not stats:
        return [("Today", "—"), ("Last 7 days", "—"), ("Last 30 days", "—")]
    def _fmt(k):
        try:
            return f"{int(stats.get(k, 0)):,}"
        except (TypeError, ValueError):
            return "—"
    return [("Today", _fmt("today")), ("Last 7 days", _fmt("last_7_days")),
            ("Last 30 days", _fmt("last_30_days"))]


# ── Maintenance: VACUUM the intraday GEX history database ───────────────────
# The dialog's sentences are module constants so a test can pin what the
# question says without copying prose, and so the two halves cannot drift.
VACUUM_CONFIRM = "Run VACUUM"
VACUUM_TITLE = "Vacuum gex_history.db now?"
VACUUM_BODY = ("The database is locked for minutes while it runs, so this is "
               "best done off-hours.")
VACUUM_PURGE_BODY = ("Old sessions are deleted first, keeping only the last "
                     "5. ") + VACUUM_BODY
# A VACUUM on a large database runs for minutes; this is the tool's own ceiling
# and also the button's spinner backstop, so neither can outlive the other.
VACUUM_TIMEOUT_SEC = 1800


def vacuum_body(purge):
    """The sentence the Vacuum confirm shows. PURE.

    The switch above the button arms ``--purge``, which DELETES every GEX
    session but the last five — so a question naming only the lock lets a
    reader confirm a deletion it never mentioned. The armed sentence keeps the
    plain one whole rather than replacing it: the lock is still true."""
    return VACUUM_PURGE_BODY if purge else VACUUM_BODY


def vacuum_command(purge):
    """``(argv, cwd)`` for ``tools/vacuum_gex.py`` — the one place the
    ``--purge`` flag is decided, which is what lets the confirm name it."""
    import sys

    from repo_paths import REPO_ROOT
    cmd = [sys.executable, str(REPO_ROOT / "tools" / "vacuum_gex.py")]
    if purge:
        cmd.append("--purge")
    return cmd, str(REPO_ROOT)


def run_vacuum(purge):
    """Run the vacuum tool and return everything it printed, stdout then
    stderr. BLOCKS for minutes — call it through ``run.io_bound``."""
    import subprocess
    cmd, cwd = vacuum_command(purge)
    out = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                         timeout=VACUUM_TIMEOUT_SEC)
    return (out.stdout or "") + (out.stderr or "")


SUBTABS = ("General", "Appearance", "Configuration")


def render():
    """Three sub-tabs under the header, the Portfolio page's pattern: General
    (the app preferences below), Appearance (every colour and font,
    ``pages/appearance.py``) and Configuration (every config/*.toml setting)."""
    import page_help as _page_help
    import shell as _shell
    from pages import appearance, config_editor

    def _build_tabs():
        # Nav chrome, not a page action: the tab strip is the shell's, and it
        # keeps the app-wide .compact-tabs look every other group's strip has.
        with ui.tabs().classes("compact-tabs").props(
                "dense no-caps inline-label align=left") as t:
            for key in SUBTABS:
                with ui.tab(key):
                    ui.tooltip(_page_help.subtab_help("/settings", key)
                               ).props("delay=350 max-width=340px")
        return t

    slot = _shell.subtab_slot()
    if slot is not None:
        with slot:
            tabs = _build_tabs()
    else:
        tabs = _build_tabs()
    _shell.bind_breadcrumb_leaf(tabs, initial="General")
    with ui.tab_panels(tabs, value="General").classes("w-full flush-panels"):
        with ui.tab_panel("General").classes("gap-4"):
            _render_general()
        with ui.tab_panel("Appearance"):
            appearance.render()
        with ui.tab_panel("Configuration"):
            config_editor.render()


def _card():
    """One preferences card. ``max-w-2xl`` is the cap these have always had —
    the PAGE is full width, like the other two Settings tabs, and a column of
    switches has no business running the width of a monitor."""
    return ui.column().classes(f"{theme.CARD} w-full max-w-2xl gap-2")


def _render_general():
    """The General tab: the app's own preferences, each written through as it
    changes, plus the API-usage counters and the one maintenance action."""
    s = app_settings.load()

    with kit.page():
        kit.header("General")        # the TAB's name; "Settings" is the breadcrumb

        with _card():
            kit.section_title("Scanner alerts")
            ui.label("Play a sound (and optional desktop notification) when new "
                     "scanner signals appear, on any page.").classes(
                     f"text-sm {theme.MUTED}")

            enable = ui.switch("Enable audio alert", value=s["alert_enabled"])
            enable.on_value_change(lambda e: app_settings.set("alert_enabled", e.value))

            with ui.row().classes("items-end gap-4 flex-wrap"):
                sound = kit.select_field("Sound", ["chime", "bell", "ping"],
                                         value=s["alert_sound"])
                sound.on_value_change(lambda e: app_settings.set("alert_sound", e.value))
                test = kit.button("Test sound", kind="secondary", icon="volume_up")

            with kit.field("Volume"):
                vol = ui.slider(min=0, max=1, step=0.05,
                                value=s["alert_volume"]).classes("w-64")
            vol.on_value_change(lambda e: app_settings.set("alert_volume", e.value))

            mh = ui.switch("Only alert during market hours (08:00–15:00 CT, weekdays)",
                           value=s["alert_market_hours_only"])
            mh.on_value_change(
                lambda e: app_settings.set("alert_market_hours_only", e.value))

            def _save_min_score(value):
                """Write an out-of-range score through to nobody.

                ``kit.number_field`` keeps ``min``/``max`` OFF the widget, so
                Quasar's own blur ``sanitize`` can no longer rewrite what was
                typed — it says what is wrong instead. This is the other half:
                a refused value must not be persisted while the field is red."""
                if kit.field_valid(mscore):
                    app_settings.set("alert_min_score", value or 0)

            mscore = kit.number_field(
                "Minimum score to alert", value=s["alert_min_score"],
                min=0, max=100, step=5, integer=True,
                on_change=lambda e: _save_min_score(e.value))

            ui.label("Tip: your browser blocks sound until you interact with the "
                     "page — clicking Test sound (or any nav link) unlocks "
                     "it.").classes(f"text-xs {theme.MUTED}")

        with _card():
            kit.section_title("Spoken alerts (Desk)")
            ui.label("Announce the ticker and the cause out loud when something "
                     "new appears on the Desk. Uses the existing market-hours gate "
                     "above.").classes(f"text-sm {theme.MUTED}")

            v_enable = ui.switch("Enable spoken alerts", value=s["voice_enabled"])
            v_enable.on_value_change(lambda e: app_settings.set("voice_enabled", e.value))

            # One switch per Desk section, UNDER the master switch: each is greyed
            # out while spoken alerts are off, because it can only narrow what the
            # master switch already allows. Order follows the Desk's own panels.
            ui.label("Speak for").classes(f"text-sm {theme.MUTED}")
            with ui.column().classes("gap-0 pl-4"):
                for key, label in VOICE_SECTION_SWITCHES:
                    sw = ui.switch(label, value=s[key])
                    sw.on_value_change(
                        lambda e, k=key: app_settings.set(k, e.value))
                    sw.bind_enabled_from(v_enable, "value")

            with ui.row().classes("items-end gap-4 flex-wrap"):
                # Wider than the sound picker above: the voice names are long.
                v_name = kit.select_field("Voice", list(voice.VOICES),
                                          value=s["voice_name"], width="w-64")
                v_name.on_value_change(lambda e: app_settings.set("voice_name", e.value))
                v_test = kit.button("Test voice", kind="secondary",
                                    icon="record_voice_over")

            with kit.field("Volume"):
                v_vol = ui.slider(min=0, max=1, step=0.05,
                                  value=s["voice_volume"]).classes("w-64")
            v_vol.on_value_change(lambda e: app_settings.set("voice_volume", e.value))

            ui.label("The first time a phrase is spoken it takes a second or two to "
                     "generate; after that it plays from a local cache. Test voice "
                     "also unlocks browser audio, which is blocked until you "
                     "interact with the page.").classes(f"text-xs {theme.MUTED}")

        with _card():
            kit.section_title("Desktop notifications")
            notif = ui.switch("Show a desktop notification too",
                              value=s["desktop_notifications"])
            notif.on_value_change(
                lambda e: app_settings.set("desktop_notifications", e.value))
            kit.button("Grant notification permission", kind="secondary",
                       icon="notifications",
                       on_click=lambda: ui.run_javascript(
                           "Notification && Notification.requestPermission()"))

            flowsw = ui.switch("Flow alerts (put/call premium crossover + unusual "
                               "activity)", value=s.get("flow_alerts_enabled", True))
            flowsw.on_value_change(
                lambda e: app_settings.set("flow_alerts_enabled", e.value))

        with _card():
            kit.section_title("Captured trade auto-management")
            ui.label("Auto-manage the captured signals (paper-only): raise the stop to "
                     "break-even after +50%, defer delta-drift cuts on recoverable "
                     "trades, and auto-close on the exit rules / expiry. Off leaves "
                     "them advisory (you close manually).").classes(
                     f"text-sm {theme.MUTED}")
            casw = ui.switch("Auto-manage captured signals",
                             value=s.get("captured_autoclose_enabled", True))
            casw.on_value_change(lambda e: apply_captured_autoclose(e.value))

            ui.label("Manual paper: break-even lifecycle (experimental)").classes(
                     f"text-sm font-semibold {theme.LABEL} pt-2")
            ui.label("Opt the MANUAL paper account into the same lifecycle: arm "
                     "break-even at +50% credit instead of taking profit immediately, "
                     "then ride toward full credit protected by a break-even stop. "
                     "Off (default) keeps today's plain take-profit at +50%. The "
                     "Driver's isolated account is never affected by this toggle."
                     ).classes(f"text-sm {theme.MUTED}")
            mplsw = ui.switch("Manual paper: break-even lifecycle (experimental)",
                              value=s.get("manual_paper_lifecycle_enabled", False))
            mplsw.on_value_change(lambda e: apply_manual_paper_lifecycle(e.value))

        with _card():
            kit.section_title("Market summary ticker")
            ui.label("Scrolling market-summary marquee at the bottom of every page "
                     "(live data items, led by the latest market report's headline). "
                     "Turning it off hides the marquee only.").classes(
                     f"text-sm {theme.MUTED}")

            tick = ui.switch("Show the ticker", value=s["ticker_enabled"])
            tick.on_value_change(lambda e: apply_ticker_enabled(e.value))

            # Labels map to a marquee duration in seconds (higher = slower). One
            # label above the field, not a floating "Speed" beside a "Scroll
            # speed" caption - this field carried both until the kit migration.
            _SPEEDS = {"Slow": 90, "Medium": 60, "Fast": 35}
            _cur = {90: "Slow", 60: "Medium", 35: "Fast"}.get(s["ticker_speed"], "Medium")
            speed = kit.select_field("Scroll speed", list(_SPEEDS), value=_cur)
            speed.on_value_change(
                lambda e: app_settings.set("ticker_speed", _SPEEDS.get(e.value, 60)))

            ui.label("Changes apply on the next page load / navigation.").classes(
                     f"text-xs {theme.MUTED}")

        # ── API usage — Schwab (proxy-counted) + Claude/Anthropic (shared store) ──
        with _card():
            kit.section_title("API usage")
            ui.label("Outbound Schwab API calls counted at the proxy per actual HTTP "
                     "request (market data + trading, including retries), and Claude "
                     "(Anthropic) API calls counted at each call site (driver decider, "
                     "Gamma Analyze). The scheduled gamma briefings "
                     "run on the Claude subscription and count here only when one "
                     "falls back to the API. Counts accumulate going "
                     "forward.").classes(f"text-sm {theme.MUTED}")

            ui.label("Schwab").classes(f"text-xs font-semibold {theme.LABEL} mt-1")
            stat_lbls = {}
            with ui.row().classes("gap-6"):
                for label, val in api_stats_rows(None):
                    with ui.column().classes("gap-0"):
                        ui.label(label).classes(theme.EYEBROW)
                        stat_lbls[label] = ui.label(val).classes(
                            f"text-[20px] font-semibold {theme.LABEL}")
            api_since = ui.label("").classes(f"text-xs {theme.MUTED}")

            ui.label("Claude (Anthropic)").classes(
                f"text-xs font-semibold {theme.LABEL} mt-2")
            claude_lbls = {}
            with ui.row().classes("gap-6"):
                for label, val in api_stats_rows(None):
                    with ui.column().classes("gap-0"):
                        ui.label(label).classes(theme.EYEBROW)
                        claude_lbls[label] = ui.label(val).classes(
                            f"text-[20px] font-semibold {theme.LABEL}")
            claude_since = ui.label("").classes(f"text-xs {theme.MUTED}")

            def _read_claude_stats():
                try:
                    from shared import anthropic_counter
                    return anthropic_counter.stats()
                except Exception:  # noqa: BLE001 — the card degrades to placeholders
                    return None

            @guard_async
            async def _load_api_stats():
                stats = await run.io_bound(_proxy.api_call_stats)
                for label, val in api_stats_rows(stats):
                    stat_lbls[label].text = val
                api_since.text = (f"Counting since {stats['since']}."
                                  if stats and stats.get("since")
                                  else "No counts yet — restart the proxy if it "
                                       "predates the counter.")
                cstats = await run.io_bound(_read_claude_stats)
                for label, val in api_stats_rows(cstats):
                    claude_lbls[label].text = val
                claude_since.text = (f"Counting since {cstats['since']}."
                                     if cstats and cstats.get("since")
                                     else "No counts yet — restart the services "
                                          "(driver / options / market) if they "
                                          "predate the counter.")

            # This card's OWN refresh, not the page's: it re-reads two counters
            # and nothing else on the screen moves, so it stays beside them.
            kit.button("Refresh", kind="secondary", icon="refresh",
                       on_click=_load_api_stats)
            ui.timer(0.1, _load_api_stats, once=True)   # initial load, off-thread

        # ── Maintenance — VACUUM the intraday GEX history DB ─────────────────
        with _card():
            kit.section_title("Maintenance")
            ui.label("VACUUM the intraday GEX history database (gex_history.db) to "
                     "shrink it on disk — the daily purge frees pages but never "
                     "shrinks the file. Locks the database for minutes: run it "
                     "off-hours. The tool refuses to run while the collector is "
                     "active (market hours / fresh lock).").classes(
                     f"text-sm {theme.MUTED}")
            vac_purge = ui.switch("Also purge old sessions first (keep last 5)",
                                  value=False)
            # Mono is right here and nowhere else on this page: it holds the
            # tool's own stdout, which is a log.
            vac_result = ui.label("").classes(
                f"text-xs whitespace-pre-wrap font-mono {theme.MUTED}")

            @guard_async
            async def _vacuum():
                """The VACUUM itself, run once the confirm has closed."""
                try:
                    vac_result.text = (
                        await run.io_bound(run_vacuum, bool(vac_purge.value))
                    ).strip() or "Done (no output)."
                except Exception as exc:  # noqa: BLE001 — surface, never crash the page
                    vac_result.text = f"VACUUM failed: {exc}"
                finally:
                    kit.set_busy(vac_btn, False)

            @guard
            def _start_vacuum():
                """Arm the page's own button and hand the work back to the loop.

                ``kit.confirm`` holds its dialog open until ``on_confirm``
                returns, and this runs for MINUTES — so the work cannot be the
                confirm's action without turning a modal into the progress
                indicator. The button carries the wait instead, beside the
                output, which is where the reader is looking."""
                kit.set_busy(vac_btn, timeout=VACUUM_TIMEOUT_SEC)
                vac_result.text = ""
                with vac_btn.parent_slot:
                    ui.timer(0.01, _vacuum, once=True)

            @guard
            def _ask_vacuum():
                """Name what is actually about to run: with the switch above
                armed this deletes every GEX session but the last five, and the
                old dialog only ever mentioned the lock."""
                vac_dlg.body.text = vacuum_body(bool(vac_purge.value))
                vac_dlg.open()

            vac_btn = kit.button("Vacuum GEX history DB", kind="danger",
                                 icon="cleaning_services", on_click=_ask_vacuum)

    # Built at render()'s own level, after the handler it calls, so the name is
    # bound when the kit reaches for it (the appearance.py order).
    vac_dlg = kit.confirm(VACUUM_TITLE, VACUUM_BODY, confirm_text=VACUUM_CONFIRM,
                          danger=True, on_confirm=_start_vacuum)

    # Test sound uses the same shared audio element + helper as the live alert.
    def _test():
        from shell import play_alert
        play_alert(app_settings.get("alert_sound"), app_settings.get("alert_volume"))
    test.on_click(_test)

    # Test voice is its sibling, but it cannot reuse play_alert: the clip is
    # synthesized on demand, and voice.ensure BLOCKS (measured ~0.9-2.4 s on a
    # cache miss), so it has to cross run.io_bound before any of this touches the
    # page. That wait is why the button now carries its own spinner.
    @guard_async
    async def _test_voice():
        kit.set_busy(v_test)
        try:
            settings = app_settings.load()
            url = await run.io_bound(voice.ensure,
                                     "S P Y. Crossover alert, calls over.",
                                     settings["voice_name"])
            if url is None:
                kit.toast("warn", "Voice unavailable — check the network and "
                                  "edge-tts.")
                return
            # The shared alert element is fine HERE: nothing else is speaking on the
            # Settings page. The Desk uses its own element so a chime cannot cut an
            # announcement off, which is a Desk-only concern.
            # Interpolated the way play_alert does, and safe for the same reason:
            # both operands are constrained at their SOURCE rather than escaped here
            # — url is voice.clip_url's "/voice/<sha1 hex>.mp3", which cannot contain
            # a quote, and the volume is clamped to a float below (a hand-edited
            # settings.json is the reason that clamp is not just decoration).
            vol = max(0.0, min(1.0, float(settings["voice_volume"] or 0)))
            ui.run_javascript(
                f"(() => {{ const a = document.getElementById('alert-audio'); "
                f"if (!a) return; "
                f"a.src = '{url}'; a.volume = {vol}; "
                f"a.play().catch(() => {{}}); }})()")
        finally:
            kit.set_busy(v_test, False)
    v_test.on_click(_test_voice)

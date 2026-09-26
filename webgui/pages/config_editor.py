"""Settings -> Configuration: every ``config/*.toml`` setting, editable in the app.

What the operator sees is organised by PURPOSE (Trade selection, Paper books, Exits,
Flow alerts, Market hours, Symbols, the Sector map, Commissions), not by
file; ``config_schema`` supplies every label, help sentence, unit and bound, and
this module only draws it.

Where a change goes, and why
    Saved values go to ``config/local/<name>.toml`` (gitignored) through
    ``config_store``; the tracked file is never written, because a dirty prod
    checkout makes ``tools/promote.sh`` refuse. Only values that differ from the
    shipped file are stored, so choosing the shipped value again removes the
    override, and "Reset" is always available.

When it takes effect
    The services read their config at start-up, so a save lists the services it
    touches and offers to restart them (the Status page's ``systemctl --user``
    restart). Scheduled timers are regenerated instead of restarted. Restarting
    the options service while the gamma collector runs costs collection minutes,
    so the dialog says so during market hours.

⚠ ``_PENDING`` and ``_restart_webgui`` are a de-facto public API between the two
Settings tabs: ``pages/appearance.py`` reads and writes the set in three places
and calls the restart. They keep their names, their module and the set type.
"""
from __future__ import annotations

import copy
import dataclasses
import subprocess
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from nicegui import run, ui

import config_schema as cs
import config_store as store
from pages import ui_kit as kit
from pages.options.theme import (BADGE_ACCENT, BADGE_MUTED, BADGE_WARN, CARD,
                                 EYEBROW, LABEL, MUTED, THEME, TXT_NEG,
                                 TXT_POS, TXT_WARN)
from pages.ui_guard import guard, guard_async

# Saved-but-not-yet-restarted units, shared across page loads of this process.
_PENDING: set = set()

_P = THEME["palette"]
_CHIP = "text-[11px] px-2 py-[1px] whitespace-nowrap"
_CHIP_UNSAVED = f"{_CHIP} {BADGE_WARN}"
_CHIP_CHANGED = f"{_CHIP} {BADGE_ACCENT}"
_CHIP_COUNT = f"{_CHIP} {BADGE_MUTED}"
_ROW = (f"w-full items-start gap-3 py-2 border-b "
        f"border-[{_P['card_border']}] last:border-b-0")
# A unit restart is a subprocess with its own 60 s (120 s for the timers)
# ceiling, and the dialog now waits for ALL of them - so the confirm's spinner
# needs a backstop longer than the kit's 30 s default, or it would report
# "finished" while systemctl is still working.
RESTART_TIMEOUT_SEC = 300.0
CT = ZoneInfo("America/Chicago")


# ── pure helpers (unit-tested) ───────────────────────────────────────────────
def expand_fields(cfg, section, shipped_flat, override_flat):
    """``[(path, field, label)]`` for one section, wildcards expanded against the
    keys the file (or its overrides) actually has, in file order."""
    keys = list(dict.fromkeys(list(shipped_flat) + list(override_flat)))
    out = []
    for f in section.fields:
        parts = cs.split_key(f.key)
        if "*" not in parts:
            out.append((tuple(parts), f, f.label))
            continue
        for path in keys:
            if cs._matches(f.key, path):
                # an exact entry elsewhere claims its own key (grace_min)
                sec, exact = cs.locate(cfg, path)
                if exact is not f:
                    continue
                out.append((path, f, f.label or cs.humanize(path[-1])))
    return out


def display_value(value, shipped_value, fld):
    """How a value reads in the "changed from shipped" chip."""
    v = cs.to_display(fld, value)
    if v is None:
        return "not set"
    if fld.kind == "bool":
        return "on" if v else "off"
    if fld.kind == "money":
        return f"${v:,.2f}".rstrip("0").rstrip(".") if isinstance(v, float) else f"${v:,}"
    if fld.kind == "fraction":
        return f"{v:g}%"
    if isinstance(v, list):
        if fld.kind == "ladder":
            return ", ".join(f"{a:g}%→{b:g}%" for a, b in v)
        return ", ".join(str(x) for x in v) or "none"
    return f"{v}{'' if not fld.unit or fld.kind == 'fraction' else ' ' + fld.unit}"


def restart_targets(cfg, paths):
    """Units to restart for a set of changed key paths in one file."""
    out = []
    for p in paths:
        sec, fld = cs.locate(cfg, p)
        for u in cs.restart_for(cfg, sec, fld):
            if u not in out:
                out.append(u)
    return out


def matches(query, *texts):
    q = (query or "").strip().lower()
    return bool(q) and all(any(w in (t or "").lower() for t in texts)
                           for w in q.split())


def market_busy(now=None):
    """True while a service restart would cost live work (collection / RTH)."""
    try:
        from shared import market_calendar as mc
        now = now or datetime.now(CT)
        return bool(mc.is_regular_hours(now) or mc.in_collection_window(now))
    except Exception:  # noqa: BLE001 - a warning, never a blocker
        return False


def change_stamp(at):
    """The change log's clock, rendered for the Recent changes list. PURE.

    ⚠ TWO STORED FORMATS, and the difference is not cosmetic. ``config_store``
    has written an AWARE Central stamp since 2026-09-20, which is converted and
    LABELLED ``CT`` here however it was zoned. Every row older than that carries
    a naive ``datetime.now()``: it was the HOST clock, and this box's host clock
    IS Central - so those rows sort and read correctly beside the new ones - but
    nothing recorded that, so they render exactly as they always did and are
    never given a ``CT`` they cannot back. An unreadable stamp is "", never an
    exception: one malformed line must not take the whole expansion down.
    """
    try:
        when = datetime.fromisoformat(str(at or ""))
    except (TypeError, ValueError):
        return ""
    if when.tzinfo is None:
        return when.strftime("%Y-%m-%d %H:%M")
    return when.astimezone(CT).strftime("%Y-%m-%d %H:%M") + " CT"


def _group_label(f, fld, path, label):
    """Rows under a wildcard are named after what they belong to: a Net Prem
    group ("Mega-caps — symbols"), a news feed's switch ("ZeroHedge — Enabled",
    keyed by the feed's name) or a news feed's field ("MarketWatch — Feed URL",
    an array item, so named by its ``name`` value)."""
    if fld.key.startswith("netprem_groups.*."):
        group = f["labels"].get(path[1], path[1])
        return f"{group} — {'tab name' if path[-1] == 'label' else 'symbols'}"
    if fld.key.startswith("feed_flags.*."):
        return f"{path[1]} — {label}"
    if fld.key.startswith("feeds.*."):
        feed = f["values"].get(("feeds", path[1], "name")) or f"Feed {path[1]}"
        return f"{feed} — {label}"
    return label


def overrides_to_save(cfg, shipped, over, values):
    """The override table a save writes for one file. PURE.

    Read-only keys (``config_schema.is_readonly``) are never WRITTEN: their
    values are left out of the build, whatever the page holds for them. What
    the existing override already says about them - a hand-written
    ``[[feeds]]`` list in config/local, say - is carried through verbatim, so a
    save of an unrelated switch neither drops nor rewrites it."""
    editable = {p: v for p, v in values.items() if not cs.is_readonly(cfg, p)}
    out = store.build_overrides(shipped, editable)
    for top, node in (over or {}).items():
        leaves = store.flatten({top: node})
        if not leaves or not all(cs.is_readonly(cfg, p) for p in leaves):
            continue
        if top in out:
            # a table shared with editable keys: lay the read-only leaves back
            # on. (A rebuilt LIST stays as built - a list cannot be merged.)
            if isinstance(out[top], dict) and isinstance(node, dict):
                out[top] = store.effective(out[top], node)
        else:
            out[top] = copy.deepcopy(node)
    return out


# ── restarting ───────────────────────────────────────────────────────────────
def _restart_units(units):
    """Restart each unit; regenerate timers for TIMERS. Returns [(unit, ok, msg)]."""
    from pages import status
    from repo_paths import REPO_ROOT
    results = []
    order = [u for u in units if u not in (cs.WEBGUI, cs.TIMERS)]
    if cs.TIMERS in units:
        order.append(cs.TIMERS)
    for u in order:
        try:
            if u == cs.TIMERS:
                r = subprocess.run(
                    [sys.executable, "-m", "deploy.systemd.generate_units", "--install"],
                    cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=120)
                ok = r.returncode == 0
                results.append((u, ok, "" if ok else (r.stderr or r.stdout)[-300:]))
            else:
                cmd = status.restart_command({"name": u})
                r = subprocess.run(cmd, cwd=str(REPO_ROOT), capture_output=True,
                                   text=True, timeout=60)
                ok = r.returncode == 0
                results.append((u, ok, "" if ok else (r.stderr or r.stdout)[-300:]))
        except Exception as exc:  # noqa: BLE001 - reported on screen
            results.append((u, False, str(exc)))
    return results


def _restart_webgui():
    from pages import status
    from repo_paths import REPO_ROOT
    subprocess.Popen(status.restart_command({"name": cs.WEBGUI}), cwd=str(REPO_ROOT))


# ── page ─────────────────────────────────────────────────────────────────────
def render():
    state = {
        "cat": cs.EDITABLE[0].name,
        "query": "",
        "files": {},      # name -> {"shipped", "over", "base", "values", "labels"}
        "edits": {},      # name -> {path: parsed value}
        "errors": {},     # (name, path) -> message
    }

    def _load(name):
        shipped, over = store.load(name)
        eff = store.effective(shipped, over)
        state["files"][name] = {
            "shipped": shipped, "over": over,
            "base": store.flatten(shipped),
            "over_flat": store.flatten(over),
            "values": store.flatten(eff),
            "labels": store.table_array_labels(shipped, "netprem_groups"),
        }

    for f in cs.FILES:
        _load(f.name)

    # ── frame ───────────────────────────────────────────────────────────────
    with kit.page() as page_col:
        # No description line: the tab's hover guide (page_help.subtab_help)
        # already says what this screen is for, and the header line is the
        # title plus the page's actions.
        kit.header("Configuration")

        pending_box = ui.row().classes("w-full")
        with kit.control_bar():
            search = kit.text_field(
                "Search", placeholder="delta, take profit, VIX",
                width="w-[520px] max-w-full")
            search.props("clearable").props('prepend-icon="search"')

        with ui.row().classes("w-full items-start gap-4 no-wrap"):
            nav = ui.column().classes("w-60 shrink-0 gap-1")
            body = ui.column().classes("grow min-w-0 gap-4")

        footer = ui.row().classes(
            f"w-full items-center gap-3 sticky bottom-0 z-10 bg-[{_P['page_bg2']}] "
            f"border-t border-[{_P['card_border']}] px-4 py-2 rounded-t-lg")
        # Built ONCE and mutated (_paint_footer). Rebuilding them swallowed the
        # click that caused a field's change: mousedown → change → repaint → the
        # button the reader pressed no longer exists when mouseup lands. This is
        # the bug pages/appearance.py already carries a note about, still live
        # on this page until the kit migration.
        with footer:
            f_icon = ui.icon("check_circle").classes(TXT_POS)
            f_status = ui.label("").classes(f"text-sm grow {MUTED}")
            discard = kit.button("Discard", kind="secondary",
                                 on_click=lambda: _discard())
            save = kit.button("Save changes", kind="primary", icon="save",
                              on_click=lambda: _save())

    # ── pending-restart banner ──────────────────────────────────────────────
    def _paint_pending():
        pending_box.clear()
        if not _PENDING:
            return
        names = ", ".join(cs.RESTART_LABELS.get(u, u) for u in sorted(_PENDING))
        with pending_box:
            with kit.notice(f"Saved changes are waiting for a restart: {names}.",
                            icon="restart_alt"):
                kit.button("Restart now", kind="primary", icon="restart_alt",
                           on_click=lambda: _restart_dialog(sorted(_PENDING)))

    # ── field rows ──────────────────────────────────────────────────────────
    def _value(name, path):
        ed = state["edits"].get(name, {})
        return ed[path] if path in ed else state["files"][name]["values"].get(path)

    def _set(name, path, parsed):
        cur = state["files"][name]["values"].get(path)
        eds = state["edits"].setdefault(name, {})
        if parsed == cur:
            eds.pop(path, None)
        else:
            eds[path] = parsed

    def _readonly_row(cfg, fld, path, label):
        """Shown, not edited: the value as text, no input, no reset."""
        f = state["files"][cfg.name]
        v = f["values"].get(path)
        with ui.row().classes(_ROW):
            with ui.column().classes("w-72 shrink-0 gap-0"):
                ui.label(label).classes(f"text-sm {LABEL}")
                if fld.help:
                    ui.label(fld.help).classes(f"text-xs {MUTED}")
            ui.label(display_value(v, v, fld)).classes(
                f"grow min-w-0 text-sm break-all {MUTED}")
            with ui.row().classes("w-64 shrink-0 items-center justify-end gap-1"):
                if v != f["base"].get(path):
                    ui.label(f"Set in config/local/{cfg.name}").classes(_CHIP_CHANGED)

    def _field_row(cfg, fld, path, label):
        if cs.is_readonly(cfg, path):
            _readonly_row(cfg, fld, path, label)
            return
        name = cfg.name
        f = state["files"][name]
        shipped_v = f["base"].get(path)
        # an optional key the file ships cannot be "unset" by an override
        required = shipped_v is not None
        status_holder = {}

        def _paint_status():
            box = status_holder["box"]
            box.clear()
            v = _value(name, path)
            with box:
                err = state["errors"].get((name, path))
                if err:
                    ui.label(err).classes(f"text-xs {TXT_NEG}")
                    return
                if path in state["edits"].get(name, {}):
                    ui.label("Unsaved").classes(_CHIP_UNSAVED)
                if v != shipped_v:
                    ui.label(f"Shipped: {display_value(shipped_v, shipped_v, fld)}") \
                        .classes(_CHIP_CHANGED)
                    kit.icon_button("restore", tooltip="Back to the shipped value",
                                    on_click=lambda: _reset_field())

        def _changed(raw):
            try:
                eff_fld = (dataclasses.replace(fld, optional=False)
                           if fld.optional and required else fld)
                parsed = cs.parse(eff_fld, raw, shipped=shipped_v)
                state["errors"].pop((name, path), None)
                _set(name, path, parsed)
            except (ValueError, TypeError) as exc:
                state["errors"][(name, path)] = str(exc)
            _paint_status()
            _paint_footer()
            _paint_nav()

        control = {}

        def _reset_field():
            state["errors"].pop((name, path), None)
            _set(name, path, shipped_v)
            _apply_to_control(control, fld, shipped_v)
            _paint_status()
            _paint_footer()
            _paint_nav()

        with ui.row().classes(_ROW):
            with ui.column().classes("w-72 shrink-0 gap-0"):
                ui.label(label).classes(f"text-sm {LABEL}")
                if fld.help:
                    ui.label(fld.help).classes(f"text-xs {MUTED}")
            with ui.column().classes("grow min-w-0 gap-1"):
                _build_control(control, fld, _value(name, path), _changed,
                               optional=fld.optional and not required)
            status_holder["box"] = ui.row().classes("w-64 shrink-0 items-center "
                                                   "justify-end gap-1 flex-wrap")
        _paint_status()

    # ── one category ────────────────────────────────────────────────────────
    def _paint_category(cfg):
        f = state["files"][cfg.name]
        with ui.column().classes("w-full gap-1"):
            with ui.row().classes("items-center gap-2"):
                ui.icon(cfg.icon).classes(f"text-xl text-[{_P['icon']}]")
                ui.label(cfg.title).classes(f"text-subtitle1 font-semibold {LABEL}")
                ui.label(f"config/{cfg.name}").classes(f"text-xs {MUTED}")
            ui.label(cfg.summary).classes(f"text-sm {MUTED}")
            if cfg.caution:
                kit.notice(cfg.caution, icon="warning_amber")
            if cfg.restart:
                ui.label("Takes effect after restarting: " + ", ".join(
                    cs.RESTART_LABELS[u] for u in cfg.restart)) \
                    .classes(f"text-xs {MUTED}")
        if not f["shipped"]:
            ui.label(f"config/{cfg.name} could not be read.").classes(
                f"text-sm {TXT_NEG}")
            return
        if cfg.editor == "readonly":
            _paint_readonly(cfg)
            return
        if cfg.editor == "sectors":
            _paint_sectors(cfg)
            return
        for sec in cfg.sections:
            rows = expand_fields(cfg, sec, f["base"], f["over_flat"])
            if not rows:
                continue
            with ui.column().classes(f"{CARD} w-full gap-0"):
                ui.label(sec.title).classes(f"{EYEBROW}")
                if sec.help:
                    ui.label(sec.help).classes(f"text-xs {MUTED} pb-1")
                for path, fld, label in rows:
                    _field_row(cfg, fld, path, _group_label(f, fld, path, label))
        with ui.row().classes("w-full justify-end"):
            # DESTRUCTIVE: it writes {} and every override in this file goes.
            # It was a quiet grey link beside a primary-blue confirm.
            # ⚠ shrink-0, and the file's name left off: a q-btn's content row
            # WRAPS, so its min-content is one word - "Reset all of Trade
            # selection to shipped values" measured 92px wide and 178px TALL in
            # a 593px window, a column of stacked words. The confirm it opens
            # still names the file, and the category's title is directly above.
            kit.button("Reset to shipped values", kind="danger", icon="restore",
                       on_click=lambda c=cfg: _confirm_reset(c)).classes("shrink-0")

    def _paint_readonly(cfg):
        f = state["files"][cfg.name]
        with ui.column().classes(f"{CARD} w-full gap-0"):
            for path, v in f["base"].items():
                with ui.row().classes(_ROW):
                    ui.label(" › ".join(path)).classes(f"w-72 text-sm {LABEL}")
                    ui.label(str(v)).classes(f"text-sm {MUTED}")

    def _paint_sectors(cfg):
        name = cfg.name
        f = state["files"][name]
        fld = cfg.sections[0].fields[0]
        box = ui.column().classes("w-full gap-2")

        def _all_symbols():
            eds = state["edits"].get(name, {})
            keys = list(f["values"]) + [p for p in eds if p not in f["values"]]
            return [p for p in keys if len(p) == 2 and p[0] == "sectors"]

        with box:
            counts = {}
            for p in _all_symbols():
                counts[_value(name, p)] = counts.get(_value(name, p), 0) + 1
            with ui.row().classes("w-full gap-1 flex-wrap"):
                for s in cs.SECTORS:
                    ui.label(f"{s} · {counts.get(s, 0)}").classes(_CHIP_COUNT)

            def _add():
                """Say what is missing UNDER the field that is missing it: both
                are two inches from this button, and the standard shows
                validation inline rather than in a toast."""
                sym = (new_sym.value or "").strip().upper()
                new_sym.error = None if sym else "Type a symbol"
                new_sec.error = None if new_sec.value else "Pick a sector"
                if not sym or not new_sec.value:
                    return
                _set(name, ("sectors", sym), new_sec.value)
                new_sym.value = ""
                flt.value = sym
                _paint_footer()
                _paint_nav()
                rows_box.refresh()

            with ui.row().classes("w-full items-end gap-3 flex-wrap"):
                flt = kit.text_field("Find", placeholder="NVDA or Energy",
                                     width="w-56")
                flt.props("clearable")
                new_sym = kit.text_field("New symbol", placeholder="NVDA",
                                         width="w-32")
                new_sec = kit.select_field("Sector", list(cs.SECTORS), value=None,
                                           width="w-56")
                kit.button("Add", kind="secondary", icon="add", on_click=_add)

            @ui.refreshable
            def rows_box():
                q = (flt.value or "").strip()
                paths = [p for p in _all_symbols()
                         if not q or matches(q, p[1], _value(name, p))]
                shown = paths[:60]
                with ui.column().classes(f"{CARD} w-full gap-0"):
                    if not paths:
                        ui.label("No symbol matches.").classes(f"text-sm {MUTED}")
                    for p in shown:
                        _field_row(cfg, fld, p, p[1])
                    if len(paths) > len(shown):
                        ui.label(f"Showing 60 of {len(paths)} — narrow the search "
                                 "to see the rest.").classes(f"text-xs {MUTED} pt-2")

            rows_box()
            flt.on_value_change(lambda _e: rows_box.refresh())

    # ── search results ──────────────────────────────────────────────────────
    def _paint_search(q):
        hits = 0
        for cfg in cs.EDITABLE:
            if cfg.editor != "fields":
                continue
            f = state["files"][cfg.name]
            rows = []
            for sec in cfg.sections:
                for path, fld, label in expand_fields(cfg, sec, f["base"],
                                                      f["over_flat"]):
                    label = _group_label(f, fld, path, label)
                    if matches(q, label, fld.help, sec.title, cfg.title,
                               " ".join(path)):
                        rows.append((sec, path, fld, label))
            if not rows:
                continue
            hits += len(rows)
            with ui.column().classes(f"{CARD} w-full gap-0"):
                ui.label(cfg.title).classes(EYEBROW)
                for sec, path, fld, label in rows:
                    _field_row(cfg, fld, path, f"{sec.title} › {label}")
        if not hits:
            kit.empty(f"No setting matches “{q}”.")

    # ── left navigation ─────────────────────────────────────────────────────
    def _paint_nav():
        nav.clear()
        with nav:
            groups = (("Settings", [c for c in cs.FILES if c.editable]),
                      ("System (read-only)", [c for c in cs.FILES if not c.editable]))
            for caption, cfgs in groups:
                ui.label(caption).classes(f"{EYEBROW} pt-2")
                for cfg in cfgs:
                    active = cfg.name == state["cat"] and not state["query"]
                    unsaved = len(state["edits"].get(cfg.name, {}))
                    f = state["files"][cfg.name]
                    changed = sum(1 for p, v in f["values"].items()
                                  if f["base"].get(p) != v)
                    cls = ("w-full items-center gap-2 px-3 py-2 rounded-lg "
                           "cursor-pointer no-wrap ")
                    cls += (f"bg-[{_P['btn_hover']}] text-[{_P['title']}]" if active
                            else f"hover:bg-[{_P['btn_bg']}] text-[{_P['text']}]")
                    with ui.row().classes(cls).on(
                            "click", lambda c=cfg: _select(c.name)):
                        ui.icon(cfg.icon).classes(f"text-lg text-[{_P['icon']}]")
                        ui.label(cfg.title).classes("text-sm grow")
                        if unsaved:
                            ui.label(str(unsaved)).classes(_CHIP_UNSAVED) \
                                .tooltip("unsaved changes")
                        elif changed:
                            ui.label(str(changed)).classes(_CHIP_CHANGED) \
                                .tooltip("values changed from shipped")

    @guard
    def _select(name):
        state["cat"] = name
        state["query"] = ""
        search.value = ""
        _paint_nav()
        _paint_body()

    def _paint_body():
        body.clear()
        with body:
            if state["query"]:
                _paint_search(state["query"])
            else:
                _paint_category(cs.BY_NAME[state["cat"]])
            _paint_history()

    def _paint_history():
        entries = store.recent_changes(20)
        if not entries:
            return
        with ui.expansion("Recent changes", icon="history").classes(
                f"w-full {CARD}"):
            for e in entries:
                cfg = cs.BY_NAME.get(e.get("file"))
                title = cfg.title if cfg else e.get("file")
                ui.label(f"{change_stamp(e.get('at'))} · {title} · "
                         f"{e.get('key')}: {e.get('from')} → {e.get('to')}") \
                    .classes(f"text-xs {MUTED}")

    # ── footer: unsaved count + Save / Discard ──────────────────────────────
    _FOOTER_CLASSES = " ".join((TXT_NEG, TXT_WARN, TXT_POS, MUTED))

    def _paint_footer():
        """MUTATE the footer - never rebuild it. See the note where it is built.

        Save stays the PRIMARY kind and is disabled rather than restyled: the
        Appearance tab's footer already works that way, and two Settings tabs
        whose Save looks different while meaning the same thing is exactly what
        this migration removes."""
        n = sum(len(v) for v in state["edits"].values())
        errs = len(state["errors"])
        if errs:
            icon, cls = "error_outline", TXT_NEG
            text = f"{errs} value{'s' if errs != 1 else ''} to fix before saving"
        elif n:
            icon, cls = "edit_note", TXT_WARN
            text = f"{n} unsaved change{'s' if n != 1 else ''}"
        else:
            icon, cls, text = "check_circle", TXT_POS, "All changes saved"
        f_icon.name = icon
        # a finite class set, removed then added: the repo's reactive-colour rule
        f_icon.classes(remove=_FOOTER_CLASSES, add=cls)
        f_status.text = text
        f_status.classes(remove=_FOOTER_CLASSES, add=MUTED if not (n or errs) else cls)
        discard.set_enabled(bool(n or errs))
        save.set_enabled(bool(n) and not errs)

    @guard
    def _discard():
        state["edits"].clear()
        state["errors"].clear()
        _paint_all()
        kit.toast("info", "Unsaved changes discarded.")

    @guard
    def _save():
        if state["errors"]:
            kit.toast("warn", "Fix the highlighted values first.")
            return
        problems = []
        for name, eds in state["edits"].items():
            if not eds:
                continue
            f = state["files"][name]
            problems += cs.cross_check(name, {**f["values"], **eds})
        if problems:
            # ONE error, not one per clash. cross_check returns a sentence per
            # clash and sessions.toml can raise NINE at once - nine 8-second
            # toasts stacked in the same corner, each hiding the one under it.
            # Joined instead: nothing is dropped, and kit.toast turns it
            # multi-line on its own past 80 characters.
            kit.toast("error", " ".join(problems))
            return
        units: list = []
        saved = 0
        for name, eds in list(state["edits"].items()):
            cfg = cs.BY_NAME[name]
            # a read-only key has no control, so this is belt and braces
            eds = {p: v for p, v in eds.items() if not cs.is_readonly(cfg, p)}
            if not eds:
                continue
            f = state["files"][name]
            values = {**f["values"], **eds}
            over = overrides_to_save(cfg, f["shipped"], f["over"], values)
            changes = [(" › ".join(p), f["values"].get(p), v) for p, v in eds.items()]
            try:
                store.save(name, over, changes=changes)
            except Exception as exc:  # noqa: BLE001 - shown, nothing half-written
                kit.toast("error", f"Could not save {cfg.title}: {exc}")
                return
            for u in restart_targets(cfg, eds):
                if u not in units:
                    units.append(u)
            saved += len(eds)
            _load(name)
        state["edits"].clear()
        _PENDING.update(units)
        _paint_all()
        kit.toast("ok", f"Saved {saved} change{'s' if saved != 1 else ''}.")
        if units:
            _restart_dialog(units)

    # ── restart dialog ──────────────────────────────────────────────────────
    def _restart_dialog(units):
        """Ask, then hold the dialog open while the restarts run.

        ⚠ Built inside ``page_col`` — the page's OWN column — and ephemeral.
        The old one was built in whatever slot the click ran under, which for
        the banner's button is ``pending_box``, and ``_go`` called
        ``_paint_pending()`` while it was still running: that CLEARS the box and
        takes the dialog with it.

        The wait lives on the dialog's own confirm rather than on the page,
        because the page behind it is a list of settings with nothing to spin —
        and a held confirm is also what stops a second click firing a second
        restart of the same units."""
        picks = {}

        @guard_async
        async def _go():
            chosen = [u for u, cb in picks.items() if cb.value]
            if not chosen:
                return               # nothing ticked: close, and do nothing
            kit.set_busy(dlg.confirm, timeout=RESTART_TIMEOUT_SEC)
            try:
                results = await run.io_bound(_restart_units, chosen)
            finally:
                kit.set_busy(dlg.confirm, False)
            # At most TWO toasts, never one per unit: three units failing used
            # to mean three 8-second errors stacked on each other. The unit
            # names and the failure text are all still here.
            done, failed = [], []
            for u, ok, msg in results:
                label = cs.RESTART_LABELS.get(u, u)
                if ok:
                    _PENDING.discard(u)
                    done.append(label)
                else:
                    failed.append(f"{label} — {msg}" if msg else label)
            if done:
                kit.toast("ok", "Restarted: " + ", ".join(done) + ".")
            if failed:
                kit.toast("error", "Could not restart: " + "; ".join(failed))
            _paint_pending()
            if cs.WEBGUI in chosen:
                _PENDING.discard(cs.WEBGUI)
                kit.toast("warn", "Restarting the web app — this page reloads in "
                                  "a few seconds.")
                _restart_webgui()

        with page_col:
            dlg = kit.confirm(
                "Apply the changes?",
                "These read their settings when they start, so they need a "
                "restart.", confirm_text="Restart now", on_confirm=_go,
                ephemeral=True)
        with dlg.content:
            for u in units:
                picks[u] = ui.checkbox(cs.RESTART_LABELS.get(u, u), value=True)
            if market_busy() and any(u != cs.TIMERS for u in units):
                kit.notice("The market is open. Restarting the options service "
                           "now loses a minute or two of gamma collection; "
                           "after the close is safer.", icon="warning_amber")
        dlg.open()

    def _confirm_reset(cfg):
        @guard
        def _do():
            f = state["files"][cfg.name]
            changed = [p for p, v in f["values"].items()
                       if f["base"].get(p) != v]
            try:
                store.save(cfg.name, {}, changes=[
                    (" › ".join(p), f["values"].get(p), f["base"].get(p))
                    for p in changed])
            except Exception as exc:  # noqa: BLE001 - shown; the override stands
                kit.toast("error", f"Could not reset {cfg.title}: {exc}")
                return
            state["edits"].pop(cfg.name, None)
            for k in [k for k in state["errors"] if k[0] == cfg.name]:
                state["errors"].pop(k)
            _load(cfg.name)
            units = restart_targets(cfg, changed) if changed else []
            _PENDING.update(units)
            _paint_all()
            kit.toast("ok", f"{cfg.title} is back to the shipped values.")
            if units:
                _restart_dialog(units)

        # Ephemeral and in the page's own column, for the reason _restart_dialog
        # records: _do repaints the very containers this used to be built in.
        with page_col:
            dlg = kit.confirm(
                f"Put every {cfg.title} setting back to its shipped value?",
                "Every override you saved for this file is removed. It takes "
                "effect when the services that read it restart.",
                confirm_text="Reset", danger=True, on_confirm=_do, ephemeral=True)
        dlg.open()

    def _paint_all():
        _paint_pending()
        _paint_nav()
        _paint_body()
        _paint_footer()

    def _on_search(e):
        state["query"] = (e.value or "").strip()
        _paint_nav()
        _paint_body()

    search.on_value_change(_on_search)
    _paint_all()


# ── controls ─────────────────────────────────────────────────────────────────
def _apply_to_control(control, fld, value):
    """Push a stored value back into the control (Reset)."""
    shown = cs.to_display(fld, value)
    setter = control.get("set")
    if setter:
        setter(shown)


def _build_control(control, fld, value, on_change, *, optional=False):
    """One editor widget for ``fld``; ``on_change(raw)`` receives the typed value.

    ⚠ Deliberately NOT moved onto the kit's field builders. Its row layout is
    the page's own - a fixed label column, a growing control, a status column -
    and its numbers keep ``min``/``max`` OFF the widget so ``config_schema.parse``
    can say WHY a value is refused instead of Quasar silently clamping it. Only
    the two BUTTONS in the ladder editor go through the kit, because an icon
    with no tooltip does not say what it does - and that one deletes a rung."""
    shown = cs.to_display(fld, value)
    k = fld.kind
    num_props = "dense outlined"

    if k == "bool" and optional:
        opts = {None: "Not set (inherited)", True: "On", False: "Off"}
        el = ui.select(opts, value=shown).props("dense outlined").classes("w-56")
        el.on_value_change(lambda e: on_change(e.value if e.value is not None else ""))
        control["set"] = lambda v: setattr(el, "value", v)
        return

    if k == "bool":
        el = ui.switch(value=bool(shown))
        el.on_value_change(lambda e: on_change(e.value))
        control["set"] = lambda v: setattr(el, "value", bool(v))
        return

    if k in ("int", "float", "money", "fraction"):
        prefix = "$" if k == "money" else None
        suffix = fld.unit if k != "money" and fld.unit else None
        # No min/max on the widget: Quasar would clamp silently on blur, where
        # the page should say WHY a value is refused (config_schema.parse).
        el = ui.number(value=shown, step=fld.step, prefix=prefix, suffix=suffix,
                       placeholder="not set" if optional else "") \
            .props(num_props + (" clearable" if optional else "")).classes("w-48")
        el.on_value_change(lambda e: on_change(e.value))
        control["set"] = lambda v: setattr(el, "value", v)
        return

    if k == "text":
        el = ui.input(value=shown or "").props("dense outlined").classes("w-64")
        el.on_value_change(lambda e: on_change(e.value))
        control["set"] = lambda v: setattr(el, "value", v)
        return

    if k == "time":
        el = ui.input(value=shown or "").props(
            'dense outlined mask="##:##" fill-mask hint="HH:MM"').classes("w-32")
        el.on_value_change(lambda e: on_change(e.value))
        control["set"] = lambda v: setattr(el, "value", v)
        return

    if k == "date":
        el = ui.input(value=shown or "").props(
            'dense outlined mask="####-##-##" hint="YYYY-MM-DD"').classes("w-40")
        el.on_value_change(lambda e: on_change(e.value))
        control["set"] = lambda v: setattr(el, "value", v)
        return

    if k in ("choice", "sector"):
        el = ui.select(list(fld.choices), value=shown).props("dense outlined") \
            .classes("w-64")
        el.on_value_change(lambda e: on_change(e.value))
        control["set"] = lambda v: setattr(el, "value", v)
        return

    if k == "symbols":
        el = ui.input_chips(value=list(shown or []), new_value_mode="add-unique",
                            clearable=True).props("dense outlined") \
            .classes("w-full max-w-xl")
        el.on_value_change(lambda e: on_change(list(e.value or [])))
        control["set"] = lambda v: setattr(el, "value", list(v or []))
        return

    if k == "pair":
        lo_hi = list(shown or [None, None])
        with ui.row().classes("items-center gap-2 no-wrap"):
            lo = ui.number(value=lo_hi[0], step=fld.step, placeholder="low") \
                .props(num_props).classes("w-28")
            ui.label("to").classes(MUTED)
            hi = ui.number(value=lo_hi[1], step=fld.step, placeholder="high") \
                .props(num_props).classes("w-28")
        cb = lambda _e: on_change([lo.value, hi.value])  # noqa: E731
        lo.on_value_change(cb)
        hi.on_value_change(cb)

        def _set_pair(v):
            lo.value, hi.value = (list(v) + [None, None])[:2]
        control["set"] = _set_pair
        return

    if k == "ladder":
        rungs = [list(r) for r in (shown or [])]
        holder = ui.column().classes("gap-1")

        def _emit():
            on_change([list(r) for r in rungs])

        def _paint():
            holder.clear()
            with holder:
                for i, r in enumerate(rungs):
                    with ui.row().classes("items-center gap-2 no-wrap"):
                        ui.label("Peak").classes(f"text-xs {MUTED}")
                        a = ui.number(value=r[0], step=5, suffix="%") \
                            .props(num_props).classes("w-28")
                        ui.label("→ lock").classes(f"text-xs {MUTED}")
                        b = ui.number(value=r[1], step=5, suffix="%") \
                            .props(num_props).classes("w-28")

                        def _upd(_e, i=i, a=a, b=b):
                            rungs[i] = [a.value, b.value]
                            _emit()
                        a.on_value_change(_upd)
                        b.on_value_change(_upd)

                        def _rm(i=i):
                            rungs.pop(i)
                            _paint()
                            _emit()
                        kit.icon_button("close", tooltip="Remove this rung",
                                        on_click=_rm)
                kit.button("Add a rung", kind="quiet", icon="add",
                           on_click=lambda: (rungs.append(
                               [(rungs[-1][0] + 10) if rungs else 50,
                                (rungs[-1][1] + 10) if rungs else 0]),
                               _paint(), _emit()))

        def _set_ladder(v):
            rungs[:] = [list(r) for r in (v or [])]
            _paint()
        control["set"] = _set_ladder
        _paint()
        return

    ui.label(str(shown)).classes(MUTED)

"""Settings -> Configuration: every ``config/*.toml`` setting, editable in the app.

What the operator sees is organised by PURPOSE (Trade selection, Exits, the
driver, Flow alerts, Market hours, Symbols, the Sector map, Commissions), not by
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
"""
from __future__ import annotations

import dataclasses
import subprocess
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from nicegui import run, ui

import config_schema as cs
import config_store as store
from pages.options.theme import BTN, BTN_PRIMARY, CARD, EYEBROW, LABEL, MUTED
from pages.ui_guard import guard, guard_async

# Saved-but-not-yet-restarted units, shared across page loads of this process.
_PENDING: set = set()

_CHIP = "text-[11px] px-2 py-[1px] rounded-full whitespace-nowrap"
_CHIP_UNSAVED = f"{_CHIP} bg-amber-500/20 text-amber-300"
_CHIP_CHANGED = f"{_CHIP} bg-sky-500/20 text-sky-300"
_ROW = "w-full items-start gap-3 py-2 border-b border-[#1d2942] last:border-b-0"


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
        now = now or datetime.now(ZoneInfo("America/Chicago"))
        return bool(mc.is_regular_hours(now) or mc.in_collection_window(now))
    except Exception:  # noqa: BLE001 - a warning, never a blocker
        return False


def _group_label(f, fld, path, label):
    """A Net Prem group's rows are named after the group ("Mega-caps — symbols")."""
    if not fld.key.startswith("netprem_groups.*."):
        return label
    group = f["labels"].get(path[1], path[1])
    return f"{group} — {'tab name' if path[-1] == 'label' else 'symbols'}"


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

    # ── header ──────────────────────────────────────────────────────────────
    with ui.column().classes("w-full gap-1"):
        ui.label("Configuration").classes(f"text-h6 {LABEL}")
        ui.label("Every setting the trading services read from config/*.toml, in "
                 "plain words. Your changes are saved as overrides on top of the "
                 "shipped values, so Reset always takes you back, and updates to "
                 "the app never overwrite them.").classes(f"text-sm {MUTED}")

    pending_box = ui.row().classes("w-full")
    with ui.row().classes("w-full items-center gap-3"):
        search = ui.input(placeholder="Search every setting — e.g. \"delta\", "
                                      "\"take profit\", \"VIX\"") \
            .props("dense outlined clearable").classes("w-full max-w-xl")
        search.props('prepend-icon="search"')

    with ui.row().classes("w-full items-start gap-4 no-wrap"):
        nav = ui.column().classes("w-60 shrink-0 gap-1")
        body = ui.column().classes("grow min-w-0 gap-4")

    footer = ui.row().classes(
        "w-full items-center gap-3 sticky bottom-0 z-10 bg-[#0c1424] "
        "border-t border-[#213152] px-4 py-2 rounded-t-lg")

    # ── pending-restart banner ──────────────────────────────────────────────
    def _paint_pending():
        pending_box.clear()
        if not _PENDING:
            return
        names = ", ".join(cs.RESTART_LABELS.get(u, u) for u in sorted(_PENDING))
        with pending_box:
            with ui.row().classes("w-full items-center gap-3 rounded-lg px-3 py-2 "
                                  "bg-amber-500/10 border border-amber-500/30"):
                ui.icon("restart_alt").classes("text-amber-300")
                ui.label(f"Saved changes are waiting for a restart: {names}.") \
                    .classes("text-sm text-amber-200 grow")
                ui.button("Restart now", color=None,
                          on_click=lambda: _restart_dialog(sorted(_PENDING))) \
                    .props("no-caps dense").classes(BTN_PRIMARY)

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

    def _field_row(cfg, fld, path, label):
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
                    ui.label(err).classes("text-xs text-rose-300")
                    return
                if path in state["edits"].get(name, {}):
                    ui.label("Unsaved").classes(_CHIP_UNSAVED)
                if v != shipped_v:
                    ui.label(f"Shipped: {display_value(shipped_v, shipped_v, fld)}") \
                        .classes(_CHIP_CHANGED)
                    ui.button(icon="restore", color=None,
                              on_click=lambda: _reset_field()) \
                        .props("flat dense round size=sm") \
                        .classes("text-sky-300") \
                        .tooltip("Back to the shipped value")

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
                ui.icon(cfg.icon).classes("text-xl text-[#8794b4]")
                ui.label(cfg.title).classes(f"text-subtitle1 font-bold {LABEL}")
                ui.label(f"config/{cfg.name}").classes(f"text-xs {MUTED}")
            ui.label(cfg.summary).classes(f"text-sm {MUTED}")
            if cfg.caution:
                with ui.row().classes("items-start gap-2 rounded-md px-3 py-2 "
                                      "bg-amber-500/10 border border-amber-500/25"):
                    ui.icon("warning_amber").classes("text-amber-300")
                    ui.label(cfg.caution).classes("text-xs text-amber-100")
            if cfg.restart:
                ui.label("Takes effect after restarting: " + ", ".join(
                    cs.RESTART_LABELS[u] for u in cfg.restart)) \
                    .classes(f"text-xs {MUTED}")
        if not f["shipped"]:
            ui.label(f"config/{cfg.name} could not be read.").classes(
                "text-rose-300 text-sm")
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
            ui.button(f"Reset all of {cfg.title} to shipped values", icon="restore",
                      color=None, on_click=lambda c=cfg: _confirm_reset(c)) \
                .props("no-caps flat dense").classes("text-[#8794b4]")

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
                    ui.label(f"{s} · {counts.get(s, 0)}").classes(
                        f"{_CHIP} bg-[#15213b] text-[#cdd8ee]")
            with ui.row().classes("w-full items-center gap-2"):
                flt = ui.input(placeholder="Find a symbol or a sector") \
                    .props("dense outlined clearable").classes("w-72")
                ui.label("·").classes(MUTED)
                new_sym = ui.input(placeholder="New symbol").props(
                    "dense outlined").classes("w-32")
                new_sec = ui.select(list(cs.SECTORS), value=None,
                                    label="Sector").props("dense outlined") \
                    .classes("w-56")

                def _add():
                    sym = (new_sym.value or "").strip().upper()
                    if not sym or not new_sec.value:
                        ui.notify("Type a symbol and pick its sector.", type="warning")
                        return
                    _set(name, ("sectors", sym), new_sec.value)
                    new_sym.value = ""
                    flt.value = sym
                    _paint_footer()
                    _paint_nav()
                    rows_box.refresh()

                ui.button("Add", icon="add", color=None, on_click=_add) \
                    .props("no-caps dense").classes(BTN)

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
            ui.label(f"No setting matches “{q}”.").classes(f"text-sm {MUTED}")

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
                    cls += ("bg-[#1b2950] text-[#eaf0fb]" if active
                            else "hover:bg-[#15213b] text-[#cdd8ee]")
                    with ui.row().classes(cls).on(
                            "click", lambda c=cfg: _select(c.name)):
                        ui.icon(cfg.icon).classes("text-lg text-[#8794b4]")
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
                ui.label(f"{e.get('at', '')[:16].replace('T', ' ')} · {title} · "
                         f"{e.get('key')}: {e.get('from')} → {e.get('to')}") \
                    .classes(f"text-xs {MUTED}")

    # ── footer: unsaved count + Save / Discard ──────────────────────────────
    def _paint_footer():
        footer.clear()
        n = sum(len(v) for v in state["edits"].values())
        errs = len(state["errors"])
        with footer:
            if errs:
                ui.icon("error_outline").classes("text-rose-300")
                ui.label(f"{errs} value{'s' if errs != 1 else ''} to fix before "
                         "saving").classes("text-sm text-rose-200 grow")
            elif n:
                ui.icon("edit_note").classes("text-amber-300")
                ui.label(f"{n} unsaved change{'s' if n != 1 else ''}").classes(
                    "text-sm text-amber-100 grow")
            else:
                ui.icon("check_circle").classes("text-emerald-300")
                ui.label("All changes saved").classes(f"text-sm {MUTED} grow")
            # A disabled primary button still reads as clickable in this theme
            # (Quasar's dimming is only 0.6), so Save wears the plain secondary
            # style until there is something to save.
            off_discard = not (n or errs)
            off_save = (not n) or bool(errs)
            ui.button("Discard", color=None, on_click=_discard) \
                .props(f"no-caps {'disable' if off_discard else ''}").classes(BTN)
            ui.button("Save changes", icon="save", color=None, on_click=_save) \
                .props(f"no-caps {'disable' if off_save else ''}") \
                .classes(BTN if off_save else BTN_PRIMARY)

    @guard
    def _discard():
        state["edits"].clear()
        state["errors"].clear()
        _paint_all()
        ui.notify("Unsaved changes discarded.")

    @guard
    def _save():
        if state["errors"]:
            ui.notify("Fix the highlighted values first.", type="warning")
            return
        problems = []
        for name, eds in state["edits"].items():
            if not eds:
                continue
            f = state["files"][name]
            problems += cs.cross_check(name, {**f["values"], **eds})
        if problems:
            for p in problems:
                ui.notify(p, type="negative", multi_line=True)
            return
        units: list = []
        saved = 0
        for name, eds in list(state["edits"].items()):
            if not eds:
                continue
            cfg = cs.BY_NAME[name]
            f = state["files"][name]
            values = {**f["values"], **eds}
            over = store.build_overrides(f["shipped"], values)
            changes = [(" › ".join(p), f["values"].get(p), v) for p, v in eds.items()]
            try:
                store.save(name, over, changes=changes)
            except Exception as exc:  # noqa: BLE001 - shown, nothing half-written
                ui.notify(f"Could not save {cfg.title}: {exc}", type="negative")
                return
            for u in restart_targets(cfg, eds):
                if u not in units:
                    units.append(u)
            saved += len(eds)
            _load(name)
        state["edits"].clear()
        _PENDING.update(units)
        _paint_all()
        ui.notify(f"Saved {saved} change{'s' if saved != 1 else ''}.",
                  type="positive")
        if units:
            _restart_dialog(units)

    # ── restart dialog ──────────────────────────────────────────────────────
    def _restart_dialog(units):
        with ui.dialog() as dlg, ui.card().classes(f"{CARD} min-w-[420px] gap-3"):
            ui.label("Apply the changes").classes(f"text-subtitle1 font-bold {LABEL}")
            ui.label("These read their settings when they start, so they need a "
                     "restart:").classes(f"text-sm {MUTED}")
            picks = {}
            for u in units:
                picks[u] = ui.checkbox(cs.RESTART_LABELS.get(u, u), value=True)
            if market_busy() and any(u != cs.TIMERS for u in units):
                with ui.row().classes("items-start gap-2 rounded-md px-3 py-2 "
                                      "bg-amber-500/10 border border-amber-500/25"):
                    ui.icon("warning_amber").classes("text-amber-300")
                    ui.label("The market is open. Restarting the options service "
                             "now loses a minute or two of gamma collection; after "
                             "the close is safer.").classes("text-xs text-amber-100")
            with ui.row().classes("w-full justify-end gap-2"):
                ui.button("Later", color=None, on_click=dlg.close) \
                    .props("no-caps").classes(BTN)

                @guard_async
                async def _go():
                    chosen = [u for u, cb in picks.items() if cb.value]
                    dlg.close()
                    if not chosen:
                        return
                    ui.notify("Restarting…")
                    results = await run.io_bound(_restart_units, chosen)
                    for u, ok, msg in results:
                        if ok:
                            _PENDING.discard(u)
                        ui.notify(f"{cs.RESTART_LABELS.get(u, u)}: "
                                  f"{'done' if ok else 'failed — ' + msg}",
                                  type="positive" if ok else "negative",
                                  multi_line=True)
                    _paint_pending()
                    if cs.WEBGUI in chosen:
                        _PENDING.discard(cs.WEBGUI)
                        ui.notify("Restarting the web app — this page reloads in "
                                  "a few seconds.", type="warning")
                        _restart_webgui()

                ui.button("Restart now", icon="restart_alt", color=None,
                          on_click=_go).props("no-caps").classes(BTN_PRIMARY)
        dlg.open()

    def _confirm_reset(cfg):
        with ui.dialog() as dlg, ui.card().classes(f"{CARD} gap-3"):
            ui.label(f"Put every {cfg.title} setting back to its shipped value?") \
                .classes(LABEL)
            with ui.row().classes("w-full justify-end gap-2"):
                ui.button("Cancel", color=None, on_click=dlg.close) \
                    .props("no-caps").classes(BTN)

                @guard
                def _do():
                    dlg.close()
                    f = state["files"][cfg.name]
                    changed = [p for p, v in f["values"].items()
                               if f["base"].get(p) != v]
                    store.save(cfg.name, {}, changes=[
                        (" › ".join(p), f["values"].get(p), f["base"].get(p))
                        for p in changed])
                    state["edits"].pop(cfg.name, None)
                    for k in [k for k in state["errors"] if k[0] == cfg.name]:
                        state["errors"].pop(k)
                    _load(cfg.name)
                    units = restart_targets(cfg, changed) if changed else []
                    _PENDING.update(units)
                    _paint_all()
                    ui.notify(f"{cfg.title} is back to the shipped values.",
                              type="positive")
                    if units:
                        _restart_dialog(units)

                ui.button("Reset", color=None, on_click=_do) \
                    .props("no-caps").classes(BTN_PRIMARY)
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
    """One editor widget for ``fld``; ``on_change(raw)`` receives the typed value."""
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
                        ui.button(icon="close", color=None, on_click=_rm) \
                            .props("flat dense round size=sm").classes("text-[#8794b4]")
                ui.button("Add a rung", icon="add", color=None,
                          on_click=lambda: (rungs.append(
                              [(rungs[-1][0] + 10) if rungs else 50,
                               (rungs[-1][1] + 10) if rungs else 0]),
                              _paint(), _emit())) \
                    .props("flat dense no-caps").classes("text-[#8794b4]")

        def _set_ladder(v):
            rungs[:] = [list(r) for r in (v or [])]
            _paint()
        control["set"] = _set_ladder
        _paint()
        return

    ui.label(str(shown)).classes(MUTED)

# App UI consistency — Phase 1 (Options boards) Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Move the nine table-and-list Options pages onto the page kit. Each gets a header line with an Updated stamp and page actions, a status line, one table style and one loading region, and puts every row action in the detail panel footer. Every destructive action confirms, and every toast has a type.

**Architecture:** Phase 0 built `pages/ui_kit.py`, the app-wide surface and the guard. This phase adds two small kit pieces (an information dialog, a section title) and an action footer on the shared Trade detail panel, then rewrites each page's `render()` layout with the kit. The pure row builders are left alone except where a status text loses its clock time (the header now carries it) or a column list loses its per-row icon column (those actions move to the panel).

**Tech Stack:** NiceGUI 3.13, pytest.

**Prerequisite:** Phase 0 ([`2026-09-19-app-ui-consistency-phase0-plan.md`](2026-09-19-app-ui-consistency-phase0-plan.md)) is done and green. Read the design first: [`2026-09-19-app-ui-consistency-design.md`](2026-09-19-app-ui-consistency-design.md), and the sample the operator approved (Paper Ledger before/after): the title and Updated stamp share one line with the page actions; the selected row is marked; the row's buttons sit in its panel with danger left and the main action right; and Delete asks first, with Cancel before Delete.

---

## Conventions

Same as Phase 0. The worktree root is `D:\WebGUI Trading with Schwab\.claude\worktrees\market-summary-social-images-2333b5`. `$PY` is `"D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe"`, and tests run with `cd webgui && $PY -m pytest …`. Commit with explicit paths, never `--amend`, and end every commit message with `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`. The baseline is Phase 0's final full-suite run; compare the failing and skipped sets.

**The rules every page task applies** (so each task below only shows what is page-specific):

1. **Frame.** Wrap the body in `with kit.page():`. Delete any `calc-v2 {PAGE}` wrapper, any `ui.add_css(QUASAR_INTERNAL_CSS)`, and any page-level table CSS that only set a height, padding or sticky header (`PAPER_CSS`, `CAPTURED_CSS`, `_RESCUE_CSS`). The shell now supplies all of that. `SCAN_CSS` stays: the Scanner's column count needs its compact padding.
2. **Header line.** `head = kit.header("<Page name>", view="<primary view>")`. Pass `stale=True` only for a view published on a schedule: here, the Market Scanner (`options:scan`) and the Opportunity Board (`options:matrix`). Add page actions inside `with head.actions:`, danger first and primary last.
3. **No description line.** Delete the EYEBROW sentence under the old in-body title. Before deleting it, check that `page_help.HELP_MD["<route>"]` already says the same thing, and add the sentence there if it doesn't (`webgui/page_help.py`).
4. **Status line.** Show counts only, in `kit.status_line()`, placed directly under the header. Any clock time leaves the status text, because the header stamp owns it.
5. **Loading.** Use one `kit.region(...)` around what a refresh replaces. Show `region.busy.show(<message for that action>)` on each request and `.hide()` in the repaint. Delete any separate `ui.spinner` and any `_busy.build_busy` on a container that a repaint clears.
6. **Table.** Build it with `kit.table(columns, numeric=(…))`. On a row click, call `kit.mark_selected(table.rows, <id>)` and then `table.update()`. Re-stamp in every repaint that assigns rows.
7. **Row actions.** Put them in `detail_panel.actions`: danger first with `.classes("mr-auto")`, secondary buttons next, primary last. Drop the per-row icon column. Nothing prints "Click a row first" any more, because the footer is only visible while a row is shown.
8. **Dialogs.** Build each `kit.confirm(...)` **once**, at the page's own level, and retitle it per use. Every delete or reset uses `danger=True`.
9. **Toasts.** Use `kit.toast(kind, text)`, where kind is `info`, `ok`, `warn` or `error`. Drop any toast that only repeats what a spinner already shows.
10. **Tests.** When an assertion pins behaviour this plan deliberately changes (a status clock, the `actions` column, a row-click navigation), change it to the new behaviour and say so in the commit. Never delete one to get to green.

---

### Task 1: Two kit pieces — the information dialog and the section title

**Files:** Modify `webgui/pages/ui_kit.py`; Test `webgui/tests/test_ui_kit.py`

**Step 1: Failing tests** (append to `test_ui_kit.py`):

```python
# -- information dialog, section title -------------------------------------------
def test_an_information_dialog_has_a_close_x_and_no_footer():
    with ui.card():
        d = kit.info_dialog("Trade analysis")
    assert d.title.text == "Trade analysis"
    buttons = [e for e in d.dialog.descendants() if isinstance(e, ui.button)]
    assert len(buttons) == 1 and buttons[0]._props.get("icon") == "close"
    d.open()
    d.close()
    assert d.dialog.value is False


def test_section_title_is_the_one_heading_style():
    with ui.card():
        s = kit.section_title("Open positions")
    assert s.text == "Open positions" and theme.LABEL in s.classes
```

**Step 2: Run** `cd webgui && $PY -m pytest tests/test_ui_kit.py -q -k "information or section"` — Expected: FAIL.

**Step 3: Implement** (append to `ui_kit.py`):

```python
# ── information dialog, section title ──────────────────────────────────────
def info_dialog(title, *, width="w-[720px]"):
    """An information dialog: a title, a close ✕ top-right, no footer (the
    standard - a dialog that ASKS is ``confirm``). Put the body in
    ``handle.content``; build it once and repaint the content per use."""
    # ns-app on the card: a dialog is teleported outside the shell's column.
    with ui.dialog() as dlg, ui.card().classes(f"ns-app {_t.CARD} {width} max-w-full gap-3"):
        with ui.row().classes("w-full items-center no-wrap gap-2"):
            title_lbl = ui.label(title).classes(f"text-subtitle1 font-semibold {_t.LABEL}")
            ui.space()
            icon_button("close", tooltip="Close", on_click=dlg.close)
        content = ui.column().classes("w-full gap-3")
    return SimpleNamespace(dialog=dlg, title=title_lbl, content=content,
                           open=dlg.open, close=dlg.close)


def section_title(text):
    """A heading inside a page ("Open positions", "Analytics")."""
    return ui.label(text).classes(f"text-subtitle2 font-semibold {_t.LABEL}")
```

**Step 4: Run** `cd webgui && $PY -m pytest tests/test_ui_kit.py tests/test_ui_kit_guard.py -q` — Expected: PASS.

**Step 5: Commit**

```bash
git add webgui/pages/ui_kit.py webgui/tests/test_ui_kit.py
git commit -m "feat(ui_kit): information dialog and section title

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: The Trade detail panel gets an action footer

**Files:** Modify `webgui/pages/options/detail.py` (`_Handle`, `render`); Test `webgui/tests/test_options_detail.py` (create it if absent; otherwise append)

**Step 1: Failing tests:**

```python
"""The detail panel's action footer - where a selected row's buttons live."""
from nicegui import ui

from pages.options import detail

SIG = {"symbol": "SPY", "type": "PCS", "expiration": "2026-10-17",
       "short_strike": 560, "long_strike": 555, "credit": 1.42}


def _panel():
    with ui.row():
        return detail.render()


def test_the_footer_is_hidden_until_a_row_is_shown():
    h = _panel()
    assert not h.actions.visible
    h.update(SIG)
    assert h.actions.visible
    h.clear()
    assert not h.actions.visible


def test_collapsing_hides_the_footer_and_expanding_restores_it():
    h = _panel()
    h.update(SIG)
    h.collapse()
    assert not h.actions.visible
    h.open()
    assert h.actions.visible


def test_the_footer_sits_below_the_body_and_right_aligns():
    h = _panel()
    col = h.actions.parent_slot.parent
    kids = list(col.default_slot.children)
    assert kids.index(h._body) < kids.index(h.actions)
    assert "justify-end" in h.actions.classes
```

**Step 2: Run** — Expected: FAIL (`AttributeError: actions`).

**Step 3: Implement.** In `render()`, directly after the `body = ui.column()…` block and still inside `with col:`, add:

```python
        # The selected row's actions (the 2026-09-19 standard): danger leftmost
        # (a caller adds ``mr-auto`` to it), primary rightmost. Pages build their
        # buttons into it ONCE; it shows only while a row is shown and open.
        actions = ui.row().classes(
            "w-full items-center justify-end gap-2 flex-wrap pt-2 "
            f"border-t border-[{THEME['palette']['card_border']}]")
    actions.set_visibility(False)
```

Import `THEME` alongside the existing theme imports: `from .theme import (…, THEME)`.

In `set_open(flag)`, add `actions.visible = state["open"] and state["has_signal"]`.

Pass `actions` into `_Handle(...)` as a new last argument. In `_Handle.__init__`, take it and store it as `self.actions = actions`. In `clear()`, add `self.actions.set_visibility(False)`. In `update()`, after `self._header.set_visibility(self._state["open"])`, add `self.actions.set_visibility(self._state["open"])`.

**Step 4: Run** `cd webgui && $PY -m pytest tests/test_options_detail.py tests/ -q -k detail` — Expected: PASS.

**Step 5: Commit**

```bash
git add webgui/pages/options/detail.py webgui/tests/test_options_detail.py
git commit -m "feat(detail): an action footer for the selected row

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: Hand-off dialogs and toasts through the kit

`handoff.py` builds the Paper-trade and the open-into-account dialogs by hand, with Create and Cancel left-aligned and Cancel in capitals, and it sends toasts through twelve raw `ui.notify` calls.

**Files:**
- Modify: `webgui/pages/options/handoff.py`
- Modify: `webgui/tests/test_paper_dialog_view.py` (the fake-`ui` tests become real-NiceGUI tests; **same assertions**)
- Modify: `webgui/tests/test_ui_kit_guard.py` (delete the `options/handoff.py` entry)

**Step 1: Rewrite the three fake-`ui` tests to drive the real dialog.** In `test_paper_dialog_view.py`, delete the `_El` and `_FakeUi` classes and `_open_dialog`, then add:

```python
import asyncio

from nicegui import ui


def _open_dialog(monkeypatch, request, read=lambda view: CAPS):
    """The real send_to_paper, with the bus and the toast stubbed."""
    notes = []
    monkeypatch.setattr(handoff.bus_client, "read", read)
    monkeypatch.setattr(handoff.bus_client, "request", request)
    monkeypatch.setattr(handoff.kit, "toast", lambda kind, text: notes.append((text, kind)))
    with ui.card():
        dlg = handoff.send_to_paper(SIG)
    return dlg, notes


def _qty(dlg):
    return next(e for e in dlg.content.descendants() if isinstance(e, ui.number))
```

Then rewrite the three tests. They keep every assertion, now against real elements:

```python
def test_a_second_click_sends_nothing(monkeypatch):
    sent = []
    dlg, notes = _open_dialog(monkeypatch, lambda domain, cmd: sent.append((domain, cmd)))
    _qty(dlg).value = 2.0
    asyncio.run(dlg.run())
    asyncio.run(dlg.run())         # the queued double click
    assert len(sent) == 1
    assert sent[0][1]["args"]["qty"] == 2
    assert dlg.confirm.enabled is False
    assert notes == [("Sent 2 contracts — the paper ledger answers in a moment.", "info")]
    assert dlg.dialog.value is False


def test_a_send_that_cannot_reach_the_bus_can_be_retried(monkeypatch):
    calls = []

    def down(domain, cmd):
        calls.append(cmd)
        raise ConnectionError("redis down")

    dlg, notes = _open_dialog(monkeypatch, down)
    asyncio.run(dlg.run())
    assert notes == [("Could not reach the options service — the trade was "
                      "not sent.", "error")]
    assert dlg.confirm.enabled is True and dlg.dialog.value is True
    asyncio.run(dlg.run())         # the retry really runs again
    assert len(calls) == 2


def test_an_unreadable_caps_view_leaves_create_enabled(monkeypatch):
    def boom(view):
        raise ConnectionError("redis down")

    dlg, _notes = _open_dialog(monkeypatch, lambda d, c: None, read=boom)
    assert dlg.confirm.enabled is True
    shown = [e.text for e in dlg.content.descendants()
             if isinstance(e, ui.label) and e.visible]
    assert book_fit.UNAVAILABLE in shown
```

Check `test_send_to_paper_reads_caps_once_and_rechecks_on_confirm`. Its AST checks must still hold unchanged: the inner function is still named `confirm`, it still sets `state['sent'] = True` and calls `create.disable()` before `bus_client.request`, and `if state['sent']` still comes before `paper_dialog_view`. Do not edit that test.

**Step 2: Run** `cd webgui && $PY -m pytest tests/test_paper_dialog_view.py -q` — Expected: FAIL (`send_to_paper` returns None; `handoff.kit` missing).

**Step 3: Implement.** In `handoff.py`:
- Add `from pages import ui_kit as kit`. Drop `BTN_3D` from the theme import if it is now unused.
- Replace every `ui.notify(text, type="warning")` with `kit.toast("warn", text)`, `type="negative"` with `"error"`, `type="info"` with `"info"`, and `type="positive"` with `"ok"`.
- In `watch_paper_results._on_change`, replace `ui.notify(toast[0], type=toast[1])` with `kit.toast(_TOAST_KIND.get(toast[1], "info"), toast[0])`, and add at module level: `_TOAST_KIND = {"positive": "ok", "warning": "warn", "negative": "error", "info": "info"}`. `paper_result_toast` and its tests stay as they are.
- In `send_to_paper`, replace the `with ui.dialog() as dlg, ui.card()…:` block. The dialog's title, the preview labels and the quantity field go into a `kit.confirm`:

```python
    view = state["view"]
    # ephemeral: this dialog is built per click, so it removes itself on close
    # rather than leaving one element behind in the page each time.
    dlg = kit.confirm(view["title"], confirm_text="Create", ephemeral=True,
                      on_confirm=lambda: confirm())
    create = dlg.confirm
    with dlg.content:
        risk = ui.label(view["risk_text"]).classes(f"text-sm {MUTED}")
        lines_box = ui.column().classes("gap-1 w-full")
        note = ui.label("").classes(f"text-xs {MUTED}")
        fits = ui.label("").classes(f"text-xs {MUTED}")
        # min only: the ceiling MOVES with the quantity (paper_dialog_view's
        # qty_max is never below a typed quantity that fits), so it is painted
        # into _props["max"] by paint() below, never frozen into the check.
        qty = kit.number_field("Quantity", value=1, min=1, integer=True)

    def confirm():
        if state["sent"]:
            return
        current = paper_dialog_view(signal, caps, qty.value)
        if not current["can_create"]:
            return False              # keep the dialog open; the red line says why
        n = int(qty.value)
        state["sent"] = True
        create.disable()
        try:
            bus_client.request("options", {
                "type": "paper_create",
                "args": {"signal": signal, "qty": n},
            })
        except Exception:  # noqa: BLE001 - said on screen; the reader retries
            state["sent"] = False
            create.enable()
            kit.toast("error", SEND_FAILED_TEXT)
            return False
        kit.toast("info", sent_text(n))
```

Keep `paint(v)` and `on_qty(e)` exactly as they are. `paint` writes `qty._props["max"]`, and NiceGUI's own blur clamp reads that prop, so the ceiling behaves as before. `kit.number_field` never passes `max` to Quasar, so `paint` is the only thing that sets it. End the function with `dlg.open()` and `return dlg`.

Keep the original comments on the latch, the re-check and the enqueue, beside the lines they explain.

- In `open_in_paper_account`, replace the dialog block with:

```python
    kind = "cash-secured put" if row.get("type") == "SHORT_PUT" else "covered call"
    dlg = kit.confirm(
        f"Open {row.get('symbol')} {kind} {row.get('expiration', '')}",
        "This opens into the paper account: collateral is reserved, and an "
        "assignment becomes shares.",
        confirm_text="Open", ephemeral=True, on_confirm=lambda: _open())
    with dlg.content:
        qty = kit.number_field("Contracts", value=default_qty, min=1, max=100,
                               integer=True)

    def _open():
        if not qty.validate():
            return False
        bus_client.request("options", {
            "type": "income_open",
            "args": {"row": row, "qty": int(qty.value or 1)},
        })
        kit.toast("info", "Sent to the paper account — the result appears here "
                          "in a moment.")

    dlg.open()
    return dlg
```

**Step 4: Run** `cd webgui && $PY -m pytest tests/test_paper_dialog_view.py tests/test_paper_create_toast.py tests/test_options_handoff.py tests/test_income_page.py tests/test_options_swing.py -q` — Expected: PASS. Then run the guard. `options/handoff.py` should report no guarded calls, so delete its `ALLOWED` entry.

**Step 5: Commit**

```bash
git add webgui/pages/options/handoff.py webgui/tests/test_paper_dialog_view.py webgui/tests/test_ui_kit_guard.py
git commit -m "refactor(handoff): Paper and open-into-account dialogs and toasts via ui_kit

Cancel now comes before Create/Open, right-aligned, in sentence case. The
fake-ui dialog tests drive the real dialog; every assertion kept.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: Paper Ledger

The before/after sample the operator approved.

**Files:** Modify `webgui/pages/options/paper.py`, `webgui/page_help.py` ("Reload" → "Refresh" if the /options/paper help says it); Test `webgui/tests/test_options_paper.py`, `webgui/tests/test_ui_kit_guard.py`

**Step 1: Failing tests** (append to `test_options_paper.py`; and change the existing `assert "actions" in {c["field"] for c in paper.paper_columns()}` to `assert "actions" not in …`, because the row actions moved to the panel):

```python
import ast
import inspect


def test_ledger_status_counts_trades_and_open():
    assert paper.ledger_status([]) == ""
    assert paper.ledger_status([{"status": "OPEN"}]) == "1 trade · 1 open"
    assert paper.ledger_status([{"status": "OPEN"}, {"status": "CLOSED"},
                                {"status": "EXPIRED"}]) == "3 trades · 1 open"


def _enclosing_functions_of_requests(fn, command):
    """Names of the functions inside ``fn`` that enqueue ``command``."""
    tree = ast.parse(inspect.getsource(fn))
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            for call in ast.walk(node):
                if (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                        and call.func.attr == "request"
                        and f"'{command}'" in ast.unparse(call)):
                    out.add(node.name)
    return out - {fn.__name__}


def test_every_delete_runs_only_after_a_confirm():
    """Delete and Delete all closed used to fire on the click."""
    for cmd in ("paper_delete", "paper_delete_closed"):
        owners = _enclosing_functions_of_requests(paper.render, cmd)
        assert owners and all(n.startswith("_confirm") for n in owners), (cmd, owners)


def test_row_actions_live_in_the_panel_footer():
    src = inspect.getsource(paper.render)
    assert "detail_panel.actions" in src
    assert "Click a trade row first" not in src
    assert "q-mt-md" not in src            # the old button row under the table


def test_the_page_is_built_from_the_kit():
    src = inspect.getsource(paper.render)
    assert 'kit.header("Paper Ledger", view="options:paper_trades")' in src
    assert "PAPER_CSS" not in src
```

**Step 2: Run** `cd webgui && $PY -m pytest tests/test_options_paper.py -q` — Expected: FAIL.

**Step 3: Implement.**
- Add the pure helper beside the other pure functions:

```python
def ledger_status(trades):
    """The status line: how many trades, how many open. PURE. Blank for no
    trades - the empty table says that."""
    trades = trades or []
    if not trades:
        return ""
    n = len(trades)
    open_n = sum(1 for t in trades if str(t.get("status") or "").upper() == "OPEN")
    return f"{n} trade{'s' if n != 1 else ''} · {open_n} open"
```

- In `paper_columns()`, delete the `cols.append({"name": "actions", …})` line.
- Delete `PAPER_CSS`, and the `from pages import busy as _busy` import if it is now unused. Replace the theme import with `from pages import ui_kit as kit`, keeping `BADGE_ACCENT, BADGE_MUTED` from `.theme`.
- Add `_NUMERIC = ("quantity", "entry_credit_total", "max_loss_total", "pnl")`.
- Replace `render()` from its first line down to `detail_panel = detail.render()` with:

```python
def render():
    """Paper Ledger: the header line, the ledger and the shared detail panel,
    whose footer carries the selected trade's actions (bus-fed)."""
    raw_by_id: dict = {}
    # sel_id: the clicked trade; live: {trade_id: live-analyze detail};
    # analyze_popup_for: the trade whose Analyze BUTTON result pops the dialog;
    # close_field: the exit-price field of the open Close dialog.
    state = {"sel_id": None, "live": {}, "analyze_popup_for": None,
             "close_field": None}

    with kit.page():
        head = kit.header("Paper Ledger", view="options:paper_trades")
        with head.actions:
            kit.button("Delete all closed", kind="danger", icon="delete_sweep",
                       on_click=lambda: delete_closed_dlg.open())
            refresh_btn = kit.button("Refresh", kind="secondary", icon="refresh",
                                     on_click=lambda: _reload())
        status = kit.status_line()
        with ui.row().classes("w-full no-wrap gap-4 items-start"):
            ledger = kit.region("Refreshing the ledger…", classes="flex-grow min-w-0")
            with ledger.content:
                table = kit.table(paper_columns(), numeric=_NUMERIC)
            detail_panel = detail.render()
```

  Keep the four `table.add_slot(...)` calls exactly as they are, now placed after this block. Delete the old button row.
- After the slots, add the footer and the dialogs:

```python
    with detail_panel.actions:
        kit.button("Delete", kind="danger", icon="delete",
                   on_click=lambda: _delete()).classes("mr-auto")
        kit.button("Expected Move", kind="secondary", icon="show_chart",
                   on_click=lambda: _send_em())
        analyze_btn = kit.button("Analyze", kind="secondary", icon="biotech",
                                 on_click=lambda: _analyze())
        kit.button("Close trade", kind="primary", icon="check_circle",
                   on_click=lambda: _close())

    close_dlg = kit.confirm("Close trade", confirm_text="Close trade",
                            on_confirm=lambda: _confirm_close())
    delete_dlg = kit.confirm("Delete this trade?", "It leaves the ledger for good.",
                             confirm_text="Delete", danger=True,
                             on_confirm=lambda: _confirm_delete())
    delete_closed_dlg = kit.confirm(
        "Delete every closed trade?",
        "Their realized P&L leaves the ledger, and the deployment cap's equity "
        "moves with it.",
        confirm_text="Delete all closed", danger=True,
        on_confirm=lambda: _confirm_delete_closed())
    analyze_dlg = kit.info_dialog("Trade analysis", width="min-w-[360px] max-w-[460px]")
```

- Delete `ledger_busy = _busy.build_busy(...)`. In `_populate`, replace `ledger_busy.hide()` with `ledger.busy.hide()` and `kit.set_busy(refresh_btn, False)`. Replace the status assignments with `status.text = ledger_status(trades)`. After `table.rows = paper_rows(trades)`, add `kit.mark_selected(table.rows, state.get("sel_id"))` before `table.update()`.
- In `_select`, after setting `state["sel_id"]`, add `kit.mark_selected(table.rows, state["sel_id"])` and `table.update()`.
- In `_request_analyze`, delete the `status.text = …` line. The panel repaints when the result lands.
- Replace the `handoff.add_expected_move_action(...)` call with:

```python
    def _send_em():
        t = _selected_trade()
        if t:
            handoff.send_to_expected_move(
                handoff.signal_to_em_payload(synth_from_trade(t)))
```

- Change `_selected_trade()` to return `raw_by_id.get(state.get("sel_id"))` with no toast. The footer is only visible when a row is shown.
- `_reload`: send the request, then call `ledger.busy.show("Refreshing the ledger…")` and `kit.set_busy(refresh_btn)`. Drop its toast and status text.
- Replace `_close`, `_delete` and `_delete_closed` with:

```python
    @guard
    def _close():
        t = _selected_trade()
        if not t:
            return
        close_dlg.title.text = f"Close {t.get('symbol', '')} {t.get('strategy', '')}"
        close_dlg.content.clear()
        with close_dlg.content:
            state["close_field"] = kit.number_field(
                close_prompt_label(t), value=0.0, min=0, format="%.2f", width="w-40")
        close_dlg.open()

    def _confirm_close():
        t, field = _selected_trade(), state["close_field"]
        if not t or field is None or not field.validate():
            return False
        bus_client.request("options", {
            "type": "paper_close",
            "args": {"trade_id": t.get("trade_id"), "debit": float(field.value)},
        })
        kit.toast("info", f"Closing {t.get('symbol', '')} — the ledger updates "
                          "when the engine confirms.")

    @guard
    def _delete():
        t = _selected_trade()
        if not t:
            return
        delete_dlg.title.text = f"Delete the {t.get('symbol', '')} {t.get('strategy', '')}?"
        delete_dlg.open()

    def _confirm_delete():
        t = _selected_trade()
        if not t:
            return
        bus_client.request("options", {"type": "paper_delete",
                                       "args": {"trade_id": t.get("trade_id")}})
        kit.toast("info", f"Deleting {t.get('symbol', '')} — the row clears when "
                          "the engine confirms.")

    def _confirm_delete_closed():
        bus_client.request("options", {"type": "paper_delete_closed"})
        kit.toast("info", "Deleting every closed trade — the ledger updates when "
                          "the engine confirms.")
```

- `_analyze`: after the request, replace the toast and status lines with `kit.set_busy(analyze_btn)`. In `_maybe_repaint`'s analyze branch, before `_show_analyze_popup(res)`, add `kit.set_busy(analyze_btn, False)`. Delete the `status.text = …` line there.
- Rewrite `_show_analyze_popup(res)` to fill the page-level dialog instead of building one:

```python
    def _show_analyze_popup(res):
        res = res or {}
        action = res.get("action", "—")
        analyze_dlg.title.text = f"{res.get('symbol', '')} · Trade analysis"
        analyze_dlg.content.clear()
        with analyze_dlg.content:
            ui.label(action).classes(
                f"text-weight-bold px-2 py-1 rounded-[6px] {verdict_class(action)} "
                "text-[#111] w-fit")
            if res.get("rationale"):
                ui.label(res["rationale"]).classes("text-sm")
            if res.get("note"):
                ui.label(res["note"]).classes(f"text-sm {MUTED}")
            rows = analyze_popup_rows(res)
            if rows:
                with ui.column().classes("w-full gap-1 pt-2"):
                    for label, text, color in rows:
                        with ui.row().classes("justify-between w-full no-wrap"):
                            ui.label(label).classes(f"text-sm {MUTED}")
                            ui.label(text).classes(
                                f"text-sm text-weight-medium text-[{color}]")
        analyze_dlg.open()
```

  Import `MUTED` from `.theme`.

**Step 4: Run** `cd webgui && $PY -m pytest tests/test_options_paper.py tests/test_no_inline_style.py -q`, then the guard: set `options/paper.py`'s `ALLOWED` entry to the reported actual counts. It should be none, so delete the entry. Expected: PASS.

**Step 5: Commit**

```bash
git add webgui/pages/options/paper.py webgui/page_help.py webgui/tests/test_options_paper.py webgui/tests/test_ui_kit_guard.py
git commit -m "feat(paper): Paper Ledger on the page kit

Header line with Updated stamp and page actions; the selected trade's buttons
in its panel (danger left, Close trade right); Delete and Delete all closed now
ask first. The actions column left the table (its Expected Move is in the
panel), so test_options_paper's column assertion flips.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: Captured Signals

**Files:** Modify `webgui/pages/options/captured.py`, `webgui/page_help.py` (the /options/captured help must keep "Reprice now"; replace "Close selected" with "Close signal" and "Reload" with "Refresh" where it names them); Test `webgui/tests/test_options_captured.py`, `webgui/tests/test_ui_kit_guard.py`

**Step 1: Failing tests** (append; flip `assert "actions" in fields` at line ~92 to `not in`):

```python
import inspect


def test_captured_row_actions_live_in_the_panel_footer():
    src = inspect.getsource(captured.render)
    assert "detail_panel.actions" in src and "Close signal" in src
    assert "Select a signal first" not in src
    assert 'kit.header("Captured Signals", view="options:captured")' in src


def test_reload_and_reprice_say_different_things():
    """The overlay said "Repricing…" for Reload too."""
    src = inspect.getsource(captured.render)
    assert 'busy.show("Refreshing the signals…")' in src
    assert 'busy.show("Repricing…")' in src


def test_the_selected_row_uses_the_kit_accent_not_its_own():
    src = inspect.getsource(captured.render)
    assert "border-[#42a5f5]" not in src
    assert "kit.mark_selected" in src
```

**Step 2: Run** — Expected: FAIL.

**Step 3: Implement.**
- In `captured_columns()`, delete the `actions` append. Delete `CAPTURED_CSS`. Add `from pages import ui_kit as kit`, and `_NUMERIC = ("credit", "current_value", "max_loss", "unrealized_pnl")`.
- Replace `render()` from its start through `detail_panel = detail.render()` with:

```python
def render():
    """Captured Signals: header line, the signals table with its day footer,
    and the shared detail panel whose footer closes the selected signal."""
    raw_by_id: dict = {}
    state = {"sel_id": None, "close_fields": None}

    with kit.page():
        head = kit.header("Captured Signals", view="options:captured")
        with head.actions:
            refresh_btn = kit.button("Refresh", kind="secondary", icon="refresh",
                                     on_click=lambda: _reload())
            reprice_btn = kit.button("Reprice now", kind="primary",
                                     icon="published_with_changes",
                                     on_click=lambda: _reprice())
        status = kit.status_line()
        with ui.row().classes("w-full no-wrap gap-4 items-start"):
            box = kit.region("Refreshing the signals…", classes="flex-grow min-w-0")
            with box.content:
                table = kit.table(captured_columns(), numeric=_NUMERIC)
                # The day footer sits UNDER the table but inside the region, so
                # a reprice's spinner covers it too - its figures are as stale
                # as the marks above them while a reprice runs.
                foot = ui.row().classes(
                    "w-full flex-wrap items-start gap-x-10 gap-y-2 px-2 pt-2 mt-1 "
                    f"border-t border-[{THEME['palette']['card_border']}]")
            detail_panel = detail.render()
```

  Import `THEME` from `.theme`.
- Keep the symbol, current-value and unrealized slots. In the `body-cell-recommendation` slot, drop the `:class="props.row._selected ? …"` attribute from `<q-td>`: the row accent is the kit's now. Delete `_apply_selection` and use `kit.mark_selected(table.rows, state.get("sel_id"))` in both `_populate` and `_select`.
- Delete `table_busy = _busy.build_busy(...)`. `_populate` becomes `box.busy.hide()`, `kit.set_busy(refresh_btn, False)` and `kit.set_busy(reprice_btn, False)`. `status.text` becomes `f"{len(table.rows)} open signal{'s' if len(table.rows) != 1 else ''}"` when `cap`, else `""`.
- Footer and dialog:

```python
    with detail_panel.actions:
        kit.button("Expected Move", kind="secondary", icon="show_chart",
                   on_click=lambda: _send_em())
        kit.button("Close signal", kind="primary", icon="check_circle",
                   on_click=lambda: _close())

    close_dlg = kit.confirm("Close signal", confirm_text="Close signal",
                            on_confirm=lambda: _confirm_close())
```

- Replace `handoff.add_expected_move_action(...)` with a `_send_em()` like the Paper Ledger's, using `synth_from_captured`. `_selected_signal()` stays as it is. `_reload` and `_reprice` each send their request, then call `box.busy.show("Refreshing the signals…")` or `box.busy.show("Repricing…")`, plus `kit.set_busy(<their button>)`. Both drop their toasts and status text.
- `_close` / `_confirm_close`:

```python
    @guard
    def _close():
        sig = _selected_signal()
        if not sig:
            return
        close_dlg.title.text = f"Close {sig.get('symbol', '')} {sig.get('strategy', '')}"
        close_dlg.content.clear()
        with close_dlg.content:
            # The current mark, when repriced; the reader can still override it.
            exit_val = kit.number_field("Exit value (spread debit)",
                                        value=exit_value_default(sig), min=0,
                                        format="%.2f", width="w-40")
            reason = kit.text_field("Reason", value="MANUAL_CLOSE", width="w-40")
        state["close_fields"] = (sig.get("signal_id"), exit_val, reason)
        close_dlg.open()

    def _confirm_close():
        signal_id, exit_val, reason = state["close_fields"] or (None, None, None)
        if not signal_id or not exit_val.validate():
            return False
        bus_client.request("options", {
            "type": "captured_close",
            "args": {"signal_id": signal_id, "exit_val": float(exit_val.value),
                     "reason": reason.value or "MANUAL_CLOSE"},
        })
        kit.toast("info", "Closing the signal — the list updates when the engine "
                          "confirms.")
```

- In `_maybe_repaint`, replace the flags `ui.notify(..., type="warning")` with `kit.toast("warn", f"{f.get('symbol')}: {f.get('code')} — consider closing")`.

**Step 4: Run** `cd webgui && $PY -m pytest tests/test_options_captured.py tests/test_page_help.py -q`, then update the guard entry (delete it if zero). Expected: PASS.

**Step 5: Commit** (`captured.py`, `page_help.py`, the two tests):

```bash
git commit -m "feat(captured): Captured Signals on the page kit

Refresh and Reprice now in the header; Close signal and Expected Move in the
selected signal's panel; the overlay names the action it waits on.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: Paper Account

**Files:** Modify `webgui/pages/options/portfolio.py`; Test `webgui/tests/test_options_paper_portfolio.py`, `webgui/tests/test_ui_kit_guard.py`

**Step 1: Failing tests** (append):

```python
import inspect

from pages.options import portfolio as pp


def test_the_account_is_built_from_the_kit():
    src = inspect.getsource(pp.render)
    assert 'kit.header("Paper Account", view="options:paper_account")' in src
    assert "kit.section_title(\"Open positions\")" in src


def test_reset_asks_first_and_is_danger():
    src = inspect.getsource(pp.render)
    assert 'confirm_text="Reset", danger=True' in src


def test_the_manage_cycle_tooltip_says_hourly_not_every_five_minutes():
    """options_svc.scheduler.paper_cycle_due runs the manual account hourly,
    09:00-14:00 CT on trading days; the tooltip said every 5 min."""
    src = inspect.getsource(pp.render)
    assert "every 5 min" not in src
    assert "hourly" in src
```

**Step 2: Run** — Expected: FAIL.

**Step 3: Implement.** Add `from pages import ui_kit as kit`. Replace everything in `render()` down to and including the `ui.label("Analytics")…` description line with:

```python
    with kit.page():
        head = kit.header("Paper Account", view="options:paper_account")
        with head.actions:
            kit.button("Reset", kind="danger", icon="restart_alt",
                       tooltip="Reset the paper account to a starting balance.",
                       on_click=lambda: _reset())
            refresh_btn = kit.button("Refresh", kind="secondary", icon="refresh",
                                     on_click=lambda: _reload())
            entry_btn = kit.button(
                "Run entry cycle", kind="secondary", icon="login",
                tooltip="Scan open captured signals now and open paper positions "
                        "for the eligible ones.",
                on_click=lambda: _cycle("entry"))
            manage_btn = kit.button(
                "Run manage cycle", kind="primary", icon="manage_accounts",
                tooltip="Reprice open positions and close any that hit their target "
                        "or stop. Runs automatically hourly, 09:00-14:00 CT on "
                        "trading days; this runs it now.",
                on_click=lambda: _cycle("manage"))
        status = kit.status_line()
        account = kit.region("Refreshing the account…")
        with account.content:
            cards_box = ui.row().classes("gap-3 flex-wrap")
            # The book's own track record (C5) and its Greeks (C4), one line each.
            scorecard_label = ui.label("").classes(f"text-xs {MUTED}")
            greeks_label = ui.label("").classes(f"text-xs {MUTED}")
            kit.section_title("Open positions")
            pos_table = kit.table(position_columns(), numeric=(
                "quantity", "entry_credit", "current_value", "unrealized_pnl"))
            kit.section_title("Fills log (last 100)")
            ord_table = kit.table(order_columns(), row_key="order_id",
                                  numeric=("quantity", "fill_price"))
        kit.section_title("Analytics")
        ui.label("Realized equity curve, and how far trades ran for and against "
                 "before closing (MAE/MFE), for the manual (scanner-baseline) "
                 "book. Compare with Claude Trades' Analytics.").classes(
                     f"text-xs {MUTED}")
```

Keep the two `add_slot` calls on `pos_table` and `ord_table` after this block, and keep the chart, `excursion_label` and `analytics_empty` lines, changing each `opacity-*` class to `MUTED` (import it from `.theme`). Delete the old status row under the fills table and delete `account_busy`.

In `_populate`: call `account.busy.hide()`, and `kit.set_busy(b, False)` for `refresh_btn`, `entry_btn` and `manage_btn`. Set `status.text = f"{len(pos_table.rows)} open positions · {len(ord_table.rows)} fills"` (or `""` when there is no payload). Replace the account tiles with:

```python
                for label, value in account_cards(snap):
                    with ui.column().classes(f"{CARD} min-w-[110px] gap-0 py-2"):
                        ui.label(label).classes(EYEBROW)
                        ui.label(value).classes(f"text-base font-semibold {LABEL}")
```

and the "No paper account yet" label with `kit.empty("No paper account yet. Use Reset to start one.")`.

- `_reload`: send the request, call `account.busy.show("Refreshing the account…")` and `kit.set_busy(refresh_btn)`, and drop the toast. `_cycle(kind)`: send the request, call `kit.set_busy(manage_btn if kind == "manage" else entry_btn)`, then `kit.toast("info", f"Running the {kind} cycle — the account updates when it finishes.")`.
- `_reset`: use a page-level dialog built once after the layout:

```python
    reset_dlg = kit.confirm("Reset the paper account?",
                            "Every position and fill is cleared, and the account "
                            "starts again at this balance.",
                            confirm_text="Reset", danger=True,
                            on_confirm=lambda: _confirm_reset())
    with reset_dlg.content:
        balance = kit.number_field("Starting balance", value=25000.0, min=1,
                                   format="%.2f", width="w-40")

    def _reset():
        reset_dlg.open()

    def _confirm_reset():
        if not balance.validate():
            return False
        bus_client.request("options", {"type": "paper_reset",
                                       "args": {"starting_balance": float(balance.value)}})
        kit.toast("info", "Resetting the paper account — the book clears when the "
                          "engine confirms.")
```

**Step 4: Run** `cd webgui && $PY -m pytest tests/test_options_paper_portfolio.py -q`, then update the guard. Expected: PASS.

**Step 5: Commit**

```bash
git add webgui/pages/options/portfolio.py webgui/tests/test_options_paper_portfolio.py webgui/tests/test_ui_kit_guard.py
git commit -m "feat(paper-account): Paper Account on the page kit; fix the manage-cycle tooltip

The tooltip said the manage cycle runs every 5 minutes; it runs hourly,
09:00-14:00 CT (options_svc scheduler.paper_cycle_due).

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: Shares and Income Window

Both are read-only boards that currently draw their own title and description inside a `PAGE` + `CARD` wrapper.

**Files:** Modify `webgui/pages/options/shares.py`, `webgui/pages/options/income.py`, `webgui/page_help.py` (keep each deleted description sentence in its page's help); Test `webgui/tests/test_shares_page.py`, `webgui/tests/test_income_page.py`, `webgui/tests/test_ui_kit_guard.py`, `webgui/tests/test_no_inline_style.py` (no change needed — they stay in `OPTIONS_MATRIX_FILES`)

**Step 1: Failing tests.**

In `test_income_page.py`, append:

```python
def test_the_status_line_leaves_the_clock_to_the_header():
    """The header's Updated stamp (the view's :ts) owns the time now."""
    text = income.status_text({"candidates": [{}], "scanned_symbols": 3,
                               "ts": "2026-09-18T13:52:00+00:00"})
    assert "scanned" not in text


def test_income_is_built_from_the_kit():
    import inspect
    src = inspect.getsource(income.render)
    assert 'kit.header("Income Window", view=VIEW)' in src
    assert "calc-v2" not in src and "QUASAR_INTERNAL_CSS" not in src
```

In `test_shares_page.py`, append the matching `test_shares_is_built_from_the_kit` (expecting `kit.header("Shares", view=VIEW)`).

Any existing test asserting `"scanned HH:MM"` in `status_text` (grep `scanned` in `test_income_page.py`) pins the clock that moves to the header: change it to assert the clock is absent, and name it in the commit.

**Step 2: Run** — Expected: FAIL.

**Step 3: Implement.**

`income.status_text`: delete the three lines `ts = _short_ts(p.get("ts"))` / `if ts:` / `parts.append(f"scanned {ts}")`. If `_short_ts` is now unused, delete it and its import.

`income.render()`: replace the `ui.add_css(...)` line through `table.add_slot("body-cell-score", _SCORE_SLOT)` with:

```python
    with kit.page():
        kit.header("Income Window", view=VIEW)
        status = kit.status_line(_copy.WAITING_OPTIONS)
        board = kit.region("Loading the board…")
        with board.content:
            table = kit.table(income_columns(), numeric=(
                "dte", "credit", "capital", "roc", "yield_on_cost",
                "total_return_if_called", "pop", "breakeven", "score"))
        table.add_slot("body-cell-earnings", _EARNINGS_SLOT)
        table.add_slot("body-cell-score", _SCORE_SLOT)
```

Delete `board_busy`. Replace its `.show()` with `board.busy.show()` and its `.hide()` with `board.busy.hide()`. In `_open_result`, replace `ui.notify(message, type=tone, timeout=8000, multi_line=True)` with `kit.toast({"positive": "ok", "warning": "warn", "negative": "error"}.get(tone, "info"), message)`. The per-row wallet button stays: this page has no detail panel. Imports: add `from pages import ui_kit as kit`, and drop `PAGE`, `CARD`, `LABEL`, `EYEBROW` and `QUASAR_INTERNAL_CSS` wherever they are now unused.

`shares.render()`: apply the same shape with `kit.header("Shares", view=VIEW)`, `kit.region("Loading the share inventory…")` and `kit.table(share_columns(), numeric=("shares", "basis", "cost", "mark", "unrealized"))`, keeping the `_UNREALIZED_SLOT`.

Move the deleted description sentences into the page help. **The Shares sentence "Shares are not repriced here, so Mark and Unrealized stay blank rather than showing a number nothing measured" must survive** in `page_help.HELP_MD["/options/shares"]`. Add it if it's missing. Do the same for the Income sentence about the wallet button.

**Step 4: Run** `cd webgui && $PY -m pytest tests/test_income_page.py tests/test_shares_page.py tests/test_page_help.py tests/test_no_inline_style.py -q`, then update the guard (both entries go to zero, so delete them). Expected: PASS.

**Step 5: Commit** (the two pages, `page_help.py`, and the three tests):

```bash
git commit -m "feat(shares, income): the read-only boards on the page kit

The Income status line drops its scanned-at clock; the header's Updated stamp
carries it (test updated to match).

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 8: Opportunity Board and Flow Alerts

**Files:** Modify `webgui/pages/options/matrix.py`, `webgui/pages/options/flow.py`, `webgui/page_help.py`; Test `webgui/tests/test_options_matrix.py`, `webgui/tests/test_flow_page.py`, `webgui/tests/test_ui_kit_guard.py`

**Step 1: Failing tests.**

In `test_options_matrix.py`, **replace** `test_status_text_updated_clock_is_central` with the following. The CT conversion it guarded now lives in `ui_kit.freshness`, and `test_ui_kit.py::test_freshness_reads_central_time` covers it:

```python
def test_status_text_leaves_the_clock_to_the_header():
    text = matrix.status_text({"rows": [{}], "session_date": "2026-07-20",
                               "ts": "2026-07-20T22:03:00+00:00"})
    assert "updated" not in text
    assert "session 2026-07-20" in text


def test_the_board_is_a_scheduled_view_with_a_stale_stamp():
    import inspect
    src = inspect.getsource(matrix.render)
    assert 'kit.header("Opportunity Board", view=VIEW, stale=True)' in src
```

In `test_flow_page.py`, append:

```python
def test_a_row_click_no_longer_navigates_the_symbol_is_the_link():
    """The standard: a page without a detail panel links the SYMBOL cell and
    leaves the row click alone."""
    import inspect
    from pages.options import flow
    src = inspect.getsource(flow.render)
    assert '"rowClick"' not in src
    assert "GAMMA_EVENT" in src and "gamma_symbol_slot" in src


def test_the_symbol_link_is_only_drawn_where_it_can_go():
    from pages.options import flow
    assert "@click" in flow.gamma_symbol_slot(True)
    assert "@click" not in flow.gamma_symbol_slot(False)
```

**Step 2: Run** — Expected: FAIL.

**Step 3: Implement.**

`matrix.status_text`: delete the `ts = _short_ts(...)` / `if ts:` / `parts.append(f"updated {ts}")` lines. Delete `_short_ts` if it is now unused.

`matrix.render()`: replace the layout block (from `ui.add_css(QUASAR_INTERNAL_CSS)` through the last `table.add_slot`) with:

```python
    linked = _shell.can_navigate(DOSSIER_ROUTE)
    with kit.page():
        kit.header("Opportunity Board", view=VIEW, stale=True)
        with ui.row().classes("w-full items-center gap-2 flex-wrap"):
            status = kit.status_line(_copy.WAITING_OPTIONS)
            ui.space()
            ui.label("Signals").classes(EYEBROW + " pr-1")
            buy_chip = ui.label("Buy 0").classes(_SUM_BUY_CLASS)
            neutral_chip = ui.label("Neutral 0").classes(_SUM_NEUTRAL_CLASS)
            sell_chip = ui.label("Sell 0").classes(_SUM_SELL_CLASS)
        board = kit.region("Loading the board…")
        with board.content:
            table = kit.table(matrix_columns(), row_key="symbol", numeric=(
                "spot", "day_pct", "pc_ratio", "net_prem_m", "n_signals",
                "n_alerts", "hotness"))
    table.add_slot("body-cell-symbol", symbol_slot(linked))
    if linked:
        table.on(DOSSIER_EVENT, _open_dossier)
    table.add_slot("body-cell-signal_label", _SIGNAL_SLOT)
    table.add_slot("body-cell-day_pct", _DAYPCT_SLOT)
    table.add_slot("body-cell-trend", _TREND_SLOT)
    table.add_slot("body-cell-call_accel_disp", _CALL_SLOT)
    table.add_slot("body-cell-put_accel_disp", _PUT_SLOT)
    table.add_slot("body-cell-gex_regime", _REGIME_SLOT)
```

Replace `board_busy.show/hide` with `board.busy.show/hide`. The description sentence ("Every watchlist symbol on one row … click a symbol to open its dossier") moves to `page_help.HELP_MD["/options/matrix"]` if it isn't already there.

`flow.py`: add a module-level link slot mirroring `matrix._SYMBOL_LINK_SLOT`:

```python
GAMMA_EVENT = "open_gamma"
_GAMMA_LINK_CLASS = ("cursor-pointer underline decoration-dotted underline-offset-4 "
                     "hover:decoration-solid")


def gamma_symbol_slot(linked):
    """The symbol cell: a link to that symbol's Dealer Positioning where the
    route exists, else plain text (the public screen). PURE."""
    if not linked:
        return '<q-td :props="props">{{ props.value }}</q-td>'
    return (
        '<q-td :props="props">'
        f'<span class="{_GAMMA_LINK_CLASS}" '
        f"@click.stop=\"() => $parent.$emit('{GAMMA_EVENT}', props.row.symbol)\">"
        "{{ props.value }}<q-tooltip>Open Dealer Positioning</q-tooltip></span>"
        "</q-td>")
```

In `flow.render()`, replace the layout (from `ui.add_css(QUASAR_INTERNAL_CSS)` to the last `add_slot`) with:

```python
    from .handoff import GAMMA_ROUTE
    linked = _shell.can_navigate(GAMMA_ROUTE)
    with kit.page():
        kit.header("Flow Alerts", view=VIEW)
        with kit.control_bar():
            kind_sel = kit.select_field("Alert type", dict(_KIND_LABEL),
                                        value=list(_KIND_LABEL), multiple=True,
                                        width="w-72").props("use-chips")
            symbol_sel = kit.select_field("Symbol", ["All"], value="All", width="w-40")
        status = kit.status_line(_copy.WAITING_OPTIONS)
        region = kit.region("Loading today's alerts…")
        with region.content:
            table = kit.table(flow_columns(), numeric=("share",))
    table.add_slot("body-cell-symbol", gamma_symbol_slot(linked))
    table.add_slot("body-cell-side", _TONE_SLOT)
    table.add_slot("body-cell-text", _TONE_SLOT)
    table.add_slot("body-cell-share", _SHARE_SLOT)
    if linked:
        table.on(GAMMA_EVENT, lambda e: send_to_gamma(e.args))
```

Import `shell as _shell` at the top of the function, next to `bus_client`. Delete `_on_row_click` and its `table.on("rowClick", …)` line. Replace `table_busy` with `region.busy`. Delete the `CARD`, `LABEL`, `PAGE` and `QUASAR_INTERNAL_CSS` imports if they are now unused.

**Step 4: Run** `cd webgui && $PY -m pytest tests/test_options_matrix.py tests/test_flow_page.py tests/test_page_help.py -q`, then update the guard (delete both entries at zero). Expected: PASS. If a test in `test_flow_page.py` asserts that a row click calls `send_to_gamma`, update it to drive the `GAMMA_EVENT` instead: the behaviour moved from the row to the symbol.

**Step 5: Commit** (both pages, `page_help.py`, the tests):

```bash
git commit -m "feat(matrix, flow): Opportunity Board and Flow Alerts on the page kit

The board's status loses its clock (header stamp, stale-aware); Flow Alerts'
filters sit in a control bar, and the symbol - not the whole row - links to
Dealer Positioning.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 9: Market Scanner

**Files:** Modify `webgui/pages/options/scanner.py`; Test `webgui/tests/test_options_scanner.py`, `webgui/tests/test_ui_kit_guard.py`

**Step 1: Failing tests.** Replace `test_status_line_has_time_count_and_cadence` with the version below. The "Last scan" clock moves to the header, whose CT conversion `ui_kit.freshness` tests. Change the existing `fields[-1] == "actions"` and `names.index("stale_since") < names.index("actions")` assertions (lines ~500 and ~747) to assert `"actions" not in` the column names, and name that in the commit:

```python
def test_status_line_has_count_and_cadence_and_leaves_the_clock_to_the_header():
    out = scanner.status_line({
        "signals_0dte": [{}], "signals_swing": [{}, {}],
        "timestamp": "2026-06-15T13:32:00-05:00"})
    assert "Last scan" not in out
    assert "3 live signals" in out
    assert "auto-scans every 15 min" in out


def test_the_scanner_is_built_from_the_kit():
    import inspect
    src = inspect.getsource(scanner.render)
    assert 'kit.header("Market Scanner", view="options:scan", stale=True)' in src
    assert "detail_panel.actions" in src
    assert "add_row_actions" not in src and "add_strategy_row_actions" not in src
    assert "kit.info_dialog(FUNNEL_TITLE)" in src
```

**Step 2: Run** — Expected: FAIL.

**Step 3: Implement.**
- `status_line`: delete the `when = _short_time(...)` / `if when:` / `parts.append(f"Last scan {when}")` lines. Keep `_short_time` only if something else still uses it.
- `signal_columns()`: return `cols` without `+ [_actions_col()]`. In `directional_columns()`, drop the trailing `_actions_col()`. Delete `_actions_col`.
- In `render()`, replace `_table(columns)` with:

```python
    def _table(columns):
        t = kit.table(columns, rows_per_page=_TABLE_PAGINATION["rowsPerPage"],
                      numeric=("dte", "credit", "max_loss", "rr_pct", "pop_pct",
                               "iv_rank", "composite_score"),
                      classes="w-full scan-table")
        return t
```

  Delete `_ROW_CLASS_PROP` and its comment. `kit.ROW_CLASS_FN` already composes `row._row_class` (the stale dimming) with the selection. Also delete any test that pins `_ROW_CLASS_PROP` by name. A test that pins the **binding** of `_row_class` should assert it through `kit.ROW_CLASS_FN` instead.
- Replace the layout from `with ui.row().classes("w-full no-wrap gap-4 items-start"):` through `detail_panel = detail.render(width=290)` with:

```python
    with kit.page():
        head = kit.header("Market Scanner", view="options:scan", stale=True)
        with head.actions:
            why_btn = kit.button(FUNNEL_TITLE, kind="quiet", icon="help_outline")
            scan_btn = kit.button("Run scan", kind="primary", icon="play_arrow")
        with ui.row().classes("w-full items-center gap-3"):
            status = kit.status_line()
            ui.space()
            clear_toggle = ui.switch("Only clear", value=False)
            with clear_toggle:
                ui.tooltip(_ONLY_CLEAR_TIP).props("delay=350")
        day_note_box = ui.row().classes("w-full")    # not day_note: that is the module's function
        with ui.row().classes("w-full no-wrap gap-4 items-start"):
            scan = kit.region("Scanning…", classes="flex-grow min-w-0")
            with scan.content:
                scan_panels = ui.tab_panels(tabs, value=tab_0dte).classes(
                    "w-full scan-panels")
                with scan_panels:
                    with ui.tab_panel(tab_0dte):
                        table_0dte = _table(signal_columns())
                    with ui.tab_panel(tab_swing):
                        table_swing = _table(signal_columns())
                    with ui.tab_panel(tab_dir):
                        table_dir = _table(directional_columns())
            # Narrower than the 360px default so the signal table keeps its columns.
            detail_panel = detail.render(width=290)
```

  Delete the old `status` / `day_msg` labels and `scan_busy`.
- Selection and footer. After the slots:

```python
    sel = {"sig": None, "multi": False, "allow_paper": False, "id": None}

    with detail_panel.actions:
        kit.button("Expected Move", kind="secondary", icon="show_chart",
                   on_click=lambda: _send_em())
        kit.button("Calculator", kind="secondary", icon="calculate",
                   on_click=lambda: _send_calc())
        paper_btn = kit.button("Paper trade", kind="primary", icon="request_quote",
                               on_click=lambda: _send_paper())

    def _remember(event, sig, multi):
        row = event.args[1] if isinstance(event.args, list) and len(event.args) > 1 else event.args
        sel.update(sig=sig, multi=multi, id=(row or {}).get("id"),
                   allow_paper=bool((row or {}).get("_allow_paper")))
        paper_btn.set_visibility(sel["allow_paper"])
        _paint_tables()                      # re-stamps _selected

    @guard
    def _send_em():
        if sel["sig"]:
            handoff.send_to_expected_move(handoff.signal_to_em_payload(sel["sig"]))

    @guard
    def _send_calc():
        if sel["sig"]:
            (handoff.send_signal_to_calculator if sel["multi"]
             else handoff.send_to_calculator)(sel["sig"])

    @guard
    def _send_paper():
        if sel["sig"] and sel["allow_paper"]:
            handoff.send_to_paper(sel["sig"])
```

  In `_select`, call `_remember(event, sig, False)` after `detail_panel.update(...)`. In `_select_dir`, call `_remember(event, sig, True)`. Delete the `handoff.add_row_actions(...)` and `handoff.add_strategy_row_actions(...)` calls. `handoff.watch_paper_results()` stays. In `_paint_tables`, after `shown = …`, add `kit.mark_selected(shown, sel["id"])`.
- `_apply_populate`: replace `scan_busy.hide()` with `scan.busy.hide()` and `kit.set_busy(scan_btn, False)`. Replace the `day_msg` lines with:

```python
        day_note_box.clear()
        note = day_note(built["day_env"], today)
        if note:
            with day_note_box:
                kit.notice(note, icon="warning")
```

  Replace the warnings loop's `ui.notify(w, type="warning")` with `kit.toast("warn", w)`.
- `_request_scan`: after the request, call `scan.busy.show()` and `kit.set_busy(scan_btn)`. Drop the toast.
- The funnel dialog: replace the `with ui.dialog() as funnel_dlg, ui.card()…:` block with:

```python
    funnel = kit.info_dialog(FUNNEL_TITLE)
    with funnel.content:
        ui.label(FUNNEL_LEAD).classes(f"text-xs {MUTED}")
        funnel_chips_box = ui.row().classes("gap-2 items-center flex-wrap")
        funnel_sel = kit.select_field("Symbol", [], width="w-56", with_input=True)
        funnel_status = ui.label(FUNNEL_LOADING).classes(f"text-sm {MUTED}")
        funnel_box = ui.column().classes("w-full gap-3")
```

  Then use `funnel.open()` where the code opened `funnel_dlg`.

**Step 4: Run** `cd webgui && $PY -m pytest tests/test_options_scanner.py tests/test_options_page.py -q`, then update the guard. Expected: PASS.

**Step 5: Commit**

```bash
git add webgui/pages/options/scanner.py webgui/tests/test_options_scanner.py webgui/tests/test_ui_kit_guard.py
git commit -m "feat(scanner): Market Scanner on the page kit

Run scan and Why no trade? in the header with a stale-aware Updated stamp;
Calculator / Paper trade / Expected Move move from per-row icons to the
selected row's panel (actions column assertions flip); the status line drops
its Last-scan clock.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 10: Rescue

**Files:** Modify `webgui/pages/options/rescue.py`; Test `webgui/tests/test_options_rescue*.py` (append to the existing Rescue page test file), `webgui/tests/test_ui_kit_guard.py`

**Step 1: Failing tests:**

```python
import inspect

from pages.options import rescue


def test_the_rescue_menu_spinner_is_not_inside_what_the_repaint_clears():
    """board_busy was built INSIDE cards_col, and every row click cleared
    cards_col before showing it - so the spinner was deleted on first use."""
    src = inspect.getsource(rescue.render)
    assert "build_busy(cards_col" not in src
    assert "build_busy(adhoc_cards_col" not in src
    assert "advisory.busy" in src and "adhoc_advice.busy" in src


def test_one_loading_indicator_not_two():
    src = inspect.getsource(rescue.render)
    assert "ui.spinner(" not in src


def test_apply_asks_with_cancel_first_via_the_kit():
    src = inspect.getsource(rescue.render)
    assert "apply_dlg = kit.confirm(" in src


def test_the_adhoc_symbol_uses_the_one_symbol_field():
    assert "kit.symbol_field(" in inspect.getsource(rescue.render)
```

**Step 2: Run** — Expected: FAIL.

**Step 3: Implement.** Add `from pages import ui_kit as kit`. Delete `_RESCUE_CSS` and its injection.
- `waiting = ui.label(_copy.WAITING_OPTIONS)…` becomes `waiting = kit.empty(_copy.WAITING_OPTIONS)`. Put `kit.header("Rescue")` above it. The page has no view stamp: the at-risk board merges two views on demand.
- **Board tab.** The at-risk table becomes `at_risk_tbl = kit.table(at_risk_columns(), numeric=("short_delta", "pnl", "heat"))`, keeping its three slots. `at_risk_empty` becomes `kit.empty("No tested or critical positions right now.")`. The right column becomes:

```python
                with ui.column().classes("min-w-0 grow-[2] shrink basis-0 gap-2"):
                    advisory_head = kit.section_title(
                        "Select an at-risk position to see rescue options.")
                    payoff_chart = ui.highchart(_payoff_figure(None)).classes("w-full")
                    payoff_chart.set_visibility(False)
                    advisory = kit.region("Building the rescue menu…")
                    cards_col = advisory.content
```

  Delete `advisory_spinner` and `board_busy`, and every `set_visibility` on the spinner. In `_select`, replace `advisory_spinner.set_visibility(True)` / `board_busy.hide()` / `board_busy.show()` with `cards_col.clear()` then `advisory.busy.show()`. In `_poll_board_advisory`, replace `advisory_spinner.set_visibility(False)` with `advisory.busy.hide()`. Add `kit.mark_selected(at_risk_tbl.rows, rid)` and `at_risk_tbl.update()` in `_select`, and re-stamp in `_render_at_risk` using `state["board_id"]`.
- **Ad-hoc tab** control bar:

```python
                    with kit.control_bar():
                        with kit.field("Strategy"):
                            adhoc_strat = build_strategy_menu(
                                value="PCS", classes="w-52", boxed=True,
                                exclude=_strategies.STOCK_STRATEGIES)
                        adhoc_sym = kit.symbol_field(on_load=lambda: _adhoc_load(),
                                                     width="w-40")
                        adhoc_load_btn = kit.button("Load", kind="primary",
                                                    icon="cloud_upload",
                                                    on_click=lambda: _adhoc_load())
                        adhoc_exp_sel = kit.select_field("Expiry", [], width="w-44")
                        adhoc_contracts = kit.number_field("Contracts", value=1,
                                                           min=1, max=100,
                                                           integer=True)
                    adhoc_status = kit.status_line(
                        "Pick a strategy, load a symbol, then set the legs.")
                    adhoc_leg_box = ui.column().classes("gap-2 w-full")
                    adhoc_compute_btn = kit.button("Compute rescue options",
                                                   kind="primary", icon="healing")
```

  Keep the stock-structures comment above `build_strategy_menu`. If `build_strategy_menu` takes a `caption` argument, pass `caption=False`, since `kit.field` supplies the label. Delete the later `bind_symbol_load(adhoc_sym, _adhoc_load)` and `adhoc_load_btn.on_click(_adhoc_load)`: `kit.symbol_field` and the button's `on_click` do that work now. The right column becomes `adhoc_head = kit.section_title(...)` and `adhoc_advice = kit.region("Building the rescue menu…")` with `adhoc_cards_col = adhoc_advice.content`. Delete `adhoc_spinner` and `adhoc_busy`, and mirror the board's show/hide changes in `_adhoc_compute` and `_poll_adhoc_advisory`.
- **Candidate cards** (`_render_one_card`):
  - `ui.card().classes("w-full")` becomes `ui.column().classes(f"{CARD} w-full gap-2")`.
  - The score badge `.props("color=primary")` becomes `.classes(BADGE_ACCENT)`, a context badge `.props("outline color=grey")` becomes `.classes(BADGE_MUTED)`, and a warning badge `.props("color=red")` becomes `.classes(BADGE_NEG)`.
  - The Apply button is `kit.button("Apply", kind="primary", icon="play_arrow", on_click=apply_factory(card))`.
  - `opacity-70` / `opacity-80` become `MUTED`.
- **Apply confirm**, built once after the tabs:

```python
    apply_dlg = kit.confirm(
        "Apply this rescue?",
        "This adjusts your paper position. No real money, and no live order is placed.",
        confirm_text="Apply", on_confirm=lambda: _confirm_apply())
    pending = {"candidate": None}

    def _confirm_apply_factory(candidate):
        @guard
        def _open():
            pending["candidate"] = candidate
            apply_dlg.title.text = f"Apply rescue: {candidate.get('title') or 'this action'}?"
            apply_dlg.open()
        return _open

    def _confirm_apply():
        candidate = pending["candidate"]
        if not candidate:
            return
        adv = state["board_advisory"] or {}
        bus_client.request("options", {"type": "rescue_apply", "args": {
            "position_id": adv.get("position_id") or state["board_id"],
            "candidate": candidate.get("_raw") or {}}})
        kit.toast("info", "Applying the rescue — the result appears here.")
        advisory.busy.show("Applying…")
```

  Pass `_confirm_apply_factory` wherever `_confirm_apply` was passed as the factory.
- **Toasts.** In `_notify_apply_result`, `type="positive"` becomes `kit.toast("ok", "Rescue applied.")`, the stale branch becomes `"warn"`, and the failure branch becomes `"error"`. In `_adhoc_load` and `_adhoc_compute`, `ui.notify(..., type="warning")` becomes `kit.toast("warn", ...)`. Also convert the remaining `ui.notify` calls in `_adhoc_unsupported` the same way.

**Step 4: Run** `cd webgui && $PY -m pytest tests/ -q -k rescue`, then update the guard. `options/rescue.py` should reach zero, so delete its entry. Expected: PASS.

**Step 5: Commit**

```bash
git add webgui/pages/options/rescue.py webgui/tests/test_options_rescue*.py webgui/tests/test_ui_kit_guard.py
git commit -m "feat(rescue): Rescue on the page kit; the rescue-menu spinner survives

The spinner was built inside the cards column and each row click cleared that
column before showing it, so it never appeared. One region per menu now, one
indicator, Apply through the kit confirm.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 11: Full suite, guard and help text

**Step 1:** `cd webgui && $PY -m pytest -q -p no:randomly -rfs`. Expected: the Phase 0 baseline sets. Every change to an old assertion is one of this plan's deliberate changes: status clocks, the `actions` column, a row-click navigation, and the fake-`ui` dialog tests. Any other failure is a regression, so fix the code.

**Step 2:** Confirm the guard's `ALLOWED` has **no** entry for `options/paper.py`, `options/captured.py`, `options/portfolio.py`, `options/shares.py`, `options/income.py`, `options/matrix.py`, `options/flow.py`, `options/handoff.py` or `options/rescue.py`.

`options/scanner.py` keeps nothing either: its only `ui.` controls are now the switch and the tabs, which the guard does not count. `options/detail.py` keeps `{"button": 1}`, the collapse toggle, because it carries a floating flag badge and a retitled tooltip. Add this comment above that entry:

```python
    # The panel's collapse toggle: carries a floating flag badge and a tooltip
    # retitled on every toggle, which kit.icon_button does not model. Not an
    # action button.
```

**Step 3:** grep `webgui/page_help.py` for the retired names on these routes: `Reload`, `Close selected`, `Run scan` (it stays), `click a row` and `every 5 min`. Every help text must name the buttons the screens now show. Run `$PY -m pytest tests/test_page_help.py -q`.

**Step 4: Commit** any fixes with explicit paths.

---

### Task 12: See it in the harness

Seed the fake bus so each board has rows. Build `C:/Users/john_/AppData/Local/Temp/claude/…/scratchpad/phase1-seed.json` (any scratch path) with one realistic payload per view. **Take the shapes from the existing page tests' fixtures** (e.g. `test_options_paper.py`'s sample trades, `test_options_matrix.py`'s rows, `test_income_page.py`'s `_PCS` / `_CSP`) rather than inventing them. Use:

```json
{"options:paper_trades": {"trades": [ ...two OPEN, one CLOSED... ]},
 "options:matrix": {"rows": [ ... ], "session_date": "2026-09-19", "ts": "..."},
 "options:income": {"candidates": [ ... ], "scanned_symbols": 23},
 "options:flow_alerts": {"alerts": [ ... ]}}
```

Temporarily add a `ui-harness` entry to `.claude/launch.json`, as in Phase 0 Task 19, with `runtimeArgs` of `["tools/ui_harness.py", "options.paper", "--seed", "<seed path>"]`. Then check each page:

1. **Paper Ledger.** The header reads "Paper Ledger" with an Updated stamp and `Delete all closed` then `Refresh` on the right. Click a row: it gains the accent edge, and the panel footer shows `Delete … Expected Move · Analyze · Close trade`. Delete opens a dialog whose buttons read `Cancel`, `Delete`, with Delete solid red. Press Esc.
2. **options.matrix.** The header shows an Updated stamp. The Buy/Neutral/Sell chips sit right of the status line. Numbers are right-aligned.
3. **options.flow.** The filters sit in a card. Clicking a row does nothing. The symbol is a dotted link.
4. **options.income** and **options.shares.** Title and stamp, no description line.
5. **options.scanner** (seed `options:scan_day` and `options:scan` from `test_options_scanner.py` fixtures). Run scan is rightmost. Why no trade? is quiet text. Selecting a row shows `Expected Move · Calculator · Paper trade` in the panel, and no icon column.
6. **options.rescue** (seed `options:paper_account` with a position whose `rescue_state` is `tested`). Click the row: the menu region shows its spinner, then the cards appear.

Take one screenshot of the Paper Ledger with a row selected. Then revert `.claude/launch.json` (`git checkout -- .claude/launch.json`) and delete the seed file.

---

### Task 13: Documentation

**Files:** `docs/CHANGELOG.md`, `docs/webgui-routes.md`, `docs/manuals/user-guide/user-guide.md`, `docs/manuals/reference-guide/reference-guide.md`

**Step 1:** Add a CHANGELOG entry at the top, moving the previous **Last updated** to **Prior —**. Title it "One look and one behaviour — Phase 1: the Options boards". Its bullets:

- The nine pages.
- Row actions, including the sends, now live in the detail panel.
- Paper Ledger's two deletes now confirm.
- The Paper Account tooltip is fixed (hourly, not every 5 min).
- The Rescue spinner fix.
- Status lines lost their clocks to the header stamp.
- Flow Alerts' symbol is now the link.

**Step 2:** In `docs/webgui-routes.md`, correct each of the nine routes' descriptions where they name a button's position, a per-row icon or a row click, in place, per the maintenance rule.

**Step 3: Manuals.** grep both manuals for `Reload`, `Close selected`, `icon`, `per-row`, `click a row`, `right-click` and `every 5 min` on these pages. Rewrite each sentence to the new placement:

- **Refresh** is at the top right.
- **Click a row, then use the buttons at the bottom of its panel.**
- **Delete asks first.**

Rebuild the two manuals with `cd docs/manuals && $PY build_docs.py user-guide` and `$PY build_docs.py reference-guide`.

**Step 4: Commit**

```bash
git add docs/CHANGELOG.md docs/webgui-routes.md docs/manuals/user-guide docs/manuals/reference-guide
git commit -m "docs: Phase 1 - the Options boards on the page kit

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Done when

- The full webgui suite matches the baseline sets, with every deliberate assertion change named in its commit.
- The guard has no entry for the nine pages or `handoff.py`. `detail.py`'s one entry carries its reason.
- The harness shows each page as in Task 12.
- **Not promoted by this plan.** After the operator promotes (15:25–16:15 CT), Claude can check `/opportunity` and `/flow` on `https://live.neuralstrike.co`, since those two are public. The other seven are private, and the operator clicks through them.

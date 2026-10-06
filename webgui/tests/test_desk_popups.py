"""The Desk's two popups, as drawn: the Market read and the Market report are
dialogs opened from the page header, each with a one-line status beside its
button. Until 2026-10-06 both were full-width panels at the foot of the page.

The Market read's own rows, words and hidden states are in
test_desk_market_read.py; the report's facts in test_desk.py.
Design: docs/plans/2026-10-06-desk-read-and-report-popups-design.md.
"""
import inspect

import pytest
from pages import desk as d

REPORT_URL = "https://neuralstrike.co/report.html"
FRAME_URL = "https://neuralstrike.co/reports/latest.html?v=2026-09-14-close"


def _summary(**over):
    base = {"headline": "A rotation, not a rout",
            "highlights": ["Chips broke; software ripped"], "slot": "close",
            "slot_label": "Market close", "report_date": "2026-09-14",
            "as_of": "16:20 CT", "report_url": REPORT_URL, "frame_url": FRAME_URL}
    base.update(over)
    return base


def _render(monkeypatch, **views):
    """Render the whole Desk; return ``(popup handles, every new element)``."""
    import test_desk as td
    from nicegui import ui
    td._seed_bus(monkeypatch, {**td._full_payloads(), **views})
    made, real = [], d.build_popups
    monkeypatch.setattr(d, "build_popups", lambda actions: made.append(real(actions)) or made[0])
    before = set(ui.context.client.elements)
    d.render()
    new = [e for k, e in ui.context.client.elements.items() if k not in before]
    (popup,) = made
    return popup, new


def _texts(elements):
    return [t for t in (getattr(e, "text", None) for e in elements) if t]


def _click(button):
    for listener in button._event_listeners.values():
        if listener.type == "click":
            listener.handler(None)


# --- the page ------------------------------------------------------------------

def test_the_desk_no_longer_draws_either_panel(monkeypatch):
    popup, new = _render(monkeypatch, **{"market:summary": _summary()})
    texts = _texts(new)
    assert "MARKET SUMMARY" not in texts
    # The highlights left with the frame: the report itself replaces them.
    assert "Chips broke; software ripped" not in texts
    # One "Market read" and one "Market report" button, plus each dialog's title.
    assert texts.count(d.READ_TITLE) == 2 and texts.count(d.REPORT_TITLE) == 2


def test_both_buttons_sit_in_the_page_header(monkeypatch):
    popup, _new = _render(monkeypatch, **{"market:summary": _summary()})
    for group in (popup.read_group, popup.report_group):
        row = group.parent_slot.parent              # the header's action row
        assert "flex-wrap" in row._classes and "no-wrap" not in row._classes
        header = row.parent_slot.parent
        assert "min-h-[38px]" in header._classes    # kit.header's own row
    buttons = [e for e in popup.read_group.descendants() if e.tag == "q-btn"] + \
        [e for e in popup.report_group.descendants() if e.tag == "q-btn"]
    assert [b.text for b in buttons] == [d.READ_TITLE, d.REPORT_TITLE]


def test_each_button_opens_its_own_dialog(monkeypatch):
    popup, _new = _render(monkeypatch, **{"market:summary": _summary()})
    read_btn = next(e for e in popup.read_group.descendants() if e.tag == "q-btn")
    report_btn = next(e for e in popup.report_group.descendants() if e.tag == "q-btn")
    assert popup.read_dialog.dialog.value is False
    assert popup.report_dialog.dialog.value is False
    _click(read_btn)
    assert popup.read_dialog.dialog.value is True
    assert popup.report_dialog.dialog.value is False
    _click(report_btn)
    assert popup.report_dialog.dialog.value is True


# --- the Market report ---------------------------------------------------------

def test_the_report_button_names_the_report(monkeypatch):
    popup, _new = _render(monkeypatch, **{"market:summary": _summary()})
    assert popup.report_group.visible is True
    assert popup.report_status.text == "Market close report · 14 Sep · 16:20 CT"


def test_the_report_dialog_frames_the_report_and_links_to_it(monkeypatch):
    popup, _new = _render(monkeypatch, **{"market:summary": _summary()})
    assert popup.report_source.text == "Market close report · 14 Sep · 16:20 CT"
    frame = popup.report_frame
    assert frame.tag == "iframe" and frame._props["src"] == FRAME_URL
    assert frame._props["title"] == d.REPORT_FRAME_TITLE
    link = popup.report_link
    assert link.text == d.REPORT_OPEN and link._props["href"] == REPORT_URL
    assert link._props.get("target") == "_blank" and link.visible is True
    # The frame is a child of the dialog, so it is not in the browser's
    # document until the dialog opens: a Desk nobody opens it on fetches nothing.
    assert popup.report_dialog.dialog in list(frame.ancestors())


@pytest.mark.parametrize("view", [None, {}, {"highlights": []},
                                  {"narrative": "Fear builds.", "inputs": {}}])
def test_with_no_report_there_is_no_report_button(monkeypatch, view):
    views = {} if view is None else {"market:summary": view}
    popup, _new = _render(monkeypatch, **views)
    assert popup.report_group.visible is False
    assert popup.report_status.text == ""
    assert popup.report_frame._props["src"] == "about:blank"


def test_an_address_that_is_not_https_is_never_framed(monkeypatch):
    bad = _summary(report_url="javascript:alert(1)", frame_url="http://x/latest.html")
    popup, _new = _render(monkeypatch, **{"market:summary": bad})
    assert popup.report_group.visible is False
    assert popup.report_frame._props["src"] == "about:blank"
    assert popup.report_link._props["href"] == "#" and popup.report_link.visible is False


def test_a_new_report_changes_the_frames_address(monkeypatch):
    """The file name never changes. The address does, which is what reloads a
    dialog left open across a new report."""
    popup, _new = _render(monkeypatch, **{"market:summary": _summary()})
    later = _summary(slot_label="Premarket", report_date="2026-09-15", as_of="07:45 CT",
                     frame_url=FRAME_URL.replace("2026-09-14-close", "2026-09-15-premarket"))
    d.paint_report(popup, later)
    assert popup.report_frame._props["src"].endswith("?v=2026-09-15-premarket")
    assert popup.report_status.text == "Premarket report · 15 Sep · 07:45 CT"
    assert popup.report_source.text == popup.report_status.text


def test_an_unchanged_report_does_not_touch_the_frame(monkeypatch):
    """A repaint that re-sent the same address would cost nothing visible, but
    one that sent a DIFFERENT one reloads the reader's page under them."""
    popup, _new = _render(monkeypatch, **{"market:summary": _summary()})
    sent = []
    monkeypatch.setattr(popup.report_frame, "update", lambda: sent.append("frame"))
    monkeypatch.setattr(popup.report_link, "update", lambda: sent.append("link"))
    d.paint_report(popup, _summary())
    assert sent == []


def test_a_report_withdrawn_while_its_dialog_is_open_closes_it(monkeypatch):
    popup, _new = _render(monkeypatch, **{"market:summary": _summary()})
    popup.report_dialog.open()
    d.paint_report(popup, None)
    assert popup.report_dialog.dialog.value is False
    assert popup.report_group.visible is False


def test_the_public_desk_gets_the_same_report_button(monkeypatch):
    import shell
    monkeypatch.setattr(shell, "is_public", lambda: True)
    popup, _new = _render(monkeypatch, **{"market:summary": _summary()})
    assert popup.report_group.visible is True
    assert popup.report_frame._props["src"] == FRAME_URL


# --- built outside render -------------------------------------------------------

def test_the_popups_are_built_and_painted_outside_render():
    """``render`` is at its size ceiling: the builder and both painters are
    module-level and wired without a nested function."""
    for name in ("build_popups", "paint_read", "paint_report", "report_facts"):
        assert callable(getattr(d, name))
    src = inspect.getsource(d.render)
    assert '"summary": lambda: paint_report(popups, _view("market:summary"))' in src
    assert "def _paint_summary" not in src


def test_the_page_hard_codes_no_site_address():
    """Tier 1 takes the report's address from the view the service publishes."""
    src = inspect.getsource(d)
    assert "reports/latest.html" not in src and "/report.html" not in src
    for fn in (d.build_popups, d.paint_report, d.report_facts):
        assert "neuralstrike" not in inspect.getsource(fn)


def test_the_popups_use_the_page_kit_not_raw_dialogs_or_buttons():
    src = inspect.getsource(d.build_popups)
    assert "kit.info_dialog(" in src and "kit.button(" in src
    assert "ui.dialog(" not in src and "ui.button(" not in src
    assert ".style(" not in src and "style=" not in src

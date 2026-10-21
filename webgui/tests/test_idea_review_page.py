"""The Weekly idea review page (webgui/pages/idea_review.py) and its file route.

The reports are written by tools/weekly_idea_review.py; the page only lists the
archive folder and frames the chosen week's report.html. Nothing here reaches
Redis: the archive is a tmp folder.
"""
import inspect

import pytest
from nicegui import ui

from pages import idea_review


def _week(root, name, body="<p>review</p>"):
    d = root / name
    d.mkdir(parents=True)
    (d / "report.html").write_text(body, encoding="utf-8")


def _texts(host):
    return [str(getattr(e, "text", "")) for e in host.descendants()]


def _render(monkeypatch, root):
    monkeypatch.setattr(idea_review, "ARCHIVE_ROOT", root)
    with ui.card() as host:
        idea_review.render()
    return host


# --- the archive ------------------------------------------------------------
def test_weeks_listed_newest_first(tmp_path):
    for name in ("2026-W09", "2026-W43", "2025-W52", "2026-W42"):
        _week(tmp_path, name)
    (tmp_path / "2026-W44").mkdir()               # no report.html: not a review
    (tmp_path / "notes").mkdir()                   # not a week
    (tmp_path / "2026-W45.html").write_text("x")   # a file, not a folder
    assert idea_review.archive_weeks(tmp_path) == [
        "2026-W43", "2026-W42", "2026-W09", "2025-W52"]


def test_a_missing_archive_folder_lists_nothing(tmp_path):
    assert idea_review.archive_weeks(tmp_path / "nope") == []


def test_week_label_names_the_days(tmp_path):
    assert idea_review.week_label("2026-W43") == "2026-W43 · 19 Oct to 23 Oct"
    # a week the regex allows but the calendar does not has no dates to show
    assert idea_review.week_label("2026-W99") == "2026-W99"


def test_the_archive_is_the_one_the_tool_writes_to():
    root = idea_review.ARCHIVE_ROOT
    assert root.parts[-3:] == ("webgui", "data", "idea_review")


def test_the_words_claim_no_more_than_the_check_proves():
    """The check behind Claude's assessment proves each figure is one the app
    computed, not that it is used with the right subject: the page note and both
    hover guides say exactly that, and none says every figure "is checked"."""
    import page_help
    weekly, eod = page_help.HELP_MD["/eod/weekly"], page_help.HELP_MD["/eod"]
    for text in (idea_review.NOTE, weekly, eod):
        flat = " ".join(text.replace("*", "").replace("(or sign) ", "").split())
        assert "is one the app computed" in flat
        assert "right subject is not checked" in flat
        assert "every figure in it is checked" not in flat
        assert "is checked against them" not in flat


# --- the page ---------------------------------------------------------------
def test_empty_archive_message(monkeypatch, tmp_path):
    host = _render(monkeypatch, tmp_path)
    assert idea_review.EMPTY_TEXT == (
        "No weekly review yet — the first one is written after the close on "
        "the last trading day of the week.")
    assert idea_review.EMPTY_TEXT in _texts(host)
    assert not [e for e in host.descendants() if e.tag == "iframe"]


def test_the_page_frames_the_newest_week(monkeypatch, tmp_path):
    _week(tmp_path, "2026-W42")
    _week(tmp_path, "2026-W43")
    host = _render(monkeypatch, tmp_path)
    (frame,) = [e for e in host.descendants() if e.tag == "iframe"]
    assert frame._props["src"] == "/eod/weekly/file?week=2026-W43"
    sel = [e for e in host.descendants() if isinstance(e, ui.select)]
    assert len(sel) == 1 and sel[0].value == "2026-W43"
    assert list(sel[0].options) == ["2026-W43", "2026-W42"]


def test_choosing_a_week_repoints_the_frame(monkeypatch, tmp_path):
    _week(tmp_path, "2026-W42")
    _week(tmp_path, "2026-W43")
    host = _render(monkeypatch, tmp_path)
    (frame,) = [e for e in host.descendants() if e.tag == "iframe"]
    (sel,) = [e for e in host.descendants() if isinstance(e, ui.select)]
    sel.set_value("2026-W42")
    assert frame._props["src"] == "/eod/weekly/file?week=2026-W42"
    sel.set_value("../x")      # not an option: the frame must not follow it
    assert frame._props["src"] == "/eod/weekly/file?week=2026-W42"


def test_the_page_links_back_to_the_eod_report(monkeypatch, tmp_path):
    host = _render(monkeypatch, tmp_path)
    assert "EOD Report" in _texts(host)


def test_the_page_builds_no_widget_of_its_own_and_imports_no_engine():
    src = inspect.getsource(idea_review)
    for call in ("ui.button(", "ui.dialog(", "ui.notify(", "ui.table("):
        assert call not in src, f"{call} should go through pages/ui_kit.py"
    for banned in ("import tools", "from tools", "idea_review import",
                   "from shared import idea_review", "import shared.idea_review",
                   "sys.path"):
        assert banned not in src, banned


# --- the file route ---------------------------------------------------------
@pytest.mark.parametrize("bad", ["../x", "2026-43", "", "2026-W4", "2026-W431",
                                 "2026-W43\n", "..%2f..", "2026-W43/../../x",
                                 "x2026-W43"])
def test_weekly_file_route_refuses_bad_week(monkeypatch, tmp_path, bad):
    import main
    _week(tmp_path, "2026-W43")
    (tmp_path.parent / "x").mkdir(exist_ok=True)
    (tmp_path.parent / "x" / "report.html").write_text("SECRET")
    monkeypatch.setattr(idea_review, "ARCHIVE_ROOT", tmp_path)
    resp = main._serve_weekly_file(bad)
    assert resp.status_code == 404
    assert b"SECRET" not in resp.body


def test_weekly_file_route_serves_a_written_week_raw(monkeypatch, tmp_path):
    import main
    _week(tmp_path, "2026-W43", "<html><style>p{}</style><p>hello</p></html>")
    monkeypatch.setattr(idea_review, "ARCHIVE_ROOT", tmp_path)
    resp = main._serve_weekly_file("2026-W43")
    assert resp.status_code == 200
    assert b"<style>p{}</style>" in resp.body        # raw: its own <style> kept


def test_weekly_file_route_404s_a_week_nobody_wrote(monkeypatch, tmp_path):
    import main
    monkeypatch.setattr(idea_review, "ARCHIVE_ROOT", tmp_path)
    assert main._serve_weekly_file("2026-W43").status_code == 404


def test_the_weekly_page_is_registered_under_the_eod_tab():
    import main
    from nicegui import Client
    assert "/eod/weekly" in set(Client.page_routes.values())
    assert main.breadcrumb_trail("/eod")[-1] == "EOD Report"

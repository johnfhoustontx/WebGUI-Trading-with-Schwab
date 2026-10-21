"""Weekly idea review - the archive of the weekly trade-idea reviews.

``tools/weekly_idea_review.py`` writes one standalone document per ISO week to
``webgui/data/idea_review/<YYYY-Www>/report.html``, on the last trading day of
the week. This page lists those weeks, newest first, and frames the chosen one;
``main._serve_weekly_file`` serves the document raw (``ui.html`` would strip its
``<style>``).

The webgui never imports ``tools/``, so ``ARCHIVE_ROOT`` is resolved here the
way ``pages/eod.py`` resolves its own, and a test pins that the two agree.
"""
from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

from nicegui import ui

from pages import ui_kit as kit
from pages.options import theme
from pages.ui_guard import guard

ARCHIVE_ROOT = Path(__file__).resolve().parents[1] / "data" / "idea_review"

# fullmatch, never match: ``$`` also accepts a trailing newline.
_WEEK_RE = re.compile(r"\d{4}-W\d{2}")

EMPTY_TEXT = ("No weekly review yet — the first one is written after the close "
              "on the last trading day of the week.")

FRAME_TITLE = "Weekly idea review"
FILE_ROUTE = "/eod/weekly/file"
NOTE = ("Written after the close on the last trading day of each week. Every "
        "figure in Claude's assessment is one the app computed; whether Claude "
        "used it with the right subject is not checked. Informational only: "
        "nothing here changes a signal.")


def is_week(name) -> bool:
    """Whether ``name`` is shaped like an archive folder (``2026-W43``). PURE."""
    return isinstance(name, str) and _WEEK_RE.fullmatch(name) is not None


def report_file(week, root=None) -> Path | None:
    """The ``report.html`` for ``week``, or None.

    ``week`` arrives from a URL, so the shape is checked BEFORE anything on
    disk is touched, and the result must be an existing regular file."""
    if not is_week(week):
        return None
    path = Path(ARCHIVE_ROOT if root is None else root) / week / "report.html"
    return path if path.is_file() else None


def archive_weeks(root=None) -> list[str]:
    """Weeks that hold a written review, newest first. A folder without a
    ``report.html`` is not a review. ``YYYY-Www`` sorts as text."""
    root = Path(ARCHIVE_ROOT if root is None else root)
    if not root.is_dir():
        return []
    weeks = [p.name for p in root.iterdir()
             if p.is_dir() and is_week(p.name) and (p / "report.html").is_file()]
    return sorted(weeks, reverse=True)


def week_label(week: str) -> str:
    """``2026-W43`` → ``2026-W43 · 19 Oct to 23 Oct``; the bare key for a week
    the calendar does not have. PURE."""
    try:
        year, wk = week.split("-W")
        mon = dt.date.fromisocalendar(int(year), int(wk), 1)
        fri = dt.date.fromisocalendar(int(year), int(wk), 5)
    except (ValueError, AttributeError):
        return week
    return (f"{week} · {mon.day} {mon.strftime('%b')} to "
            f"{fri.day} {fri.strftime('%b')}")


def file_url(week: str) -> str:
    return f"{FILE_ROUTE}?week={week}"


def render() -> None:
    """A Week dropdown, an open-in-a-tab link, and the chosen review framed.

    The archive is a handful of folders a year, read once at build. The frame is
    sandboxed with no permissions: the document needs no script, and what it
    holds includes text a model wrote."""
    with kit.page():
        head = kit.header("Weekly idea review")
        with head.actions:
            kit.button("EOD Report", kind="secondary", icon="arrow_back",
                       on_click=lambda: ui.navigate.to("/eod"))
        weeks = archive_weeks()
        if not weeks:
            kit.empty(EMPTY_TEXT)
            return
        ui.label(NOTE).classes(f"text-xs {theme.MUTED}")
        with ui.row().classes("w-full items-end gap-4 flex-wrap"):
            sel = kit.select_field("Week", {w: week_label(w) for w in weeks},
                                   value=weeks[0], width="w-72")
            link = ui.link("Open in a new tab", file_url(weeks[0]),
                           new_tab=True).classes(
                f"text-xs underline {theme.MUTED}")
        # The document is a page of its own with its own scroll; the height is
        # the window less the header, note and picker, so the page does not grow
        # a second scrollbar around it. White behind it, as a browser gives a
        # document that sets no background.
        frame = ui.element("iframe").classes(
            "w-full h-[calc(100vh-240px)] min-h-[320px] border-0 rounded bg-white"
        ).props(f'sandbox="" title="{FRAME_TITLE}" referrerpolicy="no-referrer"')
        frame._props["src"] = file_url(weeks[0])

    @guard
    def _on_week(e) -> None:
        if e.value in weeks:
            frame._props["src"] = file_url(e.value)
            frame.update()
            link._props["href"] = file_url(e.value)
            link.update()

    sel.on_value_change(_on_week)

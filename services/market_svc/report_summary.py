"""The Desk's MARKET SUMMARY, read off the latest published market report.

The five daily NeuralStrike market reports are written outside this repo and
uploaded into ``deploy/site/reports/`` (see CLAUDE.md, "The public live
screens"): ``latest.html`` is the rendered report and ``latest.txt`` is
``"<day> <n> <slot> <as_of>"``. This module turns that page into the summary
payload (``cache:market:summary``) — the report's verdict headline, its section
headlines as up to ``MAX_HIGHLIGHTS`` highlights, and a link to the full report.

It makes NO Claude call. Until 2026-09-16 market_svc wrote its own sentence with
a change-driven Claude call; the report already says it, at more depth, so the
summary now quotes it.

The report's markup is the contract with its renderer (``render.build`` in the
report tooling): ``<div class="slotchip">`` holds "<label> · <as_of>", ``<h1>``
the verdict, each ``<section class="sec">`` one ``<h2>`` headline. A report that
does not parse publishes nothing, so the last good summary stays.
"""
import logging
from html.parser import HTMLParser

from repo_paths import SITE_HOST, SITE_ROOT

log = logging.getLogger("market_svc.report_summary")

REPORTS_DIR = SITE_ROOT / "reports"
MAX_HIGHLIGHTS = 5
REPORT_URL = f"https://{SITE_HOST}/report.html"


def _squash(text):
    return " ".join(str(text or "").split())


class _ReportParser(HTMLParser):
    """Collects the text of the slot chip, the ``h1`` and every ``h2``."""

    _WATCH = {"h1", "h2"}
    # Elements with no end tag: counting them as nesting would never unwind.
    _VOID = {"br", "img", "wbr", "hr", "input", "meta", "link", "source"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.headline = ""
        self.chip = ""
        self.sections = []
        self._tag = None        # the element whose text is being collected
        self._depth = 0         # nesting inside it (inline spans)
        self._buf = []

    def handle_starttag(self, tag, attrs):
        if tag in self._VOID:
            if self._tag is not None:
                self._buf.append(" ")
            return
        if self._tag is not None:
            self._depth += 1
            return
        classes = (dict(attrs).get("class") or "").split()
        if tag in self._WATCH or (tag == "div" and "slotchip" in classes):
            self._tag, self._depth, self._buf = tag, 0, []

    def handle_endtag(self, tag):
        if self._tag is None or tag in self._VOID:
            return
        if self._depth:
            self._depth -= 1
            return
        text = _squash("".join(self._buf))
        if self._tag == "h1" and not self.headline:
            self.headline = text
        elif self._tag == "h2" and text:
            self.sections.append(text)
        elif self._tag == "div" and not self.chip:
            self.chip = text
        self._tag = None

    def handle_data(self, data):
        if self._tag is not None:
            self._buf.append(data)


def parse_report(html_text, latest_txt=""):
    """The summary payload for one rendered report, or ``None`` when the page
    carries no headline (not a report, or a renderer this parser does not know).

    Highlights are the section headlines, in report order, capped at
    ``MAX_HIGHLIGHTS``; a report with no sections falls back to its headline so
    the frame is never empty while a report exists. Pure."""
    p = _ReportParser()
    try:
        p.feed(html_text or "")
        p.close()
    except Exception:  # noqa: BLE001 — a malformed page is "no report", never a crash.
        return None
    if not p.headline:
        return None

    parts = str(latest_txt or "").split()
    day = parts[0] if parts else ""
    slot = parts[2] if len(parts) > 2 else ""
    label, _, as_of = p.chip.partition("·")
    highlights = p.sections[:MAX_HIGHLIGHTS] or [p.headline]
    return {"headline": p.headline, "highlights": highlights,
            "slot": slot, "slot_label": label.strip(), "report_date": day,
            "as_of": as_of.strip(), "report_url": REPORT_URL}


def report_stamp(reports_dir=REPORTS_DIR):
    """A cheap change key for the published report — ``None`` when there is no
    report. Covers ``latest.txt`` too: the publisher renames it into place just
    after ``latest.html``, so a read between the two is corrected next poll."""
    stamp = []
    for name in ("latest.html", "latest.txt"):
        try:
            st = (reports_dir / name).stat()
        except OSError:
            if name == "latest.html":
                return None
            stamp.append(None)
            continue
        stamp.append((st.st_mtime_ns, st.st_size))
    return tuple(stamp)


def read_report(reports_dir=REPORTS_DIR):
    """The summary payload for the report on disk, or ``None``."""
    try:
        html_text = (reports_dir / "latest.html").read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        latest_txt = (reports_dir / "latest.txt").read_text(encoding="utf-8")
    except OSError:
        latest_txt = ""
    payload = parse_report(html_text, latest_txt)
    if payload is None:
        log.warning("latest market report did not parse - summary left as it was")
    return payload

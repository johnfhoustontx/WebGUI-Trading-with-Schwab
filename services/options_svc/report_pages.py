"""One published market report's own page on the public site (neuralstrike.co).

    deploy/site/r/<YYYY-MM-DD>-<n>-<slot>/index.html    the site's page around it
    deploy/site/r/<id>/report.html                      a lasting copy of the report
    deploy/site/r/<id>/card.jpg                         its card, 1200 wide
    deploy/site/r/sitemap.txt                           every page's address

``report.html`` frames ``reports/latest.html``, which the report tooling
replaces five times a trading day, so a post linking it shows a different
report by the next slot. A post links this page instead. NOTHING here is ever
pruned; the folder is gitignored generated state like ``ideas/``.

Written by ``options_svc`` in the ``x_post_report`` handler before any post,
from the report on disk: ``latest.txt`` names it (``"<day> <n> <slot> <as_of>"``)
and must name the same day and slot as the command's payload, or a newer report
has replaced it and nothing is written. A folder already there is never
rewritten, so ``market_svc``'s re-enqueue on every restart writes nothing.

The page is the tracked ``report.html`` with its frame pointed at the copy, its
title and headline the report's, and preview tags for the card. The copy is
the report's own bytes but for its one relative link, the PDF beside it in
``reports/``. Never raises: a page must never cost a post.
"""
import html
import logging
import pathlib
import re

import repo_paths
from services.options_svc import site_ideas as _site
from services.options_svc import trade_pages as _tp

log = logging.getLogger(__name__)

REPORT_DIR = "r"
TEMPLATE = "report.html"
REPORTS = "reports"
PAGE, COPY, CARD_JPG, SITEMAP = "index.html", "report.html", "card.jpg", "sitemap.txt"

_TXT_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\s+(\d{1,2})\s+([a-z][a-z_]*)(\s|$)")
_ID_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-\d{1,2}-[a-z][a-z_]*$")
_PDF_HREF_RE = re.compile(r'href="(NeuralStrike-[A-Za-z0-9_-]+\.pdf)"')
_FRAME_RE = re.compile(r'(<iframe class="ns-report-frame" src=")reports/latest\.html#ns-framed(")')
_OWN_PAGE = 'href="reports/latest.html"'
_H1_RE = re.compile(r'(<h1 class="ns-live-title">)(.*?)(</h1>)', re.S)
_TITLE_RE = re.compile(r"<title>.*?</title>", re.S)
_DESC_RE = re.compile(r'<meta name="description" content="[^"]*">')


def page_id(latest_txt):
    """``"2026-11-23 4 midday 11:35CT"`` -> ``"2026-11-23-4-midday"``, or None."""
    m = _TXT_RE.match(str(latest_txt or "").strip())
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None


def address(pid, site_host=None) -> str:
    host = repo_paths.SITE_HOST if site_host is None else site_host
    return f"https://{host}/{REPORT_DIR}/{pid}/"


def _esc(value) -> str:
    return html.escape(str(value or ""), quote=True)


def page_html(template, report, pid, image_height, site_host=None):
    """PURE. ``template`` (the tracked report.html's text) as this report's page,
    or None when the template no longer has the one frame this rewrites."""
    if len(_FRAME_RE.findall(template)) != 1 or "<head>" not in template:
        return None
    addr = _esc(address(pid, site_host))
    label = str(report.get("slot_label") or "Market report").strip()
    headline = str(report.get("headline") or "").strip()
    title = _esc(f"{label}: {headline}" if headline else label)
    tags = "\n".join((
        f'<link rel="canonical" href="{addr}">',
        '<meta property="og:type" content="article">',
        f'<meta property="og:title" content="{title}">',
        f'<meta property="og:description" content="{title}">',
        f'<meta property="og:url" content="{addr}">',
        f'<meta property="og:image" content="{addr}{CARD_JPG}">',
        f'<meta property="og:image:width" content="{_tp.IMAGE_WIDTH}">',
        f'<meta property="og:image:height" content="{int(image_height)}">',
        f'<meta property="og:image:alt" content="{title}">',
        '<meta name="twitter:card" content="summary_large_image">',
        "</head>",
    ))
    page = template.replace("<head>", '<head>\n<base href="/">', 1)
    page = _TITLE_RE.sub(lambda m: f"<title>NeuralStrike — {title}</title>", page, count=1)
    page = _DESC_RE.sub(lambda m: f'<meta name="description" content="{title}">', page, count=1)
    page = page.replace("</head>", tags, 1)
    page = _H1_RE.sub(lambda m: f"{m.group(1)}{title}{m.group(3)}", page, count=1)
    page = page.replace(_OWN_PAGE, f'href="{REPORT_DIR}/{pid}/{COPY}"', 1)
    page = _FRAME_RE.sub(lambda m: f"{m.group(1)}{REPORT_DIR}/{pid}/{COPY}#ns-framed{m.group(2)}",
                         page, count=1)
    return page


def write(root, report, png, *, reports_dir=None):
    """Write the page for the report on disk; return its address or None.

    None when the report on disk is not ``report`` (a newer one replaced it),
    when there is no card, or when anything fails. An existing page is left as it
    is and its address returned. Never raises."""
    try:
        root = pathlib.Path(root)
        if not isinstance(report, dict) or not png:
            return None
        reports = pathlib.Path(reports_dir) if reports_dir is not None else root / REPORTS
        txt = (reports / "latest.txt").read_text(encoding="utf-8")
        pid = page_id(txt)
        if pid is None:
            return None
        day, slot = pid[:10], pid.split("-", 4)[4]
        if (day, slot) != (str(report.get("report_date") or ""), str(report.get("slot") or "")):
            log.info("report page: %s is no longer on disk; no page", report.get("slot"))
            return None
        folder = root / REPORT_DIR / pid
        if (folder / PAGE).is_file():
            return address(pid)
        template = (root / TEMPLATE).read_text(encoding="utf-8")
        jpg, height = _tp.card_jpeg(png)
        if not jpg:
            return None
        page = page_html(template, report, pid, height)
        if page is None:
            log.warning("report page: report.html has no frame to point; no page")
            return None
        copy = _PDF_HREF_RE.sub(lambda m: f'href="/{REPORTS}/{m.group(1)}"',
                                (reports / "latest.html").read_text(encoding="utf-8"))
        folder.mkdir(parents=True, exist_ok=True)
        _site._write_atomic(folder / COPY, copy.encode("utf-8"))
        _site._write_atomic(folder / CARD_JPG, jpg)
        _site._write_atomic(folder / PAGE, page.encode("utf-8"))
        write_sitemap(root)
        return address(pid)
    except Exception:  # noqa: BLE001 -- a page must never cost a post
        log.warning("report page: write failed", exc_info=True)
        return None


def write_sitemap(root) -> None:
    """``r/sitemap.txt``: every page's address, newest first. Never raises."""
    try:
        folder = pathlib.Path(root) / REPORT_DIR
        folder.mkdir(parents=True, exist_ok=True)
        ids = sorted((p.name for p in folder.iterdir()
                      if p.is_dir() and _ID_RE.match(p.name) and (p / PAGE).is_file()),
                     reverse=True)
        _site._write_atomic(folder / SITEMAP,
                            "".join(address(i) + "\n" for i in ids).encode("utf-8"))
    except Exception:  # noqa: BLE001
        log.warning("report page: sitemap write failed", exc_info=True)

"""One market report's own page on the site (``report_pages.py``).

``report.html`` always frames the LATEST report, so a post linking it shows a
different report by the next slot. Each report gets ``/r/<day>-<n>-<slot>/``:
a lasting copy of the report, the site's page around it and its card.
"""
import os
import pathlib
import shutil

import pytest

import repo_paths
from services.options_svc import report_card
from services.options_svc import report_pages as R

_REPORT_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>NeuralStrike Market midday</title></head><body>
<div class="slotchip">Midday &middot; 11:35 CT</div>
<a class="pdfbtn" href="NeuralStrike-2026-11-23-4-midday.pdf" download>Download PDF</a>
<h1>Buyers defend the flip &amp; breadth thin</h1>
<section class="sec"><h2>Chips lead</h2></section>
</body></html>"""

_PAYLOAD = {"headline": "Buyers defend the flip & breadth thin", "highlights": ["Chips lead"],
            "slot": "midday", "slot_label": "Midday", "report_date": "2026-11-23",
            "as_of": "11:35 CT", "report_url": f"https://{repo_paths.SITE_HOST}/report.html"}

_PID = "2026-11-23-4-midday"


@pytest.fixture()
def root(tmp_path):
    shutil.copy(pathlib.Path(repo_paths.SITE_ROOT) / "report.html", tmp_path / "report.html")
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "latest.html").write_text(_REPORT_HTML, encoding="utf-8")
    (reports / "latest.txt").write_text("2026-11-23 4 midday 11:35CT\n", encoding="utf-8")
    return tmp_path


def _png():
    return report_card.render_report_png(_PAYLOAD)


def test_page_id_from_latest_txt():
    assert R.page_id("2026-11-23 3 first_hour 09:35CT\n") == "2026-11-23-3-first_hour"
    assert R.page_id("2026-11-23 4 midday 11:35CT") == _PID
    for bad in ("garbage", "", "2026-11-23 x midday", "2026-11-23 4 ../x 1", None):
        assert R.page_id(bad) is None


def test_address_uses_the_site_host():
    assert R.address(_PID, "neuralstrike.co") == f"https://neuralstrike.co/r/{_PID}/"


def test_write_makes_the_page(root):
    assert R.write(root, _PAYLOAD, _png()) == f"https://{repo_paths.SITE_HOST}/r/{_PID}/"
    folder = root / "r" / _PID
    copy = (folder / "report.html").read_text(encoding="utf-8")
    # The copy is the report, with its PDF link pointed back at reports/.
    assert copy == _REPORT_HTML.replace('href="NeuralStrike-', 'href="/reports/NeuralStrike-')
    page = (folder / "index.html").read_text(encoding="utf-8")
    addr = f"https://{repo_paths.SITE_HOST}/r/{_PID}/"
    assert f'<meta property="og:image" content="{addr}card.jpg">' in page
    assert f'<meta property="og:url" content="{addr}">' in page
    assert "Midday: Buyers defend the flip &amp; breadth thin" in page
    assert f'src="r/{_PID}/report.html#ns-framed"' in page
    assert 'src="reports/latest.html#ns-framed"' not in page
    assert page.index('<base href="/">') < page.index("<title>")
    from PIL import Image
    with Image.open(folder / "card.jpg") as im:
        assert im.format == "JPEG" and im.width == 1200
    assert f'content="{im.height}"' in page
    assert R.address(_PID) in (root / "r" / "sitemap.txt").read_text(encoding="utf-8")


def test_write_is_idempotent(root):
    first = R.write(root, _PAYLOAD, _png())
    folder = root / "r" / _PID
    stamps = {p.name: p.stat().st_mtime_ns for p in folder.iterdir()}
    assert R.write(root, _PAYLOAD, _png()) == first
    assert {p.name: p.stat().st_mtime_ns for p in folder.iterdir()} == stamps


def test_a_replaced_report_writes_nothing(root):
    (root / "reports" / "latest.txt").write_text("2026-11-23 5 close 15:10CT\n",
                                                 encoding="utf-8")
    assert R.write(root, _PAYLOAD, _png()) is None
    assert not (root / "r").exists()


def test_write_never_raises(tmp_path):
    assert R.write(tmp_path, _PAYLOAD, _png()) is None          # no template, no report
    assert R.write(tmp_path, None, None) is None


def test_no_card_still_no_page(root):
    assert R.write(root, _PAYLOAD, None) is None


def test_sitemap_of_no_pages_is_empty(tmp_path):
    R.write_sitemap(tmp_path)
    assert (tmp_path / "r" / "sitemap.txt").read_text(encoding="utf-8") == ""


@pytest.mark.skipif(os.name != "posix", reason="file modes")
def test_files_are_world_readable(root):
    R.write(root, _PAYLOAD, _png())
    for p in (root / "r" / _PID).iterdir():
        assert p.stat().st_mode & 0o777 == 0o644

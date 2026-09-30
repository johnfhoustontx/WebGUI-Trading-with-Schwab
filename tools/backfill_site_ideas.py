"""Put the recent trade ideas on the public site from the card archive.

Each posted trade idea is kept at ``TRADE_IDEAS_DIR/<day>/trade-idea-<day>-<HHMM>-
<SYMBOL>.png`` (+ a ``.txt`` caption) by ``trade_idea.archive``. Publishing to the
site (``services/options_svc/site_ideas.py``) started later, so this republishes
the newest ``keep_days`` days of that archive into ``deploy/site`` -- run once on
the serving box so ideas.html is not empty on its first day. Safe to re-run: a
card already on the site is replaced, never duplicated.

    .venv/bin/python -m tools.backfill_site_ideas            # the [site] keep_days
    .venv/bin/python -m tools.backfill_site_ideas --days 3
"""
import argparse
import datetime as _dt
import pathlib
import re
import sys
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import repo_paths  # noqa: E402
from services.options_svc import site_ideas  # noqa: E402

_CT = ZoneInfo("America/Chicago")
_NAME = re.compile(r"^trade-idea-(\d{4}-\d{2}-\d{2})-(\d{2})(\d{2})-(.+)\.png$")
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_GRADE = re.compile(r"\bGrade (\w+)")


def parse_archive_name(name):
    """``(post time in Central, filename symbol)``, or None for another file."""
    m = _NAME.match(name)
    if not m:
        return None
    day, hh, mm, sym = m.groups()
    d = _dt.date.fromisoformat(day)
    return _dt.datetime(d.year, d.month, d.day, int(hh), int(mm), tzinfo=_CT), sym


def parse_caption(text):
    """``(symbol, label, grade)`` out of ``trade_idea.caption``; blanks if not one."""
    text = (text or "").strip()
    if not text.startswith("Trade idea: "):
        return "", "", ""
    head = text[len("Trade idea: "):].split(" · ", 1)[0]
    symbol, _, label = head.partition(" ")
    grade = _GRADE.search(text)
    return symbol, label, grade.group(1) if grade else ""


def backfill(archive_dir, site_root, keep_days) -> int:
    """Publish the newest ``keep_days`` archive days, oldest card first (so the
    manifest's trim sees them in posting order). Returns cards published."""
    archive_dir = pathlib.Path(archive_dir)
    if not archive_dir.is_dir():
        return 0
    days = sorted(p for p in archive_dir.iterdir() if p.is_dir() and _DAY.match(p.name))
    published = 0
    for day in days[-keep_days:]:
        for png in sorted(day.glob("trade-idea-*.png")):
            parsed = parse_archive_name(png.name)
            if parsed is None:
                continue
            when, file_symbol = parsed
            txt = png.with_suffix(".txt")
            caption = txt.read_text(encoding="utf-8").strip() if txt.exists() else ""
            symbol, label, grade = parse_caption(caption)
            idea = {"symbol": symbol or file_symbol, "label": label, "grade": grade}
            if site_ideas.publish(idea, png.read_bytes(), caption, when,
                                  root=site_root, keep_days=keep_days):
                published += 1
    return published


def main(argv=None):
    from shared.notify import switches
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--days", type=int, default=switches.site_settings()["keep_days"])
    args = ap.parse_args(argv)
    n = backfill(repo_paths.TRADE_IDEAS_DIR, repo_paths.SITE_ROOT, args.days)
    print(f"published {n} trade idea card(s) into {repo_paths.SITE_ROOT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

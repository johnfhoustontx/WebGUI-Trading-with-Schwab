"""Every social card on the public site, as one tar on stdout (for the PC's copy).

Run ON THE SERVER, from the checkout, by ``tools/pull_social_cards.ps1`` on the PC,
which pipes this file to ``.venv/bin/python -`` over one ssh connection and unpacks
the tar into ``D:\\WebGUI Trading with Schwab\\Social Media``. Read-only: it opens
the site's two manifests and the card files, and writes nothing.

The tar holds, for every card the site has:

    Blog/<published day> <slug>.png / .jpg / .txt
    Trade Ideas/<date> <HHMM>-<SYMBOL>.png / .jpg / .txt

The ``.txt`` is the caption the Social Cards page copies: a blog entry's is
``social_cards.blog_caption`` (title, summary, address, hashtags) and a trade
idea's is the caption it was posted with (the manifest's ``alt``). A blog entry
without both card files, and a trade idea from before the JPEG existed, are left
out. Every name is built from a slug or a file name that passed the same rule the
site writes by, so nothing in the tar can land outside its folder.

    .venv/bin/python tools/social_cards_export.py > cards.tar
"""
import datetime as _dt
import io
import json
import pathlib
import re
import sys
import tarfile
from zoneinfo import ZoneInfo

if __name__ == "__main__" and "__file__" in globals():
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import repo_paths  # noqa: E402
from shared import blog_inbox, social_cards  # noqa: E402

CENTRAL = ZoneInfo("America/Chicago")
_IDEA_RE = re.compile(r"^ideas/(\d{4}-\d{2}-\d{2})/([A-Za-z0-9-]+)\.png$")
BLOG, IDEAS = "Blog", "Trade Ideas"


def _manifest(path):
    try:
        data = json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _day(iso):
    """The Central-time day of ``iso``, or "" when it does not parse."""
    try:
        at = _dt.datetime.fromisoformat(str(iso))
    except ValueError:
        return ""
    return (at.astimezone(CENTRAL) if at.tzinfo else at).date().isoformat()


def _files(folder, png, jpg):
    """``(png_path, jpg_path)`` when both are plain files, else None."""
    p, j = folder / png, folder / jpg
    ok = all(f.is_file() and not f.is_symlink() for f in (p, j))
    return (p, j) if ok else None


def members(root, host=None, max_hashtags=None) -> list:
    """``[(name in the tar, path or caption bytes), ...]``: three per card."""
    root = pathlib.Path(root)
    host = host or repo_paths.SITE_HOST
    if max_hashtags is None:
        max_hashtags = social_cards.config()["caption"]["max_hashtags"]
    out = []
    for e in _manifest(root / "blog.json").get("entries") or []:
        slug = e.get("slug") if isinstance(e, dict) else None
        if not (isinstance(slug, str) and blog_inbox.SLUG_RE.match(slug)):
            continue
        found = _files(root / "blog" / slug, "card.png", "card.jpg")
        day = _day(e.get("published"))
        if not found or not day:
            continue
        stem = f"{BLOG}/{day} {slug}"
        caption = social_cards.blog_caption(e.get("title"), e.get("summary"),
                                            f"https://{host}/blog/{slug}/", e.get("tags"),
                                            max_hashtags)
        out += [(f"{stem}.png", found[0]), (f"{stem}.jpg", found[1]),
                (f"{stem}.txt", (caption + "\n").encode("utf-8"))]
    for d in _manifest(root / "ideas.json").get("days") or []:
        for i in (d.get("ideas") if isinstance(d, dict) else None) or []:
            m = _IDEA_RE.match(i.get("full", "")) if isinstance(i, dict) else None
            if not m:
                continue
            found = _files(root / "ideas" / m.group(1), f"{m.group(2)}.png", f"{m.group(2)}.jpg")
            if not found:
                continue
            stem = f"{IDEAS}/{m.group(1)} {m.group(2)}"
            caption = str(i.get("alt") or "")
            out += [(f"{stem}.png", found[0]), (f"{stem}.jpg", found[1]),
                    (f"{stem}.txt", (caption + "\n").encode("utf-8"))]
    return out


def write_tar(items, stream) -> int:
    """Write ``items`` to ``stream`` as an uncompressed tar; returns the count."""
    with tarfile.open(fileobj=stream, mode="w|", format=tarfile.PAX_FORMAT) as tar:
        for name, src in items:
            data = src if isinstance(src, bytes) else src.read_bytes()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mtime = (src.stat().st_mtime if not isinstance(src, bytes)
                          else _dt.datetime.now().timestamp())
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(data))
    return len(items)


def main(root=None) -> int:
    items = members(repo_paths.SITE_ROOT if root is None else root)
    if not items:
        print("no cards found under the site folder", file=sys.stderr)
        return 1
    write_tar(items, sys.stdout.buffer)
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())

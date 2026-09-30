"""Posted trade ideas on the public site (neuralstrike.co).

After ``handlers.run_trade_idea`` posts a card, it lands here too:

    deploy/site/ideas/<YYYY-MM-DD>/<HHMM>-<SYMBOL>.png    the card as posted
    deploy/site/ideas/<YYYY-MM-DD>/<HHMM>-<SYMBOL>.webp   a 1200-wide copy for the page
    deploy/site/ideas.json                                 the manifest the page reads

``deploy/site/assets/ideas.js`` renders the home page's strip and ``ideas.html``
from the manifest. Both paths are GITIGNORED generated state, exactly like
``live/*.webp``: committed, the first post would dirty prod's tree and
``tools/promote.sh`` refuses a dirty tree.

The manifest sits at the site ROOT, not inside ``ideas/``, so Caddy gives it
``no-cache`` with the pages while the images under ``/ideas/*`` get a lifetime -
one path, one Cache-Control rule.

Only POSTED ideas come here - the same cards already public on X, Discord and
Telegram - so the site discloses nothing new. ``publish`` never raises: a site
write must never cost a post.
"""
import datetime as _dt
import io
import json
import logging
import os
import pathlib
import re
import shutil
import tempfile

import repo_paths

log = logging.getLogger(__name__)

SITE_ROOT = pathlib.Path(repo_paths.SITE_ROOT)   # tests redirect this
IDEAS_DIR = "ideas"
MANIFEST = "ideas.json"
WEB_WIDTH = 1200          # the card is drawn at 2x (2400 wide); half is plenty on a page
WEBP_QUALITY = 86

_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_REF_RE = re.compile(r"^ideas/\d{4}-\d{2}-\d{2}/[A-Za-z0-9-]+\.(png|webp)$")


def slug(symbol) -> str:
    """A file-safe symbol: '$SPX' -> 'SPX', 'BRK/B' -> 'BRK-B'."""
    s = "".join(c if c.isalnum() else "-" for c in str(symbol or "").lstrip("$"))
    return s.strip("-") or "trade"


def entry(symbol, label, grade, caption_text, now) -> dict:
    """One manifest row. ``now`` is the post time (Central); it names the files."""
    day, stem = f"{now:%Y-%m-%d}", f"{now:%H%M}-{slug(symbol)}"
    return {"time": f"{now:%H:%M}", "symbol": str(symbol or ""),
            "label": str(label or ""), "grade": str(grade or ""),
            "alt": str(caption_text or ""),
            "img": f"{IDEAS_DIR}/{day}/{stem}.webp",
            "full": f"{IDEAS_DIR}/{day}/{stem}.png"}


def _valid_idea(i) -> bool:
    return (isinstance(i, dict) and isinstance(i.get("time"), str)
            and all(isinstance(i.get(k), str) and _REF_RE.match(i[k])
                    for k in ("img", "full")))


def _valid_days(manifest) -> list:
    """The old manifest's days, each cleaned; anything malformed is dropped."""
    days = manifest.get("days") if isinstance(manifest, dict) else None
    out = []
    for d in days if isinstance(days, list) else ():
        if not (isinstance(d, dict) and isinstance(d.get("date"), str)
                and _DAY_RE.match(d["date"]) and isinstance(d.get("ideas"), list)):
            continue
        out.append({"date": d["date"], "ideas": [i for i in d["ideas"] if _valid_idea(i)]})
    return out


def merge_manifest(manifest, day, new_entry, keep_days, updated) -> dict:
    """PURE. The manifest after ``new_entry`` posts on ``day``.

    A re-post of one slot (same ``full``) replaces, never duplicates. Ideas and
    days run newest first; only the newest ``keep_days`` days survive. A day
    exists only because something posted on it, so this needs no holiday list."""
    by_date = {d["date"]: d["ideas"] for d in _valid_days(manifest)}
    ideas = [i for i in by_date.get(day, []) if i.get("full") != new_entry.get("full")]
    ideas.append(new_entry)
    by_date[day] = sorted(ideas, key=lambda i: i["time"], reverse=True)
    dates = sorted(by_date, reverse=True)[:max(1, int(keep_days))]
    return {"updated": updated, "days": [{"date": d, "ideas": by_date[d]} for d in dates]}


def webp_copy(png, width=WEB_WIDTH):
    """A ``width``-wide WebP of the card, or None when it cannot be made."""
    try:
        from PIL import Image
        with Image.open(io.BytesIO(png)) as im:
            im = im.convert("RGB")
            if im.width > width:
                im = im.resize((width, round(im.height * width / im.width)),
                               Image.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, "WEBP", quality=WEBP_QUALITY, method=6)
            return buf.getvalue()
    except Exception:  # noqa: BLE001 -- the PNG is still published
        log.warning("trade idea: WebP copy failed", exc_info=True)
        return None


def _read_manifest(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except Exception:  # noqa: BLE001 -- a broken manifest is rebuilt, not fatal
        log.warning("trade idea: %s unreadable - starting a new one", path)
        return None


def _write_atomic(path, data: bytes):
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.chmod(tmp, 0o644)          # served by Caddy, a different user
        os.replace(tmp, path)
    except BaseException:
        pathlib.Path(tmp).unlink(missing_ok=True)
        raise


def _prune(ideas_dir, keep):
    """Delete ``ideas/<YYYY-MM-DD>`` folders not in ``keep``. Nothing else."""
    for p in ideas_dir.iterdir():
        if p.is_dir() and _DAY_RE.match(p.name) and p.name not in keep:
            shutil.rmtree(p, ignore_errors=True)


def publish(idea, png, caption_text, now, *, root=None, keep_days=6) -> bool:
    """Write the card, its web copy and the manifest into the site tree.

    Never raises; False when nothing usable was written."""
    try:
        root = pathlib.Path(root) if root is not None else SITE_ROOT
        row = entry(idea.get("symbol"), idea.get("label"), idea.get("grade"),
                    caption_text, now)
        full = root / row["full"]
        full.parent.mkdir(parents=True, exist_ok=True)
        _write_atomic(full, png)
        web = webp_copy(png)
        if web:
            _write_atomic(root / row["img"], web)
        else:
            row["img"] = row["full"]
        path = root / MANIFEST
        manifest = merge_manifest(_read_manifest(path), f"{now:%Y-%m-%d}", row,
                                  keep_days, now.isoformat(timespec="seconds"))
        _write_atomic(path, json.dumps(manifest, indent=1).encode("utf-8"))
        _prune(root / IDEAS_DIR, {d["date"] for d in manifest["days"]})
        return True
    except Exception:  # noqa: BLE001 -- a site write must never cost a post
        log.warning("trade idea: site publish failed", exc_info=True)
        return False

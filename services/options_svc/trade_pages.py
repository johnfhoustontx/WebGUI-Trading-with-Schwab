"""One posted trade idea's own page on the public site (neuralstrike.co).

    deploy/site/t/<YYYY-MM-DD>-<HHMM>-<SYMBOL>/index.html   the page
    deploy/site/t/<id>/trade.json                          its row, for ideas.js
    deploy/site/t/<id>/card.jpg                            the card, 1200 wide (preview + Instagram)
    deploy/site/t/<id>/card.webp                           the card the page shows
    deploy/site/t/sitemap.txt                              every page's address
    deploy/site/t/open.json                                pages whose result still moves

A Facebook or Instagram post links one of these for as long as the post
exists, so NOTHING here is ever pruned (``ideas/<day>/`` is, after
``keep_days``). The folder is gitignored generated state like ``ideas/``.

The page's head and facts are written here, once; its RESULT is drawn by
``assets/ideas.js`` from ``trade.json`` in the same words ``ideas.html`` uses,
so the two can never disagree. ``trade.json`` is the manifest row with its
image paths pointed at this folder; ``site_ideas`` rewrites it while the
result moves (``rewrite``).

Every writer here never raises: a page must never cost a post.
"""
import datetime as _dt
import html
import io
import json
import logging
import pathlib
import re

import repo_paths
from services.options_svc import site_ideas as _site
from services.options_svc import trade_idea as _ti
from shared import card_draw as _card_draw
from shared import site_nav as _site_nav
from shared import social_cards as _social_cards

log = logging.getLogger(__name__)

TRADE_DIR = "t"
TEMPLATE = "ideas.html"
PAGE, DATA, CARD_JPG, CARD_WEBP = "index.html", "trade.json", "card.jpg", "card.webp"
SITEMAP = "sitemap.txt"
OPEN_LIST = "open.json"
IMAGE_WIDTH = 1200

_FULL_RE = re.compile(r"^ideas/(\d{4}-\d{2}-\d{2})/(\d{4}-[A-Za-z0-9-]+)\.png$")
_ID_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-\d{4}-[A-Za-z0-9-]+$")
# The results a page stops moving at; the same set ``site_ideas`` treats as final.
_FINAL = ("target", "stop", "trail", "expired", "closed")


# ── names ───────────────────────────────────────────────────────────────────

def page_id(row):
    """``"ideas/2026-11-23/0930-SPY.png"`` -> ``"2026-11-23-0930-SPY"``; None when
    the row's ``full`` is not one of the site's idea cards."""
    m = _FULL_RE.match(str((row or {}).get("full") or ""))
    return f"{m.group(1)}-{m.group(2)}" if m else None


def address(pid, site_host=None) -> str:
    host = repo_paths.SITE_HOST if site_host is None else site_host
    return f"https://{host}/{TRADE_DIR}/{pid}/"


def page_row(row, pid) -> dict:
    """PURE. ``row`` with its images pointed at the page's own folder."""
    base = f"{TRADE_DIR}/{pid}/"
    out = dict(row)
    out["img"] = base + CARD_WEBP
    out["full"] = out["jpg"] = base + CARD_JPG
    out["page"] = base
    return out


def is_final(row, *, in_manifest, ledger_row) -> bool:
    """The terminal rule. A page is final when its result is closed, or when it
    has left the manifest and no Ledger row follows it (nothing will move it)."""
    status = ((row or {}).get("result") or {}).get("status")
    if status in _FINAL:
        return True
    return not in_manifest and not isinstance(ledger_row, dict)


# ── the page ────────────────────────────────────────────────────────────────

def _esc(value) -> str:
    return html.escape(value if isinstance(value, str) else str(value or ""), quote=True)


def _expiry_long(expiration) -> str:
    try:
        d = _dt.date.fromisoformat(str(expiration)[:10])
    except ValueError:
        return str(expiration or "")
    return f"{d:%b} {d.day}, {d.year}"


def _facts(row):
    """``[(label, value)]`` the card prints, from the row's ``card`` (else the row)."""
    card = row.get("card") if isinstance(row.get("card"), dict) else {}
    get = lambda k: card.get(k) if card.get(k) is not None else row.get(k)  # noqa: E731
    out = []
    if get("label"):
        out.append(("Structure", str(get("label"))))
    legs = [lg for lg in (get("legs") or []) if isinstance(lg, dict)]
    try:
        legs_text = " / ".join(_ti.leg_text({**lg, "qty": int(lg.get("qty") or 1)})
                               for lg in legs)
    except (KeyError, TypeError, ValueError, IndexError):
        legs_text = ""
    if legs_text:
        out.append(("Legs", legs_text))
    if get("expiration"):
        out.append(("Expires", _expiry_long(get("expiration"))))
    if "max_loss" in card or "max_loss" in row:
        out.append(("Max risk", _ti.money(get("max_loss"))))
    if "max_profit" in card or "max_profit" in row:
        out.append(("Max profit", _ti.money(get("max_profit"))))
    be = [b for b in (get("breakevens") or []) if isinstance(b, (int, float))]
    if be:
        out.append(("Breakeven", " / ".join(f"{b:,.2f}" for b in be[:2])))
    pop = get("pop_pct")
    if isinstance(pop, (int, float)):
        out.append(("Probability of profit", f"{pop:.0f}%"))
    if get("grade"):
        score = get("score")
        grade = str(get("grade"))
        out.append(("Grade", f"{grade} · {score:.0f} / 100"
                    if isinstance(score, (int, float)) else grade))
    return out


def page_html(parts, row, pid, site_host, *, image_height=630) -> str:
    """PURE. The whole page for one idea. Every value from ``row`` is escaped.

    ``<base href="/">`` comes first: the tracked menu's addresses and the
    scripts are written from the site root."""
    addr = _esc(address(pid, site_host))
    title = _esc(f"{row.get('symbol') or ''} {row.get('label') or ''}".strip()
                 or "Trade idea")
    caption = _esc(row.get("alt") or title)
    day, hhmm = pid[:10], pid[11:15]
    when = _esc(f"{_expiry_long(day)} · {hhmm[:2]}:{hhmm[2:]} CT")
    facts = "\n".join(f"      <dt>{_esc(k)}</dt><dd>{_esc(v)}</dd>" for k, v in _facts(row))
    head = "\n".join((
        "<!doctype html>",
        '<html lang="en">',
        "<head>",
        '<base href="/">',
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>NeuralStrike — Trade idea: {title}</title>",
        f'<meta name="description" content="{caption}">',
        f'<link rel="canonical" href="{addr}">',
        '<meta property="og:type" content="article">',
        f'<meta property="og:title" content="Trade idea: {title}">',
        f'<meta property="og:description" content="{caption}">',
        f'<meta property="og:url" content="{addr}">',
        f'<meta property="og:image" content="{addr}{CARD_JPG}">',
        f'<meta property="og:image:width" content="{IMAGE_WIDTH}">',
        f'<meta property="og:image:height" content="{int(image_height)}">',
        f'<meta property="og:image:alt" content="{caption}">',
        '<meta name="twitter:card" content="summary_large_image">',
        '<meta name="theme-color" content="#161826">',
        *parts["icons"],
        *parts["styles"],
        "</head>",
    ))
    body = (
        "<body>\n"
        '<div class="ns-page-sub ns-live-page">\n'
        f'  {parts["nav"]}\n'
        '  <main class="ns-live-main">\n'
        '    <div class="ns-live-head">\n'
        '      <p class="ns-eyebrow">Trade idea</p>\n'
        f'      <h1 class="ns-live-title">{title}</h1>\n'
        f'      <p class="ns-live-lede">Posted {when}. '
        '<a href="ideas.html">All trade ideas</a> · <a href="index.html">NeuralStrike</a></p>\n'
        "    </div>\n"
        f'    <div class="ns-ideas-grid ns-ideas-grid-page" data-idea-page '
        f'data-src="/{TRADE_DIR}/{pid}/{DATA}">\n'
        f"      <noscript><p>{caption}</p></noscript>\n"
        "    </div>\n"
        '    <dl class="ns-idea-facts">\n'
        f"{facts}\n"
        "    </dl>\n"
        "  </main>\n"
        f'  <footer class="ns-foot ns-live-foot">{parts["footer"]}</footer>\n'
        "</div>\n"
        '<script src="assets/ideas.js" defer></script>\n'
        '<script src="assets/market-clock.js" defer></script>\n'
        '<script src="assets/market-glow.js" defer></script>\n'
        '<script src="assets/blog-new.js" defer></script>\n'
        "</body>\n"
        "</html>\n"
    )
    return head + "\n" + body


# ── files ───────────────────────────────────────────────────────────────────

def _json_bytes(data) -> bytes:
    return json.dumps(data, indent=1, sort_keys=True).encode("utf-8")


def card_jpeg(png):
    """``(1200-wide JPEG bytes, height)`` of the card, or ``(None, None)``."""
    try:
        from PIL import Image
        with Image.open(io.BytesIO(png)) as im:
            im = im.convert("RGB")
            if im.width > IMAGE_WIDTH:
                im = im.resize((IMAGE_WIDTH, round(im.height * IMAGE_WIDTH / im.width)),
                               Image.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, "PNG")
            height = im.height
        jpg = _card_draw.jpeg_from_png(buf.getvalue(),
                                       _social_cards.config()["cards"]["jpeg_quality"])
        return (jpg, height) if jpg else (None, None)
    except Exception:  # noqa: BLE001 -- no JPEG, no page
        log.warning("trade page: JPEG failed", exc_info=True)
        return None, None


def write(root, row, png):
    """Write ``row``'s page folder under ``root``; return its address or None.

    Writes the card (JPEG and WebP) first and the page last, so a page never
    names an image that is not there. Never raises."""
    try:
        root = pathlib.Path(root)
        pid = page_id(row)
        parts = _site_nav.page_parts(root, TEMPLATE)
        if pid is None or parts is None or not png:
            return None
        jpg, height = card_jpeg(png)
        web = _site.webp_copy(png)
        if not jpg or not web:
            return None
        folder = root / TRADE_DIR / pid
        folder.mkdir(parents=True, exist_ok=True)
        _site._write_atomic(folder / CARD_JPG, jpg)
        _site._write_atomic(folder / CARD_WEBP, web)
        _site._write_atomic(folder / DATA, _json_bytes(page_row(row, pid)))
        page = page_html(parts, row, pid, repo_paths.SITE_HOST, image_height=height)
        _site._write_atomic(folder / PAGE, page.encode("utf-8"))
        return address(pid)
    except Exception:  # noqa: BLE001 -- a page must never cost a post
        log.warning("trade page: write failed", exc_info=True)
        return None


def read_row(root, pid):
    """The page's ``trade.json``, or None."""
    try:
        data = json.loads((pathlib.Path(root) / TRADE_DIR / pid / DATA)
                          .read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:  # noqa: BLE001
        return None


def rewrite(root, pid, row) -> bool:
    """Rewrite the page's ``trade.json`` when its bytes change. True when written.
    A page that does not exist is not created here. Never raises."""
    try:
        path = pathlib.Path(root) / TRADE_DIR / pid / DATA
        if not _ID_RE.match(str(pid)) or not (path.parent / PAGE).is_file():
            return False
        data = _json_bytes(row)
        try:
            if path.read_bytes() == data:
                return False
        except OSError:
            pass
        _site._write_atomic(path, data)
        return True
    except Exception:  # noqa: BLE001
        log.warning("trade page: rewrite of %s failed", pid, exc_info=True)
        return False


def _ids(root):
    folder = pathlib.Path(root) / TRADE_DIR
    try:
        return sorted((p.name for p in folder.iterdir()
                       if p.is_dir() and _ID_RE.match(p.name) and (p / PAGE).is_file()),
                      reverse=True)
    except OSError:
        return []


def write_sitemap(root) -> None:
    """``t/sitemap.txt``: every page's address, newest first. Never raises."""
    try:
        folder = pathlib.Path(root) / TRADE_DIR
        folder.mkdir(parents=True, exist_ok=True)
        lines = "".join(address(pid) + "\n" for pid in _ids(root))
        _site._write_atomic(folder / SITEMAP, lines.encode("utf-8"))
    except Exception:  # noqa: BLE001
        log.warning("trade page: sitemap write failed", exc_info=True)


def open_ids(root) -> list:
    """The ids whose result may still move; ``[]`` when none or unreadable."""
    try:
        data = json.loads((pathlib.Path(root) / TRADE_DIR / OPEN_LIST)
                          .read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    return [i for i in data if isinstance(i, str) and _ID_RE.match(i)] \
        if isinstance(data, list) else []


def set_open(root, ids) -> None:
    """Replace the open list. Never raises."""
    try:
        folder = pathlib.Path(root) / TRADE_DIR
        folder.mkdir(parents=True, exist_ok=True)
        _site._write_atomic(folder / OPEN_LIST, json.dumps(list(ids)).encode("utf-8"))
    except Exception:  # noqa: BLE001
        log.warning("trade page: open list write failed", exc_info=True)

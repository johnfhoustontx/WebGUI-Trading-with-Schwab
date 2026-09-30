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
import threading
from zoneinfo import ZoneInfo

import repo_paths
from services.options_svc import trade_idea as _ti

log = logging.getLogger(__name__)

SITE_ROOT = pathlib.Path(repo_paths.SITE_ROOT)   # tests redirect this
IDEAS_DIR = "ideas"
MANIFEST = "ideas.json"
WEB_WIDTH = 1200          # the card is drawn at 2x (2400 wide); half is plenty on a page
WEBP_QUALITY = 86

# An expiring idea settles on its expiry-day close once the session is over
# (15:00 CT); five minutes of slack so the daily candle carries the close.
SETTLE_AFTER = _dt.time(15, 5)

# Every read-modify-write of ideas.json holds this: the post (trade idea branch)
# and the result refresh run on different executor threads of one process.
_LOCK = threading.Lock()

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


def _finite(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and f not in (float("inf"), float("-inf")) else None


def entry_facts(idea, *, approx=False) -> dict:
    """What a result is measured from - the legs, expiry, entry cash (per contract,
    commission in), max loss and the stock price at the post. All of it is already
    printed on the card. ``{}`` when the idea cannot be measured."""
    legs = []
    for lg in idea.get("legs") or []:
        strike = _finite(lg.get("strike")) if isinstance(lg, dict) else None
        if strike is None or lg.get("kind") not in ("call", "put") \
                or lg.get("side") not in ("long", "short"):
            return {}
        legs.append({"side": lg["side"], "kind": lg["kind"], "strike": strike,
                     "qty": int(lg.get("qty") or 1)})
    cash, max_loss = _finite(idea.get("entry_cash")), _finite(idea.get("max_loss"))
    expiration = str(idea.get("expiration") or "")[:10]
    if not legs or cash is None or not max_loss or max_loss <= 0 \
            or not _DAY_RE.match(expiration):
        return {}
    return {"legs": legs, "expiration": expiration, "entry_cash": cash,
            "max_loss": max_loss, "spot": _finite(idea.get("spot")), "approx": bool(approx)}


def _result(facts, spot, now):
    pnl = round(_ti.payoff(facts["legs"], facts["entry_cash"], spot), 2)
    entry_spot = facts.get("spot")
    move = round((spot - entry_spot) / entry_spot * 100.0, 2) if entry_spot else None
    return {"spot": round(spot, 2), "move_pct": move, "pnl": pnl,
            "pnl_pct": round(pnl / facts["max_loss"] * 100.0, 1),
            "as_of": now.isoformat(timespec="seconds")}


def open_result(facts, spot, now) -> dict:
    """An open idea: the stock's move and the EXPIRY payoff at today's price.
    Not a mark - a long option also carries time value, and the page says so."""
    return {"status": "open", **_result(facts, spot, now)}


def settled_result(facts, close, day, now) -> dict:
    """An expired idea, settled at intrinsic against the expiry-day close. Final."""
    return {"status": "expired", **_result(facts, close, now), "settled": day}


def _measurable(i):
    return bool(i.get("legs")) and (i.get("result") or {}).get("status") != "expired"


def _settles(i, now):
    exp, today = i.get("expiration"), f"{now:%Y-%m-%d}"
    return bool(exp) and (exp < today or (exp == today and now.time() >= SETTLE_AFTER))


def due(manifest, now):
    """``(symbols to quote, {(symbol, expiration)} to settle)`` for a refresh."""
    quotes, settle = set(), set()
    for d in _valid_days(manifest):
        for i in d["ideas"]:
            if not _measurable(i):
                continue
            if _settles(i, now):
                settle.add((i["symbol"], i["expiration"]))
            else:
                quotes.add(i["symbol"])
    return quotes, settle


def apply_results(manifest, quotes, closes, now) -> dict:
    """PURE. The manifest with each measurable idea's result recomputed. A missing
    quote or close leaves the previous result (and its time) alone - never a 0."""
    days = _valid_days(manifest)
    for d in days:
        for i in d["ideas"]:
            if not _measurable(i):
                continue
            facts = {k: i.get(k) for k in ("legs", "entry_cash", "max_loss", "spot")}
            if _settles(i, now):
                close = _finite(closes.get((i["symbol"], i["expiration"])))
                if close:
                    i["result"] = settled_result(facts, close, i["expiration"], now)
                continue
            last = _finite(quotes.get(i["symbol"]))
            if last:
                i["result"] = open_result(facts, last, now)
    out = dict(manifest) if isinstance(manifest, dict) else {}
    out["days"] = days
    return out


def refresh(quote_fn, close_fn, now, *, root=None) -> int:
    """Recompute every open idea's result and rewrite the manifest.

    ``quote_fn(symbols) -> {symbol: last}`` is ONE batched call; ``close_fn(symbol,
    day) -> close`` runs once per expiring idea. Returns the number of ideas given a
    new result. Never raises."""
    try:
        path = (pathlib.Path(root) if root is not None else SITE_ROOT) / MANIFEST
        with _LOCK:
            manifest = _read_manifest(path)
            symbols, settle = due(manifest, now)
            if not symbols and not settle:
                return 0
            quotes = quote_fn(sorted(symbols)) if symbols else {}
            closes = {(s, e): close_fn(s, e) for s, e in sorted(settle)}
            before = json.dumps(manifest, sort_keys=True)
            out = apply_results(json.loads(before), quotes or {}, closes, now)
            n = sum(1 for d in out["days"] for i in d["ideas"]
                    if (i.get("result") or {}).get("as_of") == now.isoformat(timespec="seconds"))
            if n:
                _write_atomic(path, json.dumps(out, indent=1).encode("utf-8"))
            return n
    except Exception:  # noqa: BLE001 -- a result refresh must never break the scheduler
        log.warning("trade idea: site result refresh failed", exc_info=True)
        return 0


# ── Schwab /pricehistory candles (raw: epoch-ms stamps) ─────────────────────
# Read raw rather than through proxy_client's DataFrame helpers, whose datetime
# column is NAIVE UTC (the documented trap). Daily candles are stamped midnight
# Central, so the Central date of the stamp is the session date.
_CT = ZoneInfo("America/Chicago")
PRICE_AT_MAX_GAP = _dt.timedelta(minutes=15)


def _candle_time(c):
    try:
        return _dt.datetime.fromtimestamp(int(c["datetime"]) / 1000, _CT)
    except (TypeError, ValueError, KeyError, OverflowError, OSError):
        return None


def close_on(candles, day):
    """The close of the daily candle for ``day`` (YYYY-MM-DD), or None."""
    for c in candles or ():
        t = _candle_time(c) if isinstance(c, dict) else None
        if t is not None and f"{t:%Y-%m-%d}" == day:
            return _finite(c.get("close"))
    return None


def price_at(candles, when):
    """The close of the last minute bar at or before ``when`` - None when there is
    none, or it is more than PRICE_AT_MAX_GAP old (not the price at the post)."""
    best = None
    for c in candles or ():
        t = _candle_time(c) if isinstance(c, dict) else None
        if t is not None and t <= when and (best is None or t > best[0]):
            best = (t, _finite(c.get("close")))
    if best is None or when - best[0] > PRICE_AT_MAX_GAP:
        return None
    return best[1]


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


def publish(idea, png, caption_text, now, *, root=None, keep_days=6, approx=False) -> bool:
    """Write the card, its web copy and the manifest into the site tree.

    The manifest row also carries the idea's entry facts (``entry_facts``) so its
    result can be refreshed. Never raises; False when nothing usable was written."""
    try:
        with _LOCK:
            return _publish(idea, png, caption_text, now, root, keep_days, approx)
    except Exception:  # noqa: BLE001 -- a site write must never cost a post
        log.warning("trade idea: site publish failed", exc_info=True)
        return False


def _publish(idea, png, caption_text, now, root, keep_days, approx):
    root = pathlib.Path(root) if root is not None else SITE_ROOT
    row = entry(idea.get("symbol"), idea.get("label"), idea.get("grade"),
                caption_text, now)
    row.update(entry_facts(idea, approx=approx))
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

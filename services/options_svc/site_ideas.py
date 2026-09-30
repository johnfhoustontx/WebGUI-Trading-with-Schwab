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
import math
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

_CT = ZoneInfo("America/Chicago")
# The expiry-day close: 16:00 ET = 15:00 CT. An idea not closed at a target or
# stop by then settles at intrinsic on that day's close.
SETTLE_AT = _dt.time(15, 0)

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
    if isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def entry_facts(idea, *, approx=False, posted=None) -> dict:
    """What a result is measured from - the legs, structure, expiry, entry cash (per
    contract, commission in), max loss / profit, the stock price at the post and the
    post time. All of it is already printed on the card. ``{}`` when the idea
    cannot be measured."""
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
    return {"legs": legs, "type": str(idea.get("type") or ""), "expiration": expiration,
            "entry_cash": cash, "max_loss": max_loss,
            "max_profit": _finite(idea.get("max_profit")),
            "spot": _finite(idea.get("spot")), "approx": bool(approx),
            "posted": posted.isoformat(timespec="seconds") if posted else None}


# ── The exit rules (config/trade_mgmt.toml, the same numbers the app trades) ──
def exit_levels(facts):
    """``(target, stop)`` in P&L dollars per contract; either may be None.

    Target = ``tp_frac`` of the credit on a credit trade, of the DEBIT on a single
    long option (it has no max profit), and of the max profit on any other debit
    structure (signal_recommender._debit_target_base). Stop = ``stop_mult`` x the
    credit where the structure keeps its loss rules, or ``debit_stop_frac`` of the
    debit where one is set (it ships unset). The delta and time rules are not
    modelled."""
    from shared import trade_mgmt
    rules = trade_mgmt.structure_rules(facts.get("type") or None)
    cash, legs = facts["entry_cash"], facts["legs"]
    tp = _finite(rules.get("tp_frac"))
    if cash >= 0:                                        # credit
        target = tp * cash if tp else None
        mult = _finite(rules.get("stop_mult"))
        stop = mult * cash if mult and rules.get("loss_rules", True) else None
    else:                                                # debit
        debit = -cash
        single_long = len(legs) == 1 and legs[0]["side"] == "long"
        mp = facts.get("max_profit")
        base = debit if single_long or not mp or mp <= 0 else mp
        target = tp * base if tp else None
        frac = _finite(rules.get("debit_stop_frac"))
        stop = frac * debit if frac else None
    rnd = lambda v: None if v is None else round(v, 2)   # noqa: E731
    return rnd(target), rnd(stop)


# ── The option model: Black-Scholes at the entry's implied volatility ─────────
# Stock prices only. The IV is SOLVED from the entry price at the post, then held
# constant - the one assumption the page names ("modelled").
_IV_LO, _IV_HI = 0.01, 5.0


def _calc():
    """options-scanner's pricing module - the ONE home of the rate, the pricer
    and time-to-expiry (CLAUDE.md: never re-declare them)."""
    import sys
    if str(repo_paths.OPTIONS_SCANNER) not in sys.path:
        sys.path.insert(0, str(repo_paths.OPTIONS_SCANNER))
    import options_calculator
    return options_calculator


def _settlement(expiration):
    return _dt.datetime.combine(_dt.date.fromisoformat(expiration), SETTLE_AT, _CT)


def model_pnl(facts, iv, spot, when):
    """P&L per contract (commission in) if the position is worth its model value."""
    oc = _calc()
    T = oc.expiry_time_to_years(when, _dt.date.fromisoformat(facts["expiration"]))
    value = sum((1 if lg["side"] == "long" else -1) * lg["qty"]
                * oc.bs_price(spot, lg["strike"], T, oc.RISK_FREE_RATE, iv, lg["kind"])
                for lg in facts["legs"])
    return value * _ti.MULT + facts["entry_cash"]


def solve_iv(facts):
    """The volatility that prices the position at its entry, or None (no entry
    stock price, no time left, or no volatility reaches the entry price)."""
    spot, posted = facts.get("spot"), facts.get("posted")
    if not spot or not posted:
        return None
    when = _dt.datetime.fromisoformat(posted)
    if when >= _settlement(facts["expiration"]):
        return None
    f = lambda s: model_pnl(facts, s, spot, when)       # noqa: E731
    lo, hi = f(_IV_LO), f(_IV_HI)
    if lo * hi > 0:
        return None
    a, b = _IV_LO, _IV_HI
    for _ in range(80):
        mid = (a + b) / 2
        if (f(mid) > 0) == (hi > 0):
            b = mid
        else:
            a = mid
    return round((a + b) / 2, 6)


def scan(facts, iv, candles, since, now):
    """Walk the 1-minute bars after ``since`` (up to ``now`` and the expiry close)
    and return ``(exit, last_bar_time)``, the order ``_recommend_debit`` checks in:
    stop first, then target. ``exit`` is None when neither was reached.

    Fills, both read cautiously: a TARGET is a resting limit, booked AT the target
    even when the price gapped past it; a STOP is a market order, so a bar that
    OPENS beyond it (an overnight gap) fills at that open's modelled value - worse
    than the level - and one reached intraday fills at the level. A bar that could
    hit both counts as the stop."""
    target, stop = exit_levels(facts)
    end = min(now, _settlement(facts["expiration"]))
    bars = sorted((t, c) for c in candles or () if isinstance(c, dict)
                  for t in [_candle_time(c)] if t is not None and since < t < end)
    last = since
    for t, c in bars:
        prices = [p for p in (_finite(c.get("high")), _finite(c.get("low")),
                              _finite(c.get("close"))) if p]
        if not prices:
            continue
        last = t
        opening = _finite(c.get("open"))
        if opening and stop is not None:
            at_open = model_pnl(facts, iv, opening, t)
            if at_open <= -stop:                          # gapped through the stop
                return _closed(facts, "stop", at_open, opening, t), last
        pnls = [(model_pnl(facts, iv, p, t), p) for p in prices]
        worst, best = min(pnls), max(pnls)
        if stop is not None and worst[0] <= -stop:
            return _closed(facts, "stop", -stop, worst[1], t), last
        if target is not None and best[0] >= target:
            return _closed(facts, "target", target, best[1], t), last
    return None, last


def _move(facts, spot):
    entry = facts.get("spot")
    return round((spot - entry) / entry * 100.0, 2) if entry else None


def _pct(facts, pnl):
    return round(pnl / facts["max_loss"] * 100.0, 1)


def _closed(facts, status, pnl, spot, when):
    return {"status": status, "pnl": round(pnl, 2), "pnl_pct": _pct(facts, pnl),
            "spot": round(spot, 2), "move_pct": _move(facts, spot),
            "closed_at": when.isoformat(timespec="seconds"),
            "as_of": when.isoformat(timespec="seconds")}


def _open(facts, iv, spot, now):
    """Open: the modelled value now, or - with no model - the expiry payoff at
    this price (``basis`` says which, and the page words each differently)."""
    if iv:
        pnl, basis = model_pnl(facts, iv, spot, now), "model"
    else:
        pnl, basis = _ti.payoff(facts["legs"], facts["entry_cash"], spot), "expiry"
    target, _ = exit_levels(facts)
    return {"status": "open", "basis": basis, "pnl": round(pnl, 2),
            "pnl_pct": _pct(facts, pnl), "spot": round(spot, 2),
            "move_pct": _move(facts, spot), "target": target,
            "as_of": now.isoformat(timespec="seconds")}


def _expired(facts, close, now):
    pnl = _ti.payoff(facts["legs"], facts["entry_cash"], close)
    return {"status": "expired", "pnl": round(pnl, 2), "pnl_pct": _pct(facts, pnl),
            "spot": round(close, 2), "move_pct": _move(facts, close),
            "settled": facts["expiration"], "as_of": now.isoformat(timespec="seconds")}


# ── Which ideas a refresh touches ────────────────────────────────────────────
_FINAL = ("target", "stop", "expired")
# Settle only once the bars have been scanned to within this of the close (so a
# late target hit is not missed), or give up waiting after GIVE_UP.
SCAN_COMPLETE_SLACK = _dt.timedelta(minutes=15)
GIVE_UP = _dt.timedelta(days=1)


def _facts_of(i):
    return {k: i.get(k) for k in ("legs", "type", "expiration", "entry_cash",
                                  "max_loss", "max_profit", "spot", "posted")}


def _measurable(i):
    return bool(i.get("legs")) and bool(i.get("posted")) \
        and (i.get("result") or {}).get("status") not in _FINAL


def _since(i):
    return _dt.datetime.fromisoformat(i.get("checked_to") or i["posted"])


def due(manifest, now):
    """``(symbols to quote, {(symbol, expiration)} past expiry, {symbol: earliest
    time its bars are needed from})`` for a refresh."""
    quotes, settle, since = set(), set(), {}
    for d in _valid_days(manifest):
        for i in d["ideas"]:
            if not _measurable(i):
                continue
            sym = i["symbol"]
            start = _since(i)
            if i.get("spot") is None:                   # the entry price is missing
                start = min(start, _dt.datetime.fromisoformat(i["posted"])
                            - PRICE_AT_MAX_GAP)
            since[sym] = min(since.get(sym, start), start)
            if now >= _settlement(i["expiration"]):
                settle.add((sym, i["expiration"]))
            else:
                quotes.add(sym)
    return quotes, settle, since


# Schwab's minute history takes periodType=day with period in {1,2,3,4,5,10}.
_MINUTE_PERIODS = (1, 2, 3, 4, 5, 10)


def minute_days(since, now):
    """The smallest period Schwab accepts that reaches back to ``since``."""
    need = (now.date() - since.astimezone(_CT).date()).days + 1
    return next((p for p in _MINUTE_PERIODS if p >= need), _MINUTE_PERIODS[-1])


def apply_results(manifest, quotes, closes, candles, now) -> dict:
    """PURE. The manifest after one refresh: fill a missing entry price from the
    bars, solve the entry IV once, scan the new bars for a target or stop, then
    settle an expired idea or model an open one. A target, stop or expiry is
    FINAL. A missing quote or close leaves the previous result alone."""
    days = _valid_days(manifest)
    for d in days:
        for i in d["ideas"]:
            if not _measurable(i):
                continue
            posted = _dt.datetime.fromisoformat(i["posted"])
            bars = candles.get(i["symbol"]) or []
            if i.get("spot") is None:
                i["spot"] = price_at(bars, posted)
            facts = _facts_of(i)
            if not i.get("iv") and i.get("spot"):
                i["iv"] = solve_iv(facts)
            iv = i.get("iv")
            if iv:
                hit, last = scan(facts, iv, bars, _since(i), now)
                if last > _since(i):
                    i["checked_to"] = last.isoformat(timespec="seconds")
                if hit:
                    i["result"] = hit
                    continue
            settle_at = _settlement(i["expiration"])
            if now >= settle_at:
                complete = (not iv or _since(i) >= settle_at - SCAN_COMPLETE_SLACK
                            or now >= settle_at + GIVE_UP)
                close = _finite(closes.get((i["symbol"], i["expiration"])))
                if complete and close:
                    i["result"] = _expired(facts, close, now)
                continue
            last_quote = _finite(quotes.get(i["symbol"]))
            if last_quote:
                i["result"] = _open(facts, iv, last_quote, now)
    out = dict(manifest) if isinstance(manifest, dict) else {}
    out["days"] = days
    return out


def refresh(quote_fn, close_fn, now, *, root=None, minutes_fn=None) -> int:
    """Recompute every open idea's result and rewrite the manifest.

    ``quote_fn(symbols) -> {symbol: last}`` is ONE batched call;
    ``minutes_fn(symbol, days) -> raw 1-minute candles`` runs once per symbol with
    open ideas; ``close_fn(symbol, day) -> close`` once per expired idea. Returns
    the number of ideas whose result changed. Never raises."""
    try:
        path = (pathlib.Path(root) if root is not None else SITE_ROOT) / MANIFEST
        with _LOCK:
            manifest = _read_manifest(path)
            symbols, settle, since = due(manifest, now)
            if not symbols and not settle:
                return 0
            quotes = quote_fn(sorted(symbols)) if symbols else {}
            candles = ({s: minutes_fn(s, minute_days(t, now)) for s, t in sorted(since.items())}
                       if minutes_fn else {})
            closes = {(s, e): close_fn(s, e) for s, e in sorted(settle)}
            before = json.loads(json.dumps(manifest))
            out = apply_results(json.loads(json.dumps(manifest)), quotes or {}, closes,
                                candles, now)
            n = sum(1 for d0, d1 in zip(_valid_days(before), out["days"])
                    for a, b in zip(d0["ideas"], d1["ideas"])
                    if a.get("result") != b.get("result"))
            if out != before:
                _write_atomic(path, json.dumps(out, indent=1).encode("utf-8"))
            return n
    except Exception:  # noqa: BLE001 -- a result refresh must never break the scheduler
        log.warning("trade idea: site result refresh failed", exc_info=True)
        return 0


# ── Schwab /pricehistory candles (raw: epoch-ms stamps) ─────────────────────
# Read raw rather than through proxy_client's DataFrame helpers, whose datetime
# column is NAIVE UTC (the documented trap). Daily candles are stamped midnight
# Central, so the Central date of the stamp is the session date.
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
    row.update(entry_facts(idea, approx=approx, posted=now))
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

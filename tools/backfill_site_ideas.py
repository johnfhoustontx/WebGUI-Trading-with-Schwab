"""Put the recent trade ideas on the public site from the card archive.

Each posted trade idea is kept at ``TRADE_IDEAS_DIR/<day>/trade-idea-<day>-<HHMM>-
<SYMBOL>.png`` (+ a ``.txt`` caption) by ``trade_idea.archive``. Publishing to the
site (``services/options_svc/site_ideas.py``) started later, so this republishes
the newest ``keep_days`` days of that archive into ``deploy/site`` -- run once on
the serving box so ideas.html is not empty on its first day. Safe to re-run: a
card already on the site is replaced, never duplicated.

    .venv/bin/python -m tools.backfill_site_ideas            # the [site] keep_days
    .venv/bin/python -m tools.backfill_site_ideas --days 3
    .venv/bin/python -m tools.backfill_site_ideas --no-results   # cards only

By default each card also gets its entry facts, rebuilt from the caption, and the
stock price at its post minute (one 1-minute history call per symbol), and then
every result is computed once (``site_ideas.refresh``) - stock prices only.
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


_LEG = re.compile(r"([+-])([\d,]+(?:\.\d+)?)([CP])\b")
_MONEY = r"\$([\d,]+(?:\.\d+)?)"
_RISK = re.compile(r"\bRisk " + _MONEY)
_PROFIT = re.compile(r"\bProfit (?:" + _MONEY + r"|(Unlimited))")
_MONTHS = {m: n for n, m in enumerate(
    ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), 1)}
# The caption's figures are whole dollars; rebuilt numbers must agree within this.
_AGREE = 1.5


def _money(text):
    return float(text.replace(",", ""))


def facts_from_caption(text, posted):
    """``site_ideas.entry_facts`` rebuilt from a ``trade_idea.caption`` line.

    The caption prints each leg (``-880P / +875P``), the expiry (``Oct 9``) and the
    whole-dollar Risk and Profit, but no quantity and no stock price. The entry cash
    is solved from Risk; the rebuilt legs must then reproduce BOTH figures, or a leg
    the caption could not show (a 1x2) would be measured wrong - so a disagreement
    returns ``{}`` and the card shows no result. Marked ``approx``."""
    from services.options_svc import site_ideas, trade_idea
    parts = (text or "").split(" · ")
    if len(parts) < 2 or not parts[0].startswith("Trade idea: "):
        return {}
    mon, _, rest = parts[1].partition(" ")
    day_text, _, legs_text = rest.partition(" ")
    risk, profit = _RISK.search(text), _PROFIT.search(text)
    if mon not in _MONTHS or not day_text.isdigit() or not risk or not profit:
        return {}
    legs = [{"side": "long" if s == "+" else "short", "kind": "call" if k == "C" else "put",
             "strike": _money(v), "qty": 1} for s, v, k in _LEG.findall(legs_text)]
    if not legs:
        return {}
    try:
        exp = _dt.date(posted.year, _MONTHS[mon], int(day_text))
    except ValueError:
        return {}
    if exp < posted.date():
        exp = exp.replace(year=exp.year + 1)
    max_loss = _money(risk.group(1))
    points = [0.0] + sorted({lg["strike"] for lg in legs})
    cash = -max_loss - min(trade_idea.payoff(legs, 0.0, p) for p in points)
    mp, ml, _ = trade_idea.economics(legs, cash)
    want_mp = None if profit.group(2) else _money(profit.group(1))
    if ml is None or abs(ml - max_loss) > _AGREE:
        return {}
    if (mp is None) != (want_mp is None) or (mp is not None and abs(mp - want_mp) > _AGREE):
        return {}
    label = parts[0][len("Trade idea: "):].partition(" ")[2].strip().lower()
    types = {v.lower(): k for k, v in trade_idea.STRATEGY_LABELS.items()}
    return site_ideas.entry_facts({"legs": legs, "type": types.get(label, ""),
                                   "expiration": exp.isoformat(),
                                   "entry_cash": round(cash, 2), "max_loss": max_loss,
                                   "max_profit": mp}, approx=True, posted=posted)


def backfill(archive_dir, site_root, keep_days, minute_fn=None) -> int:
    """Publish the newest ``keep_days`` archive days, oldest card first (so the
    manifest's trim sees them in posting order). Returns cards published.

    With ``minute_fn(symbol) -> raw 1-minute candles`` each card also gets its entry
    facts (``facts_from_caption``) and the stock price at its post minute, so the
    site can measure how it did. One history call per symbol."""
    candles = {}
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
            facts = facts_from_caption(caption, when) if minute_fn else {}
            if facts:
                sym = idea["symbol"]
                if sym not in candles:
                    candles[sym] = minute_fn(sym)
                facts["spot"] = site_ideas.price_at(candles[sym], when)
                idea.update(facts)
            if site_ideas.publish(idea, png.read_bytes(), caption, when, root=site_root,
                                  keep_days=keep_days, approx=bool(facts)):
                published += 1
    return published


def main(argv=None):
    from shared.notify import switches
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--days", type=int, default=switches.site_settings()["keep_days"])
    ap.add_argument("--no-results", action="store_true",
                    help="publish the cards only: no entry facts, no Schwab calls")
    args = ap.parse_args(argv)
    minute_fn = None
    if not args.no_results:
        from services.options_svc import compute
        minute_fn = compute.minute_candles
    n = backfill(repo_paths.TRADE_IDEAS_DIR, repo_paths.SITE_ROOT, args.days, minute_fn)
    print(f"published {n} trade idea card(s) into {repo_paths.SITE_ROOT}")
    if minute_fn:
        from services.options_svc import compute, site_ideas
        now = _dt.datetime.now(_CT)
        r = site_ideas.refresh(compute.site_idea_quotes, compute.daily_close, now,
                               minutes_fn=compute.minute_candles)
        print(f"results computed for {r} idea(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

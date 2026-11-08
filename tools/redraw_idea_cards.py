"""Redraw the past trade ideas on the public site in the new card (one-off).

Ideas posted before the Social Cards build carry the old image. This reads each
idea in ``ideas.json`` and rebuilds the card from what the site already holds:

    manifest row   symbol, label, type, legs, expiration, entry cash, max loss /
                   profit, stock price, post time; the caption (``alt``) gives the
                   probability of profit
    Ledger row     the score and grade (``idea_score`` / ``idea_grade``)
    economics      the breakevens, computed from the legs and the entry cash

Every figure the card PRINTS must be found. When one is missing the idea keeps its
old image: a card with an invented number is worse than an old card. The expected
move is the one field left empty - the band is a drawing, not a printed figure.

A redrawn row is never listed on the Social page: it was posted before
``[cards] listed_from``. A row already in the new card (``shape == "wide"`` or a
stored ``card``) and a row posted on or after ``listed_from`` are skipped.

``--write`` has no lock against ``options_svc``, which rewrites ``ideas.json`` on
every post and results pass. Run it outside market hours, when no idea is posting.
It re-reads ``ideas.json`` just before it writes, so a row added meanwhile is kept.

    .venv/bin/python -m tools.redraw_idea_cards                    # dry run
    .venv/bin/python -m tools.redraw_idea_cards --root DIR         # a copy of deploy/site
    .venv/bin/python -m tools.redraw_idea_cards --write            # replace the files
"""
import argparse
import datetime as _dt
import json
import logging
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import repo_paths  # noqa: E402,F401 - puts the repo on the path / checks the clock
from services.options_svc import site_ideas, trade_idea, trade_idea_card  # noqa: E402
from shared import card_draw, social_cards  # noqa: E402

log = logging.getLogger(__name__)

_POP = re.compile(r"POP (\d+)%")


def _num(v):
    return site_ideas._finite(v)


def _bias(row, legs):
    kind = str(row.get("type") or "")
    if kind in trade_idea._CREDIT_BIAS:
        return trade_idea._CREDIT_BIAS[kind]
    if len(legs) == 1 and legs[0]["side"] == "long":
        return "bullish" if legs[0]["kind"] == "call" else "bearish"
    return None


def facts_for(row, ledger):
    """``(CARD_FIELDS dict, "")``, or ``(None, name of the missing figure)``.

    ``ledger`` is this idea's Ledger row, or None."""
    ledger = ledger or {}
    legs = []
    for lg in row.get("legs") or []:
        if not isinstance(lg, dict) or _num(lg.get("strike")) is None:
            return None, "legs"
        legs.append({"kind": lg.get("kind"), "side": lg.get("side"),
                     "strike": _num(lg["strike"]), "expiration": row.get("expiration"),
                     "qty": int(lg.get("qty") or 1)})
    if not legs:
        return None, "legs"
    expiration = str(row.get("expiration") or "")[:10]
    try:
        exp_day = _dt.date.fromisoformat(expiration)
        post_day = _dt.datetime.fromisoformat(str(row.get("posted"))).date()
    except (TypeError, ValueError):
        return None, "expiration" if not expiration else "posted"
    bias = _bias(row, legs)
    if bias is None:
        return None, "bias"
    cash, spot = _num(row.get("entry_cash")), _num(row.get("spot"))
    for name, v in (("entry_cash", cash), ("spot", spot)):
        if v is None:
            return None, name
    try:
        max_profit, max_loss, breakevens = trade_idea.economics(legs, cash)
    except Exception:  # noqa: BLE001 -- one odd idea must not abort a --write run
        return None, "breakevens"
    if _num(row.get("max_loss")) is None:
        return None, "max_loss"
    if row.get("max_profit") is None and max_profit is not None:
        return None, "max_profit"
    if not breakevens:
        return None, "breakevens"
    pop = _POP.search(str(row.get("alt") or ""))
    if not pop:
        return None, "pop_pct"
    score = _num(ledger.get("idea_score"))
    if score is None:
        return None, "score"
    grade = ledger.get("idea_grade") or row.get("grade")
    if not grade:
        return None, "grade"
    for name in ("symbol", "label", "type"):
        if not row.get(name):
            return None, name
    return {"symbol": row["symbol"], "type": row["type"], "label": row["label"],
            "bias": bias, "legs": legs, "expiration": expiration,
            "dte": (exp_day - post_day).days, "spot": spot, "grade": grade,
            "score": score, "pop_pct": float(pop.group(1)), "entry_cash": cash,
            "max_profit": _num(row.get("max_profit")), "max_loss": _num(row.get("max_loss")),
            "breakevens": breakevens, "em": None}, ""


def _ideas(manifest):
    for d in (manifest or {}).get("days") or []:
        for i in d.get("ideas") or []:
            if isinstance(i, dict) and isinstance(i.get("full"), str):
                yield i


def _skip_reason(row):
    """Why a row must not be redrawn, or "". The card of a row drawn after launch is
    what X received; a row posted on or after ``listed_from`` would become listed."""
    if row.get("shape") == "wide" or "card" in row:
        return "already in the new card"
    if social_cards.listed(row.get("posted"), social_cards.config()):
        return "posted on or after [cards] listed_from"
    return ""


def _load_ledger():
    try:
        from services.options_svc import idea_ledger
        return idea_ledger.site_rows()
    except Exception as exc:  # noqa: BLE001 -- a tool: say so and carry on without scores
        print(f"ledger unreadable ({exc}); no idea can be redrawn", file=sys.stderr)
        return {}


def main(argv=None, ledger=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", default=None, help="the site folder (default deploy/site)")
    ap.add_argument("--write", action="store_true", help="replace the files (default: dry run)")
    args = ap.parse_args(argv)
    root = pathlib.Path(args.root) if args.root else site_ideas.SITE_ROOT
    manifest = site_ideas.read_manifest(root)
    if not manifest:
        print(f"no ideas.json under {root}", file=sys.stderr)
        return 1
    ledger = ledger if ledger is not None else _load_ledger()
    quality = social_cards.config()["cards"]["jpeg_quality"]

    done = {}        # full path -> the three new fields
    for i in _ideas(manifest):
        why = _skip_reason(i)
        if why:
            print(f"skip   {i['full']} ({i.get('symbol', '')}): {why}")
            continue
        key = site_ideas.ledger_key(i.get("symbol"), i.get("posted"))
        facts, missing = facts_for(i, ledger.get(key))
        name = f"{i['full']} ({i.get('symbol', '')})"
        if facts is None:
            print(f"keep   {name}: no {missing}")
            continue
        if not args.write:
            print(f"redraw {name}")
            continue
        when = _dt.datetime.fromisoformat(i["posted"])
        png = trade_idea_card.render_trade_idea_png(facts, now=when)
        jpg = card_draw.jpeg_from_png(png, quality) if png else None
        if not png or not jpg or site_ideas._png_size(png) != (2400, 1260):
            print(f"keep   {name}: the card did not draw")
            continue
        web = site_ideas.webp_copy(png)
        site_ideas._write_atomic(root / i["full"], png)
        site_ideas._write_atomic(root / (i["full"][:-len(".png")] + ".jpg"), jpg)
        if web and str(i.get("img", "")).endswith(".webp"):
            site_ideas._write_atomic(root / i["img"], web)
        done[i["full"]] = {"jpg": i["full"][:-len(".png")] + ".jpg", "shape": "wide",
                           "card": json.loads(json.dumps(trade_idea_card.card_facts(facts)))}
        print(f"done   {name}")

    if done:
        with site_ideas._LOCK:
            path = root / site_ideas.MANIFEST
            fresh = site_ideas._read_manifest(path) or manifest
            for i in _ideas(fresh):
                i.update(done.get(i["full"], {}))
            site_ideas._write_atomic(path, json.dumps(fresh, indent=1).encode("utf-8"))
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    sys.exit(main())

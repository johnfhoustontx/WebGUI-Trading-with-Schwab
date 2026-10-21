"""Write the week's trade-idea review, unattended.

Runs after the close on the last trading day of the ISO week (a systemd timer
fires it every weekday; it exits quietly on the other days). It reads the
trade-idea rows off the Paper Ledger's published view, computes every number in
code (``shared/idea_review.py``), asks Claude for a judgment that may cite only
those numbers, checks that judgment, and writes one HTML document to
``webgui/data/idea_review/<YYYY-Www>/report.html``, which the app shows.

**The bus is read exactly as the EOD report reads it**: ``pages.eod.
read_snapshot()``, with the repo root and ``webgui`` on ``sys.path``, so the two
tools cannot come to disagree about what the Ledger holds.

**Claude runs on the subscription, through the Claude Code CLI, and only
there.** The user asked for the weekly query "instead of the API call", so this
tool builds a ``CliMessagesClient`` itself and never the briefings' fallback
client, which would quietly repeat a failed run on the paid key. When the CLI
cannot answer, the report is still written with every number and one line
saying the judgment is missing, and the tool exits 3 so the unit's failure
alert fires.

**Claude's text is checked, not trusted.** A suggestion must cite fact ids that
exist and may state only the numbers of the facts it cites; a paragraph holding
a number the facts do not hold anywhere is dropped whole (see :func:`verify`).
Code states the facts; the model only joins them and adds judgment. So every
figure that survives is one the app computed, but whether Claude used it with
the right subject or sign is NOT checked: "SPY lost 100.00" passes when SPY
made 100.00, and a paragraph may pin one group's figure on another, so the
prompt tells Claude to keep each figure's sign and subject.

Exit codes: 0 written (or not the review day), 2 the bus was unreadable or the
Ledger view was missing or held no rows at all (nothing written: a page of
empty tables would replace a real week's review), 3 written without Claude's
judgment.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
from datetime import date, datetime
from html import escape as _escape
from zoneinfo import ZoneInfo

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
for _p in (str(_REPO_ROOT), str(_REPO_ROOT / "webgui")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from repo_paths import ENV_FLAGS  # noqa: E402
from shared import idea_review  # noqa: E402

CT = ZoneInfo("America/Chicago")
ARCHIVE_ROOT = _REPO_ROOT / "webgui" / "data" / "idea_review"
PROMPT_FILE = pathlib.Path(__file__).resolve().parent / "prompts" / "weekly_idea_review.md"
TOOL_NAME = "weekly_judgment"
# The judgment's length cap, as the Messages API shape asks for one. It is NOT a
# setting: the review runs on the Claude Code CLI, and claude_cli._request_shape
# does not pass max_tokens to `claude -p` (the CLI has no per-call output cap),
# so the value is inert on that path. It matters only if this call is ever moved
# to the API, which is a code change anyway.
MAX_TOKENS = 4000

JUDGMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "assessment": {"type": "array", "items": {"type": "string"}},
        "suggestions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "cites": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["text", "cites"],
            },
        },
    },
    "required": ["assessment", "suggestions"],
}

MISSING_LINE = ("Claude's assessment is missing this week; the numbers below "
                "are complete.")
THIN_LABEL = "too few trades to act on"
# What the check behind the assessment proves, and no more (see verify).
TRUST_LINE = ("Every figure in Claude's assessment is one the app computed; "
              "whether Claude used it with the right subject is not checked.")

_DIM_TITLES = {
    "grade": "By grade",
    "strategy": "By structure",
    "trade_type": "By trade type",
    "score_band": "By score band",
    "post_hour": "By hour posted (Central)",
    "symbol": "By symbol",
}


def escape(text) -> str:
    """HTML-escape text content (quotes stay as written: nothing here goes in
    an attribute)."""
    return _escape(text, quote=False)


def _cfg() -> dict:
    from shared import paper_limits
    return paper_limits.idea_trades()


def _eod():
    """The webgui EOD module, imported lazily (NiceGUI costs ~2 s to import,
    and a day that is not the review day should not pay it)."""
    from pages import eod
    return eod


def week_label(d: date) -> str:
    """``2026-W43``: the ISO year and week of ``d``, the archive folder's name."""
    year, week, _ = d.isocalendar()
    return f"{year}-W{week:02d}"


# ---- checking Claude's judgment -------------------------------------------

# A signed number, optionally with a dollar sign, thousands commas and a
# percent sign. A trailing period is not captured (no digit follows it).
_NUMBER = re.compile(r"-?\$?\d[\d,]*(?:\.\d+)?%?")
# Terms that hold a digit (or a number word) but state no figure.
_VOCABULARY = ("0-DTE", "0DTE", "zero-DTE")

# A figure written in words is still a figure. "one" is left out on purpose:
# it is far more often a pronoun ("one of the strongest groups") than a count,
# and reading it as 1 would drop honest text. Inside a hyphenated compound
# ("twenty-one") it is read, because there it can only be a number.
_UNITS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve "
    "thirteen fourteen fifteen sixteen seventeen eighteen nineteen "
    "twenty".split())}
_TENS = {w: 10 * i for i, w in enumerate(
    "twenty thirty forty fifty sixty seventy eighty ninety".split(), start=2)}
_WORDS = {**{w: v for w, v in _UNITS.items() if w != "one"}, **_TENS,
          "hundred": 100, "half": 0.5, "twice": 2, "double": 2, "triple": 3}
_WORD = re.compile(r"[a-z]+(?:-[a-z]+)*")


def _group_facts(f: dict):
    """Every citable fact: each scope's total and its group rows."""
    for scope in ("week", "all_time"):
        s = f.get(scope) or {}
        if s.get("total"):
            yield s["total"]
        for dim in idea_review.DIMENSIONS:
            yield from s.get(dim) or ()


def _labels(f: dict) -> list[str]:
    """Text that names something rather than stating a figure: every fact id,
    and every group name that is not a bare number (``60-69``, ``80+``,
    ``0-DTE``). Longest first, so an id is removed before its own tail."""
    ids = idea_review.fact_ids(f)
    names = {i.split(".", 2)[2] for i in ids if i.count(".") >= 2}
    names = {n for n in names if any(c.isdigit() for c in n) and not n.isdigit()}
    return sorted(ids | names | set(_VOCABULARY), key=len, reverse=True)


def _word_values(text: str) -> list[float]:
    """The figures ``text`` writes in words: each number word on its own, and
    a hyphenated tens-and-unit compound ("twenty-five") as one value."""
    out = []
    for token in _WORD.findall(text.lower()):
        parts = token.split("-")
        if len(parts) == 2 and parts[0] in _TENS and parts[1] in _UNITS \
                and 0 < _UNITS[parts[1]] < 10:
            out.append(float(_TENS[parts[0]] + _UNITS[parts[1]]))
            continue
        out.extend(float(_WORDS[w]) for w in parts if w in _WORDS)
    return out


def _numbers_ok(text: str, allowed: list[float], labels: list[str]) -> bool:
    for label in labels:
        text = re.sub(re.escape(label), " ", text, flags=re.IGNORECASE)
    values = []
    for token in _NUMBER.findall(text):
        try:
            values.append(float(token.replace("$", "").replace(",", "")
                                .replace("%", "")))
        except ValueError:
            return False
    values += _word_values(text)
    return all(any(abs(v - a) <= 0.005 for a in allowed) for v in values)


_TOTALS = ("week.total", "all_time.total")


def _floats(strings) -> list[float]:
    out = []
    for s in strings:
        try:
            out.append(float(s))
        except ValueError:
            continue
    return out


def _cited_numbers(cites, by_id) -> list[float]:
    """The figures a suggestion may state: those of the facts it cites. A group
    fact carries its own figures and no rows, so these are its fields."""
    out: set[str] = set()
    for c in cites:
        out |= idea_review.numbers_of(by_id[c])
    return _floats(out)


def verify(judgment: dict, f: dict, min_n: int) -> dict:
    """Keep only what the facts support.

    A suggestion is dropped when its ``cites`` is empty or names an id not in
    ``fact_ids(f)``, or when it holds a number that is not, within 0.005, one of
    the numbers of the facts it CITES (``numbers_of`` each cited fact: a group
    fact carries its own figures, not its rows). An assessment paragraph cites
    nothing, so it is dropped whole when it holds a number that is not one of
    ``fact_numbers(f)``. ``$``, ``,`` and ``%`` are stripped first, so "62.5",
    "62.50" and "62.50%" all match 62.50.
    A number written in words ("nine", "twenty-five", "half", "twice") is
    checked the same way; "one" is not read as a number (see ``_WORDS``). Fact
    ids and group names are not read as numbers.

    A kept suggestion carries ``n``, the LARGEST ``n`` among the facts it
    cites (cited groups overlap, so a sum would count one trade several times),
    and ``thin``, true when that ``n`` is under ``min_n``. The two totals
    (``week.total``, ``all_time.total``) count only when nothing else is cited:
    citing the whole book beside a thin group does not make the group's
    pattern less thin.

    Raises ``ValueError`` when ``judgment`` is not the schema's shape: a reply
    that is not a judgment at all is a failed run, not an empty one.
    """
    if not isinstance(judgment, dict) \
            or not isinstance(judgment.get("assessment"), list) \
            or not isinstance(judgment.get("suggestions"), list):
        raise ValueError("Claude's reply is not the weekly_judgment shape")
    by_id = {x["id"]: x for x in _group_facts(f)}
    allowed = _floats(idea_review.fact_numbers(f))
    labels = _labels(f)

    paragraphs = [p for p in judgment["assessment"]
                  if isinstance(p, str) and p.strip()]
    kept_paragraphs = [p for p in paragraphs if _numbers_ok(p, allowed, labels)]

    kept, dropped = [], 0
    for s in judgment["suggestions"]:
        text = s.get("text") if isinstance(s, dict) else None
        cites = s.get("cites") if isinstance(s, dict) else None
        if not isinstance(text, str) or not text.strip() \
                or not isinstance(cites, list) or not cites \
                or not all(isinstance(c, str) and c in by_id for c in cites) \
                or not _numbers_ok(text, _cited_numbers(cites, by_id), labels):
            dropped += 1
            continue
        cites = list(dict.fromkeys(cites))
        groups = [c for c in cites if c not in _TOTALS] or cites
        n = max(int(by_id[c]["n"]) for c in groups)
        kept.append({"text": text, "cites": cites, "n": n,
                     "thin": n < int(min_n)})
    return {
        "assessment": kept_paragraphs,
        "suggestions": kept,
        "dropped_paragraphs": len(paragraphs) - len(kept_paragraphs),
        "dropped_suggestions": dropped,
    }


def _rounded(v):
    """The facts as Claude is sent them: every float at two decimals, the way
    the report prints it, so the figure Claude copies is the figure shown."""
    if isinstance(v, float):
        return round(v, 2)
    if isinstance(v, dict):
        return {k: _rounded(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_rounded(x) for x in v]
    return v


def ask_claude(client, f: dict, model: str) -> dict:
    """Claude's raw judgment (a dict); raises on any failure."""
    resp = client.messages.create(
        model=model,
        max_tokens=MAX_TOKENS,
        system=PROMPT_FILE.read_text(encoding="utf-8"),
        messages=[{"role": "user", "content": json.dumps(_rounded(f))}],
        tools=[{"name": TOOL_NAME, "input_schema": JUDGMENT_SCHEMA}],
        tool_choice={"type": "tool", "name": TOOL_NAME},
    )
    return resp.content[0].input


# ---- the document -----------------------------------------------------------

def _money(v) -> str:
    if v is None:
        return "—"
    return f"{'-' if v < 0 else ''}${abs(v):,.2f}"


def _pct(v) -> str:
    return "—" if v is None else f"{v:.2f}%"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _held(hours) -> str:
    """In whole words: "1 day 3 hours" from a day on, "2 hours 5 minutes"
    under it; a zero part is left out."""
    if hours is None:
        return "—"
    mins = int(round(hours * 60))
    days, rem = divmod(mins, 1440)
    if days:
        parts = [_plural(days, "day")]
        parts += [_plural(rem // 60, "hour")] if rem // 60 else []
    else:
        parts = [_plural(rem // 60, "hour")] if rem // 60 else []
        parts += [_plural(rem % 60, "minute")] if rem % 60 or not parts else []
    return " ".join(parts)


# The Ledger's exit reasons in plain words.
_REASONS = {"TARGET": "Target reached", "STOP": "Stopped out",
            "EXPIRED": "Expired", "MANUAL": "Closed by hand"}


def _pn(v) -> str:
    return "" if not v else ("pos" if v > 0 else "neg")


def _td(text, cls="") -> str:
    attr = f' class="{cls}"' if cls else ""
    return f"<td{attr}>{escape('' if text is None else str(text))}</td>"


def _table(headers, rows, empty) -> str:
    if not rows:
        return f'<p class="none">{escape(empty)}</p>'
    head = "".join(f"<th>{escape(h)}</th>" for h in headers)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(rows)}</tbody></table>"


def _judgment_html(judgment) -> str:
    if judgment is None:
        return f'<h2>Assessment</h2><p class="summary-line">{escape(MISSING_LINE)}</p>'
    paras = "".join(f"<p>{escape(p)}</p>" for p in judgment["assessment"]) \
        or '<p class="none">Nothing in Claude\'s assessment survived the check against the facts.</p>'
    items = []
    for s in judgment["suggestions"]:
        n = s["n"]
        basis = f"based on a group of {_plural(n, 'trade')}"
        if s["thin"]:
            basis += f"; {THIN_LABEL}"
        items.append(f"<li>{escape(s['text'])} "
                     f'<span class="book-note">({escape(basis)}; '
                     f'{escape(", ".join(s["cites"]))})</span></li>')
    sugg = f"<ul>{''.join(items)}</ul>" if items else \
        '<p class="none">No suggestions this week.</p>'
    dropped = judgment.get("dropped_paragraphs", 0) + judgment.get("dropped_suggestions", 0)
    note = (f'<p class="book-note">{dropped} part{"" if dropped == 1 else "s"} of '
            f"Claude's reply {'was' if dropped == 1 else 'were'} left out for "
            "citing a fact that does not exist, or for stating a number the app "
            "did not compute (for a suggestion: for the groups it cites).</p>") \
        if dropped else ""
    return f"<h2>Assessment</h2>{paras}<h2>Suggestions</h2>{sugg}{note}"


# The Ledger's own money convention: an entry is + for a credit received and -
# for a debit paid; an exit is what closing cost a credit trade, or what closing
# brought in for a debit trade.
_CLOSED_COLS = ["Symbol", "Structure", "Grade", "Rating",
                "Entry (+ credit received, − debit paid)",
                "Exit (credit trade: paid to close; debit trade: received)",
                "Reason", "Held", "MAE % of max loss", "P&L"]
_OPEN_COLS = ["Symbol", "Structure", "Grade", "Days open", "MAE $ per contract",
              "MAE % of max loss", "Open P&L"]
_GROUP_COLS = ["Group", "Trades", "Targets", "Stops", "Expired", "By hand",
               "Win rate", "Average P&L", "Total P&L", "Average MAE % of max loss",
               "Average held", ""]


def _closed_rows(lines) -> list[str]:
    return ["<tr>" + _td(t["symbol"]) + _td(t["strategy"]) + _td(t["grade"])
            + _td(t["rating"]) + _td(_money(t.get("entry")))
            + _td(_money(t.get("exit")))
            + _td(_REASONS.get(t.get("exit_reason"), t.get("exit_reason") or "—"))
            + _td(_held(t.get("held_h"))) + _td(_pct(t.get("mae_pct")))
            + _td(_money(t["realized_pnl"]), _pn(t["realized_pnl"])) + "</tr>"
            for t in lines]


def _open_rows(lines) -> list[str]:
    return ["<tr>" + _td(t["symbol"]) + _td(t["strategy"]) + _td(t["grade"])
            + _td("—" if t.get("days_open") is None else t["days_open"])
            + _td(_money(t.get("mae_pnl")), _pn(t.get("mae_pnl")))
            + _td(_pct(t.get("mae_pct")))
            + _td(_money(t.get("unrealized_pnl")), _pn(t.get("unrealized_pnl")))
            + "</tr>"
            for t in lines]


def _group_row(x: dict, label: str) -> str:
    return ("<tr>" + _td(label) + _td(x["n"]) + _td(x["targets"]) + _td(x["stops"])
            + _td(x["expired"]) + _td(x["manual"]) + _td(_pct(x["win_rate"]))
            + _td(_money(x["avg_pnl"]), _pn(x["avg_pnl"]))
            + _td(_money(x["total_pnl"]), _pn(x["total_pnl"]))
            + _td(_pct(x["avg_mae_pct"])) + _td(_held(x["avg_held_h"]))
            + _td("few trades" if x["thin"] else "") + "</tr>")


def _scope_html(scope: dict, title: str) -> str:
    parts = [f"<h2>{escape(title)}</h2>",
             _table(_GROUP_COLS, [_group_row(scope["total"], "All trade ideas")],
                    "No closed trades.")]
    for dim in idea_review.DIMENSIONS:
        rows = [_group_row(x, x["id"].split(".", 2)[2]) for x in scope.get(dim) or ()]
        parts.append(f"<h3>{escape(_DIM_TITLES[dim])}</h3>")
        parts.append(_table(_GROUP_COLS, rows, "No closed trades."))
    return "".join(parts)


def render(f: dict, judgment: dict | None, week: str) -> str:
    """The whole review as a standalone HTML document. PURE but for the theme."""
    eod = _eod()
    year, wk = week.split("-W")
    monday = date.fromisocalendar(int(year), int(wk), 1)
    sunday = date.fromisocalendar(int(year), int(wk), 7)
    span = (f"{monday:%A} {monday.day} {monday:%B} to "
            f"{sunday:%A} {sunday.day} {sunday:%B %Y}")
    repeats = "".join(
        f"<li>{escape(str(r['idea_id']))}: booked {r['count']} times</li>"
        for r in f.get("repeats") or ())
    fragment = (
        '<div class="eod-report">'
        f"<h1>Weekly trade-idea review, {escape(week)}</h1>"
        f'<p class="meta">{escape(span)}. Every number below is computed from '
        "the Paper Ledger's trade-idea rows. " + escape(TRUST_LINE)
        + " A group marked 'few trades' holds too few to act on.</p>"
        + _judgment_html(judgment)
        + "<h2>This week's closed trades</h2>"
        + _table(_CLOSED_COLS, _closed_rows(f.get("closed_week") or ()),
                 "No trade ideas closed this week.")
        + "<h2>Open trades</h2>"
        + _table(_OPEN_COLS, _open_rows(f.get("open") or ()),
                 "No open trade ideas.")
        + (f"<h3>Ideas booked more than once this week</h3><ul>{repeats}</ul>"
           if repeats else "")
        + _scope_html(f["week"], "This week, by group")
        + _scope_html(f["all_time"], "All time, by group")
        + "</div>")
    from pages.options import theme
    return eod.wrap_document(fragment, eod.build_eod_css(theme.THEME),
                             f"Weekly review {week}")


# ---- the run ------------------------------------------------------------------

def parse_args(argv):
    p = argparse.ArgumentParser(
        description="Write the week's trade-idea review to the archive.")
    p.add_argument("--date", type=date.fromisoformat, default=None,
                   help="the run date, YYYY-MM-DD (default: today, Central)")
    p.add_argument("--force", action="store_true",
                   help="run even when the date is not the last trading day "
                        "of its week")
    return p.parse_args(argv)


def _default_client():
    if not ENV_FLAGS.get("allow_claude", True):
        raise RuntimeError("Claude is switched off in this environment")
    from services.options_svc.claude_cli import CliMessagesClient
    return CliMessagesClient(cli_path=os.environ.get("CLAUDE_CLI_PATH"),
                             token_file=os.environ.get("CLAUDE_CLI_TOKEN_FILE"))


def main(argv=None, *, client=None, read_snapshot=None, archive_root=None) -> int:
    """Run the review. ``client``, ``read_snapshot`` and ``archive_root`` are
    for tests; production uses the CLI client, ``pages.eod.read_snapshot`` and
    :data:`ARCHIVE_ROOT`."""
    args = parse_args(sys.argv[1:] if argv is None else list(argv))
    today = args.date or datetime.now(CT).date()
    if not args.force and not idea_review.is_review_day(today):
        print(f"Skipped: {today:%Y-%m-%d} is not the last trading day of its "
              f"week. Nothing written. Use --force to run anyway.")
        return 0

    try:
        snap = (read_snapshot or (lambda: _eod().read_snapshot()))()
        view = snap.get("paper_trades") if isinstance(snap, dict) else None
        trades = view.get("trades") if isinstance(view, dict) else None
    except Exception as exc:
        print(f"REFUSED: the bus could not be read ({type(exc).__name__}: {exc}). "
              "Nothing written.", file=sys.stderr)
        return 2
    # A list with no rows at all is refused too: the Ledger always holds the
    # user's own trades beside the ideas, so a view with nothing in it is one
    # that was not really answered, not a quiet week.
    if not isinstance(trades, list) or not trades:
        print("REFUSED: the Paper Ledger view (options:paper_trades) was empty. "
              "Nothing written -- a review of empty tables would replace this "
              "week's real one. Is the stack up, and does this process have the "
              "bus credentials (MEMURAI_PASSWORD)?", file=sys.stderr)
        return 2

    cfg = _cfg()
    f = idea_review.facts(trades, today, cfg)
    week = week_label(today)

    judgment = None
    try:
        raw = ask_claude(client or _default_client(), f, cfg["review_model"])
        judgment = verify(raw, f, cfg["min_n"])
    except Exception as exc:
        print(f"Claude's judgment is missing ({type(exc).__name__}: {exc}); "
              "writing the numbers only.", file=sys.stderr)

    out = (pathlib.Path(archive_root) if archive_root else ARCHIVE_ROOT) / week / "report.html"
    doc = render(f, judgment, week)
    out.parent.mkdir(parents=True, exist_ok=True)
    _eod().atomic_write_text(out, doc)   # the app never serves a half-written file
    print(f"Weekly idea review written for {week}: {out}")
    return 0 if judgment is not None else 3


if __name__ == "__main__":
    sys.exit(main())

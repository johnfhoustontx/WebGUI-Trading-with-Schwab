"""Debate-replay scoring — PURE arithmetic over closed signals.

Version: 1.0.0
Last Updated: 2026-08-28

The falsification harness behind the Research Desk proposal
(``docs/plans/2026-08-28-research-desk-design.md``). It asks one question:

    On signals this stack ALREADY graded and ALREADY closed, would a bull/bear
    debate have improved on ``entry_grade``?

Nothing here reads a database, calls a model, or raises. The I/O lives in
``tools/replay_debate.py`` — same split as ``labeler`` / ``label_journal``.

**The null model is the whole design.** 78% of the closed signals in
``signals.db`` won. A debater that approves everything scores 78% and looks
skilled; one that approves only what it likes scores whatever selection bias
buys it. So ``base_rate`` is computed first, ``lift`` (approved minus base) is
the only headline, and the two ways of reaching a high approved-rate without
skill — approve everything, veto everything — are named STATUSES rather than
numbers.

**Why the debater is not shown the grade.** ``build_case`` withholds
``entry_grade`` and ``entry_score``. Handing a model the number it is being
compared against measures how well it echoes that number; the comparison only
means something if the two reads are independent.

**Why the whitelist.** ``build_case`` enumerates what the debater may see
rather than removing what it may not. A blacklist admits every column added to
``signals`` after this file was written — and the columns most likely to be
added to a signals table are outcome columns.
"""
import math
import re

# Below this the sample is not a small effect, it is no measurement. Mirrors
# ``services/trade_svc/live_ic.MIN_READINGS`` and its reasoning: the edge being
# looked for is a few points of win rate, so a number computed from a dozen
# rows would be sampling noise printed to two decimals.
MIN_CASES = 20

# A cell (vetoed, promoted) below this reports its COUNT but not a rate. These
# are the smallest and most over-read cells in the table — "the debate vetoed 3
# signals and 2 lost" is a fact; "67% veto accuracy" is a fiction.
MIN_CELL = 10

# Outside this band the debate has not discriminated, it has agreed. Reported
# as a status because a lift computed against a 99% approval rate is arithmetic
# on a sample of one.
DEGENERATE_LO = 0.05
DEGENERATE_HI = 0.95

# How far a sample's base rate may sit from the population's before the
# report calls the sample unrepresentative. Wide on purpose: this catches a
# broken sampler, not ordinary sampling noise.
BASE_RATE_TOLERANCE = 0.15

# The entry-time context a debater may see. Everything absent from this tuple
# is withheld, including anything added to the table later.
CASE_FIELDS = (
    "signal_id",
    "symbol",
    "scanner_type",
    "strategy",
    "short_strike",
    "long_strike",
    "width",
    "expiration",
    "dte_at_entry",
    "entry_credit",
    "entry_max_loss",
    "entry_short_delta",
    "entry_net_theta",
    "entry_net_delta_position",
    "entry_spread_bid",
    "entry_spread_ask",
    "entry_iv_rank",
    "entry_underlying",
    "first_seen_date",
)

VERDICTS = ("TAKE", "PASS")


def _num(v):
    """A real number, or None. Rejects NaN and bool (``float(True)`` is 1.0)."""
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def build_case(row):
    """The debater's view of one signal — a whitelist of entry-time context.

    ``row`` is a ``sqlite3.Row`` or any Mapping. Missing fields are omitted
    rather than filled: a prompt saying ``iv_rank: None`` invites the model to
    reason about the absence, which is not the experiment."""
    if not row:
        return {}
    case = {}
    for field in CASE_FIELDS:
        try:
            value = row[field]
        except (KeyError, IndexError):
            continue
        if value is not None:
            case[field] = value
    return case


def outcome_of(outcome_row):
    """``"win"`` / ``"loss"`` / ``None`` for a signal that has not closed.

    Exactly zero is a LOSS, not a win: the position paid commissions to arrive
    back where it started, and this repo's economics rule folds commissions into
    every P&L. ``None`` propagates so ``score`` can drop the row rather than
    guess."""
    if not outcome_row:
        return None
    pnl = _num(outcome_row.get("realized_pnl")
               if hasattr(outcome_row, "get") else outcome_row["realized_pnl"])
    if pnl is None:
        return None
    return "win" if pnl > 0 else "loss"


def verdict_from_text(text):
    """``"TAKE"`` / ``"PASS"`` parsed from a judge response, else ``None``.

    Reads the LAST ``VERDICT:`` line, because models routinely restate the
    required format before answering ("I will end with VERDICT: TAKE or
    VERDICT: PASS") and the first match would capture the instruction.

    An unparseable response returns ``None`` and is DROPPED by ``score`` — never
    defaulted to TAKE. Defaulting would pad the approve side with every
    malformed answer, and the approve side is the one measured against the base
    rate."""
    if not isinstance(text, str) or not text.strip():
        return None
    found = None
    for line in text.splitlines():
        upper = line.upper()
        if "VERDICT:" not in upper:
            continue
        tail = upper.split("VERDICT:", 1)[1]
        # First whole word after the colon; a trailing em-dash rationale on the
        # same line is common and must not defeat the match.
        for token in tail.replace("*", " ").replace("—", " ").split():
            token = token.strip(".,:;`-").upper()
            if token in VERDICTS:
                found = token
                break
    return found


# ── the manual (Claude Chat) path ───────────────────────────────────────────
#
# Running the debate by hand instead of through the API changes ONE thing that
# can invalidate the whole measurement: the verdicts come back as pasted text,
# so a verdict can attach to the wrong signal. Position-matching would do that
# silently and every outcome in the sample would then be scored against
# somebody else's call. So the id travels in the prompt, comes back in the
# answer, and ``apply_results`` matches on it — never on order.
#
# ⚠ A batched prompt is a WEAKER test than the API path, and the difference
# should travel with any number it produces. In the API path bull and bear are
# independent generations; in a batch they are one pass, and the model can see
# the whole cross-section at once, which lets it calibrate how many to pass on.
# ``batch_size=1`` recovers the faithful form and is the way to check whether
# batching moved the answer.

_ID_VERDICT = re.compile(
    r"^\W*([0-9A-Za-z_-]{4,64})\s*(?:\||:|-|\t|\s)\s*\**\s*(TAKE|PASS)\b",
    re.IGNORECASE | re.MULTILINE,
)


def batch_views(views, batch_size):
    """Split ``views`` into consecutive batches. Order is preserved."""
    views = list(views or [])
    size = max(1, int(batch_size or 1))
    return [views[i:i + size] for i in range(0, len(views), size)]


def render_prompt(views, batch_no=1, batch_total=1):
    """A self-contained prompt for one batch, to paste into Claude Chat.

    Carries the same whitelisted view the API path sends, so the blind property
    survives the copy-paste boundary: no grade, no score, no outcome, and no
    hint at how many of these are expected to be taken. A stated target rate
    would turn the exercise into following an instruction, and the rate is the
    thing being measured."""
    lines = [
        f"# Trade review - batch {batch_no} of {batch_total}",
        "",
        "You are a trading desk running a structured review of defined-risk",
        "options credit spreads that were proposed for entry. For EACH trade",
        "below, in order:",
        "",
        "1. **Bull** - the strongest honest case FOR taking it: what has to be",
        "   true, and what in the setup supports it.",
        "2. **Bear** - the strongest honest case AGAINST: what breaks it, and",
        "   what the credit is not paying for.",
        "3. **Verdict** - as portfolio manager, weigh the two and decide.",
        "",
        "Judge each trade on its own risk-adjusted merit: does the credit",
        "compensate for the risk actually being taken? You are selecting from a",
        "stream of candidates, so 'acceptable' is not enough - pass on anything",
        "you would not actively choose. Judge each trade independently; do not",
        "aim for any particular number of TAKEs across the batch.",
        "",
        "Keep the bull and bear to two sentences each.",
        "",
        "## Trades",
        "",
    ]
    for v in views:
        lines.append(f"### {v.get('signal_id')}")
        for key in CASE_FIELDS:
            if key == "signal_id" or key not in v:
                continue
            lines.append(f"- {key}: {v[key]}")
        lines.append("")
    lines += [
        "## Required output",
        "",
        "After the reasoning, end your reply with a single fenced code block",
        "containing one line per trade, using the id exactly as given above:",
        "",
        "```",
        "<id> | TAKE",
        "<id> | PASS",
        "```",
        "",
        "Every id above must appear exactly once. Use only TAKE or PASS.",
    ]
    return "\n".join(lines)


def parse_results(text):
    """``{signal_id: "TAKE"|"PASS"}`` from a pasted reply. Never raises.

    Tolerant of the prose and fencing a chat wraps around a table, and of the
    separators models actually use (``|``, ``:``, ``-``, tab, space). A line
    carrying no recognisable verdict is skipped rather than guessed at."""
    if not isinstance(text, str):
        return {}
    out = {}
    for match in _ID_VERDICT.finditer(text):
        sid, verdict = match.group(1), match.group(2).upper()
        if sid.upper() in VERDICTS:          # a header row, not an id
            continue
        out[sid] = verdict
    return out


def apply_results(cases, results):
    """Attach parsed verdicts to ``cases`` in place, matching on ``signal_id``.

    Returns counts, because each one is a distinct failure worth seeing:

      ``applied``  verdicts attached
      ``missing``  cases the reply never mentioned — left unjudged and DROPPED
                   by ``score``, never defaulted to TAKE
      ``unknown``  ids in the reply that were never sent (a hallucinated or
                   mistyped row) — refused rather than silently added
      ``bad``      a word that is neither TAKE nor PASS

    Idempotent: re-pasting a batch overwrites with the same value rather than
    double-counting."""
    results = dict(results or {})
    by_id = {c.get("signal_id"): c for c in (cases or [])}
    applied = bad = 0
    for sid, verdict in results.items():
        case = by_id.get(sid)
        if case is None:
            continue
        if verdict not in VERDICTS:
            bad += 1
            continue
        case["verdict"] = verdict
        applied += 1
    unknown = sum(1 for sid in results if sid not in by_id)
    missing = sum(1 for c in (cases or []) if c.get("verdict") not in VERDICTS)
    return {"applied": applied, "missing": missing, "unknown": unknown,
            "bad": bad}


def _rate(wins, n):
    return (wins / n) if n else None


def _cell(cases):
    """Summarise one subset. A cell below ``MIN_CELL`` keeps its counts and
    reports ``accuracy`` as None rather than a rate it cannot support."""
    n = len(cases)
    won = sum(1 for c in cases if c["outcome"] == "win")
    lost = n - won
    return {
        "n": n,
        "would_have_won": won,
        "would_have_lost": lost,
        # For a VETO, being right means the signal would have lost.
        "accuracy": _rate(lost, n) if n >= MIN_CELL else None,
    }


def _subset_stats(cases):
    n = len(cases)
    approved = [c for c in cases if c["verdict"] == "TAKE"]
    base = _rate(sum(1 for c in cases if c["outcome"] == "win"), n)
    app = _rate(sum(1 for c in approved if c["outcome"] == "win"), len(approved))
    return {
        "n": n,
        "base_rate": base,
        "approved_n": len(approved),
        "approval_rate": _rate(len(approved), n),
        "approved_rate": app,
        "lift": (app - base) if (app is not None and base is not None) else None,
    }


def score(cases, population_base_rate=None):
    """Compare the debate against the base rate. Never raises.

    ``population_base_rate`` is the win rate over the whole eligible
    population. When given, the sample's own base rate is checked against it
    and ``sample_is_unrepresentative`` is set — because a biased SAMPLE is
    indistinguishable from a real effect on the face of a report, and the first
    run of this harness proved it: a survivorship-filtered draw came back at 4%
    against a population of 80.9%, and every downstream number was arithmetic
    on a sample where vetoing everything was 96% accurate by construction.
    ``None`` when no population rate was supplied — no claim either way.

    ``cases`` are dicts carrying ``entry_grade``, ``verdict`` and ``outcome``.
    Rows missing a verdict (unparseable) or an outcome (still open) are counted
    in ``dropped`` and excluded — they are "not measured", which is different
    from a neutral result.

    ``status`` gates how the numbers should be read:

      ``thin``                    fewer than ``MIN_CASES`` usable rows
      ``degenerate_approve_all``  approved ~everything: agreement, not skill
      ``degenerate_pass_all``     vetoed ~everything: the same, mirrored
      ``ok``                      a real split of adequate size
    """
    cases = list(cases or [])
    usable = [c for c in cases
              if c.get("verdict") in VERDICTS and c.get("outcome") in ("win", "loss")]
    dropped = len(cases) - len(usable)

    out = dict(_subset_stats(usable))
    out["dropped"] = dropped
    out["vetoed"] = _cell([c for c in usable if c["verdict"] == "PASS"])
    out["approved"] = _cell([c for c in usable if c["verdict"] == "TAKE"])

    grades = {}
    for c in usable:
        grades.setdefault(c.get("entry_grade") or "unknown", []).append(c)
    out["by_grade"] = {g: _subset_stats(rows) for g, rows in sorted(grades.items())}

    # A sample whose base rate is this far from the population's is not a
    # sample of it. Wide on purpose: the point is to catch a broken sampler,
    # not to police ordinary sampling noise.
    out["population_base_rate"] = population_base_rate
    if population_base_rate is None or out["base_rate"] is None:
        out["sample_is_unrepresentative"] = None
    else:
        out["sample_is_unrepresentative"] = (
            abs(out["base_rate"] - population_base_rate) > BASE_RATE_TOLERANCE)

    n = out["n"]
    ar = out["approval_rate"]
    if n < MIN_CASES:
        out["status"] = "thin"
        out["lift"] = None
    elif ar is not None and ar >= DEGENERATE_HI:
        out["status"] = "degenerate_approve_all"
    elif ar is not None and ar <= DEGENERATE_LO:
        out["status"] = "degenerate_pass_all"
    else:
        out["status"] = "ok"
    return out

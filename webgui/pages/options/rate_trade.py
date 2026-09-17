"""Rate my trade - the verdict word the Calculator's rating dialog shows. PURE.

The service grades a hand-built trade with the Strategy Finder's own scorer
(``calc_rate``); the page judges it with the same Go/No-Go checklist the Trade
detail panel runs. This module composes the two into ONE word. Design:
docs/plans/2026-09-16-calc-rate-my-trade-design.md, section 2.

| grade \\ checklist | Clear   | cautions / not fully checked | Blocked |
|--------------------|---------|------------------------------|---------|
| Strong / Good      | BUY     | CAUTION                      | PASS    |
| Marginal           | CAUTION | PASS                         | PASS    |
| Weak / no grade    | PASS    | PASS                         | PASS    |

⚠ The thresholds are NOT fitted to outcomes: they reuse the grade and the
checklist the app already trusts, and no calibration bucket exists for hand-built
trades. The dialog says so.
"""
from ..fmt import num

#: The word -> its text colour. A finite map, never a runtime-built class.
WORD_TONE = {"BUY": "text-emerald-400", "CAUTION": "text-amber-400",
             "PASS": "text-rose-400"}

_GOOD_GRADES = ("Strong", "Good")
_MARGINAL = "Marginal"


def verdict_word(grade, checklist_state):
    """BUY / CAUTION / PASS from the scorer's grade and ``checks.summary``'s state.

    ``pos`` is Clear and ``neg`` is Blocked. Every other state - a caution, a
    partly-run or unrun checklist, or a state this function does not know - is a
    caution: a check that could not run must never let a trade earn BUY. A grade
    that is missing or not one of the scorer's words is PASS, not CAUTION - a
    rating the service could not produce is no endorsement."""
    if checklist_state == "neg":
        return "PASS"
    clear = checklist_state == "pos"
    if grade in _GOOD_GRADES:
        return "BUY" if clear else "CAUTION"
    if grade == _MARGINAL:
        return "CAUTION" if clear else "PASS"
    return "PASS"


def reasons(row, items):
    """The "why" lines under the word, most serious first: the scorer's failed
    gates, the checklist's blocks, its cautions, a note for checks that could not
    run, then what the rating could not know about the structure."""
    row = row if isinstance(row, dict) else {}
    items = [c for c in (items or []) if isinstance(c, dict)]
    out = []
    gate = str(row.get("grade_reason") or "").strip()
    if gate.startswith("Fails:"):
        out.append(gate)
    for tone in ("neg", "warn"):
        for c in items:
            if c.get("tone") == tone:
                out.append(f"{c.get('label', '')}: {c.get('text', '')}")
    if any(c.get("tone") == "muted" for c in items):
        out.append("Some checks could not run - they count as cautions")
    if row.get("vol_gate_blocks"):
        out.append("Volatility is below the floor the scanners require "
                   "before selling premium")
    if row.get("structure_known") is False:
        out.append("A custom structure - judged against the debit bars")
    return out


def banner_view(row, checklist_state, items):
    """``{word, tone, grade, score, reasons}`` for the dialog's banner. A missing
    grade or score is an em-dash, never a zero."""
    row = row if isinstance(row, dict) else {}
    word = verdict_word(row.get("grade"), checklist_state)
    score = num(row.get("composite_score"))
    return {"word": word, "tone": WORD_TONE[word],
            "grade": row.get("grade") or "—",
            "score": f"{round(score):d}" if score is not None else "—",
            "reasons": reasons(row, items)}


def request_matches(payload, request_id):
    """Whether a ``cache:options:calc_rating`` payload answers the open request.
    An old answer, another tab's, or no open request paints nothing."""
    return (bool(request_id) and isinstance(payload, dict)
            and payload.get("request_id") == request_id)

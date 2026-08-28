"""Replay a bull/bear debate over signals this stack already graded and closed.

Version: 1.0.0
Last Updated: 2026-08-28

The falsification harness for the Research Desk proposal
(``docs/plans/2026-08-28-research-desk-design.md``). Before building a sixth
service, a vendored LangGraph fork and 31 new dependencies, answer the cheap
question first:

    On the 814 closed signals in ``options-scanner/data/signals.db``, would a
    bull/bear debate have improved on ``entry_grade``?

``entry_grade`` already separates: Good 80% win, Marginal 71%. That, and the
78% base rate, are what a debate has to beat.

**This reads PROD's signals.db read-only and writes nothing to it.** The only
file it creates is its own response cache, so re-running the report is free.

**Spending is opt-in.** Without ``--live`` the run uses a clearly-labelled stub
debater and touches no network — a mistyped command costs nothing. With
``--live`` it makes THREE calls per signal (bull, bear, judge; bull and bear are
independent, which is what makes it a debate rather than one pass) against
``claude-sonnet-5``, and every response is cached.

⚠ ``ENV_FLAGS['allow_claude']`` is deliberately NOT consulted. That flag exists
to stop *background* spend in dev; this is an operator typing ``--live`` at a
prompt, the same category as the ``TRADING_ENABLE_SCHEDULERS`` escape hatch.
The estimate-and-confirm gate below is what stands in its place.

Usage:
    .venv\\Scripts\\python tools\\replay_debate.py                    # stub, free
    .venv\\Scripts\\python tools\\replay_debate.py --limit 60 --live  # ~180 calls
    .venv\\Scripts\\python tools\\replay_debate.py --scanner SWING --live --yes
"""
import argparse
import hashlib
import json
import logging
import pathlib
import sqlite3
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from repo_paths import OPTIONS_SCANNER, TRADE_SVC_DATA         # noqa: E402
from tools import replay_scoring as RS                          # noqa: E402

log = logging.getLogger(__name__)

DEFAULT_SIGNALS_DB = OPTIONS_SCANNER / "data" / "signals.db"
DEFAULT_CACHE_DB = TRADE_SVC_DATA / "replay_debate_cache.db"

# Never Opus without asking — the house rule. Three calls per signal makes the
# model choice the dominant cost lever here.
MODEL = "claude-sonnet-5"
CALLS_PER_CASE = 3
PROMPT_VERSION = "v1"

CACHE_SCHEMA = """
CREATE TABLE IF NOT EXISTS verdicts (
    cache_key   TEXT PRIMARY KEY,
    -- TEXT, not INTEGER: prod's signal_id is a hex digest.
    signal_id   TEXT,
    verdict     TEXT,
    judge_text  TEXT,
    bull_text   TEXT,
    bear_text   TEXT,
    model       TEXT,
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP
);
"""

_BULL = (
    "You are the bull researcher on a trading desk. Below is a defined-risk "
    "options credit spread that was proposed. Argue the STRONGEST honest case "
    "FOR taking it: what has to be true, and what in the setup supports it. "
    "Be concrete about the credit, the distance to the short strike, the "
    "delta, the IV rank and the time to expiry. Do not hedge into neutrality "
    "and do not state a final recommendation — that is the judge's job. "
    "Six sentences maximum."
)
_BEAR = (
    "You are the bear researcher on a trading desk. Below is a defined-risk "
    "options credit spread that was proposed. Argue the STRONGEST honest case "
    "AGAINST taking it: what breaks it, what the credit is not paying for, and "
    "what the risk/reward misses. Be concrete about the credit, the distance "
    "to the short strike, the delta, the IV rank and the time to expiry. Do "
    "not hedge into neutrality and do not state a final recommendation — that "
    "is the judge's job. Six sentences maximum."
)
_JUDGE = (
    "You are the portfolio manager. You have a bull case and a bear case for a "
    "defined-risk options credit spread. Decide whether the desk takes it.\n\n"
    "Judge on risk-adjusted merit: does the credit compensate for the risk "
    "actually being taken? You are selecting from a stream of candidates, so "
    "'acceptable' is not enough — pass on anything you would not actively "
    "choose. Give at most three sentences of reasoning, then a final line "
    "exactly of the form:\n\nVERDICT: TAKE\nor\nVERDICT: PASS"
)


# ── I/O ─────────────────────────────────────────────────────────────────────

def open_signals(path=None):
    """Read-only connection to the signals DB. Writes raise, by construction."""
    p = pathlib.Path(path or DEFAULT_SIGNALS_DB)
    uri = "file:" + p.as_posix().replace("?", "%3f").replace("#", "%23") + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def load_cases(conn, scanner=None, limit=None):
    """Closed signals, newest first, as scoring cases.

    Each case carries ``view`` (the whitelisted payload a prompt is built from)
    alongside ``entry_grade`` and ``outcome``, which are for SCORING only and
    never reach the model."""
    sql = ("SELECT s.*, o.realized_pnl FROM signals s "
           "JOIN signal_outcomes o USING(signal_id) "
           "WHERE o.realized_pnl IS NOT NULL")
    args = []
    if scanner and scanner.lower() != "all":
        sql += " AND s.scanner_type = ?"
        args.append(scanner)
    sql += " ORDER BY s.first_seen_date DESC, s.signal_id DESC"
    if limit:
        sql += " LIMIT ?"
        args.append(int(limit))

    cases = []
    for row in conn.execute(sql, args):
        outcome = RS.outcome_of({"realized_pnl": row["realized_pnl"]})
        if outcome is None:
            continue
        cases.append({
            "signal_id": row["signal_id"],
            "symbol": row["symbol"],
            "entry_grade": row["entry_grade"],
            "outcome": outcome,
            "view": RS.build_case(row),
            "verdict": None,
        })
    return cases


def open_cache(path):
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(CACHE_SCHEMA)
    return conn


def _cache_key(view, prompt_version, model):
    blob = json.dumps(view, sort_keys=True, default=str)
    digest = hashlib.sha256(
        f"{prompt_version}|{model}|{blob}".encode("utf-8")).hexdigest()
    return digest[:32]


# ── the debaters ────────────────────────────────────────────────────────────

def _fmt_case(view):
    order = [k for k in RS.CASE_FIELDS if k in view and k != "signal_id"]
    return "\n".join(f"  {k}: {view[k]}" for k in order)


def stub_debater(view):
    """A deterministic, transparently arbitrary stand-in. NOT a result.

    Keyed off the signal id so a stub run is reproducible and the plumbing can
    be exercised end to end, but it reads no feature of the trade — the report
    labels every stub run so nobody mistakes its numbers for a finding.

    ⚠ ``signal_id`` is a hex TEXT digest in prod, not an integer. The first
    version of this function called ``int()`` on it and raised for 793 of 814
    rows; hashing the STRING is what makes it total over the real column."""
    sid = str(view.get("signal_id", ""))
    digest = hashlib.sha256(sid.encode("utf-8")).digest()[0]
    return "VERDICT: TAKE" if digest % 2 else "VERDICT: PASS"


def _anthropic_api_key():
    import os
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key.strip()
    try:
        from repo_paths import SHARED_DIR
        p = SHARED_DIR / "anthropic_key.txt"
        if p.exists():
            return p.read_text(encoding="utf-8").strip()
    except Exception:  # noqa: BLE001
        log.debug("reading anthropic_key.txt failed", exc_info=True)
    return None


def _make_client():
    """A real ``anthropic.Anthropic``, or ``None``. Never raises."""
    key = _anthropic_api_key()
    if not key:
        return None
    try:
        import anthropic
        return anthropic.Anthropic(api_key=key, timeout=60.0, max_retries=2)
    except Exception:  # noqa: BLE001
        log.debug("building the anthropic client failed", exc_info=True)
        return None


def make_live_debater(client, model=MODEL):
    """Bull, bear, judge — three calls, bull and bear independent.

    Independence is the point: a bear that has already read the bull case
    argues against a text rather than against the trade, which is one pass
    wearing two hats. This is the cheapest faithful version of the design's
    §3 structure."""
    def _ask(system, user, max_tokens):
        resp = client.messages.create(
            model=model, max_tokens=max_tokens, system=system,
            messages=[{"role": "user", "content": user}],
        )
        try:
            from shared import anthropic_counter
            anthropic_counter.record(1)
        except Exception:  # noqa: BLE001
            log.debug("recording the anthropic call failed", exc_info=True)
        return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")

    def debater(view):
        case = _fmt_case(view)
        bull = _ask(_BULL, f"Candidate trade:\n{case}", 400)
        bear = _ask(_BEAR, f"Candidate trade:\n{case}", 400)
        judge = _ask(
            _JUDGE,
            f"Candidate trade:\n{case}\n\nBULL CASE:\n{bull}\n\nBEAR CASE:\n{bear}",
            300,
        )
        return judge, bull, bear

    return debater


# ── the run ─────────────────────────────────────────────────────────────────

def run(signals_db=None, cache_db=None, *, live=False, debater=None,
        scanner=None, limit=None, prompt_version=PROMPT_VERSION,
        model=MODEL, log=print):
    """Replay the debate and score it. Returns the report dict."""
    conn = open_signals(signals_db)
    try:
        cases = load_cases(conn, scanner=scanner, limit=limit)
    finally:
        conn.close()

    mode = "live"
    if debater is None:
        if not live:
            # Announced here as well as in the report: a caller that consumes
            # the log and never renders must still see that this is a dry run.
            log("STUB debater - no network, no spend, NOT A RESULT. "
                "Re-run with --live for a real measurement.")
            debater, mode = stub_debater, "stub"
        else:
            client = _make_client()
            if client is None:
                log("no Anthropic client (no key, or the SDK is missing) - "
                    "nothing was called and nothing was spent")
                return {"mode": "no_client", "errors": 0, "model": model,
                        "score": RS.score([])}
            debater = make_live_debater(client, model)

    cache = open_cache(cache_db or DEFAULT_CACHE_DB)
    errors = 0
    try:
        for i, case in enumerate(cases, 1):
            key = _cache_key(case["view"], prompt_version, model if mode == "live" else "stub")
            hit = cache.execute(
                "SELECT verdict FROM verdicts WHERE cache_key=?", (key,)).fetchone()
            if hit is not None:
                case["verdict"] = hit["verdict"]
                continue
            try:
                result = debater(case["view"])
            except Exception as exc:  # noqa: BLE001
                # One upstream failure must not cost the whole sample; the case
                # is dropped (counted), never defaulted to TAKE.
                errors += 1
                log(f"  [{i}/{len(cases)}] signal {case['signal_id']}: {exc}")
                continue
            judge, bull, bear = result if isinstance(result, tuple) else (result, "", "")
            case["verdict"] = RS.verdict_from_text(judge)
            cache.execute(
                "INSERT OR REPLACE INTO verdicts "
                "(cache_key, signal_id, verdict, judge_text, bull_text, bear_text, model) "
                "VALUES (?,?,?,?,?,?,?)",
                (key, case["signal_id"], case["verdict"], judge, bull, bear,
                 model if mode == "live" else "stub"))
            cache.commit()
            if mode == "live":
                log(f"  [{i}/{len(cases)}] {case['symbol']:<6} "
                    f"{case['entry_grade'] or '?':<9} -> {case['verdict']}")
    finally:
        cache.close()

    return {"mode": mode, "errors": errors,
            "model": model if mode == "live" else None,
            "score": RS.score(cases)}


# ── the report ──────────────────────────────────────────────────────────────

def _pct(v):
    return "  n/a" if v is None else f"{100 * v:5.1f}%"


def render_report(report):
    s = report["score"]
    L = []
    L.append("")
    L.append("=" * 66)
    L.append("  DEBATE REPLAY vs entry_grade")
    L.append("=" * 66)
    if report["mode"] == "stub":
        L.append("  ** STUB DEBATER - NOT A RESULT. Re-run with --live. **")
    elif report["mode"] == "no_client":
        L.append("  ** NO CLIENT - NOT A RESULT. Nothing was called. **")
    L.append(f"  model            {report.get('model') or '(stub)'}")
    L.append(f"  usable cases     {s['n']}   (dropped {s['dropped']}, "
             f"errors {report['errors']})")
    L.append("")
    L.append("  THE NULL MODEL - approve everything:")
    L.append(f"    base rate      {_pct(s['base_rate'])}   <- beat this")
    L.append("")
    L.append("  THE DEBATE:")
    L.append(f"    approval rate  {_pct(s['approval_rate'])}   "
             f"({s['approved_n']} of {s['n']} taken)")
    L.append(f"    approved win   {_pct(s['approved_rate'])}")
    L.append(f"    lift           {_pct(s['lift'])}   <- the headline")
    L.append("")

    if s["status"] != "ok":
        why = {
            "thin": f"fewer than {RS.MIN_CASES} usable cases - this is no "
                    "measurement, not a small effect",
            "degenerate_approve_all": "the debate approved almost everything - "
                                      "that is agreement, not discrimination",
            "degenerate_pass_all": "the debate vetoed almost everything - a "
                                   "veto that refuses everything trivially "
                                   "avoids losses",
        }.get(s["status"], s["status"])
        L.append(f"  ** NOT A RESULT ({s['status']}): {why}. **")
        L.append("")

    v, a = s["vetoed"], s["approved"]
    L.append("  WHERE THEY DISAGREED:")
    L.append(f"    vetoed         n={v['n']:<4} would have won {v['would_have_won']:<4} "
             f"lost {v['would_have_lost']:<4} veto accuracy {_pct(v['accuracy'])}")
    L.append(f"    approved       n={a['n']:<4} won {a['would_have_won']:<4} "
             f"lost {a['would_have_lost']}")
    if v["n"] < RS.MIN_CELL:
        L.append(f"    (a cell under {RS.MIN_CELL} carries no rate - counts only)")
    L.append("")

    if s["by_grade"]:
        L.append("  BY GRADE - promotion and veto are different claims:")
        L.append(f"    {'grade':<10}{'n':>5}{'base':>8}{'approved':>10}{'lift':>8}")
        for g, gs in s["by_grade"].items():
            L.append(f"    {g:<10}{gs['n']:>5}{_pct(gs['base_rate']):>8}"
                     f"{_pct(gs['approved_rate']):>10}{_pct(gs['lift']):>8}")
        L.append("")
    L.append("=" * 66)
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--live", action="store_true",
                    help="make real Anthropic calls (default: free stub)")
    ap.add_argument("--yes", action="store_true", help="skip the cost prompt")
    ap.add_argument("--limit", type=int, help="cap the number of signals")
    ap.add_argument("--scanner", default="all", help="0DTE | SWING | all")
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--signals-db", default=None)
    ap.add_argument("--cache-db", default=None)
    args = ap.parse_args(argv)

    if args.live and not args.yes:
        conn = open_signals(args.signals_db)
        try:
            n = len(load_cases(conn, scanner=args.scanner, limit=args.limit))
        finally:
            conn.close()
        print(f"\n{n} signals x {CALLS_PER_CASE} calls = "
              f"~{n * CALLS_PER_CASE} Anthropic calls on {args.model}.")
        print("Cached signals are skipped, so a re-run costs less than this.")
        if input("Proceed? [y/N] ").strip().lower() not in ("y", "yes"):
            print("aborted — nothing spent")
            return 1

    report = run(signals_db=args.signals_db, cache_db=args.cache_db,
                 live=args.live, scanner=args.scanner, limit=args.limit,
                 model=args.model)
    print(render_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

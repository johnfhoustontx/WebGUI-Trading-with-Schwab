"""Utility: (re)generate Gamma Analyze briefing reports from the stored history.

Run MANUALLY (never in a request path). Reads the ``gamma_briefings.db`` history
(written by options_svc each Auto briefing / ad-hoc run) and regenerates the
standalone HTML report from the stored STRUCTURED analysis — the report is a pure,
deterministic function of the data (``compute.analyze_history_doc``), so old
briefings re-render in the current infographic design.

Can also generate a FRESH briefing right now (calls Claude via
``compute.gamma_analyze`` — needs the proxy running + an ANTHROPIC key), store it to
history, and write its report.

Usage (from the repo root, venv active):
  python services/options_svc/gamma_briefing_report.py --list [--days N]
  python services/options_svc/gamma_briefing_report.py --date 2026-07-02 [--slot midday] [--out FILE]
  python services/options_svc/gamma_briefing_report.py --range 2026-06-29 2026-07-02 [--out FILE]
  python services/options_svc/gamma_briefing_report.py --generate [--slot manual] [--out FILE]

With no report --out, the HTML is written under options-scanner/data/gamma_reports/
and the path is printed.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import pathlib
import sys
from zoneinfo import ZoneInfo

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from repo_paths import OPTIONS_SCANNER  # noqa: E402
if str(OPTIONS_SCANNER) not in sys.path:
    sys.path.insert(0, str(OPTIONS_SCANNER))

import gamma_briefing_history_db as gbh          # noqa: E402
from services.options_svc import compute          # noqa: E402

_CT = ZoneInfo("America/Chicago")
_REPORT_DIR = OPTIONS_SCANNER / "data" / "gamma_reports"


def _now_ct():
    return _dt.datetime.now(_CT)


def _write(html: str, out: pathlib.Path | None, stem: str) -> pathlib.Path:
    path = pathlib.Path(out) if out else (_REPORT_DIR / f"{stem}.html")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    return path


def cmd_list(conn, days: int | None) -> int:
    start = None
    if days:
        start = (_now_ct().date() - _dt.timedelta(days=days - 1)).isoformat()
    rows = gbh.list_briefings(conn, start_date=start)
    if not rows:
        print("No briefings stored yet.")
        return 0
    print(f"{'DATE':11} {'SLOT':14} {'GENERATED':20} {'BIAS':>5}  HEADLINE")
    for r in rows:
        bias = "" if r["bias"] is None else f"{r['bias']:+.0f}"
        print(f"{r['date']:11} {r['slot']:14} {(r['generated_at'] or '')[:19]:20} "
              f"{bias:>5}  {(r['headline'] or '')[:60]}")
    print(f"\n{len(rows)} briefing(s).")
    return 0


def _report(conn, *, date=None, slot=None, start=None, end=None, out=None) -> int:
    if date and slot:
        row = gbh.get_briefing(conn, date, slot)
        rows = [row] if row else []
        title, stem = f"Gamma Briefing — {date} · {slot}", f"gamma_{date}_{slot}"
    elif date:
        rows = gbh.briefings_for_date(conn, date)
        title, stem = f"Gamma Briefings — {date}", f"gamma_{date}"
    else:
        rows = gbh.list_briefings(conn, start_date=start, end_date=end, with_analysis=True)
        rows = list(reversed(rows))  # oldest→newest reads naturally in a range report
        title = f"Gamma Briefings — {start or 'start'} → {end or 'latest'}"
        stem = f"gamma_{start or 'all'}_{end or 'latest'}"
    if not rows:
        print("No matching briefings found.")
        return 1
    path = _write(compute.analyze_history_doc(rows, title=title), out, stem)
    print(f"Wrote {len([r for r in rows if r and r.get('analysis')])} briefing(s) -> {path}")
    return 0


def cmd_generate(conn, slot_label: str | None, out) -> int:
    now = _now_ct()
    slot = slot_label or f"manual-{now.strftime('%H%M')}"
    print(f"Generating a fresh briefing (slot={slot})… this calls Claude + the proxy.")
    res = compute.gamma_analyze(label=f"Manual · {now.strftime('%b %d %I:%M %p')} CT")
    analysis = res.get("analysis")
    if not analysis:
        print("The live run produced no structured analysis (market closed / no key / "
              "API error). Nothing stored. The HTML message page was still produced.")
        _write(res.get("html", ""), out, f"gamma_{now.date().isoformat()}_{slot}")
        return 1
    gbh.insert_briefing(conn, date=now.date().isoformat(), slot=slot,
                        generated_at=now.isoformat(), symbol_scope="$SPX/SPY/QQQ",
                        model=getattr(compute, "_ANALYZE_MODEL", None),
                        bias=analysis.get("bias"), headline=analysis.get("headline"),
                        analysis=analysis)
    print(f"Stored briefing {now.date().isoformat()} / {slot}.")
    return _report(conn, date=now.date().isoformat(), slot=slot, out=out)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Generate/regenerate Gamma briefing reports.")
    p.add_argument("--list", action="store_true", help="list stored briefings")
    p.add_argument("--days", type=int, default=None, help="with --list: only the last N days")
    p.add_argument("--date", help="report for a single date (YYYY-MM-DD)")
    p.add_argument("--slot", help="restrict to one slot (premarket/open/midday/close/…)")
    p.add_argument("--range", nargs=2, metavar=("START", "END"),
                   help="report across a date range (inclusive)")
    p.add_argument("--generate", action="store_true",
                   help="generate a fresh briefing now, store it, and report it")
    p.add_argument("--out", help="output HTML path (default: options-scanner/data/gamma_reports/)")
    args = p.parse_args(argv)

    conn = gbh.connect()
    try:
        if args.generate:
            return cmd_generate(conn, args.slot, args.out)
        if args.list:
            return cmd_list(conn, args.days)
        if args.date:
            return _report(conn, date=args.date, slot=args.slot, out=args.out)
        if args.range:
            return _report(conn, start=args.range[0], end=args.range[1], out=args.out)
        p.print_help()
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())

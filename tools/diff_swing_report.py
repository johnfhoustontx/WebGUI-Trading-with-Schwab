"""What moved between two swing-model research reports.

A refit that quietly halves the measured edge is the single most consequential
thing that can happen to this model, and it happened once already: Phase 0
re-ran the SAME methodology on the SAME universe two months later and OOS IC
fell 44%. Nobody would have noticed from the artifact alone.

So the monthly refit prints a diff. Pure text parsing — it reads the two
markdown reports and reports the numbers that matter, not a line diff, because a
line diff of a table nobody reads is how the 44% went unremarked.

Usage:
    .venv\\Scripts\\python tools\\diff_swing_report.py OLD.md NEW.md
"""
import argparse
import pathlib
import re
import sys

# Metric label -> the regex that finds it. Kept small on purpose: a diff that
# reports everything reports nothing.
_OOS = re.compile(r"Composite OOS IC:\s*([+-]?\d*\.?\d+)")


def _cells(line):
    """The cells of a markdown table row, or None if it is not one."""
    if not line.strip().startswith("|"):
        return None
    return [c.strip() for c in line.strip().strip("|").split("|")]


def parse(text):
    """``{"oos_ic": float|None, "factors": {name: (mean_ic, weight)}}``.

    Columns are located by their HEADER NAME rather than by position. A
    positional read (or a lazy regex, which is the same thing) silently picks up
    the neighbouring column when the report grows one — and here that would mean
    diffing `n_days` while calling it a weight, which looks like a finding."""
    m = _OOS.search(text or "")
    out = {"oos_ic": float(m.group(1)) if m else None, "factors": {}}
    idx = None
    for line in (text or "").splitlines():
        cells = _cells(line)
        if not cells:
            continue
        low = [c.lower() for c in cells]
        if "factor" in low and "weight" in low:
            idx = {"name": low.index("factor"), "ic": low.index("mean ic"),
                   "weight": low.index("weight")}
            continue
        if idx is None or max(idx.values()) >= len(cells):
            continue
        try:
            out["factors"][cells[idx["name"]]] = (float(cells[idx["ic"]]),
                                                 float(cells[idx["weight"]]))
        except ValueError:
            continue          # the |---|---| separator row, and any prose
    return out


# ASCII only, deliberately. This script's output is piped by
# `refit_swing_model.bat`, and a redirected Windows stdout encodes as cp1252 —
# which cannot represent an arrow. Found by running it for real: the scheduled
# refit crashed with UnicodeEncodeError at exactly the moment it had something
# to report, and a monthly job that dies while reporting a decay is worse than
# no job at all.
UP, DOWN, FLAT, WARN = "UP", "DN", "--", "!!"


def _arrow(delta, tol=1e-9):
    return FLAT if abs(delta) <= tol else (UP if delta > 0 else DOWN)


def render(old, new):
    lines = []
    a, b = old.get("oos_ic"), new.get("oos_ic")
    if a is not None and b is not None:
        d = b - a
        pct = (d / abs(a) * 100) if a else 0.0
        lines.append(f"  Composite OOS IC  {a:+.4f} {_arrow(d)} {b:+.4f}"
                     f"   ({d:+.4f}, {pct:+.0f}%)")
        if a > 0 and d < 0 and abs(d) > abs(a) * 0.25:
            lines.append(f"  {WARN} the measured edge fell by more than a quarter - "
                         "read the report before trusting the new artifact.")
    else:
        lines.append("  Composite OOS IC  could not be parsed from one of the "
                     "reports.")

    fa, fb = old.get("factors", {}), new.get("factors", {})
    gone = sorted(set(fa) - set(fb))
    added = sorted(set(fb) - set(fa))
    if gone:
        lines.append(f"  dropped from the fit: {', '.join(gone)}")
    if added:
        lines.append(f"  new in the fit:      {', '.join(added)}")

    flips = []
    for name in sorted(set(fa) & set(fb)):
        w_old, w_new = fa[name][1], fb[name][1]
        if w_old * w_new < 0:
            flips.append(f"{name} ({w_old:+.3f} to {w_new:+.3f})")
    if flips:
        lines.append(f"  {WARN} WEIGHT SIGN FLIPPED: " + "; ".join(flips))
        lines.append("    A factor that changed sign is now recommending the "
                     "opposite of what it did last month.")

    moves = sorted(((abs(fb[n][0] - fa[n][0]), n) for n in set(fa) & set(fb)),
                   reverse=True)[:5]
    if moves:
        lines.append("  largest IC moves:")
        for _mag, n in moves:
            lines.append(f"    {n:<16} {fa[n][0]:+.4f} {_arrow(fb[n][0] - fa[n][0])} "
                         f"{fb[n][0]:+.4f}")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("old")
    ap.add_argument("new")
    args = ap.parse_args()
    old_p, new_p = pathlib.Path(args.old), pathlib.Path(args.new)
    if not old_p.exists() or not new_p.exists():
        print("  (no prior report to diff against)")
        return 0
    print(render(parse(old_p.read_text(encoding="utf-8")),
                 parse(new_p.read_text(encoding="utf-8"))))
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Who visited the public site: read Caddy's visitor log by IP address.

Caddy writes one JSON line per request to neuralstrike.co (and www.) and to
live.neuralstrike.co, under ``config/edge.toml [access_log] dir`` -- ``site.log``
and ``live.log``, a new file each day, the old ones gzipped beside them and
deleted after ``keep_days``. This reads all of them, current and rolled, so the
answer covers every day still kept. app.neuralstrike.co is never logged.

Run it on the box, from the repo root (the files are 0644, no sudo needed)::

    .venv/bin/python tools/visitor_log.py                  # today's visitors, by IP
    .venv/bin/python tools/visitor_log.py --days 7         # the last week
    .venv/bin/python tools/visitor_log.py --host live      # live.neuralstrike.co only
    .venv/bin/python tools/visitor_log.py --ip 203.0.113.9 # every request from one IP
    .venv/bin/python tools/visitor_log.py --days 90 --csv > visitors.csv

Times print in Central. "Pages" counts requests for a page rather than for the
images, styles and scripts a page pulls in, so one ordinary visit reads as a
handful of requests and one or two pages.
"""
import argparse
import csv
import datetime
import gzip
import json
import pathlib
import sys
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from deploy.caddy import generate_caddyfile as edge  # noqa: E402
from repo_paths import NAIVE_WALLCLOCK_TZ  # noqa: E402

CT = ZoneInfo(NAIVE_WALLCLOCK_TZ)

# A request for one of these is a part of a page, not a page.
ASSET_SUFFIXES = (".css", ".js", ".png", ".jpg", ".jpeg", ".webp", ".gif",
                  ".svg", ".ico", ".woff", ".woff2", ".json", ".txt", ".xml",
                  ".map")

CSV_COLUMNS = ("time_ct", "ip", "host", "method", "uri", "status", "bytes",
               "referer", "user_agent")


def log_files(directory, name):
    """Every file one host's log is spread over, oldest name first: the
    current ``site.log`` plus each rolled ``site-<time>.log`` / ``.log.gz``."""
    d = pathlib.Path(directory)
    stem = pathlib.Path(name).stem
    rolled = sorted(p for p in d.glob(f"{stem}-*.log*") if p.is_file())
    current = d / name
    return rolled + ([current] if current.is_file() else [])


def _lines(path):
    opener = gzip.open if path.suffix == ".gz" else open
    try:
        with opener(path, "rt", encoding="utf-8", errors="replace") as fh:
            yield from fh
    except (OSError, EOFError) as exc:
        # A rolled file mid-compression, or one deleted under us by the roll.
        print(f"skipped {path}: {exc}", file=sys.stderr)


def _header(headers, name):
    vals = (headers or {}).get(name) or []
    return vals[0] if vals else ""


def parse(line):
    """One Caddy access-log line as a flat record, or ``None`` for anything
    that is not an access entry (a blank line, a torn last line)."""
    try:
        doc = json.loads(line)
    except ValueError:
        return None
    req = doc.get("request") if isinstance(doc, dict) else None
    if not isinstance(req, dict) or not isinstance(doc.get("ts"), (int, float)):
        return None
    headers = req.get("headers") or {}
    return {
        "ts": float(doc["ts"]),
        # client_ip is what Caddy decided the visitor is; with nothing in front
        # of the edge it equals remote_ip. Older Caddy wrote only remote_ip.
        "ip": req.get("client_ip") or req.get("remote_ip") or "",
        "host": req.get("host") or "",
        "method": req.get("method") or "",
        "uri": req.get("uri") or "",
        "status": doc.get("status"),
        "bytes": doc.get("size"),
        "referer": _header(headers, "Referer"),
        "user_agent": _header(headers, "User-Agent"),
    }


def is_page(uri):
    path = uri.split("?", 1)[0].lower()
    return not path.endswith(ASSET_SUFFIXES)


def read(directory, names, since_ts=None, ip=None):
    """Every request in the named logs at or after ``since_ts``, oldest first."""
    out = []
    for name in names:
        for path in log_files(directory, name):
            for line in _lines(path):
                rec = parse(line)
                if rec is None:
                    continue
                if since_ts is not None and rec["ts"] < since_ts:
                    continue
                if ip is not None and rec["ip"] != ip:
                    continue
                out.append(rec)
    out.sort(key=lambda r: r["ts"])
    return out


def summarize(records):
    """One row per IP address, busiest first."""
    by_ip = {}
    for r in records:
        s = by_ip.setdefault(r["ip"], {"ip": r["ip"], "requests": 0, "pages": 0,
                                      "first": r["ts"], "last": r["ts"],
                                      "hosts": set(), "paths": {},
                                      "user_agent": ""})
        s["requests"] += 1
        s["first"] = min(s["first"], r["ts"])
        s["last"] = max(s["last"], r["ts"])
        s["hosts"].add(r["host"])
        if r["user_agent"]:
            s["user_agent"] = r["user_agent"]
        if is_page(r["uri"]):
            s["pages"] += 1
            path = r["uri"].split("?", 1)[0]
            s["paths"][path] = s["paths"].get(path, 0) + 1
    rows = list(by_ip.values())
    rows.sort(key=lambda s: (-s["requests"], s["ip"]))
    return rows


def _when(ts):
    return datetime.datetime.fromtimestamp(ts, CT).strftime("%Y-%m-%d %H:%M:%S")


def _clip(text, width):
    return text if len(text) <= width else text[:width - 3] + "..."


def print_summary(rows, out=None):
    out = out or sys.stdout
    if not rows:
        print("No visitors in that window.", file=out)
        return
    print(f"{'IP address':<40} {'Requests':>8} {'Pages':>6}  {'First seen (CT)':<19}  "
          f"{'Last seen (CT)':<19}  Most-visited page  |  Browser", file=out)
    for s in rows:
        top = max(s["paths"].items(), key=lambda kv: kv[1])[0] if s["paths"] else "-"
        print(f"{s['ip']:<40} {s['requests']:>8} {s['pages']:>6}  {_when(s['first'])}  "
              f"{_when(s['last'])}  {_clip(top, 30)}  |  {_clip(s['user_agent'], 60)}",
              file=out)
    total = sum(s["requests"] for s in rows)
    print(f"\n{len(rows)} IP addresses, {total} requests.", file=out)


def print_requests(records, out=None):
    out = out or sys.stdout
    for r in records:
        print(f"{_when(r['ts'])}  {r['ip']:<40} {r['status'] or '-':>3}  {r['method']:<4} "
              f"{r['host']}{r['uri']}  |  {_clip(r['user_agent'], 60)}", file=out)


def write_csv(records, out=None):
    out = out or sys.stdout
    w = csv.writer(out)
    w.writerow(CSV_COLUMNS)
    for r in records:
        w.writerow([_when(r["ts"]), r["ip"], r["host"], r["method"], r["uri"],
                    r["status"], r["bytes"], r["referer"], r["user_agent"]])


def main(argv=None, now=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=float, default=1,
                    help="how far back to look, in days (default 1; 0 = everything kept)")
    ap.add_argument("--host", choices=("site", "live", "both"), default="both",
                    help="neuralstrike.co (site), live.neuralstrike.co (live), or both")
    ap.add_argument("--ip", help="only this address; prints each request")
    ap.add_argument("--requests", action="store_true",
                    help="one line per request instead of one per IP")
    ap.add_argument("--csv", action="store_true",
                    help="every request as CSV on stdout, for a spreadsheet")
    ap.add_argument("--dir", help="the log folder (default: config/edge.toml)")
    args = ap.parse_args(argv)

    directory = args.dir
    if directory is None:
        al = edge.access_log()
        if al is None:
            print("The visitor log is off (config/edge.toml [access_log]).",
                  file=sys.stderr)
            return 1
        directory = al["dir"]
    if not pathlib.Path(directory).is_dir():
        print(f"No log folder at {directory}.", file=sys.stderr)
        return 1

    which = ("site", "live") if args.host == "both" else (args.host,)
    names = [edge.ACCESS_LOG_FILES[w] for w in which]
    now = datetime.datetime.now().timestamp() if now is None else now
    since = None if args.days <= 0 else now - args.days * 86400
    records = read(directory, names, since_ts=since, ip=args.ip)

    if args.csv:
        write_csv(records)
    elif args.ip or args.requests:
        print_requests(records)
    else:
        print_summary(summarize(records))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

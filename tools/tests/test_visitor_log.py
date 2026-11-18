"""tools/visitor_log.py reads Caddy's visitor log, current and rolled."""
import gzip
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from tools import visitor_log as vl  # noqa: E402

T0 = 1_795_000_000.0          # a fixed instant; "now" in these tests


def _entry(ts, ip, uri="/", host="neuralstrike.co", status=200, ua="Mozilla/5.0"):
    return json.dumps({
        "level": "info", "ts": ts, "logger": "http.log.access.log0",
        "msg": "handled request", "status": status, "size": 1234,
        "request": {"remote_ip": ip, "client_ip": ip, "host": host,
                    "method": "GET", "uri": uri,
                    "headers": {"User-Agent": [ua], "Cookie": ["REDACTED"]}},
    })


def _write(path, lines, gz=False):
    text = "\n".join(lines) + "\n"
    if gz:
        with gzip.open(path, "wt", encoding="utf-8") as fh:
            fh.write(text)
    else:
        path.write_text(text, encoding="utf-8")


def test_reads_the_current_and_the_rolled_files_in_time_order(tmp_path):
    _write(tmp_path / "site-2026-11-16T00-00-00.000.log.gz",
           [_entry(T0 - 2 * 86400, "198.51.100.1")], gz=True)
    _write(tmp_path / "site-2026-11-17T00-00-00.000.log",
           [_entry(T0 - 86400, "198.51.100.2")])
    _write(tmp_path / "site.log", [_entry(T0 - 60, "198.51.100.3")])
    recs = vl.read(tmp_path, ["site.log"])
    assert [r["ip"] for r in recs] == ["198.51.100.1", "198.51.100.2", "198.51.100.3"]


def test_the_other_hosts_file_is_not_read(tmp_path):
    _write(tmp_path / "site.log", [_entry(T0, "198.51.100.1")])
    _write(tmp_path / "live.log",
           [_entry(T0, "198.51.100.9", host="live.neuralstrike.co")])
    assert [r["ip"] for r in vl.read(tmp_path, ["live.log"])] == ["198.51.100.9"]


def test_a_window_and_an_ip_filter(tmp_path):
    _write(tmp_path / "site.log", [
        _entry(T0 - 3 * 86400, "198.51.100.1"),
        _entry(T0 - 3600, "198.51.100.1"),
        _entry(T0 - 60, "198.51.100.2"),
    ])
    assert len(vl.read(tmp_path, ["site.log"], since_ts=T0 - 86400)) == 2
    only = vl.read(tmp_path, ["site.log"], ip="198.51.100.1")
    assert [r["ts"] for r in only] == [T0 - 3 * 86400, T0 - 3600]


def test_a_torn_or_foreign_line_is_skipped_not_fatal(tmp_path):
    torn = '{"ts": 1, "request": {"remote_ip"'
    foreign = json.dumps({"level": "info", "msg": "not an access entry"})
    (tmp_path / "site.log").write_text(
        "\n".join([_entry(T0, "198.51.100.1"), foreign, "", torn]) + "\n",
        encoding="utf-8")
    assert [r["ip"] for r in vl.read(tmp_path, ["site.log"])] == ["198.51.100.1"]


def test_falls_back_to_remote_ip_when_client_ip_is_absent():
    line = json.dumps({"ts": T0, "status": 200,
                       "request": {"remote_ip": "2001:db8::1", "uri": "/"}})
    assert vl.parse(line)["ip"] == "2001:db8::1"


def test_summary_counts_requests_and_pages_per_ip():
    recs = [
        vl.parse(_entry(T0, "198.51.100.1", "/")),
        vl.parse(_entry(T0 + 1, "198.51.100.1", "/site.css")),
        vl.parse(_entry(T0 + 2, "198.51.100.1", "/fonts/a.woff2")),
        vl.parse(_entry(T0 + 3, "198.51.100.1", "/blog.html?x=1")),
        vl.parse(_entry(T0 + 4, "198.51.100.2", "/")),
    ]
    rows = vl.summarize(recs)
    assert [r["ip"] for r in rows] == ["198.51.100.1", "198.51.100.2"]
    first = rows[0]
    assert (first["requests"], first["pages"]) == (4, 2)
    assert (first["first"], first["last"]) == (T0, T0 + 3)
    assert set(first["paths"]) == {"/", "/blog.html"}


def test_main_prints_a_summary_and_a_csv(tmp_path, capsys):
    _write(tmp_path / "site.log", [_entry(T0 - 60, "198.51.100.1"),
                                   _entry(T0 - 30, "198.51.100.1", "/live.html")])
    _write(tmp_path / "live.log", [_entry(T0 - 10, "203.0.113.5", "/desk",
                                          host="live.neuralstrike.co")])
    assert vl.main(["--dir", str(tmp_path)], now=T0) == 0
    out = capsys.readouterr().out
    assert "198.51.100.1" in out and "203.0.113.5" in out
    assert "2 IP addresses, 3 requests." in out

    assert vl.main(["--dir", str(tmp_path), "--host", "site", "--csv"], now=T0) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert lines[0].split(",")[:3] == ["time_ct", "ip", "host"]
    assert len(lines) == 3 and all("198.51.100.1" in ln for ln in lines[1:])


def test_main_refuses_a_missing_folder(tmp_path, capsys):
    assert vl.main(["--dir", str(tmp_path / "nope")], now=T0) == 1
    assert "No log folder" in capsys.readouterr().err


def test_the_reader_asks_the_generator_for_the_file_names():
    """Writer and reader name the files from ONE mapping, so they cannot part."""
    assert vl.edge.ACCESS_LOG_FILES == {"site": "site.log", "live": "live.log"}

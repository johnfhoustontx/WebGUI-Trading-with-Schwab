#!/usr/bin/env python
"""Wait until an HTTP server actually ANSWERS, not merely until a port is bound.

WHY THIS EXISTS. Every launcher used to probe with a bare TCP connect::

    powershell -Command "try{(New-Object Net.Sockets.TcpClient).Connect(...)}..."

which asks only "is something holding this port". On 2026-08-16 a promote left
PROD's web GUI in a state that passes that check and is nonetheless dead: the
process was alive and had logged "NiceGUI ready to go on http://127.0.0.1:8500",
then its asyncio accept loop died with ``WinError 64`` (the specified network
name is no longer available). The socket was bound; nothing was ever accepted
again. ``promote.bat`` waited for the bind, saw it, printed "Promoted." and
exited, and the stack sat with no UI until someone opened a browser.

An HTTP GET catches it because completing a request REQUIRES the accept loop.

WHAT COUNTS AS ALIVE: **any HTTP status**, including 3xx, 404 and 500. The
question is "did the server accept a connection and speak HTTP", not "does this
route exist" -- the proxy answers 404 on ``/`` and the web GUI now answers 307
there (``/`` redirects to the Market Dashboard), and both are healthy. Demanding
200 would make this probe a route test that breaks whenever a route moves.

WHERE IT DOES **NOT** APPLY, deliberately:
  * **Memurai (:6379)** speaks RESP, not HTTP. Those probes stay TCP.
  * ``promote.bat``'s ``wait_port_free`` has INVERSE semantics -- it waits for a
    socket to disappear. A GET failing there is ambiguous (gone, or bound but
    broken) and would report a still-held port as free, so it stays TCP too.

Usage::

    python tools/wait_http.py --port 8500 --timeout 90
    python tools/wait_http.py --url http://127.0.0.1:8100/health --timeout 30

Exit code 0 once the server answers, 1 on timeout -- so a ``.bat`` can branch on
``if errorlevel 1``.
"""
from __future__ import annotations

import argparse
import sys
import time
import urllib.error
import urllib.request

DEFAULT_TIMEOUT = 90.0
DEFAULT_INTERVAL = 1.0
_PER_REQUEST_TIMEOUT = 3.0


def probe(url: str, *, timeout: float = _PER_REQUEST_TIMEOUT) -> bool:
    """True when ``url`` answers with ANY HTTP status.

    An ``HTTPError`` is a SUCCESS here: the server accepted the connection and
    replied, which is exactly what is being tested. Only a transport-level
    failure (refused, reset, timed out) counts as not-yet-up.
    """
    try:
        with urllib.request.urlopen(url, timeout=timeout):
            return True
    except urllib.error.HTTPError:
        return True                      # answered -- 404/500 still means alive
    except Exception:
        return False                     # refused / reset / timed out / bad DNS


def wait_for(url: str, *, timeout: float = DEFAULT_TIMEOUT,
             interval: float = DEFAULT_INTERVAL, sleep=time.sleep,
             clock=time.monotonic) -> bool:
    """Poll ``url`` until it answers or ``timeout`` seconds elapse.

    Probes once BEFORE sleeping, so an already-up server returns immediately
    rather than paying the first interval.
    """
    deadline = clock() + timeout
    while True:
        if probe(url):
            return True
        if clock() >= deadline:
            return False
        sleep(interval)


def url_for_port(port: int, host: str = "127.0.0.1", path: str = "/") -> str:
    return f"http://{host}:{int(port)}{path}"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url")
    ap.add_argument("--port", type=int)
    ap.add_argument("--path", default="/")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    ap.add_argument("--interval", type=float, default=DEFAULT_INTERVAL)
    ap.add_argument("--label", default="")
    args = ap.parse_args(argv[1:])

    if not args.url and args.port is None:
        ap.error("one of --url or --port is required")
    url = args.url or url_for_port(args.port, path=args.path)

    if wait_for(url, timeout=args.timeout, interval=args.interval):
        return 0
    what = args.label or url
    print(f"  timed out after {args.timeout:.0f}s waiting for {what} to answer")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

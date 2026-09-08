"""Emit the Caddyfile that fronts this box's three public hostnames.

Like the systemd units, this is GENERATED and there is no Caddyfile in git.
Every value comes from ``repo_paths`` -- the hostnames, the checkout root, the
web GUI's port -- for exactly the reason ports live in one config file: a
committed Caddyfile would be a second copy of all of that, free to drift from
the first, and the drift would surface as a **502 in production** rather than as
a failing test.

Run it in the prod checkout::

    .venv/bin/python -m deploy.caddy.generate_caddyfile          # print it
    sudo .venv/bin/python -m deploy.caddy.generate_caddyfile --install
    sudo caddy validate --config /etc/caddy/Caddyfile && sudo systemctl reload caddy

⚠ ``--install`` writes a **root-owned system path**, so it needs sudo. Caddy runs
as a SYSTEM unit, not a user one -- the reverse of the stack's own units. It has
to bind :443 and must not die with a login session, and it restarts nothing on
anyone's behalf, so the reason user units exist for the stack (a network-facing
app restarting its own siblings without a polkit rule) does not apply here.

**Three hostnames, three ORIGINS.** ``SITE_HOST`` serves a static one-pager out
of ``deploy/site``; ``LIVE_HOST`` proxies the PUBLIC read-only screens
(no login); ``APP_HOST`` proxies the web GUI behind its login. Separate
origins rather than separate paths on one host, because the public page carries
third-party links and embeds and sharing an origin would put someone else's
widget inside the app's cookie scope -- and because a public origin separated
from the trading UI by a path filter is a filter someone has to keep getting
right, where an origin is not.

**No ``rate_limit``, decided.** Caddy's rate limiter is in no prebuilt binary; it
needs an ``xcaddy`` build and a manual rebuild on every future Caddy release,
with no apt security updates. This runs stock Caddy from the official repo.

⚠ **That decision covers the APP block only, and the difference matters.** On
``APP_HOST`` throttling lives in the app, where ``LockoutState`` refuses before
Argon2 and the login form token rejects a blind POST for the cost of an HMAC.
``LIVE_HOST`` has **neither** -- it is unauthenticated, so there is no lockout
to key and no form to reject, and its origin is therefore **unthrottled**.
Measured: one plain anonymous GET retains ~619 KB of NiceGUI ``Client`` for
~70 s, and nothing bounds the arrival rate. What is in place is a **blast-radius
cap, not a limit**: ``MemoryHigh``/``MemoryMax`` on the ``webgui_live`` unit, so
a flood takes the public screens down alone. The rate itself is open --
``docs/plans/2026-09-07-public-live-screens-design.md`` records it under
"Deliberately not built", and this is the file the fix would land in.
"""
import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from repo_paths import (APP_HOST, ENV_NAME, LIVE_HOST,  # noqa: E402
                        NICEGUI_LIVE_PORT, NICEGUI_PORT, SITE_HOST, SITE_ROOT)

# One year, the shortest value browsers will preload. EVERY block sends it: the
# app because its session cookie must never travel in clear, the public site
# because a downgrade there is a foothold on a neighbouring name.
#
# ⚠ No `includeSubDomains`, so `live.` does NOT inherit the apex policy -- which
# is precisely why its block has to send its own rather than lean on this one.
HSTS = "max-age=31536000"

# Where Caddy reads its config on Debian/Ubuntu when installed from the official
# repository. Named here rather than buried in main() so the tests can assert
# the path and moving it is one edit.
CADDY_CONFIG = "/etc/caddy/Caddyfile"


def _refuse_outside_prod():
    """There is ONE Caddy on this box and it fronts prod.

    A dev checkout emitting an edge config could only ever overwrite prod's --
    with dev's port, dev's checkout root, and a certificate request for a name
    dev does not own. Guarded in ``render()`` rather than in ``main()`` so no
    path through this module can produce one, printing included.

    ⚠ ``repo_paths`` pins ENV_NAME to ``prod`` under pytest regardless of the
    local marker, so a test of this branch must monkeypatch it.
    """
    if ENV_NAME != "prod":
        raise SystemExit(
            f"refusing to generate a Caddyfile in the {ENV_NAME!r} checkout.\n"
            f"There is one edge on this box and it fronts prod; this config would\n"
            f"point {APP_HOST} at :{NICEGUI_PORT} and root the public site at\n"
            f"{_site_root()}. Run it in the prod checkout.")


def _site_root():
    """The served root, as a POSIX path.

    Computed per call, not frozen at import, so a test can point it elsewhere --
    and ``as_posix`` because a Caddyfile is a Linux artifact unconditionally.
    Rendering a Windows ``str(Path)`` here would emit a backslash path that Caddy
    reads as RELATIVE, quietly serving the wrong tree.
    """
    return pathlib.PurePosixPath(pathlib.Path(SITE_ROOT).as_posix())


def _public_block():
    """The one-pager. A file server and nothing else.

    ⚠ ``root *`` is the most damaging line in this file. One level up publishes
    shared/tokens.json, shared/appsettings.json, shared/webgui_auth.json and
    config/env.local.toml to the internet, and the site would look perfect while
    it happened. It is pinned by two tests, and a third refuses anything but
    site assets under the directory itself.

    Nothing in this block may name the app: no proxy, no loopback address, no
    port. Not a security control on its own -- the subdomain is in Certificate
    Transparency regardless -- but the public face should not point at the door.
    """
    return f"""{SITE_HOST}, www.{SITE_HOST} {{
    encode zstd gzip

    # STATIC ONLY. This tree is world-readable by definition; see the docstring.
    root * "{_site_root()}"
    file_server

    header Strict-Transport-Security "{HSTS}"
}}"""


def _live_block():
    """The PUBLIC read-only screens (``webgui/live_main.py``).

    A separate ORIGIN from ``APP_HOST``, never a path under it. The app is
    behind a login and these are not, so the two are separated by something a
    misconfiguration cannot merge rather than by a path filter someone has to
    keep getting right. The upstream still binds 127.0.0.1 -- Caddy is the only
    thing that talks to it, the same rule the app follows.

    **No authentication of any kind, deliberately, and pinned by test**, so that
    a later copy-paste of the app block cannot quietly put a login in front of a
    public site -- or, worse, a login that does not work and reads as an outage.

    Carried across from the app block, and why:

    * ``encode zstd gzip`` -- same NiceGUI payloads.
    * ``Strict-Transport-Security`` -- the header carries no
      ``includeSubDomains``, so this name does not inherit the apex policy.
    * ``header_up X-Edge 1``. ⚠ **This process never reads it.** ``live_main``
      mounts no auth middleware, has no ``_client_ip`` and keys no lockout. It
      is stamped because the invariant ``test_every_reverse_proxy_stamps_the_edge_header``
      pins is file-wide and unconditional: EVERY ``reverse_proxy`` here stamps
      it. An exception carved out for "the block that does not need it" is a
      hole the next block -- one proxying the APP -- could sit in, and the header
      costs nothing.

    **``robots.txt``: ``Disallow: /``, and this was a decision.** The origin
    404'd on ``/robots.txt`` before, which crawlers read as crawl-everything --
    so the archived state was arriving by default rather than by choice. Three
    things decided it against indexing:

    * **Publishing is reversible; archiving is not.** The owner accepted
      *publishing* positions and signals. Making them keyword-searchable and
      permanently held in a search cache, the Wayback Machine and Common Crawl
      is a further and irreversible step, and it was never decided anywhere.
    * **Discoverability is not lost.** ``SITE_HOST`` stays fully crawlable and
      its ``live.html`` grid links every screen, so the project is findable;
      what is not indexed is the live book itself.
    * **A crawler is the most likely realistic load.** This origin has no rate
      limit (Caddy's needs an ``xcaddy`` build), and one anonymous GET retains
      ~619 KB of NiceGUI ``Client`` for ~70 s. Fourteen screens crawled on a
      schedule is exactly the shape the unit's ``MemoryMax`` exists to survive.

    Served from here rather than from the app because ``live_main`` registers
    the fourteen screens and nothing else -- adding a fifteenth route to the
    public process to say "do not index" would widen the surface the route-set
    test exists to keep narrow.

    ⚠ Multi-line **quoted** body, not ``\\n`` escapes: quoted tokens have spanned
    lines since v2.0, while ``\\n`` inside them is a later addition. And
    ``Content-Type`` is set explicitly -- ``respond`` sets none, and a crawler
    sniffing an unlabelled body is not something to leave to chance.

    Deliberately NOT carried:

    * **``Content-Security-Policy: frame-ancestors 'self'``.** The app forbids
      framing so the public one-pager cannot wrap a logged-in session. There is
      no session here to wrap and nothing behind a credential; every byte this
      origin serves is world-readable by definition. Setting it would also
      forbid ``SITE_HOST`` -- a different origin -- from ever embedding a
      screen, closing a door the design lists under "deliberately not built"
      rather than "never".
    * **``handle /wall* { respond 404 }``.** The wall route does not exist in
      this process at all; ``live_main`` registers the fourteen screens and
      nothing else. A 404 handler for a path FastAPI already 404s is a rule
      that reads as a control and is decoration.

    **Nothing about websockets, and that is not an omission.** Caddy v2's
    ``reverse_proxy`` proxies an ``Upgrade`` natively; the app block sets no
    websocket directive either and NiceGUI has run behind it since. Recorded
    because getting it wrong fails in the worst way -- every page would load
    once and then never repaint, which reads as a frozen tape rather than as an
    edge misconfiguration.
    """
    return f"""{LIVE_HOST} {{
    encode zstd gzip

    header Strict-Transport-Security "{HSTS}"

    # Public to READ, not to ARCHIVE. Without this the origin 404s here, which
    # crawlers read as crawl-everything -- see the docstring for why that is
    # the one default worth overriding.
    handle /robots.txt {{
        header Content-Type "text/plain; charset=utf-8"
        respond "User-agent: *
Disallow: /
" 200
    }}

    # NO login, by design -- these fourteen screens are public. See the
    # docstring before adding any auth directive here.
    handle {{
        reverse_proxy 127.0.0.1:{NICEGUI_LIVE_PORT} {{
            # Not read by this process; stamped so the file-wide "every proxied
            # route stamps it" rule keeps no exceptions. (The rule is enforced
            # as a COUNT over this whole file, so do not name the directive in
            # a comment -- a mention counts as an occurrence.)
            header_up X-Edge 1
        }}
    }}
}}"""


def _app_block():
    """The web GUI, behind its own login.

    ``header_up X-Edge 1`` is load-bearing and easy to delete by accident.
    Behind a proxy every request arrives from 127.0.0.1, so
    ``webgui.main._client_ip`` keys the per-client lockout on the LAST
    ``X-Forwarded-For`` hop -- but only when that header is present. Without it
    the app degrades to the peer and the per-client backoff, which ramps to
    900 s where the global one is deliberately 60 s, silently becomes GLOBAL: a
    bot spraying the advertised hostname would lock the owner out of the UI that
    arms the trading driver and stops the stack.

    Two rules follow, both pinned by test:

    * every proxied route stamps it, and
    * nothing here suppresses or overwrites ``X-Forwarded-For``. Caddy sets it
      by default and APPENDS the peer it observed, which is precisely what makes
      reading the tail safe -- a client-supplied prefix can lengthen the list but
      cannot change its tail.

    The header also does a second job: ``auth_middleware._is_kiosk`` refuses any
    request that carries it, so a request through the edge cannot claim to be
    the on-box wall browser.
    """
    return f"""{APP_HOST} {{
    encode zstd gzip

    header {{
        Strict-Transport-Security "{HSTS}"
        # The public one-pager is a separate origin and must not frame this.
        Content-Security-Policy "frame-ancestors 'self'"
    }}

    # The wall never leaves the box. The kiosk reaches it on loopback, where it
    # authenticates by being loopback WITHOUT the edge header -- so the route has
    # no business being offered here at all.
    handle /wall* {{
        respond 404
    }}

    handle {{
        reverse_proxy 127.0.0.1:{NICEGUI_PORT} {{
            # Read the docstring before touching this line.
            header_up X-Edge 1
        }}
    }}
}}"""


def render():
    """The complete Caddyfile text."""
    _refuse_outside_prod()
    banner = ("# GENERATED by deploy/caddy/generate_caddyfile.py -- do not edit.\n"
              "# Every value here is derived from repo_paths; an edit made in place\n"
              "# is lost on the next run and, until then, is a second source of\n"
              "# truth for the port and the checkout root.\n")
    # Ordered least-privileged first -- static site, public screens, then the
    # app behind its login. It is the order the design's own diagram uses, and
    # it puts the one block that may never carry an upstream at the top.
    return (f"{banner}\n{_public_block()}\n\n{_live_block()}\n\n"
            f"{_app_block()}\n")


def install(dest=None):
    """Write the config. Returns the path written."""
    dest = pathlib.Path(dest or CADDY_CONFIG)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(render(), encoding="utf-8")
    return dest


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--install", action="store_true",
                    help=f"write it to {CADDY_CONFIG} (needs sudo)")
    ap.add_argument("--dest", help="write it somewhere else instead")
    args = ap.parse_args(argv)

    if args.install or args.dest:
        p = install(args.dest)
        print(f"wrote {p}")
        print(f"\nNow:  sudo caddy validate --config {p}"
              f"\n      sudo systemctl reload caddy")
        return 0

    print(render(), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Emit the Caddyfile that fronts this box's two public hostnames.

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

**Two hostnames, two ORIGINS.** ``SITE_HOST`` serves a static one-pager out of
``deploy/site``; ``APP_HOST`` proxies the web GUI. Separate origins rather than
separate paths on one host, because the public page carries third-party links
and embeds and sharing an origin would put someone else's widget inside the
app's cookie scope.

**No ``rate_limit``, decided.** Caddy's rate limiter is in no prebuilt binary; it
needs an ``xcaddy`` build and a manual rebuild on every future Caddy release,
with no apt security updates. Throttling lives in the app instead, where
``LockoutState`` refuses before Argon2 and the login form token rejects a blind
POST for the cost of an HMAC. This runs stock Caddy from the official repo.
"""
import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from repo_paths import (APP_HOST, ENV_NAME, NICEGUI_PORT,  # noqa: E402
                        SITE_HOST, SITE_ROOT)

# One year, the shortest value browsers will preload. Both blocks send it: the
# app because its session cookie must never travel in clear, the public site
# because a downgrade there is a foothold on a neighbouring name.
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
    return f"{banner}\n{_public_block()}\n\n{_app_block()}\n"


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

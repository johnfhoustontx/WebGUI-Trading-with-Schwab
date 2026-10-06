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

**``rate_limit`` on the PUBLIC host only, and OFF unless ``config/edge.toml``
turns it on (2026-09-21).** Caddy's rate limiter is in no prebuilt apt binary;
it needs a custom build and a manual re-download on every Caddy release, with
no apt security updates for that binary. That cost was declined while the only
public thing was a static site; the public Strategy Finder made the public host
a place a visitor can make the box do work, so the switch now exists. Off, this
file is byte-identical to what it emitted before the switch existed.

⚠ **That decision covers the APP block only, and the difference matters.** On
``APP_HOST`` throttling lives in the app, where ``LockoutState`` refuses before
Argon2 and the login form token rejects a blind POST for the cost of an HMAC.
``LIVE_HOST`` has **neither** -- it is unauthenticated, so there is no lockout
to key and no form to reject, and its origin is therefore **unthrottled**.
Measured: one plain anonymous GET retains ~619 KB of NiceGUI ``Client`` for
~70 s, and nothing bounds the arrival rate. What is in place is a **blast-radius
cap, not a limit**: ``MemoryHigh``/``MemoryMax`` on the ``webgui_live`` unit, so
a flood takes the public screens down alone. The rate itself is bounded only
when ``config/edge.toml`` turns the page-load limit on (below; runbook section
"Edge rate limit"), which needs the custom Caddy build.
"""
import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from repo_paths import (APP_HOST, EDGE_TOML, ENV_NAME, LIVE_HOST,  # noqa: E402
                        NICEGUI_LIVE_PORT, NICEGUI_PORT, SITE_HOST, SITE_ROOT)
from shared.blog_inbox import ENTRY_CSP  # noqa: E402
from shared.config_toml import toml_loader  # noqa: E402

EDGE_DEFAULTS = {"live_rate_limit": {"enabled": False, "events": 30,
                                     "window_sec": 60, "ipv6_prefix": 64},
                 # The largest POST /login body the edge passes on, in KB.
                 "limits": {"login_body_kb": 16}}
load_edge, reset_edge = toml_loader(EDGE_TOML, EDGE_DEFAULTS, label="edge.toml")

# The requests that do NOT count toward the limit: NiceGUI's own versioned
# assets and its websocket/long-poll transport, and the bundled /static tree. A
# page load fetches dozens of those (measured on /finder: 11 under /_nicegui plus
# the favicon); only the page itself creates a session.
RATE_LIMIT_EXEMPT = ("/_nicegui/*", "/_nicegui_ws/*", "/static/*", "/favicon.ico")

# One year, the shortest value browsers will preload. EVERY block sends it: the
# app because its session cookie must never travel in clear, the public site
# because a downgrade there is a foothold on a neighbouring name.
#
# ⚠ No `includeSubDomains`, so `live.` does NOT inherit the apex policy -- which
# is precisely why its block has to send its own rather than lean on this one.
HSTS = "max-age=31536000"

# Thumbnail freshness, in seconds. Must stay WELL BELOW the 15-minute
# [windows.live_capture] interval in config/sessions.toml: at or above it, a
# visitor can be served a tile older than the one on disk. Derived from nothing
# on purpose -- the capture interval lives in a config this module may not read,
# so the relationship is stated here and pinned by a test instead.
CAPTURE_MAX_AGE = 300

# The two self-hosted woff2 faces. Long, because they are byte-stable for the
# life of the brand and are the largest repeated download on the site.
FONT_MAX_AGE = 2592000          # 30 days

# The trade idea cards under /ideas/<day>/ (services/options_svc/site_ideas.py).
# Each is named for its day and minute, so it never changes under its name.
IDEAS_MAX_AGE = 86400           # 1 day

# A Blog entry's framed document, and nothing beside it: /blog/<slug>/entry.html
# (services/blog_svc/sitewriter.py writes it). The entry's own page is
# index.html in the same folder and must NOT match: the policy this selects
# carries `sandbox` and `default-src 'none'`, which on that page would switch
# off the site's stylesheet and menu.
#
# Anchored at both ends; unanchored, a path_regexp matches anywhere in the path.
# The class is looser than shared.blog_inbox.SLUG_RE on purpose (it admits a
# doubled hyphen, which can only name a 404) and must never be tighter: a
# missed entry is served with no policy. Go's RE2 reads this exactly as Python
# does; the tests compile it. It is emitted as an UNQUOTED Caddyfile token, so
# it may hold no whitespace, quote or brace.
BLOG_ENTRY_PATH = r"^/blog/[a-z0-9-]+/entry\.html$"

# A Blog entry's PAGE as it is linked: /blog/<slug>/, a folder address. It ends
# in no extension, so the *.html revalidation rule does not see it and it would
# be left to heuristic caching: a returning visitor could be shown an entry page
# from before a promote changed the menu. A Caddy `path` glob, not a regexp:
# the `*` matches one folder name and never crosses a slash.
BLOG_PAGE_PATH = "/blog/*/"

# Where Caddy reads its config on Debian/Ubuntu when installed from the official
# repository. Named here rather than buried in main() so the tests can assert
# the path and moving it is one edit.
CADDY_CONFIG = "/etc/caddy/Caddyfile"


def login_body_kb() -> int:
    """``[limits] login_body_kb``: the largest sign-in body the edge forwards.
    Anything that is not a whole number from 1 to 1024 reads as the shipped
    value, so a typo cannot emit a directive Caddy rejects or a limit of zero
    that locks the owner out."""
    v = ((load_edge() or {}).get("limits") or {}).get("login_body_kb")
    if isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= 1024:
        return EDGE_DEFAULTS["limits"]["login_body_kb"]
    return v


def live_rate_limit():
    """The public host's rate limit as ``{events, window_sec, ipv6_prefix}``, or
    ``None`` when off. Any value that is not a positive whole number (a bool
    included) turns the whole limit OFF rather than emitting a directive Caddy
    would reject at reload."""
    cfg = (load_edge() or {}).get("live_rate_limit") or {}
    if cfg.get("enabled") is not True:
        return None
    out = {}
    for key, lo, hi in (("events", 1, 100000), ("window_sec", 1, 86400),
                        ("ipv6_prefix", 1, 128)):
        v = cfg.get(key, EDGE_DEFAULTS["live_rate_limit"][key])
        if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
            return None
        out[key] = v
    return out


def _rate_limit_lines(rl):
    """The ``rate_limit`` block for the public host, or ``""`` when off."""
    if rl is None:
        return ""
    exempt = " ".join(RATE_LIMIT_EXEMPT)
    return f"""
    # Page loads per visitor (config/edge.toml). Needs a Caddy built with
    # github.com/mholt/caddy-ratelimit; the stock binary rejects this at
    # `caddy validate`, which leaves the running config in place.
    rate_limit {{
        zone live_pages {{
            match {{
                not path {exempt}
            }}
            key {{remote_host}}
            events {rl['events']}
            window {rl['window_sec']}s
            ipv6_prefix {rl['ipv6_prefix']}
        }}
    }}
"""


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

    **One Content-Security-Policy, on one kind of file.** Everything else here
    is the site's own markup and is sent with no policy. A Blog entry is a
    document written elsewhere, so ``@blog_entries`` sends
    ``shared.blog_inbox.ENTRY_CSP`` on it: the third of three layers (cleaning,
    the frame's sandbox, this header) and the only one that still applies when
    the document is opened outside its frame. The string is imported, never
    retyped: the service writes the same tokens into the frame and the private
    preview sends the same header, and three copies would drift.
    """
    return f"""{SITE_HOST}, www.{SITE_HOST} {{
    encode zstd gzip

    # STATIC ONLY. This tree is world-readable by definition; see the docstring.
    root * "{_site_root()}"
    file_server

    header Strict-Transport-Security "{HSTS}"

    # -- Caching ------------------------------------------------------------
    # This site shipped with NO Cache-Control. file_server sends ETag and
    # Last-Modified but no freshness directive, so browsers fall back to
    # HEURISTIC caching -- they invent a lifetime and will not even revalidate
    # until it expires.
    #
    # Measured in production the day the live grid shipped: the grid rendered
    # completely unstyled for a returning visitor, because their browser held a
    # `site.css` from before the deploy while serving fresh HTML from the same
    # origin. Nothing was wrong with the deploy.
    #
    # NOTE: every filename here is UNVERSIONED (`site.css`, not
    # `site.abc123.css`), so a freshness lifetime is a promise the next deploy
    # cannot keep. The fonts are the one exception, below.

    # `no-cache` does NOT mean "do not cache" -- it means "cache, but always
    # revalidate", which with the ETag already being sent makes the common case
    # a 304 carrying no body rather than a re-download.
    #
    # `/` and `/blog/*/` are here because a page asked for by its FOLDER does
    # not end in .html: the matcher sees the address as requested, not the
    # index.html the file server answers with. `/` is the home page; the second
    # is a Blog entry's page, which is linked, shared and listed in its sitemap
    # as /blog/<slug>/. A `*` in the middle of a path is a glob that stops at a
    # slash, so it names that one folder level and nothing under it.
    @revalidate path / *.html *.css *.js *.json {BLOG_PAGE_PATH}
    header @revalidate Cache-Control "no-cache"

    # The thumbnails are rewritten under the SAME filenames every 15 minutes.
    # They cannot be cached long -- but a page view pulls fourteen, and
    # revalidating each one every view is fourteen round trips for images that
    # change four times an hour. Well under the capture interval is the trade;
    # a lifetime at or above it could show a thumbnail older than the one on
    # disk.
    @captures path /live/*
    header @captures Cache-Control "max-age={CAPTURE_MAX_AGE}, must-revalidate"

    # The trade idea cards (ideas.json, their manifest, is *.json above). A card
    # never changes under its name, but a pruned day must not linger, hence
    # must-revalidate after a day rather than a longer lifetime.
    @ideas path /ideas/*
    header @ideas Cache-Control "max-age={IDEAS_MAX_AGE}, must-revalidate"

    # -- Blog entries -------------------------------------------------------
    # An entry is an HTML document written outside this repo. Three layers keep
    # script out of it, and any one of them is enough:
    #   1. the blog service cleans the document before it stores it;
    #   2. the entry's page frames it with a sandbox attribute that grants no
    #      script and no forms;
    #   3. this header, sent on the document itself, so the same holds when a
    #      visitor opens it directly, outside its frame.
    # The value is shared.blog_inbox.ENTRY_CSP: one definition, read by this
    # generator, by the service that writes the frame and by the private
    # preview, so the three cannot drift. This file is generated; change the
    # policy there, never here.
    #
    # Only the framed document is named. The entry's own page is index.html in
    # the same folder: this site's markup, which a sandbox would break. The
    # typefaces under /blog/fonts/ need no rule of their own either. Each is
    # named by a hash of its content, and the *.woff2 rule below covers them.
    # Freshness is the revalidation rule above: entry.html by its extension,
    # the manifest as *.json, and the entry's page by its folder address.
    @blog_entries path_regexp {BLOG_ENTRY_PATH}
    header @blog_entries Content-Security-Policy "{ENTRY_CSP}"

    # Two self-hosted faces, byte-stable for the life of the brand, and the
    # largest repeated download on the site. The only thing here allowed to
    # skip revalidation.
    @fonts path *.woff2
    header @fonts Cache-Control "max-age={FONT_MAX_AGE}"
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

    ⚠ **Why not ``X-Robots-Tag: noindex`` instead, and why not BOTH.** They are
    mutually exclusive in effect: a crawler has to FETCH a page to see a
    noindex header, so a ``Disallow`` hides the very header that would tell it
    not to index -- Google says so explicitly. Sending both is a contradiction
    where the ``Disallow`` wins. ``Disallow`` is the right half here because the
    thing being protected is the CONTENT (positions, marks, P&L), and it is
    never fetched at all; noindex would have every crawler pulling all fourteen
    screens on a schedule, which is the load problem above. The residue is that
    a disallowed URL can still be listed URL-only when something links to it,
    and ``live.html`` links all fourteen -- a listing with no content, which is
    the trade taken knowingly.

    ⚠ **Not a guarantee, and the honest limit is worth stating**: robots.txt is
    a request. The Internet Archive announced in 2017 that it would largely stop
    honouring it, so ``Disallow`` moves the Wayback case from *invited* to
    *unrequested* and no further. Nothing short of not publishing does better,
    which is the decision recorded under "Exposure" in the design doc.

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
{_rate_limit_lines(live_rate_limit())}
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
    runs the paper books and stops the stack.

    Two rules follow, both pinned by test:

    * every proxied route stamps it, and
    * nothing here suppresses or overwrites ``X-Forwarded-For``. Caddy sets it
      by default and APPENDS the peer it observed, which is precisely what makes
      reading the tail safe -- a client-supplied prefix can lengthen the list but
      cannot change its tail.

    The header also does a second job: ``main._client_ip`` reads the LAST
    ``X-Forwarded-For`` hop only when this stamp is present, so the login
    throttle buckets real visitors instead of lumping every request behind the
    edge into one loopback bucket. The auth gate itself does NOT read it -- it
    decides on the session cookie and nothing else.
    """
    return f"""{APP_HOST} {{
    encode zstd gzip

    header {{
        Strict-Transport-Security "{HSTS}"
        # The public one-pager is a separate origin and must not frame this.
        Content-Security-Policy "frame-ancestors 'self'"
    }}

    # POST /login is the one route open without a session. Refuse a large body
    # here, before the app sees it. Scoped to that path: the app's own uploads
    # (the /x page's image) are far larger and sit behind the login.
    @login path /login
    request_body @login {{
        max_size {login_body_kb()}KB
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

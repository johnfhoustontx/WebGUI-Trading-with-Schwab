"""Runnable blog service (port 8217).

Owns the site Blog's drafts and entries (``store``) and is the only writer of
the blog's public files (``sitewriter``). One command stream, ``cmd:blog``, fed
by the private app's Blog page: ``draft_submit`` (an upload becomes a draft),
``publish``, ``discard``, ``unpublish`` (``handlers.handle_command``). Publishes
``cache:blog:drafts`` / ``cache:blog:posts`` and answers every command on
``cache:blog:result``. One scheduler job (``scheduler.loop``): repair and
rebuild at start, then the views again on ``[site] republish_min``.

No Schwab, no Claude. The one thing that leaves the box is the typeface copy at
upload (``fonts.localize``, Google Fonts only).

There is no second stream: the connector that would feed ``cmd:blog_inbox`` is
not built, and nothing here reads it.

Importable without side effects; starts uvicorn only under __main__.
"""
import pathlib
import sys

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from services._scaffold import make_app  # noqa: E402
from services.blog_svc import handlers, scheduler  # noqa: E402
from shared import blog_inbox  # noqa: E402

app = make_app(blog_inbox.OWNER_DOMAIN, scheduler=scheduler.loop,
               command_handler=handlers.handle_command,
               on_dropped=handlers.on_dropped)


if __name__ == "__main__":
    import uvicorn

    from repo_paths import SERVICE_PORTS

    uvicorn.run(app, host="127.0.0.1", port=SERVICE_PORTS["blog"])

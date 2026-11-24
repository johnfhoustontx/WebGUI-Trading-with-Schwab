"""The PUBLIC X Feed screen (``live.neuralstrike.co/x-feed``).

The private /news/x page less every owner control: no filters, no "Load older
posts", no adding, moving or removing an account, no Settings link and no
Symbol-page links. Reached through ``xfeed.render()``, which hands off here on
the public origin (``shell.is_public``) before the private page builds
anything. It WRITES NOTHING: ``XFeedPage`` sends only behind
``_may_enqueue``, which is False here whatever the process says, so no control
that would send is drawn either.

⚠ IT READS TWO VIEWS AND NO OTHER - ``xfeed:latest_public`` and
``xfeed:accounts_public`` - which ``services/xfeed_svc`` writes with only the
accounts config/xfeed.toml ``[public]`` names, and with no account error text.
The public process's Redis user reads ``~cache:*``, which covers the private
pair too - a wildcard grant cannot exclude one key - so the ACL does not stand
behind this rule; the code does, pinned by ``tests/test_xfeed_live.py`` (a bus
serving a different post under each key).

Every region is the private page's own (``XFeedPage`` and its painters in
``xfeed_parts``), so the two origins cannot drift. A post's text, names and
URLs are THIRD-PARTY text and reach the page only through those painters'
labels and links (see ``xfeed_parts``).
"""
from __future__ import annotations

from pages.xfeed import XFeedPage


def render() -> XFeedPage:
    """Build the public X Feed page; returns it for tests."""
    return XFeedPage(False, symbol_linked=False, public=True)

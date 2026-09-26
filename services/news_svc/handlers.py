"""Publish the three news views; dispatch cmd:news.

⚠ The two FEED views carry no timestamp of their own. ``skip_unchanged`` skips a
write only when the payload is byte-identical, so a ``ts`` in it would bump the
version - and repaint every reader - on every poll. "Updated at" comes from the
bus instead: ``{key}:ts`` (``bus_client.read_meta`` - what ``ui_kit.header(view=)``
already draws) is refreshed on EVERY publish, skipped or not, and so means "last
confirmed current"; the envelope's own ``ts`` means "last changed". The status
view keeps its ``ts``: its per-feed ``last_poll`` moves every poll anyway."""
import logging

log = logging.getLogger("news_svc.handlers")

CACHE_FEED = "cache:news:feed"
EVENT_FEED = "events:news:feed"
CACHE_PUBLIC = "cache:news:feed_public"
EVENT_PUBLIC = "events:news:feed_public"
CACHE_STATUS = "cache:news:status"
EVENT_STATUS = "events:news:status"


def publish_feed(bus, rows) -> int:
    return bus.cache_set(CACHE_FEED, {"items": rows}, event=EVENT_FEED, skip_unchanged=True)


def publish_feed_public(bus, rows) -> int:
    """⚠ ``rows`` MUST come from ``store.newest(public_sources=<the feeds public
    NOW>)`` - the store decides the public view, from the CURRENT flags, at
    every publish (``compute.run_poll``). Never build it by filtering the
    private view, and never from the ingest-time ``public`` column alone: a feed
    switched private must vanish from here on the next poll."""
    return bus.cache_set(CACHE_PUBLIC, {"items": rows}, event=EVENT_PUBLIC,
                         skip_unchanged=True)


def publish_status(bus, feeds, now) -> int:
    """``feeds``: one row per configured feed (``compute.status_rows``)."""
    return bus.cache_set(CACHE_STATUS, {"feeds": feeds, "ts": now}, event=EVENT_STATUS)


def handle_command(bus, command) -> None:
    kind = getattr(command, "type", None)
    if kind == "news_refresh":
        from services.news_svc import compute
        result = compute.poll_now(bus)
        if isinstance(result, dict) and result.get("skipped"):
            log.info("news_refresh skipped: a poll is already running")
        return
    log.debug("ignoring cmd:news %s", kind)

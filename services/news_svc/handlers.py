"""Publish the three news views; dispatch cmd:news."""
import logging

log = logging.getLogger("news_svc.handlers")

CACHE_FEED = "cache:news:feed"
EVENT_FEED = "events:news:feed"
CACHE_PUBLIC = "cache:news:feed_public"
EVENT_PUBLIC = "events:news:feed_public"
CACHE_STATUS = "cache:news:status"
EVENT_STATUS = "events:news:status"


def publish_feed(bus, rows, now) -> int:
    return bus.cache_set(CACHE_FEED, {"items": rows, "ts": now}, event=EVENT_FEED,
                         skip_unchanged=True)


def publish_feed_public(bus, rows, now) -> int:
    """⚠ ``rows`` MUST come from ``store.newest(public_sources=<the feeds public
    NOW>)`` - the store decides the public view, from the CURRENT flags, at
    every publish (``compute.run_poll``). Never build it by filtering the
    private view, and never from the ingest-time ``public`` column alone: a feed
    switched private must vanish from here on the next poll."""
    return bus.cache_set(CACHE_PUBLIC, {"items": rows, "ts": now}, event=EVENT_PUBLIC,
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

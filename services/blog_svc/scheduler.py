"""The blog service's one background job: keep the site and the views true.

At start (``first_pass``):

1. ``store.repair()`` - make the files agree with the rows after whatever the
   last run was doing when it stopped;
2. ``sitewriter.rebuild`` - every entry page again, so a promote that changed
   the site menu changes the entry pages (they carry a copy of it);
3. ``handlers.publish_views`` - the drafts and entries lists.

Then every ``[site] republish_min`` minutes (``later_pass``): the site rebuild
again, and the views again. The rebuild rewrites nothing that already agrees
with the store, so on a good site it changes nothing; it is what retries a
publish whose site write failed, and what catches the site up after ``[site]
enabled`` is switched back on (a rebuild with the site off writes nothing and
is OK, so there is nothing "pending" to key on). The views heal a flushed Redis
and are otherwise skipped unchanged.

The loop wakes every ``TICK_S`` seconds rather than sleeping a whole interval:
each wake beats the heartbeat ``/health`` judges the service by, and re-reads
the interval, so an edit applies without a restart. A pass that fails (the bus
is down, the store cannot be opened) is counted and tried again at the next
wake; the first pass stays the first pass until one has finished.

No Schwab, no Claude, no network: this reads the store and writes local files.

⚠ Schedulers do not run outside prod or under pytest
(``_scaffold._schedulers_enabled``), so the two passes are plain functions the
suite calls directly.
"""
import asyncio
import datetime as dt
import logging
import time

from services import _degrade, _heartbeat
from services.blog_svc import _trace, handlers, sitewriter
from shared import blog_inbox

log = logging.getLogger("blog_svc.scheduler")

AREA = "blog.scheduler"
TICK_S = 30          # how often the loop wakes (heartbeat, interval re-read)

# Seams for tests (a fake clock and sleep; never patch asyncio.sleep globally).
_sleep = asyncio.sleep
_monotonic = time.monotonic


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _note_repair(report) -> None:
    """Say what ``repair`` found, by count and by name - never by content. An
    entry left without its document, or a repair that could not finish, is a
    WARNING and is counted: the first is a published page the store can no
    longer rebuild."""
    counts = {key: (len(value) if isinstance(value, list) else value)
              for key, value in report.items() if key != "ok"}
    missing = report.get("entries_missing") or []
    if report.get("ok") and not missing:
        log.info("blog: store repair found %s", counts)
        return
    # ``failed`` holds the KINDS of step that failed (the store never puts a
    # name there), so it is said as it is.
    failed = report.get("failed")
    log.warning("blog: store repair %s (failed steps: %s); entries without a document: "
                "%s; found %s",
                "finished" if report.get("ok") else "DID NOT FINISH",
                ", ".join(str(kind) for kind in failed) if isinstance(failed, list) and failed
                else "none",
                ", ".join(str(slug) for slug in missing) or "none", counts)
    _degrade.degraded("blog.repair", exc_info=False,
                      detail="an entry has no document" if report.get("ok")
                      else "repair did not finish")


def _rebuild(store) -> None:
    report = sitewriter.rebuild(sitewriter.SITE_ROOT, store, _now())
    # A shortfall is already counted by the writer (``blog.site``); this is the
    # line that says which entries it was.
    (log.info if report.get("ok") else log.warning)(
        "blog: site rebuild ok=%s written=%s removed=%s skipped=%s",
        report.get("ok"), report.get("written"), report.get("removed"),
        ", ".join(report.get("skipped") or []) or "none")


def first_pass(bus) -> None:
    """Repair, rebuild, publish the views. Run once, at start."""
    with handlers.open_store() as store:
        _note_repair(store.repair())
        _rebuild(store)
        handlers.publish_views(bus, store)


def later_pass(bus) -> None:
    """Rebuild the site and publish the views again, every time.

    The rebuild is not held back for "only while the last one fell short": a
    rebuild with ``[site] enabled = false`` writes nothing and is OK, so nothing
    is pending after it, and switching the site back on would then put nothing
    on it (and take nothing down) until a restart or the next publish.
    ``sitewriter.rebuild`` is idempotent - it rewrites no file that already
    agrees with the store - so on a site that is right this costs a read."""
    with handlers.open_store() as store:
        _rebuild(store)
        handlers.publish_views(bus, store)


def interval_s() -> int:
    """Seconds between passes: ``[site] republish_min``, already held to its
    bounds by ``blog_inbox``."""
    return int(blog_inbox.site()["republish_min"]) * 60


async def _to_thread(fn, *args):
    """Run a blocking pass on the default executor. A seam: tests swap in an
    inline call."""
    return await asyncio.get_running_loop().run_in_executor(None, fn, *args)


async def _run(fn, bus) -> bool:
    """One pass. Never raises (but a cancellation); True when it finished."""
    try:
        await _to_thread(fn, bus)
        return True
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # one bad pass must not end the scheduler
        _degrade.degraded(AREA, detail=_trace.where("a blog scheduler pass", exc),
                          exc_info=False)
        return False


async def loop(bus) -> None:
    """Every ``TICK_S``: beat, and when a pass is due, run it.

    The first pass is ``first_pass`` and stays so until one has finished; after
    that ``later_pass`` every ``interval_s()``, measured from when the last
    pass ENDED. A pass that failed is due again at the next wake."""
    started = False
    last_end = None
    while True:
        _heartbeat.tick()
        if last_end is None or _monotonic() - last_end >= interval_s():
            if await _run(later_pass if started else first_pass, bus):
                started = True
                last_end = _monotonic()
            else:
                last_end = None
        await _sleep(TICK_S)

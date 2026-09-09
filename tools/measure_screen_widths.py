"""Measure, for real, which public live screens scroll sideways and below what width.

The dashboard pages on ``live.neuralstrike.co`` are authored for a wide monitor.
Below some width the panels stop fitting and the WHOLE DOCUMENT scrolls
sideways, which carries the panel heading and the row-identifying column off
screen. The fix is to move that scroll inside each panel -- but that is thirteen
page modules, and only the Desk's clipping was ever established. This tool
produces the evidence for the rest.

**Why a browser-driving library rather than the two obvious things.** Both were
tried and both produce confident fiction:

* ``chrome --headless --screenshot`` renders a picture and cannot report an
  element width. Worse, ``--hide-scrollbars`` -- which this repo's capture tools
  pass, for good reasons of their own -- makes a page that merely scrolls look
  clipped. That already produced one wrong conclusion in this work.
* The Claude Browser pane returns ``viewport: 0`` on this app, so every number
  derived from it is invented. CLAUDE.md records the rule that came out of it:
  always print the viewport beside the measurement.

Hence Selenium, which gives ``execute_script`` against a real window size -- and
hence ``viewport_verdict``, which refuses to report a row whose viewport is not
the width we asked for. **An untrustworthy row is printed as a reason, never as
a number**, because the output of this tool decides which page modules get
edited and a fabricated width sends that edit at the wrong files.

The file is split so the judgement is testable without a browser: everything
that decides what a number MEANS is a module-level pure function, and the
Selenium driving is thin wrappers around them.

Usage (needs the dev dependency: ``pip install -r requirements-dev.txt``)::

    python tools/measure_screen_widths.py
    python tools/measure_screen_widths.py --screens desk,flow --widths 1280,1920
    python tools/measure_screen_widths.py --base http://127.0.0.1:8501 --json out.json
"""
import argparse
import importlib.util
import json
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import repo_paths  # noqa: E402

# The six the plan names. 1280 and 1366 are the common laptop widths, 1440/1600
# the common "wide but not huge" ones, 1920 the desk monitor, 2560 the ceiling
# these pages were actually authored against.
DEFAULT_WIDTHS = [1280, 1366, 1440, 1600, 1920, 2560]

# Tall enough that a dashboard's lower panels are laid out rather than deferred,
# and tall enough to be a plausible browser. Height is not what is under test.
DEFAULT_HEIGHT = 1000

# ``scrollWidth`` is an integer rounded UP, so a layout landing on a fraction of
# a pixel reports 1px of overflow on a page that does not scroll at all. Treat
# anything at or below this as noise -- calling it a clip would nominate every
# screen in the app for Task 4.
CLIP_THRESHOLD_PX = 2

# A resize is a request, not a guarantee. Allow a pixel or two of rounding at a
# non-integer device scale, and nothing more.
VIEWPORT_TOLERANCE_PX = 2

# ⚠ A screen that did not paint cannot overflow, so "no data" is indistinguishable
# from "fits" unless the tool says so. Three things can produce that here: a cold
# Redis view (the page draws the shared waiting line), a settle that gave up
# early, or a route that 404'd. So every row carries how much page there WAS.
#
# The marker is the stem shared by all three ``webgui/pages/copy.py`` waiting
# sentences; a test pins that it really is a substring of each, so a reworded
# line cannot silently turn this check off.
WAITING_MARKER = "hasn't published this session"

# Below this the DOM is the NiceGUI shell and little else. Measured live: the
# thinnest real screen renders ~700 elements, the fullest ~4,000.
MIN_REAL_ELEMENTS = 150

# How many overflowing elements to print per width, ON TOP of the document-level
# ones, which are never dropped. Enough to tell a panel-level overflow (a table
# inside a card) from a document-level one (``html``/``body`` themselves), which
# is the distinction Task 4 turns on.
ELEMENT_LIMIT = 8

# ``html`` and ``body`` overflowing IS the whole-document sideways scroll being
# fixed. They are always reported, however many panels outrank them.
DOC_LEVEL_TAGS = ("html", "body")

# A Tailwind arbitrary value is one class and can run past 180 characters
# (measured on the Desk's positions grid). The leading characters identify the
# element; the rest wraps the table it is meant to explain.
MAX_CLASS_CHARS = 26

# Settle policy. These pages fetch from Redis on a watcher tick and repaint over
# a websocket, so a measurement taken at ``load`` is a measurement of skeletons.
# Poll for a DOM that stopped changing rather than sleeping a fixed guess.
SETTLE_POLL_SEC = 0.35
SETTLE_STABLE_POLLS = 4          # ~1.4s unchanged
SETTLE_TIMEOUT_SEC = 25.0
SETTLE_MIN_SEC = 3.0             # never believe a DOM that settled instantly

PAGE_LOAD_TIMEOUT_SEC = 60


# --------------------------------------------------------------------------
# The published screen table. NOT a typed list of URLs -- that goes stale the
# moment a screen is published, silently, and a screen nobody measured looks
# exactly like a screen that measured clean.
# --------------------------------------------------------------------------

def _live_screens():
    """``webgui/live_screens.py``, loaded BY PATH rather than by import.

    ``webgui/`` holds top-level modules named ``main``, ``proxy``, ``auth`` and
    ``wall``; putting that directory on ``sys.path`` would let any of them
    shadow a same-named module elsewhere in this process -- and this file is
    imported by a test that runs alongside ``tests``, ``deploy`` and the rest of
    ``tools/tests`` in one pytest session. One file loaded by its path reaches
    nothing else, and it is still a real import, so a broken table fails loudly.
    (The pattern is ``tools/capture_live_shots.py``'s, for the same reason.)
    """
    path = pathlib.Path(repo_paths.WEBGUI) / "live_screens.py"
    spec = importlib.util.spec_from_file_location("_live_screens_for_widths", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def screens():
    """Every published screen, in published order. PURE."""
    return list(_live_screens().SCREENS)


def select_screens(all_screens, slugs):
    """``--screens`` filter. Raises on a slug nobody publishes.

    Refusing is the point: a mistyped slug that silently selected nothing would
    print an empty table, which reads exactly like "nothing clips".
    """
    if not slugs:
        return list(all_screens)
    by_slug = {s.slug: s for s in all_screens}
    unknown = [s for s in slugs if s not in by_slug]
    if unknown:
        raise ValueError(f"no such screen(s): {', '.join(unknown)} "
                         f"(published: {', '.join(by_slug)})")
    return [by_slug[s] for s in slugs]


def screen_url(base, screen):
    """``base`` + the screen's public route. PURE."""
    return f"{base.rstrip('/')}{screen.route}"


# --------------------------------------------------------------------------
# What the numbers mean. All pure; all tested without a browser.
# --------------------------------------------------------------------------

def viewport_verdict(requested, inner_width, client_width):
    """``None`` if this row can be trusted, else the REASON it cannot.

    This is the guard the whole file exists around. A browser that reports a
    zero or wrong viewport still reports a ``scrollWidth``, and the subtraction
    still yields a number that looks like a measurement -- which is how the
    Browser pane's ``viewport: 0`` produced a chart "collapsed at 20x680".

    ``inner_width`` (with the scrollbar) is what is compared against the width
    we asked for; ``client_width`` (without it) is only checked for being
    present, since a classic vertical scrollbar legitimately eats ~15px off it.
    """
    if not inner_width:
        return f"viewport reported 0 (asked for {requested}) - not a measurement"
    if not client_width:
        return f"documentElement.clientWidth reported 0 at viewport {inner_width}"
    if abs(inner_width - requested) > VIEWPORT_TOLERANCE_PX:
        return f"viewport is {inner_width}, not the {requested} we asked for"
    return None


def content_warning(element_count, waiting):
    """``None`` if this page really rendered, else why its "ok" means nothing.

    Distinct from ``viewport_verdict`` on purpose. That one says the MEASUREMENT
    is fiction; this one says the measurement is fine and the PAGE was not there
    to measure -- a screen showing the shared "hasn't published this session"
    line has no table to overflow, and would otherwise be filed as "fits at
    1280" for Task 4 to skip.
    """
    if waiting:
        return "page shows the 'no data yet' waiting line - nothing to overflow"
    if element_count < MIN_REAL_ELEMENTS:
        return f"only {element_count} elements rendered - page did not paint"
    return None


def overflow_px(scroll_width, client_width):
    """How far the content sticks out past the box that holds it. PURE."""
    return scroll_width - client_width


def is_clipping(overflow):
    """Is this overflow real, or integer-rounding noise? PURE."""
    return overflow > CLIP_THRESHOLD_PX


def element_ident(tag, classes):
    """``div.a.b.c`` -- tag plus at most three classes, each truncated. PURE.

    Short on purpose: these pages carry long Tailwind class strings -- a single
    arbitrary value can exceed 180 characters -- and the full attribute would
    drown the table it is meant to explain. A truncated class ends in ``~`` so
    the reader knows to go and look rather than grepping for a string that does
    not exist in the source.
    """
    parts = [tag]
    for c in list(classes)[:3]:
        parts.append(f".{c}" if len(c) <= MAX_CLASS_CHARS
                     else f".{c[:MAX_CLASS_CHARS]}~")
    return "".join(parts)


def is_self_containing(overflow_x):
    """Does this element already contain its own overflow? PURE.

    An element with ``overflow-x: auto`` scrolls itself, which IS what Task 4
    is for -- so it is finished, and listing it as work would be a false
    positive in that task's input. ``hidden``/``clip`` contain it too (by
    truncation rather than by scrolling), which is a different product
    decision but equally not a document-level leak.
    """
    return overflow_x in ("auto", "scroll", "hidden", "clip")


def top_overflowing(raw, limit=ELEMENT_LIMIT):
    """The overflowing elements worth printing, annotated. PURE.

    ``raw`` is what the page script returned: dicts of ``tag`` / ``classes`` /
    ``overflow_x`` / ``scroll_width`` / ``client_width``.

    Three deliberate reductions, each of which was a defect on the first live
    run against ``/desk``:

    * **Zero-width elements are dropped.** Inline and SVG nodes report a zero
      ``clientWidth`` alongside a real ``scrollWidth``, and there are enough of
      them to crowd out the panel that actually overflows.
    * **Identical idents collapse to one row with a count.** A table body is one
      row element repeated; eight identical lines say nothing an ``x8`` does not.
    * **``html``/``body`` are never dropped.** They ARE the whole-document
      sideways scroll being fixed, and on the Desk eight repeated grid rows
      outranked them and pushed them off the end -- which turns a document-level
      leak into what reads like a panel-level one.
    """
    seen = {}
    for el in raw:
        cw = int(el.get("client_width") or 0)
        if cw <= 0:
            continue
        ov = overflow_px(int(el.get("scroll_width") or 0), cw)
        if not is_clipping(ov):
            continue
        tag = el.get("tag", "?")
        ident = element_ident(tag, el.get("classes") or [])
        prev = seen.get(ident)
        if prev is not None:
            prev["count"] += 1
            if ov > prev["overflow"]:
                prev.update(overflow=ov, scroll_width=int(el["scroll_width"]),
                            client_width=cw)
            continue
        seen[ident] = {"ident": ident,
                       "overflow_x": el.get("overflow_x", ""),
                       "self_containing": is_self_containing(el.get("overflow_x", "")),
                       "document_level": tag in DOC_LEVEL_TAGS,
                       "scroll_width": int(el["scroll_width"]),
                       "client_width": cw,
                       "overflow": ov,
                       "count": 1}
    rows = sorted(seen.values(), key=lambda e: e["overflow"], reverse=True)
    doc = [e for e in rows if e["document_level"]]
    rest = [e for e in rows if not e["document_level"]][:limit]
    return sorted(doc + rest, key=lambda e: e["overflow"], reverse=True)


def uncontained(elements):
    """How many of these still leak into the document. PURE."""
    return sum(1 for e in elements if not e["self_containing"])


def clip_summary(rows):
    """One screen's verdict, over its per-width rows. PURE.

    ``clips`` is deliberately three-valued. ``False`` means every width we could
    trust came back clean; ``None`` means we had no usable row at all, which is
    a different thing entirely and must not be reported as "fine".

    Two kinds of row are excluded from the verdict, in BOTH directions: one we
    could not trust (``untrustworthy``), and one where the page never painted
    (``empty``). A blank page cannot overflow, so counting it as a clean width
    would be the exact false negative this tool exists to avoid.
    """
    trusted = [r for r in rows if not r.get("untrustworthy")]
    bad = len(rows) - len(trusted)
    evidential = [r for r in trusted if not r.get("empty")]
    blank = len(trusted) - len(evidential)
    if not evidential:
        return {"clips": None, "worst_width": None, "clean_from": None,
                "untrustworthy": bad, "empty": blank}
    clipping = [r for r in evidential if is_clipping(r["overflow"])]
    clean = [r for r in evidential if not is_clipping(r["overflow"])]
    return {"clips": bool(clipping),
            # The WIDEST width that still clips -- i.e. "clips at or below this".
            "worst_width": max((r["width"] for r in clipping), default=None),
            # The narrowest width that came back clean.
            "clean_from": min((r["width"] for r in clean), default=None),
            "untrustworthy": bad, "empty": blank}


def failed_row(url, width, error):
    """A width that could not be measured at all. PURE.

    Recorded as an UNTRUSTWORTHY row rather than dropped, for the reason the
    whole file is built around: a width that silently vanishes from the table
    reads as a width nobody needed to test, and ``clip_summary`` would then draw
    its verdict from the widths that happened to work.
    """
    return {"url": url, "width": width, "viewport": 0, "scrollbar": None,
            "scroll_width": 0, "client_width": 0, "body_scroll_width": 0,
            "overflow": 0, "untrustworthy": f"measurement failed: {error}",
            "settle_sec": 0.0, "element_count": 0, "text_len": 0,
            "empty": None, "elements": [], "uncontained": 0}


def corrected_outer(outer, inner, target):
    """The outer window width to ask for next, to land ``inner`` on ``target``.

    A window rect includes chrome this tool does not control, so setting 1280
    can yield a 1264 viewport. The correction is just the delta -- applied
    iteratively, since the second resize can shift the chrome again. PURE.
    """
    return outer + (target - inner)


def is_settled(history, stable_polls=SETTLE_STABLE_POLLS):
    """Has the DOM stopped changing? PURE, over the last N signatures.

    The signature carries ``readyState`` precisely so that a page which is
    stable only because it has not begun cannot pass this.
    """
    if len(history) < stable_polls:
        return False
    tail = history[-stable_polls:]
    if len(set(tail)) != 1:
        return False
    return tail[0].startswith("complete|")


def parse_widths(text):
    """``"1280,1920"`` -> ``[1280, 1920]``. Raises on junk. PURE."""
    out = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if not part.isdigit():
            raise ValueError(f"not a width: {part!r}")
        out.append(int(part))
    if not out:
        raise ValueError("no widths given")
    return out


# --------------------------------------------------------------------------
# The browser. Thin wrappers only -- no judgement lives below this line.
# --------------------------------------------------------------------------

# One script, one round trip: the viewport, the document box, and every
# overflowing element. Collected together so nothing can be read from a
# different layout pass than the number beside it.
_MEASURE_JS = r"""
const de = document.documentElement;
const els = [];
const all = document.querySelectorAll('*');
for (let i = 0; i < all.length; i++) {
  const el = all[i];
  const cw = el.clientWidth;
  if (!cw) continue;
  if (el.scrollWidth - cw <= 1) continue;
  const cs = window.getComputedStyle(el);
  els.push({
    tag: el.tagName.toLowerCase(),
    classes: (el.getAttribute('class') || '').split(/\s+/).filter(Boolean),
    overflow_x: cs.overflowX,
    scroll_width: el.scrollWidth,
    client_width: cw
  });
}
const text = document.body ? (document.body.innerText || '') : '';
return {
  inner_width: window.innerWidth,
  doc_scroll_width: de.scrollWidth,
  doc_client_width: de.clientWidth,
  body_scroll_width: document.body ? document.body.scrollWidth : 0,
  element_count: all.length,
  text_len: text.length,
  waiting: text.indexOf(arguments[0]) !== -1,
  elements: els
};
"""

_SIGNATURE_JS = ("return [document.readyState,"
                 " document.getElementsByTagName('*').length,"
                 " document.documentElement.scrollWidth,"
                 " document.documentElement.scrollHeight].join('|');")


def build_driver(chrome_binary=None):
    """A headless Chrome. Selenium is imported HERE, not at module scope.

    Everything above this line is pure and must stay importable -- and testable
    -- on a machine with no browser stack at all.
    """
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options

    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-first-run")
    opts.add_argument("--no-default-browser-check")
    opts.add_argument("--force-device-scale-factor=1")
    # ⚠ NO --hide-scrollbars. It is what the capture tools pass, and it is
    # exactly what made a merely-scrolling page look clipped: it changes how
    # much width the content is given. The scrollbar is reported instead.
    if chrome_binary:
        opts.binary_location = chrome_binary
    driver = webdriver.Chrome(options=opts)
    driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT_SEC)
    return driver


def set_viewport(driver, width, height, attempts=4):
    """Resize until ``window.innerWidth`` really is ``width``. Returns it.

    Returns whatever it actually achieved -- including a wrong value -- because
    lying about this is the failure mode the whole tool is built around.
    ``viewport_verdict`` is what turns a wrong value into a refused row.
    """
    outer = width
    inner = 0
    for _ in range(attempts):
        driver.set_window_rect(width=outer, height=height)
        # ⚠ Read AFTER a beat, not immediately. Measured live on /gamma: an
        # innerWidth read the instant the rect is set came back 1472 for a 1440
        # request -- wider than the window asked for -- and the correction then
        # chased a value that was mid-reflow. The guard refused the row, which
        # is right, but the row was measurable.
        time.sleep(0.2)
        inner = int(driver.execute_script("return window.innerWidth;") or 0)
        if inner and abs(inner - width) <= VIEWPORT_TOLERANCE_PX:
            return inner
        if not inner:
            break
        outer = corrected_outer(outer, inner, width)
    return inner


def wait_settled(driver, timeout=SETTLE_TIMEOUT_SEC, min_sec=SETTLE_MIN_SEC):
    """Poll until the DOM stops changing, or give up. Returns seconds waited.

    A fixed sleep was the simpler option and is the wrong one here: these pages
    paint from Redis over a websocket on a 2s watcher tick, so the interesting
    content arrives well after ``load`` and the arrival time varies by screen.
    """
    started = time.monotonic()
    history = []
    while time.monotonic() - started < timeout:
        try:
            history.append(driver.execute_script(_SIGNATURE_JS))
        except Exception:                                    # noqa: BLE001
            history.append("error|0|0|0")
        waited = time.monotonic() - started
        if waited >= min_sec and is_settled(history):
            return waited
        time.sleep(SETTLE_POLL_SEC)
    return time.monotonic() - started


def measure_one(driver, url, width, height):
    """One screen at one width, LOADED FRESH at that width.

    Resizing a loaded page would be quicker, and would measure the wrong thing:
    a NiceGUI page is built per client and this app's Highcharts have no
    ResizeObserver (documented in CLAUDE.md -- a chart that mounts at one width
    keeps it). What a visitor at 1280 actually gets is a page BUILT at 1280.
    """
    set_viewport(driver, width, height)
    driver.get(url)
    waited = wait_settled(driver)
    # The load may have shifted the window chrome; confirm the viewport again.
    set_viewport(driver, width, height)
    time.sleep(0.4)                       # let the reflow finish before reading
    raw = driver.execute_script(_MEASURE_JS, WAITING_MARKER)
    inner = int(raw.get("inner_width") or 0)
    client = int(raw.get("doc_client_width") or 0)
    scroll = int(raw.get("doc_scroll_width") or 0)
    reason = viewport_verdict(width, inner, client)
    elements = top_overflowing(raw.get("elements") or [])
    return {
        "url": url,
        "width": width,
        "viewport": inner,                      # printed beside EVERY number
        "scrollbar": (inner - client) if (inner and client) else None,
        "scroll_width": scroll,
        "client_width": client,
        "body_scroll_width": int(raw.get("body_scroll_width") or 0),
        "overflow": overflow_px(scroll, client),
        "untrustworthy": reason,
        "settle_sec": round(waited, 1),
        "element_count": int(raw.get("element_count") or 0),
        "text_len": int(raw.get("text_len") or 0),
        "empty": content_warning(int(raw.get("element_count") or 0),
                                 bool(raw.get("waiting"))),
        "elements": elements,
        "uncontained": uncontained(elements),
    }


def measure_screen(driver, base, screen, widths, height):
    """One screen at every width -- a fresh load per width. See ``measure_one``."""
    url = screen_url(base, screen)
    rows = []
    for width in widths:
        try:
            rows.append(measure_one(driver, url, width, height))
        except Exception as exc:                              # noqa: BLE001
            # One width that would not load is not the other five. Recorded as
            # a refused row, never dropped -- see ``failed_row``.
            print(f"    {screen.slug} @{width}: {exc}", file=sys.stderr)
            rows.append(failed_row(url, width, exc))
    return {"slug": screen.slug, "title": screen.title, "route": screen.route,
            "url": url, "rows": rows, "summary": clip_summary(rows)}


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------

def format_screen(result):
    """The per-screen block. PURE (takes the dict, returns the text)."""
    out = [f"=== {result['slug']}  {result['route']}  ({result['title']})",
           f"    {result['url']}"]
    if result.get("error"):
        out.append(f"    FAILED - {result['error']}")
        out.append("    -> NO VERDICT: this screen was not measured")
        return "\n".join(out)
    out.append("    width  viewport  scrollbar  scrollW  clientW  overflow  elems  verdict")
    for r in result["rows"]:
        vp = r["viewport"]
        sb = "-" if r["scrollbar"] is None else str(r["scrollbar"])
        if r["untrustworthy"]:
            verdict = f"UNTRUSTWORTHY - {r['untrustworthy']}"
            ov = "?"
        elif r.get("empty"):
            verdict = f"NO VERDICT - {r['empty']}"
            ov = str(r["overflow"])
        else:
            verdict = "CLIPS" if is_clipping(r["overflow"]) else "ok"
            ov = str(r["overflow"])
        out.append(f"    {r['width']:>5}  {vp:>8}  {sb:>9}  "
                   f"{r['scroll_width']:>7}  {r['client_width']:>7}  {ov:>8}  "
                   f"{r.get('element_count', 0):>5}  {verdict}")
    # Which widths get an element breakdown: every width that clips, plus the
    # NARROWEST usable one even when it does not.
    #
    # That second case is not padding. A screen reading clean tells Task 4
    # nothing about WHY -- it may reflow, or it may already have a panel with
    # ``overflow-x: auto`` doing exactly the job that task was going to add. The
    # elements list is the only place that distinction appears, and it is the
    # difference between "leave this file alone" and "this file is already done".
    usable = [i for i, r in enumerate(result["rows"])
              if not r["untrustworthy"] and not r.get("empty")]
    show = {i for i, r in enumerate(result["rows"]) if is_clipping(r["overflow"])}
    if usable:
        show.add(min(usable, key=lambda i: result["rows"][i]["width"]))
    for i, r in enumerate(result["rows"]):
        if (r["untrustworthy"] or r.get("empty")
                or i not in show or not r["elements"]):
            continue
        head = "overflowing elements" if is_clipping(r["overflow"]) else \
            "document fits; elements overflowing INSIDE it"
        out.append(f"    {head} @{r['width']} "
                   f"({r['uncontained']} of {len(r['elements'])} still leak):")
        for e in r["elements"]:
            mark = "contained" if e["self_containing"] else "LEAKS    "
            times = f" x{e['count']}" if e["count"] > 1 else ""
            out.append(f"      {mark}  overflow-x:{e['overflow_x']:<8} "
                       f"+{e['overflow']:<6} {e['scroll_width']}/{e['client_width']}"
                       f"  {e['ident']}{times}")
    s = result["summary"]
    if s["clips"] is None:
        out.append("    -> NO VERDICT: no width produced usable evidence "
                   f"({s['untrustworthy']} untrustworthy, {s.get('empty', 0)} blank)")
    elif s["clips"]:
        clean = s["clean_from"]
        out.append(f"    -> CLIPS at or below {s['worst_width']}px"
                   + (f"; clean from {clean}px up" if clean else "; clean at no width tested"))
    else:
        out.append(f"    -> clean at every width tested (from {s['clean_from']}px up)")
    return "\n".join(out)


def format_report(results):
    """The whole run: per-screen blocks then a one-line-per-screen summary. PURE."""
    blocks = [format_screen(r) for r in results]
    lines = ["", "=" * 78, "SUMMARY", "=" * 78,
             f"{'screen':<26} {'clips below':>12}  {'worst':>7}  {'elems':>6}  note"]
    for r in results:
        s = r["summary"]
        good = [x for x in r["rows"] if not x["untrustworthy"] and not x.get("empty")]
        worst = max((x["overflow"] for x in good), default=0)
        elems = max((x.get("element_count", 0) for x in r["rows"]), default=0)
        if s["clips"] is None:
            verdict = "no verdict"
        elif s["clips"]:
            verdict = f"<={s['worst_width']}px"
        else:
            verdict = "never"
        note = ""
        if r.get("error"):
            note = f"FAILED: {r['error']}"
        elif s["untrustworthy"] or s.get("empty"):
            note = (f"{s['untrustworthy']} untrustworthy, "
                    f"{s.get('empty', 0)} blank of {len(r['rows'])}")
        lines.append(f"{r['slug']:<26} {verdict:>12}  {worst:>6}px  {elems:>6}  {note}")
    return "\n\n".join(blocks) + "\n" + "\n".join(lines)


def main(argv=None):
    """``argv=None`` means NO ARGUMENTS, not ``sys.argv`` -- so a call from a
    test cannot parse pytest's own command line and exit 2."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", default="https://live.neuralstrike.co",
                    help="origin to measure (default: the public live origin)")
    ap.add_argument("--widths", default=",".join(str(w) for w in DEFAULT_WIDTHS))
    ap.add_argument("--height", type=int, default=DEFAULT_HEIGHT)
    ap.add_argument("--screens", default="", help="comma-separated slugs (default: all)")
    ap.add_argument("--chrome", default=None, help="path to the Chrome binary")
    ap.add_argument("--json", default=None, help="also write the raw results here")
    args = ap.parse_args([] if argv is None else argv)

    widths = parse_widths(args.widths)
    slugs = [s.strip() for s in args.screens.split(",") if s.strip()]
    todo = select_screens(screens(), slugs)

    driver = build_driver(args.chrome)
    results = []
    try:
        for screen in todo:
            print(f"... {screen.slug}", file=sys.stderr, flush=True)
            try:
                results.append(measure_screen(driver, args.base, screen, widths, args.height))
            except Exception as exc:                          # noqa: BLE001
                # One screen that will not load is not the run. Recorded as a
                # screen with no verdict, never as a screen that measured clean.
                print(f"    FAILED {screen.slug}: {exc}", file=sys.stderr)
                results.append({"slug": screen.slug, "title": screen.title,
                                "route": screen.route,
                                "url": screen_url(args.base, screen),
                                "rows": [], "summary": clip_summary([]),
                                "error": str(exc)})
    finally:
        driver.quit()

    print(format_report(results))
    if args.json:
        pathlib.Path(args.json).write_text(json.dumps(results, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

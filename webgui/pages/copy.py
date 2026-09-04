"""Shared user-facing copy for the pages.

The sibling of ``pages/fmt.py``, which does the same job for the shared NUMERIC
vocabulary and carries the same "the ONE copy" comment at every call site. This
file holds sentences instead: text that more than one screen shows for one
condition, where two screens wording it differently would be a defect rather
than a style difference.

**Why a module and not an import between pages.** ``pages/desk.py`` imports both
``pages.options.flow`` (for ``alert_rows`` and its tone map) and
``pages.options.matrix`` (for ``signal_class``), so neither of those can import
back without a cycle. The Flow Alerts pass settled for a guarded copy — a
restated literal plus a test pinning it equal to the Desk's. That is the right
answer for two copies and the wrong one for three, so this module exists and the
guard is gone with the copy it guarded.

It imports nothing from ``pages``, which is what makes it safe for any page to
import at any depth.
"""

# A domain's feed has published NOTHING this session — shown by /desk (four
# panels), /options/flow and /options/matrix.
#
# ⚠ This is NOT the line for "the feed is fine and has nothing to report". That
# distinction is the whole reason this app must never print a zero it did not
# read, and every page drawing this one also carries its own quiet-market line:
# a dead service and a still tape must never render the same words. See
# ``desk.EMPTY_*`` and ``flow.status_text``'s empty branch.
#
# It names no service. "Waiting for the options service" is what it said until
# 2026-09-04, and off-hours — when it is the most-read text on three screens —
# that made a closed market read as a fault worth chasing.
WAITING_OPTIONS = "No data yet — the options feed hasn't published this session."

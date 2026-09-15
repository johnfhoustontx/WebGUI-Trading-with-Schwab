"""The checklist's Checks column and "Only clear" filter - the PURE parts every
candidate table shares (Market Scanner, Strategy Finder).

No widget library and no page module: the Finder must not import the scanner
PAGE for these, and two tables wording or filtering one verdict differently
would be a defect, not a style difference. ``checks`` / ``checks_feed`` are
imported lazily inside the functions, as they were in ``scanner.py``.
"""


# The checklist's one-chip verdict, coloured by its state (a fixed class per state).
CHECKS_SLOT = r'''
  <q-td :props="props">
    <span :class="props.row._checks_class + ' text-xs whitespace-nowrap'">{{ props.row._checks_short || '—' }}</span>
    <q-tooltip v-if="props.value">{{ props.value }}</q-tooltip>
  </q-td>
'''

# Every field ``stamp_checks`` writes - what a memo entry holds.
CHECK_FIELDS = ("checks", "_checks_state", "_checks_class", "_checks_short", "_checks_clear")

ONLY_CLEAR_TIP = ("Hide rows with a block, a caution, a feed that hasn't loaded, "
                  "or a paper book fit that couldn't be checked")


def stamp_checks(rows, signals, ctx, build=None, memo=None):
    """Stamp the checklist verdict (``checks`` / ``_checks_state`` /
    ``_checks_class`` / ``_checks_short`` / ``_checks_clear``) onto display rows,
    joined by id (the builders re-sort).

    ``_checks_clear`` is what "Only clear" filters on: the chip reads Clear AND no
    Paper book line is grey. A grey book line (no risk stamp, or a book fit that
    could not be computed) does not change the chip's verdict, but a row whose fit
    was never checked must not pass a filter that promises it was.

    Runs AFTER the table has settled each row's ``_allow_paper`` (the scanner's
    ``stamp_stale``; the Finder's gate is fixed by structure): the Paper book line
    needs that gate, which raw signals don't carry - passed the bare signal, every
    row would silently lose that line. ``build`` is injectable for tests;
    production uses ``checks_feed.checks_for``.

    ``memo`` (a caller-owned dict, ``{id: stamps}``) spares a list that is rebuilt
    from the same signals - a Finder chip click - from re-checking every row: a
    row whose id is in it takes those stamps, and every row stamped here adds its
    own. It is valid only for ONE context and one ``_allow_paper`` per id, so the
    caller empties it when either can change."""
    from . import checks, checks_feed
    build = build or checks_feed.checks_for
    by_id = {s.get("id"): s for s in (signals or []) if s.get("id")}
    for r in rows:
        key = r.get("id")
        if memo is not None and key in memo:
            r.update(memo[key])
            continue
        sig = by_id.get(key)
        items = build({**sig, "_allow_paper": r.get("_allow_paper")}, ctx) if sig else []
        chip = checks.verdict(items)
        r["checks"], r["_checks_state"], r["_checks_class"] = (
            chip["text"], chip["state"], chip["class"])
        r["_checks_short"] = chip["short"]
        r["_checks_clear"] = chip["state"] == "pos" and not any(
            c.get("key") == "book" and c.get("tone") == "muted" for c in items)
        if memo is not None and key is not None:
            memo[key] = {f: r[f] for f in CHECK_FIELDS}
    return rows


def only_clear(rows):
    """Rows whose checklist reads Clear - no blocks, no cautions, nothing missing -
    and whose Paper book fit, when the row has that line, was actually checked.

    A row with no ``_checks_clear`` stamp is hidden: the filter fails closed."""
    return [r for r in rows
            if r.get("_checks_state") == "pos" and r.get("_checks_clear", False)]


def filtered_tab_label(base, total, shown, *, have, filtering):
    """Tab header while "Only clear" may be on: ``'Swing (3 of 40)'`` filtered,
    ``'Swing (40)'`` not, and the bare name before today's scan exists."""
    if not have:
        return base
    if filtering:
        return f"{base} ({shown} of {total})"
    return f"{base} ({total})"


def only_clear_empty_label(full_rows, shown_rows, *, filtering):
    """The table's empty-state line when "Only clear" has hidden every row, or
    ``None`` for the table's normal empty label.

    Every hidden row partly checked means a feed has not loaded, which the reader
    can do nothing about but should know; anything else is the filter doing its
    job."""
    full_rows = full_rows or []
    if not filtering or not full_rows or shown_rows:
        return None
    n = len(full_rows)
    if all(r.get("_checks_state") == "muted" for r in full_rows):
        return ("Every row is only partly checked — a feed the checks read hasn't "
                f"loaded. Turn off Only clear to see all {n}.")
    # "fully clear", not "reads Clear": a hidden row can read Clear on its chip
    # while its paper book fit went unchecked.
    return f"No row is fully clear — {n} hidden by Only clear."


def restamp(rows_by_key, sigs_by_key, ctx, build=None, memo=None):
    """Re-stamp the checklist onto SHALLOW COPIES of painted rows.

    Copies, because the event loop may be filtering the very same dicts for the
    "Only clear" switch while this runs off it. Every other stamp (``_new``,
    ``_allow_paper``, the stale marks) rides along on the copy."""
    out = {}
    for key, rows in (rows_by_key or {}).items():
        copies = [dict(r) for r in rows or []]
        stamp_checks(copies, (sigs_by_key or {}).get(key) or [], ctx, build=build,
                     memo=memo)
        out[key] = copies
    return out


def read_and_restamp(rows, signals):
    """``(ctx, copies, memo)``: read the checklist's live context, then re-stamp
    SHALLOW COPIES of one table's ``rows`` against it, filling a fresh memo.
    **Blocking** (a Redis read) - go through ``run.io_bound``."""
    from . import checks_feed
    ctx = checks_feed.read_context()
    memo = {}
    fresh = restamp({"rows": rows}, {"rows": signals}, ctx, memo=memo)["rows"]
    return ctx, fresh, memo

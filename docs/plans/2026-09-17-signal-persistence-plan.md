# Signal Age and Persistence Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Show, for every candidate in today's scan, how long its *setup* has been live and whether its score is rising or fading.

**Architecture:** A `setups` map is added beside the three row lists on the `cache:options:scan_day` envelope, keyed on a coarse `symbol|type|expiration` key that excludes strikes. Rows carry only a `setup_key` string, stamped Tier-2-side. Tier 1 renders two new Market Scanner columns from pure functions over that map.

**Tech Stack:** Python 3.11, pytest, NiceGUI/Quasar tables, Redis via `shared.bus`.

**Design:** [`2026-09-17-signal-persistence-design.md`](2026-09-17-signal-persistence-design.md)

**Land this BEFORE the Symbol Dossier** — the dossier's signal band consumes `score_trend` and `persistence_facts` from Task 7.

---

## Background an implementer needs

`services/options_svc/compute.py:138` `merge_day_signals` is a **pure** function that merges one scan's signals into the day's union. It runs on the live scan path, called from `handlers.rescan` (`handlers.py:641`) inside a `try` whose `except` **leaves the previous envelope untouched**. That is why Task 5 puts the new code in its own inner `try`: a crash here would not disable persistence, it would freeze the entire Scanner page at the last good scan.

Three facts that drive the whole design:

1. On reappearance the merge takes the **fresh** row (`fresh = _day_entry(cur_by_id[sid])`) and discards the carried one. Anything attached to a row is destroyed once per scan.
2. `_cap_day_list` (compute.py:110) evicts **oldest-stale-first**, i.e. exactly the long-lived-then-dropped rows this feature exists to show.
3. The signal `id` encodes the strikes (`scanner_engine.py:1208`), so strike drift mints a new id for the same economic setup.

Run tests with the checkout's own venv. From the repo root:

```bash
.venv/bin/python -m pytest services/options_svc -q
```

```bash
cd webgui && ../.venv/bin/python -m pytest -q
```

⚠ **A git worktree has no venv of its own**, so those relative paths fail there —
use the parent checkout's absolute path. On the Windows checkout that is
`"D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe"`; on the VPS it is
`/home/administrator/dev/.venv/bin/python`, **which is prod's** (see the
Environments section of `CLAUDE.md`).

**Measured baseline before this work, `services/options_svc`: 2441 passed, 0
failed.** ⚠ `CLAUDE.md`'s Tests section records 1216 for this suite — that entry
is stale by roughly a factor of two. Collection was checked (2452 collected once,
not double-counted), so the gap is genuine suite growth. Compare the failing
**set**, never the count.

---

## Task 1: `setup_key` — the coarse persistence identity

**Files:**
- Modify: `services/options_svc/compute.py` (new function after `_day_entry`, ~line 108)
- Test: `services/options_svc/tests/test_day_setups.py` (create)

**Step 1: Write the failing tests**

```python
"""Persistence identity + map for the day's scan union."""
import pytest

from services.options_svc import compute


def test_setup_key_excludes_strikes():
    a = {"symbol": "MU", "type": "PCS", "expiration": "2026-10-17",
         "short_strike": 180, "long_strike": 175}
    b = dict(a, short_strike=185, long_strike=180)
    assert compute.setup_key(a) == compute.setup_key(b) == "MU|PCS|2026-10-17"


def test_setup_key_separates_expirations_and_structures():
    base = {"symbol": "MU", "type": "PCS", "expiration": "2026-10-17"}
    assert compute.setup_key(base) != compute.setup_key(dict(base, type="CCS"))
    assert compute.setup_key(base) != compute.setup_key(
        dict(base, expiration="2026-10-24"))


def test_setup_key_normalises_case_and_timestamped_expiry():
    assert compute.setup_key(
        {"symbol": "mu", "type": "pcs",
         "expiration": "2026-10-17T00:00:00"}) == "MU|PCS|2026-10-17"


def test_setup_key_uses_the_front_leg_expiry_for_directional():
    row = {"symbol": "NVDA", "type": "LONG_CALL",
           "legs": [{"expiry": "2026-11-21"}, {"expiry": "2026-10-17"}]}
    assert compute.setup_key(row) == "NVDA|LONG_CALL|2026-10-17"


@pytest.mark.parametrize("row", [
    None, "MU", {}, {"symbol": "MU", "type": "PCS"},
    {"symbol": "", "type": "PCS", "expiration": "2026-10-17"},
    {"symbol": "MU", "type": "PCS", "expiration": ""},
    {"symbol": "MU", "type": "PCS", "legs": [{"expiry": ""}]},
])
def test_setup_key_is_none_when_any_component_is_missing(row):
    # A row with no derivable key has NO persistence. It must never be folded
    # into another setup's group, which a "" or partial key would do.
    assert compute.setup_key(row) is None
```

⚠ The landed file carries **12 further cases** the review added — unparseable and
padded expirations, the producer's own `expiration` leg spelling, top-level-wins
precedence, and the non-dict contract. `services/options_svc/tests/test_day_setups.py`
as committed is the source of truth; the block above is only how it started.

**Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest services/options_svc/tests/test_day_setups.py -q`
Expected: FAIL — `AttributeError: module 'compute' has no attribute 'setup_key'`

**Step 3: Implement**

> ⚠ **Corrected 2026-09-17 after review, landed as `ffb8510`.** The first draft of
> this block sliced the raw value (`str(raw)[:10]`) and validated nothing, so a
> NaN, an epoch-ms int or a bool minted a confident-looking key
> (`MU|PCS|nan`) and two whitespace-padded expirations collapsed onto one —
> re-making the very `_sig_key` bug the docstring warns about. Its docstring was
> also **factually wrong**: `strategy_scanner._assemble` emits the top-level
> `expiration` *and* the legs, with the former already `min()`-normalised over
> the option legs (`_front_expiration`), so the legs branch is a fallback, not
> the live directional path. And real legs spell it **`expiration`**
> (`strategy_scanner.py:436` `_leg_from`), not `expiry` — the original test
> exercised the one spelling no producer emits.

Insert into `services/options_svc/compute.py` immediately after `_day_entry`
(⚠ `_cap_day_list` sits between `_day_entry` and `merge_day_signals`, so the new
pair lands before it — both are standalone, so order does not matter):

```python
def _iso_date(raw):
    """``YYYY-MM-DD`` when ``raw`` really is a date, else ``''``."""
    text = str(raw).strip()[:10]
    try:
        _dt.date.fromisoformat(text)
    except (ValueError, TypeError):
        return ""
    return text


def _setup_expiry(signal):
    """The signal's FRONT expiration as ``YYYY-MM-DD``, or ``''``.

    The top-level ``expiration`` is authoritative: ``strategy_scanner._assemble``
    already emits it as ``min()`` over the option legs, so for a real row the two
    sources cannot disagree.

    ⚠ The legs branch is a DECLARED FALLBACK, not the live directional path. It
    serves leg-set structures (covered call, collar, calendars) that today never
    enter the three day lists. Both spellings are accepted because the producer
    writes ``expiration`` and the Tier-1 normalized leg dict writes ``expiry``.
    """
    if not isinstance(signal, dict):
        return ""
    top = _iso_date(signal.get("expiration"))
    if top:
        return top
    legs = signal.get("legs")
    if isinstance(legs, list):
        found = {_iso_date(leg.get("expiry") or leg.get("expiration"))
                 for leg in legs if isinstance(leg, dict)} - {""}
        if found:
            return min(found)
    return ""


def setup_key(signal):
    """Coarse persistence identity — ``SYMBOL|TYPE|EXPIRATION``, STRIKES EXCLUDED.

    The engine's ``id`` encodes the strikes (``scanner_engine.py:1208``), and
    strike selection is delta-band driven, so one increment of spot mints a
    brand-new id for what is economically the same setup — ~30% of rows churn per
    scan. Age keyed on ``id`` would report a rock-steady setup as a stream of
    one-scan newcomers, the exact inverse of the question being asked.

    ⚠ This is a persistence LOOKUP, never a row key. Row identity stays ``id``;
    ``webgui/pages/options/scanner.py`` ``_sig_key`` documents the bug from the
    opposite mistake — a coarse key collapsing genuinely distinct signals. Two
    adjacent strikes on one expiry are two rows that SHARE one age.

    ``None`` when any component is missing: such a row has no persistence and is
    never folded into another setup's group.
    """
    if not isinstance(signal, dict):
        return None
    symbol = str(signal.get("symbol") or "").strip().upper()
    structure = str(signal.get("type") or "").strip().upper()
    expiry = _setup_expiry(signal)
    if not symbol or not structure or not expiry:
        return None
    return f"{symbol}|{structure}|{expiry}"
```

**Step 4: Run to verify they pass**

Run: `.venv/bin/python -m pytest services/options_svc/tests/test_day_setups.py -q`
Expected: PASS — **23 cases** (4 named + 7 parametrized, plus the 12 validation
cases the review added; the original "7 tests" here miscounted the parametrization).

**Step 5: Commit**

```bash
git add services/options_svc/compute.py services/options_svc/tests/test_day_setups.py
git commit -m "feat(options_svc): coarse setup_key for signal persistence"
```

---

## Task 2: `merge_setups` — the day's persistence map

**Files:**
- Modify: `services/options_svc/compute.py` (after `setup_key`)
- Test: `services/options_svc/tests/test_day_setups.py`

**Step 1: Write the failing tests**

Append to `test_day_setups.py`:

```python
def test_a_newcomer_is_stamped_and_counted():
    out = compute.merge_setups({}, {"MU|PCS|2026-10-17": 62.0},
                               "2026-09-17T09:15:00", seq=1,
                               trustworthy_baseline=True)
    entry = out["MU|PCS|2026-10-17"]
    assert entry["first_seen"] == "2026-09-17T09:15:00"
    assert entry["seen"] == 1
    assert entry["scores"] == [62.0]
    assert entry["gaps"] == 0
    assert "age_unknown" not in entry


def test_a_setup_present_in_every_scan_counts_every_scan():
    setups = {}
    for seq in range(1, 6):
        setups = compute.merge_setups(
            setups, {"MU|PCS|2026-10-17": 60.0 + seq},
            f"2026-09-17T09:{seq:02d}:00", seq=seq, trustworthy_baseline=True)
    entry = setups["MU|PCS|2026-10-17"]
    assert entry["seen"] == 5
    assert entry["scores"] == [61.0, 62.0, 63.0, 64.0, 65.0]
    assert entry["gaps"] == 0
    assert entry["first_seen"] == "2026-09-17T09:01:00"


def test_an_absent_setup_is_carried_untouched():
    first = compute.merge_setups({}, {"MU|PCS|2026-10-17": 62.0},
                                 "2026-09-17T09:15:00", seq=1,
                                 trustworthy_baseline=True)
    second = compute.merge_setups(first, {}, "2026-09-17T09:30:00", seq=2,
                                  trustworthy_baseline=True)
    assert second["MU|PCS|2026-10-17"] == first["MU|PCS|2026-10-17"]


def test_a_gap_is_counted_not_erased():
    key = "MU|PCS|2026-10-17"
    s = compute.merge_setups({}, {key: 62.0}, "t1", seq=1,
                             trustworthy_baseline=True)
    s = compute.merge_setups(s, {}, "t2", seq=2, trustworthy_baseline=True)
    s = compute.merge_setups(s, {key: 61.0}, "t3", seq=3,
                             trustworthy_baseline=True)
    # The row-level `stale_since` is reset to None on return by the existing
    # merge, which is exactly the erasure this map exists to survive.
    assert s[key]["gaps"] == 1
    assert s[key]["seen"] == 2
    assert s[key]["first_seen"] == "t1"


def test_consecutive_scans_are_not_a_gap():
    key = "MU|PCS|2026-10-17"
    s = compute.merge_setups({}, {key: 62.0}, "t1", seq=1,
                             trustworthy_baseline=True)
    s = compute.merge_setups(s, {key: 63.0}, "t2", seq=2,
                             trustworthy_baseline=True)
    assert s[key]["gaps"] == 0


def test_an_untrustworthy_baseline_omits_first_seen():
    # The 2026-07-16 design deleted a first_seen field because it stamped every
    # 09:00 signal `first_seen=12:00` after a noon restart. Never fabricate.
    out = compute.merge_setups({}, {"MU|PCS|2026-10-17": 62.0},
                               "2026-09-17T12:03:00", seq=1,
                               trustworthy_baseline=False)
    entry = out["MU|PCS|2026-10-17"]
    assert "first_seen" not in entry
    assert entry["age_unknown"] is True


def test_a_setup_appearing_after_a_cold_start_gets_a_real_stamp():
    cold = compute.merge_setups({}, {"MU|PCS|2026-10-17": 62.0}, "t1", seq=1,
                                trustworthy_baseline=False)
    # ⚠ Merge 2 passes True, and the first draft of this test passed False —
    # which is UNREACHABLE. Merge 2 has a usable prev (the envelope merge 1 just
    # wrote), so _trustworthy_baseline short-circuits on prev_usable. The flag
    # and a non-empty prev map can never disagree at the call site, which is why
    # merge_setups reads the flag alone rather than also inspecting prev.
    later = compute.merge_setups(cold, {"MU|PCS|2026-10-17": 62.0,
                                        "NVDA|CCS|2026-10-17": 70.0},
                                 "t2", seq=2, trustworthy_baseline=True)
    # Carried from an age_unknown map: MU was never seen from its true
    # beginning, so it never acquires a stamp.
    assert "first_seen" not in later["MU|PCS|2026-10-17"]
    # This one genuinely arrived while we were watching, so its age is known.
    assert later["NVDA|CCS|2026-10-17"]["first_seen"] == "t2"


def test_an_unusable_score_is_not_appended():
    # A None/NaN score must not poison the trend series; the sighting still counts.
    out = compute.merge_setups({}, {"MU|PCS|2026-10-17": None}, "t1", seq=1,
                               trustworthy_baseline=True)
    entry = out["MU|PCS|2026-10-17"]
    assert entry["scores"] == []
    assert entry["seen"] == 1


def test_scores_are_bounded_and_keep_the_TAIL():
    key = "MU|PCS|2026-10-17"
    s = {}
    for seq in range(1, compute._SETUP_SCORES_MAX + 6):
        s = compute.merge_setups(s, {key: float(seq)}, f"t{seq}", seq=seq,
                                 trustworthy_baseline=True)
    scores = s[key]["scores"]
    assert len(scores) == compute._SETUP_SCORES_MAX
    # The tail, because the trend window reads from the END.
    assert scores[-1] == float(compute._SETUP_SCORES_MAX + 5)


def test_merge_setups_never_mutates_its_input():
    prev = {"MU|PCS|2026-10-17": {"seen": 1, "scores": [62.0], "gaps": 0,
                                  "last_seq": 1, "first_seen": "t1"}}
    snapshot = copy.deepcopy(prev)
    compute.merge_setups(prev, {"MU|PCS|2026-10-17": 63.0}, "t2", seq=2,
                         trustworthy_baseline=True)
    assert prev == snapshot
```

Add `import copy` at the top of the test file.

**Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest services/options_svc/tests/test_day_setups.py -q`
Expected: FAIL — no attribute `merge_setups`

**Step 3: Implement**

Append to `compute.py` after `setup_key`:

```python
# Bounded score series per setup. The autoscan fires at most once per 15-minute
# slot over 08:00-15:15 CT == 30 scans/day, so this only truncates when manual
# "Run scan" presses push a setup past it. The TAIL is kept, because the trend
# window reads from the end.
_SETUP_SCORES_MAX = 40

# Backstop on the map itself, mirroring _DAY_MAX_PER_LIST's role for the lists.
_SETUP_MAX = 4000


def merge_setups(prev_setups, live, now_iso, seq, trustworthy_baseline):
    """Merge one scan's live setups into the day's persistence map. PURE.

    ``prev_setups`` -- the previous map (or None/garbage).
    ``live``        -- ``{setup_key: representative score or None}`` for setups
                       with at least one LIVE row in this scan.
    ``seq``         -- this scan's sequence number within the day, so a GAP is
                       detectable (``last_seq < seq - 1``). Wall-clock cannot do
                       this: manual scans break the 15-minute grid.
    ``trustworthy_baseline`` -- whether ``first_seen`` may honestly be stamped
                       for a newcomer (see ``_trustworthy_baseline``).

    The representative score is the setup's BEST this scan, decided by the
    caller: a setup is several adjacent strikes, and the question being asked is
    whether the best thing it offers is improving.

    Never mutates its inputs; never raises.
    """
    out = {}
    if isinstance(prev_setups, dict):
        for key, entry in prev_setups.items():
            if isinstance(entry, dict):
                copied = dict(entry)
                scores = copied.get("scores")
                copied["scores"] = list(scores) if isinstance(scores, list) else []
                out[key] = copied

    for key, score in (live or {}).items():
        entry = out.get(key)
        if entry is None:
            entry = {"seen": 0, "scores": [], "gaps": 0}
            if trustworthy_baseline:
                entry["first_seen"] = now_iso
            else:
                # Omitted, NEVER stamped `now`. The 2026-07-16 design deleted a
                # first_seen field precisely because it lied on cold start.
                entry["age_unknown"] = True
            out[key] = entry
        else:
            last_seq = entry.get("last_seq")
            if isinstance(last_seq, int) and last_seq < seq - 1:
                entry["gaps"] = int(entry.get("gaps") or 0) + 1
        entry["seen"] = int(entry.get("seen") or 0) + 1
        entry["last_seq"] = seq
        entry["last_live"] = now_iso
        value = _finite(score)
        if value is not None:
            entry["scores"] = (entry["scores"] + [value])[-_SETUP_SCORES_MAX:]
    return out
```

⚠ **The finite guard is `_finite` (compute.py:4301), NOT `_num_or_none` (:2932)
or `_num` (:6596).** The plan first named a non-existent `_as_finite`. The two
near neighbours are the wrong ones and look right: both coerce through
`float(value)` inside a `try`, so `float(True)` is `1.0` and a **bool reads as a
score**. That is the repo's documented bug class where a parsing guard is
mistaken for a NaN guard — `_num` in `effort` / `rejection_defense` /
`session_structure` caught `TypeError`/`ValueError` while letting NaN straight
through. Leave a comment at the call site, or the next reader will "simplify" it
to the nearer name.

⚠ A forward reference is fine — `_finite` is defined far below `merge_setups`,
and module globals resolve at call time.

**Step 4: Run to verify they pass**

Run: `.venv/bin/python -m pytest services/options_svc/tests/test_day_setups.py -q`
Expected: PASS

**Step 5: Commit**

```bash
git add services/options_svc/compute.py services/options_svc/tests/test_day_setups.py
git commit -m "feat(options_svc): day persistence map for scan setups"
```

---

## Task 3: `_trustworthy_baseline` — when an age may honestly be claimed

**Files:**
- Modify: `services/options_svc/compute.py`
- Test: `services/options_svc/tests/test_day_setups.py`

A merge whose `prev` was usable can always stamp honestly. A merge with **no**
usable `prev` is ambiguous: it is either the genuine first scan of the day (stamp
is correct) or a flushed/wrong-dated envelope mid-session (stamp would lie). The
discriminator is the clock — `[windows.scan].start` via
`shared.market_calendar.window_bounds("scan")`.

**Step 1: Write the failing tests**

```python
def test_baseline_is_trustworthy_when_prev_was_usable():
    assert compute._trustworthy_baseline(True, "2026-09-17T13:45:00") is True


def test_baseline_is_trustworthy_inside_the_first_scan_slot():
    # [windows.scan].start is 08:00 CT; the first slot runs to 08:15.
    assert compute._trustworthy_baseline(False, "2026-09-17T08:02:00") is True


def test_baseline_is_untrustworthy_after_a_cold_start_mid_session():
    assert compute._trustworthy_baseline(False, "2026-09-17T12:03:00") is False


def test_baseline_degrades_to_untrustworthy_when_the_window_is_unreadable(
        monkeypatch):
    # Unknown -> "we cannot claim an age", never -> "stamp it anyway".
    import shared.market_calendar as mc
    monkeypatch.setattr(mc, "window_bounds",
                        lambda name: (_ for _ in ()).throw(RuntimeError("boom")))
    assert compute._trustworthy_baseline(False, "2026-09-17T08:02:00") is False
```

**Step 2: Run to verify they fail**

Expected: FAIL — no attribute `_trustworthy_baseline`

**Step 3: Implement**

```python
# One autoscan slot. scheduler.autoscan_due buckets on `now.minute // 15`.
_SCAN_SLOT_MIN = 15


def _trustworthy_baseline(prev_usable, now_iso):
    """Whether a NEWCOMER in this merge may honestly be stamped ``first_seen``.

    A usable ``prev`` means we have been watching, so any newcomer genuinely
    arrived now. With no usable ``prev`` we are either at the day's first scan
    (the stamp is exactly right) or recovering from a flushed / wrong-dated
    envelope mid-session (the stamp would lie, which is why the 2026-07-16
    implementation of this field was deleted). The clock separates the two.

    Anything unreadable degrades to False — "we cannot claim an age" is the safe
    direction; the page renders a dash.
    """
    if prev_usable:
        return True
    try:
        from shared import market_calendar as _mc
        start, _end = _mc.window_bounds("scan")
        now = _dt.datetime.fromisoformat(str(now_iso))
        opened = _dt.datetime.combine(now.date(), start)
        elapsed = (now - opened).total_seconds() / 60.0
        return 0 <= elapsed < _SCAN_SLOT_MIN
    except Exception:  # noqa: BLE001
        return False
```

⚠ Check `window_bounds`' actual return shape first —
`grep -n "def window_bounds" -A 12 shared/market_calendar.py`. If it returns
`datetime`s or a dict rather than a `(time, time)` pair, adapt the two lines that
build `opened`; do not adapt the test.

**Step 4: Run to verify they pass**

**Step 5: Commit**

```bash
git add services/options_svc/compute.py services/options_svc/tests/test_day_setups.py
git commit -m "feat(options_svc): only claim a signal age when it can be known"
```

---

## Task 4: capping the map

**Files:**
- Modify: `services/options_svc/compute.py`
- Test: `services/options_svc/tests/test_day_setups.py`

**Step 1: Write the failing tests**

```python
def test_cap_never_evicts_a_setup_a_surviving_row_references():
    # _cap_day_list evicts oldest-stale-first, so the naive order deletes the
    # setup entry of a row that is still on screen.
    setups = {f"S{i}|PCS|2026-10-17": {"seen": 1, "scores": [], "gaps": 0,
                                       "last_live": f"t{i}"}
              for i in range(10)}
    referenced = {"S0|PCS|2026-10-17", "S1|PCS|2026-10-17"}
    kept, dropped = compute._cap_setups(setups, referenced, max_entries=3)
    assert referenced <= set(kept)
    assert dropped == 7


def test_cap_evicts_oldest_last_live_first():
    setups = {"old|PCS|E": {"last_live": "2026-09-17T09:00:00"},
              "new|PCS|E": {"last_live": "2026-09-17T14:00:00"}}
    kept, _ = compute._cap_setups(setups, set(), max_entries=1)
    assert set(kept) == {"new|PCS|E"}


def test_cap_is_a_no_op_under_the_limit():
    setups = {"a|PCS|E": {"last_live": "t"}}
    kept, dropped = compute._cap_setups(setups, set(), max_entries=10)
    assert kept == setups and dropped == 0
```

**Step 2: Run to verify they fail**

**Step 3: Implement**

```python
def _cap_setups(setups, referenced, max_entries=None):
    """Trim the persistence map, evicting UNREFERENCED entries oldest-first.

    ⚠ Runs AFTER ``_cap_day_list``, and never evicts a key a surviving row still
    points at. The row cap evicts oldest-stale-first, so the naive order would
    delete the setup entry of a row that is still on screen — the row would render
    with a blank age for the rest of the day.

    Returns ``(kept, n_dropped)``.
    """
    max_entries = _SETUP_MAX if max_entries is None else max_entries
    over = len(setups) - max_entries
    if over <= 0:
        return setups, 0
    evictable = [k for k in setups if k not in referenced]
    evictable.sort(key=lambda k: str((setups[k] or {}).get("last_live") or ""))
    doomed = set(evictable[:over])
    if len(setups) - len(doomed) > max_entries:
        log.warning("day setups: %d referenced entries exceed the %d cap — "
                    "keeping them all", len(setups) - len(doomed), max_entries)
    kept = {k: v for k, v in setups.items() if k not in doomed}
    if doomed:
        log.warning("day setups: evicted %d oldest unreferenced setup(s) at the "
                    "%d cap", len(doomed), max_entries)
    return kept, len(doomed)
```

**Step 4: Run to verify they pass**

**Step 5: Commit**

```bash
git add services/options_svc/compute.py services/options_svc/tests/test_day_setups.py
git commit -m "feat(options_svc): cap the setups map without orphaning live rows"
```

---

## Task 5: wire persistence into `merge_day_signals`

This is the only task that edits code on the live scan path. Read the whole of
`merge_day_signals` (compute.py:138-218) before starting.

**Files:**
- Modify: `services/options_svc/compute.py:138-218`
- Test: `services/options_svc/tests/test_day_setups.py`

**Step 1: Write the failing tests**

```python
def _row(symbol, stype, expiry, short, long_, score):
    """A row shaped like scanner_engine's, including the strike-encoded id."""
    return {"id": f"{symbol}_{stype}_{expiry}_{short}_{long_}",
            "symbol": symbol, "type": stype, "expiration": expiry,
            "short_strike": short, "long_strike": long_,
            "composite_score": score}


def test_rows_are_stamped_with_their_setup_key():
    scan = {"signals_0dte": [_row("MU", "PCS", "2026-10-17", 180, 175, 62.0)]}
    day = compute.merge_day_signals(None, scan, "2026-09-17",
                                    now_iso="2026-09-17T08:02:00")
    assert day["signals_0dte"][0]["setup_key"] == "MU|PCS|2026-10-17"


def test_strike_drift_does_not_reset_the_setup_age():
    # THE central case. A delta-band strike step mints a new id, so an id-keyed
    # age would report this steady setup as a brand-new one-scan signal.
    key = "MU|PCS|2026-10-17"
    day = compute.merge_day_signals(
        None, {"signals_0dte": [_row("MU", "PCS", "2026-10-17", 180, 175, 62.0)]},
        "2026-09-17", now_iso="2026-09-17T08:02:00")
    first_seen = day["setups"][key]["first_seen"]
    for i, strike in enumerate((181, 182, 183), start=1):
        day = compute.merge_day_signals(
            day,
            {"signals_0dte": [_row("MU", "PCS", "2026-10-17", strike,
                                   strike - 5, 62.0 + i)]},
            "2026-09-17", now_iso=f"2026-09-17T09:{i:02d}:00")
    entry = day["setups"][key]
    assert entry["first_seen"] == first_seen
    assert entry["seen"] == 4
    assert entry["scores"] == [62.0, 63.0, 64.0, 65.0]
    # Four distinct rows, one shared age — the whole point of the coarse key.
    assert len({r["id"] for r in day["signals_0dte"]}) == 4


def test_the_representative_score_is_the_best_of_the_scan():
    scan = {"signals_0dte": [
        _row("MU", "PCS", "2026-10-17", 180, 175, 55.0),
        _row("MU", "PCS", "2026-10-17", 185, 180, 71.0)]}
    day = compute.merge_day_signals(None, scan, "2026-09-17",
                                    now_iso="2026-09-17T08:02:00")
    assert day["setups"]["MU|PCS|2026-10-17"]["scores"] == [71.0]


def test_a_date_change_resets_the_setups_map():
    day = compute.merge_day_signals(
        None, {"signals_0dte": [_row("MU", "PCS", "2026-10-17", 180, 175, 62.0)]},
        "2026-09-17", now_iso="2026-09-17T08:02:00")
    fresh = compute.merge_day_signals(day, {"signals_0dte": []}, "2026-09-18",
                                      now_iso="2026-09-18T08:02:00")
    assert fresh["setups"] == {}
    assert fresh["scan_seq"] == 1


def test_scan_seq_increments_within_a_day():
    day = compute.merge_day_signals(None, {}, "2026-09-17",
                                    now_iso="2026-09-17T08:02:00")
    assert day["scan_seq"] == 1
    day = compute.merge_day_signals(day, {}, "2026-09-17",
                                    now_iso="2026-09-17T08:17:00")
    assert day["scan_seq"] == 2


def test_a_broken_setups_block_does_not_break_the_row_merge(monkeypatch):
    # The caller's except leaves the PREVIOUS envelope untouched, so an
    # unguarded crash here would freeze the whole Scanner page, not just ages.
    monkeypatch.setattr(compute, "merge_setups",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    scan = {"signals_0dte": [_row("MU", "PCS", "2026-10-17", 180, 175, 62.0)]}
    day = compute.merge_day_signals(None, scan, "2026-09-17",
                                    now_iso="2026-09-17T08:02:00")
    assert len(day["signals_0dte"]) == 1
    assert day["signals_0dte"][0]["live"] is True
    assert day["setups"] == {}


def test_every_surviving_row_key_is_present_in_the_map():
    scan = {"signals_0dte": [_row("MU", "PCS", "2026-10-17", 180, 175, 62.0),
                             _row("NVDA", "CCS", "2026-10-24", 190, 195, 70.0)]}
    day = compute.merge_day_signals(None, scan, "2026-09-17",
                                    now_iso="2026-09-17T08:02:00")
    keys = {r["setup_key"] for r in day["signals_0dte"] if r.get("setup_key")}
    assert keys <= set(day["setups"])
```

**Step 2: Run to verify they fail**

**Step 3: Implement**

Inside `merge_day_signals`, make four edits.

(a) Capture whether `prev` was usable, **before** the reset — and seed the sequence:

```python
    prev_usable = isinstance(prev, dict) and prev.get("date") == today
    if not prev_usable:
        prev = {}
    seq = int(prev.get("scan_seq") or 0) + 1
    out = {"date": today, "scan_seq": seq}
```

(b) In the per-list loop, stamp `setup_key` on every entry and collect the live
representatives. Add before the loop:

```python
    live_best = {}
    referenced = set()
```

and immediately after each of the three `merged.append(...)` sites, stamp the
entry that was just appended. The cleanest form is one helper applied to the whole
merged list just before the cap:

```python
        for entry in merged:
            key = setup_key(entry)
            entry["setup_key"] = key
            if not key:
                continue
            if entry.get("live"):
                score = _finite(entry.get("composite_score"))   # NOT _num: bool
                best = live_best.get(key)
                if score is not None and (best is None or score > best):
                    live_best[key] = score
                live_best.setdefault(key, None)
        out[key_list], dropped = _cap_day_list(merged, key_list, max_per_list)
```

⚠ The existing loop variable is named `key` and now collides with the setup key.
**Rename the loop variable to `key_list`** at its `for key in _DAY_LISTS:` header
and at its three other uses (`current.get(key)`, `prev.get(key)`,
`_cap_day_list(merged, key, ...)`, `out[key]`, `truncated[key]`). Do this rename
first, as its own edit, and run the existing suite before adding anything.

After the cap, record what survived:

```python
        referenced.update(e.get("setup_key") for e in out[key_list]
                          if e.get("setup_key"))
```

(c) After the list loop, build the map in its own guard:

```python
    # Own guard: the CALLER's except leaves the previous envelope untouched, so an
    # unguarded failure here would not degrade persistence — it would freeze the
    # whole day union and the Scanner page with it.
    try:
        setups = merge_setups(prev.get("setups"), live_best, now_iso, seq,
                              _trustworthy_baseline(prev_usable, now_iso))
        setups, _evicted = _cap_setups(setups, referenced)
    except Exception:  # noqa: BLE001
        _degrade.degraded("options.merge_setups")
        setups = {}
    out["setups"] = setups
```

⚠ `compute.py` may not already import `_degrade`. Check
`grep -n "_degrade" services/options_svc/compute.py`; if absent, either add
`from . import _degrade` alongside the module's existing sibling imports or use
`log.exception("day setups merge failed (non-fatal)")`. Do not leave it silent —
`services/tests/test_no_silent_degrades.py` requires a guarded body of ≥15 lines
to speak, and this one is on the borderline.

(d) Update the docstring: add `setups` and `scan_seq` to the described envelope,
and state that `setup_key` is stamped on every row.

**Step 4: Run to verify they pass, and that nothing regressed**

Run: `.venv/bin/python -m pytest services/options_svc -q`
Expected: PASS. Compare the failing **set** against the pre-change run, not the count.

**Step 5: Commit**

```bash
git add services/options_svc/compute.py services/options_svc/tests/test_day_setups.py
git commit -m "feat(options_svc): carry setup persistence on the day scan union"
```

---

## Task 6: measure the real payload cost on prod

**This is a measurement task, not a code task. Do not skip it.** The design's
"+200 KB on a 4.5 MB key" is an ESTIMATE inferred from a churn figure. This repo
shipped a comment claiming `cache:options:gamma` was "well under ~1 MB" while it
measured 4.99 MB.

**Step 1: Read the real envelope and group it**

```bash
ssh vps2 '/home/administrator/dev/.venv/bin/python - <<PY
import json, sys
sys.path.insert(0, "/home/administrator/dev")
from shared.bus import Bus
env = Bus().cache_get("cache:options:scan_day").payload
rows = sum(len(env.get(k) or []) for k in
           ("signals_0dte","signals_swing","signals_directional"))
keys = set()
for k in ("signals_0dte","signals_swing","signals_directional"):
    for r in env.get(k) or []:
        exp = str(r.get("expiration") or "")[:10]
        keys.add((r.get("symbol"), r.get("type"), exp))
print("rows", rows, "setups", len(keys),
      "bytes", len(json.dumps(env)))
PY'
```

**Step 2: Decide**

Estimated map size is `setups x (len(key) + ~40 x 5 + ~60)` bytes. If that exceeds
**5%** of the current payload, reduce `_SETUP_SCORES_MAX` to 12 (still three hours
of context at the 15-minute cadence) and re-measure before continuing.

**Step 3: Record the measured numbers** in the design doc, replacing the estimate
paragraph, and commit that edit.

```bash
git add docs/plans/2026-09-17-signal-persistence-design.md
git commit -m "docs(plans): record the measured setups-map payload cost"
```

---

## Task 7: Tier-1 pure functions — `score_trend` and `persistence_facts`

**Files:**
- Create: `webgui/pages/options/persistence.py`
- Test: `webgui/tests/test_persistence.py` (create)

**Step 1: Write the failing tests**

```python
"""Signal age + score-trend display vocabulary (Tier 1, pure)."""
import pytest

from pages.options import persistence


@pytest.mark.parametrize("scores", [[], [60.0], [60.0, 61.0], [60.0, 61.0, 62.0]])
def test_no_direction_is_claimed_under_a_full_window(scores):
    # Same discipline as commit_direction: never name a direction two
    # independent reads do not back.
    assert persistence.score_trend(scores) == "new"


def test_a_rise_past_the_deadband_is_rising():
    assert persistence.score_trend([60.0, 61.0, 62.0, 66.0]) == "rising"


def test_a_fall_past_the_deadband_is_fading():
    assert persistence.score_trend([70.0, 68.0, 66.0, 63.0]) == "fading"


def test_a_wobble_inside_the_deadband_is_steady():
    # The composite is recomputed from scratch every scan; +-1 is noise.
    assert persistence.score_trend([60.0, 61.0, 59.0, 61.5]) == "steady"


def test_the_window_reads_from_the_END_not_the_start():
    # A setup that crashed this morning and has been climbing for an hour is
    # RISING. Comparing against scores[0] would call it fading all day.
    assert persistence.score_trend([90.0, 50.0, 52.0, 55.0, 59.0]) == "rising"


def test_persistence_facts_for_a_steady_setup():
    facts = persistence.persistence_facts(
        {"first_seen": "2026-09-17T09:15:00", "seen": 14, "gaps": 0,
         "scores": [60.0, 61.0, 62.0, 66.0]})
    assert facts["since"] == "09:15 · 14x"
    assert facts["trend"] == "rising"
    assert facts["trend_text"] == "▲ +6.0"


def test_persistence_facts_for_an_unknown_age():
    facts = persistence.persistence_facts(
        {"age_unknown": True, "seen": 3, "gaps": 0, "scores": []})
    assert facts["since"] == "—"
    assert facts["trend"] == "new"
    assert facts["trend_text"] == "new"


def test_persistence_facts_for_a_missing_setup():
    # A row with no derivable setup_key, or a map that never got built.
    facts = persistence.persistence_facts(None)
    assert facts["since"] == "—"
    assert facts["trend_text"] == "—"


def test_gaps_are_reported():
    facts = persistence.persistence_facts(
        {"first_seen": "2026-09-17T09:15:00", "seen": 9, "gaps": 2,
         "scores": []})
    assert facts["gaps"] == 2
    assert "2 gaps" in facts["detail"]
```

**Step 2: Run to verify they fail**

Run: `cd webgui && ../.venv/bin/python -m pytest tests/test_persistence.py -q`
Expected: FAIL — no module `pages.options.persistence`

**Step 3: Implement**

Create `webgui/pages/options/persistence.py`:

```python
"""Signal age + score-trend vocabulary over the day union's ``setups`` map.

PURE — no widgets, no bus. Both the Market Scanner and the Symbol Dossier render
from these, so the two cannot describe the same setup differently.

The map is built Tier-2-side by ``options_svc.compute.merge_setups``; this module
never derives a ``setup_key`` (rows carry one), so there is no cross-tier mirror.
"""
from .. import fmt as _fmt

# One hour at the 15-minute autoscan cadence. Under this, no direction is named.
TREND_WINDOW = 4

# The composite is recomputed from scratch every scan, so a small move between
# scans is noise rather than a fade.
TREND_DEADBAND = 2.0

DASH = "—"


def score_trend(scores, window=TREND_WINDOW, deadband=TREND_DEADBAND):
    """``"new" | "rising" | "steady" | "fading"`` for a setup's score series.

    Fewer than ``window`` readings -> ``"new"``, claiming NO direction. The
    comparison reads from the END of the series, so a setup that collapsed this
    morning and has climbed for an hour reads as rising rather than fading.
    """
    values = [v for v in (scores or []) if _fmt.num(v) is not None]
    if len(values) < window:
        return "new"
    delta = values[-1] - values[-window]
    if abs(delta) <= deadband:
        return "steady"
    return "rising" if delta > 0 else "fading"


def score_delta(scores, window=TREND_WINDOW):
    """The signed move over the trend window, or ``None`` when undefined."""
    values = [v for v in (scores or []) if _fmt.num(v) is not None]
    if len(values) < window:
        return None
    return values[-1] - values[-window]


_MARKS = {"rising": "▲", "fading": "▼", "steady": "▬"}


def persistence_facts(setup):
    """Display facts for one setup entry. Total — an absent entry is a dash.

    ⚠ Three different absences must not render alike: no entry at all (the row
    has no derivable setup_key, or the map failed to build), a known-present
    setup whose age cannot be claimed (``age_unknown`` — a cold start), and a
    setup with too few readings to name a direction.
    """
    if not isinstance(setup, dict):
        return {"since": DASH, "trend": "new", "trend_text": DASH,
                "gaps": 0, "detail": ""}

    seen = int(_fmt.float_or(setup.get("seen"), 0))
    first = setup.get("first_seen")
    if setup.get("age_unknown") or not first:
        since = DASH
    else:
        since = f"{str(first)[11:16]} · {seen}x"

    trend = score_trend(setup.get("scores"))
    delta = score_delta(setup.get("scores"))
    if trend == "new":
        trend_text = "new"
    else:
        trend_text = f"{_MARKS[trend]} {delta:+.1f}"

    gaps = int(_fmt.float_or(setup.get("gaps"), 0))
    detail = ""
    if since != DASH:
        detail = f"Live since {str(first)[11:16]}"
        if gaps:
            detail += f" · {gaps} gap" + ("s" if gaps != 1 else "")
    return {"since": since, "trend": trend, "trend_text": trend_text,
            "gaps": gaps, "detail": detail}
```

⚠ Confirm `webgui/pages/fmt.py` exports `num` and `float_or` with these
semantics (`num` is strict and rejects NaN **and** bool; `float_or` coerces with a
fallback). It does as of this writing — do not re-implement either.

**Step 4: Run to verify they pass**

**Step 5: Commit**

```bash
git add webgui/pages/options/persistence.py webgui/tests/test_persistence.py
git commit -m "feat(webgui): pure signal age + score-trend vocabulary"
```

---

## Task 8: `stamp_persistence` on the Scanner's display rows

**Files:**
- Modify: `webgui/pages/options/scanner.py` (new function beside `stamp_stale`, ~line 421)
- Test: `webgui/tests/test_scanner.py` (append; confirm the filename with `ls webgui/tests | grep scanner`)

**Step 1: Write the failing tests**

```python
def test_stamp_persistence_joins_rows_to_setups_through_the_signal():
    rows = [{"id": "MU_PCS_2026-10-17_180_175"}]
    signals = [{"id": "MU_PCS_2026-10-17_180_175",
                "setup_key": "MU|PCS|2026-10-17"}]
    setups = {"MU|PCS|2026-10-17": {"first_seen": "2026-09-17T09:15:00",
                                    "seen": 14, "gaps": 0,
                                    "scores": [60.0, 61.0, 62.0, 66.0]}}
    scanner.stamp_persistence(rows, signals, setups)
    assert rows[0]["seen_since"] == "09:15 · 14x"
    assert rows[0]["score_trend"] == "▲ +6.0"


def test_stamp_persistence_dashes_a_row_with_no_setup_key():
    rows = [{"id": "X"}]
    scanner.stamp_persistence(rows, [{"id": "X"}], {})
    assert rows[0]["seen_since"] == "—"
    assert rows[0]["score_trend"] == "—"


def test_stamp_persistence_dashes_every_row_when_the_map_is_absent():
    # A pre-change envelope, or a scan whose setups block degraded. Must read as
    # "no reading", never as "brand new".
    rows = [{"id": "X"}]
    scanner.stamp_persistence(rows, [{"id": "X",
                                      "setup_key": "MU|PCS|2026-10-17"}], None)
    assert rows[0]["seen_since"] == "—"
```

**Step 2: Run to verify they fail**

**Step 3: Implement**

Add to `scanner.py` directly after `stamp_stale`:

```python
def stamp_persistence(rows, signals, setups):
    """Stamp ``seen_since`` / ``score_trend`` onto display rows.

    Joined by ``id`` for the same reason ``stamp_stale`` is: the row builders
    re-sort by score, so row order does not track signal order. One stamper for
    all three tabs.

    The row reaches its setup through the SIGNAL's ``setup_key`` — Tier 2 stamps
    that field, and Tier 1 never derives it, so there is no coarse-key logic here
    to drift from the service's.

    ⚠ An absent map dashes every row. A pre-change envelope and a scan whose
    setups block degraded both look like this, and both mean "no reading" — never
    "brand new", which is a claim about the signal rather than about the data.
    """
    setups = setups if isinstance(setups, dict) else {}
    by_id = {s.get("id"): s for s in (signals or []) if s.get("id")}
    for r in rows:
        signal = by_id.get(r.get("id")) or {}
        entry = setups.get(signal.get("setup_key"))
        facts = _persistence.persistence_facts(entry)
        r["seen_since"] = facts["since"]
        r["score_trend"] = facts["trend_text"]
        r["_trend_state"] = facts["trend"]
```

Add `from . import persistence as _persistence` to the module's imports.

**Step 4: Run to verify they pass**

**Step 5: Commit**

```bash
git add webgui/pages/options/scanner.py webgui/tests/test_scanner.py
git commit -m "feat(webgui): stamp signal age and score trend onto scanner rows"
```

---

## Task 9: the two Market Scanner columns

**Files:**
- Modify: `webgui/pages/options/scanner.py` — `signal_columns` (~line 134) and `directional_columns` (~line 174)
- Test: `webgui/tests/test_scanner.py`

**Step 1: Write the failing tests**

```python
def test_both_signal_tabs_carry_the_persistence_columns():
    for cols in (scanner.signal_columns(), scanner.directional_columns()):
        names = [c["name"] for c in cols]
        assert "seen_since" in names and "score_trend" in names
        # Beside the drop marker, so the lifecycle reads as one cluster, and
        # before the actions column.
        assert names.index("seen_since") < names.index("stale_since")
        assert names.index("stale_since") < names.index("actions")


def test_the_persistence_column_labels_name_what_the_value_is():
    labels = {c["name"]: c["label"] for c in scanner.signal_columns()}
    assert labels["seen_since"] == "Seen since"
    # NOT "Score" — that column already exists and holds the composite.
    assert labels["score_trend"] == "Score trend"
```

**Step 2: Run to verify they fail**

**Step 3: Implement**

Add beside `_DROPPED_COL`:

```python
# How long the SETUP has been live today, and which way its score is going.
# "Seen since" carries a time + a count, so it is not the bare "Age" a reader
# would take for a DTE. "Score trend" and not "Score": the composite already
# owns that word, and these are different quantities.
_SEEN_COL = ("seen_since", "Seen since")
_TREND_COL = ("score_trend", "Score trend")
```

In `signal_columns`, insert into `spec` immediately before `_DROPPED_COL`:

```python
        _SEEN_COL,
        _TREND_COL,
        _DROPPED_COL,
```

In `directional_columns`, change the tail to:

```python
    return ([_col("symbol", "Symbol")] + body
            + [_checks_col(), _col(*_SEEN_COL), _col(*_TREND_COL),
               _col(*_DROPPED_COL), _actions_col()])
```

**Step 4: Run to verify they pass**

**Step 5: Commit**

```bash
git add webgui/pages/options/scanner.py webgui/tests/test_scanner.py
git commit -m "feat(webgui): Seen since and Score trend columns on the Scanner"
```

---

## Task 10: call `stamp_persistence` from the page

**Files:**
- Modify: `webgui/pages/options/scanner.py` — the row-building path that already calls `stamp_stale`

**Step 1: Locate the call sites**

```bash
grep -n "stamp_stale" webgui/pages/options/scanner.py
```

**Step 2: Add the sibling call**

At every `stamp_stale(rows, signals)` site, add immediately after:

```python
        stamp_persistence(rows, signals, (day_env or {}).get("setups"))
```

⚠ The envelope must be the **day** envelope, and the existing code already gates it
through `day_signals`/`day_is_today`. Read `setups` from the same envelope variable
those calls use — never from `cache:options:scan`, which has no map.

**Step 3: Add a colour class for the trend cell**

The trend mark is a data-driven colour, so it maps from a **finite** state to a
static class (never a runtime `text-[#hex]`):

```python
_TREND_CLASSES = {"rising": "text-emerald-400", "fading": "text-rose-400",
                  "steady": "text-slate-400", "new": "text-slate-500"}
```

Apply it the way the existing `_score_class` is applied — find it with
`grep -n "_score_class" webgui/pages/options/scanner.py` and mirror that wiring
exactly.

**Step 4: Run the full webgui suite**

Run: `cd webgui && ../.venv/bin/python -m pytest -q`
Expected: PASS. Compare the failing **set** to the pre-change baseline.

**Step 5: Commit**

```bash
git add webgui/pages/options/scanner.py
git commit -m "feat(webgui): render signal age and score trend on the Scanner"
```

---

## Task 11: verify it live

**Step 1: Run every affected suite**

```bash
.venv/bin/python -m pytest services/options_svc -q
```

```bash
cd webgui && ../.venv/bin/python -m pytest -q
```

```bash
.venv/bin/python -m pytest shared/tests tools/tests -q
```

**Step 2: Exercise the merge through a real scan, on the local harness**

Drive `merge_day_signals` over two consecutive real scan payloads (capture them
from prod with `Bus().cache_get("cache:options:scan")` fifteen minutes apart) and
confirm `setups` grows rather than resets.

**Step 3: Promote, then watch two real scans**

⚠ Push to `main` first — `promote.sh` does `git pull --ff-only origin main` and
will otherwise quietly promote the old commit.

```bash
ssh vps2 'cd /home/administrator/dev && tools/promote.sh'
```

Nothing goes after it; it already regenerates units and reloads the daemon.
Promote between **15:25 and 16:15 CT** — it stops the whole target, so the public
stream drops and GEX collection loses slots.

**Step 4: Confirm on the running stack**

```bash
ssh vps2 '/home/administrator/dev/.venv/bin/python -c "
import sys; sys.path.insert(0, \"/home/administrator/dev\")
from shared.bus import Bus
env = Bus().cache_get(\"cache:options:scan_day\").payload
print(\"seq\", env.get(\"scan_seq\"), \"setups\", len(env.get(\"setups\") or {}))
"'
```

Across two consecutive scans `scan_seq` must increment and `seen` must rise on a
setup that persisted. **Only then** are the columns trustworthy — the honest
failure here is a map that silently resets every scan, which renders as every
signal being permanently new.

**Step 5: Update the documentation**

Add the two columns to `webgui/page_help.py`'s Market Scanner entry and to the
Reference Guide's Market Scanner section. That file is the most-read prose in the
app and rots first because nothing fails when it goes stale.

```bash
git add webgui/page_help.py docs/manuals/
git commit -m "docs: signal age and score trend on the Market Scanner"
```

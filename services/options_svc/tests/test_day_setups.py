"""Persistence identity + map for the day's scan union."""
import copy
import datetime as _dt
from zoneinfo import ZoneInfo

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


@pytest.mark.parametrize("bad", [
    float("nan"), 1792713600000, True, "soon", "10/17/2026", "", None,
])
def test_an_unparseable_expiration_yields_no_key_never_a_fabricated_one(bad):
    # The slice this replaced minted "MU|PCS|nan" / "MU|PCS|1792713600".
    assert compute.setup_key(
        {"symbol": "MU", "type": "PCS", "expiration": bad}) is None


def test_padded_expirations_do_not_collide():
    # " 2026-10-17 "[:10] and " 2026-10-19 "[:10] both gave " 2026-10-1".
    a = compute.setup_key({"symbol": "MU", "type": "PCS",
                           "expiration": " 2026-10-17 "})
    b = compute.setup_key({"symbol": "MU", "type": "PCS",
                           "expiration": " 2026-10-19 "})
    assert a == "MU|PCS|2026-10-17"
    assert b == "MU|PCS|2026-10-19"


def test_the_leg_fallback_accepts_the_producers_own_spelling():
    # strategy_scanner._leg_from writes "expiration"; the normalized Tier-1 leg
    # dict writes "expiry". Both must resolve.
    row = {"symbol": "NVDA", "type": "LONG_CALL",
           "legs": [{"expiration": "2026-11-21"}, {"expiration": "2026-10-17"}]}
    assert compute.setup_key(row) == "NVDA|LONG_CALL|2026-10-17"


def test_the_top_level_expiration_wins_and_already_is_the_front_leg():
    # _assemble emits BOTH, with expiration already == min(leg expirations), so
    # the two cannot disagree for a real row. Pinning the precedence anyway.
    row = {"symbol": "NVDA", "type": "LONG_CALL", "expiration": "2026-10-17",
           "legs": [{"expiration": "2026-11-21"}]}     # legs DISAGREE — no real
    # producer emits this; the row exists only to pin the documented branch order.
    assert compute.setup_key(row) == "NVDA|LONG_CALL|2026-10-17"


def test_an_unparseable_top_level_falls_through_to_the_legs():
    row = {"symbol": "NVDA", "type": "LONG_CALL", "expiration": "soon",
           "legs": [{"expiration": "2026-10-17"}]}
    assert compute.setup_key(row) == "NVDA|LONG_CALL|2026-10-17"


def test_setup_expiry_honours_its_own_contract_on_a_non_dict():
    assert compute._setup_expiry("MU") == ""
    assert compute._setup_expiry(None) == ""


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
    # Pins the MAP's contract, not the render: persistence_facts also dashes on
    # a missing first_seen, so dropping this flag changes nothing on screen
    # today. It is the explicit record of "we could not know", where the missing
    # stamp is only an inference from an absence.
    assert later["MU|PCS|2026-10-17"]["age_unknown"] is True
    # This one genuinely arrived while we were watching, so its age is known.
    assert later["NVDA|CCS|2026-10-17"]["first_seen"] == "t2"


def test_an_unusable_score_is_not_appended():
    # A None/NaN score must not poison the trend series; the sighting still counts.
    # ⚠ bool is here because it is the ONLY thing separating _finite from its two
    # near neighbours: _num_or_none and _num both coerce through float(), so
    # float(True) is 1.0 and a bool books as a score of 1.0. Without this case,
    # swapping to either neighbour passes the whole suite.
    out = compute.merge_setups(
        {}, {"K_NONE": None, "K_NAN": float("nan"), "K_BOOL": True}, "t1",
        seq=1, trustworthy_baseline=True)
    assert out["K_NONE"]["scores"] == []
    assert out["K_NAN"]["scores"] == []
    assert out["K_BOOL"]["scores"] == []
    assert out["K_NONE"]["seen"] == 1
    assert out["K_BOOL"]["seen"] == 1


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


@pytest.mark.parametrize("junk", ["x", None, float("nan"), [], {"a": 1}])
def test_a_corrupt_prev_entry_degrades_rather_than_taking_the_map_down(junk):
    # prev comes from Redis. Every other field read from it is isinstance-guarded;
    # seen and gaps were not, so one corrupt entry raised and Task 5's guard would
    # have dropped the WHOLE day's persistence map.
    prev = {"K": {"seen": junk, "gaps": junk, "scores": [], "last_seq": 1}}
    out = compute.merge_setups(prev, {"K": 62.0}, "t2", seq=2,
                               trustworthy_baseline=True)
    assert out["K"]["seen"] == 1
    assert isinstance(out["K"]["gaps"], int)


@pytest.mark.parametrize("prev", [
    None, [], "junk", 42,                         # any SHAPE
    {"K": "junk"}, {"K": None}, {"K": []},        # any ENTRY
    {"K": {"scores": "junk", "last_seq": 1}},     # any FIELD: scores
    {"K": {"scores": [], "last_seq": "2"}},       # any FIELD: last_seq
])
def test_a_garbage_prev_is_tolerated_in_every_shape_the_docstring_claims(prev):
    # prev comes straight off Redis. The docstring promises tolerance of any
    # shape, any entry and any field; before this, four of those guards existed
    # with nothing exercising them — the same state seen/gaps were in.
    out = compute.merge_setups(prev, {"K": 62.0}, "t2", seq=2,
                               trustworthy_baseline=True)
    assert out["K"]["seen"] >= 1
    assert isinstance(out["K"]["scores"], list)
    assert out["K"]["scores"] == [62.0]
    # ⚠ Deliberate, and it is the `last_seq: "2"` case that decides it: a
    # non-int last_seq disables gap detection for that key, and the honest
    # answer is to claim NO gap rather than invent one. A gap is a positive
    # statement about the tape ("blinked three times"), so fabricating one from
    # a corrupt field is the same error class as stamping a first_seen we never
    # observed. The blindness is exactly one merge long: last_seq is rewritten
    # to a real int on this very merge, and the next true gap is counted.
    assert out["K"]["gaps"] == 0


# ── The day union's default timestamp basis ─────────────────────────────────
#
# A fixed instant: 13:02:30 UTC == 08:02:30 CT (CDT, UTC-5) on 2026-09-17.
_FROZEN_UTC = _dt.datetime(2026, 9, 17, 13, 2, 30, tzinfo=_dt.timezone.utc)
# ...seen from a host whose wall clock is NOT Central. Pretending the host is
# nine hours ahead is what makes the test machine-independent: it fails on a
# Central box exactly as it fails anywhere else.
_FAKE_HOST_TZ = _dt.timezone(_dt.timedelta(hours=9))


class _FrozenDatetime(_dt.datetime):
    """``datetime`` frozen at ``_FROZEN_UTC``, on a host nine hours ahead."""

    @classmethod
    def now(cls, tz=None):
        if tz is None:
            return _FROZEN_UTC.astimezone(_FAKE_HOST_TZ).replace(tzinfo=None)
        return _FROZEN_UTC.astimezone(tz)


class _FrozenClockModule:
    """Stands in for compute's ``_dt`` alias, swapping ONLY ``datetime``."""

    datetime = _FrozenDatetime

    def __getattr__(self, name):
        return getattr(_dt, name)


def test_the_day_union_stamps_ct_never_the_host_wall_clock(monkeypatch):
    """``merge_day_signals``' default ``now_iso`` must be Central.

    ``_trustworthy_baseline`` compares that stamp against 08:00 CT, and the
    production caller (``handlers.rescan``) passes no ``now_iso`` at all — so on
    a host that is not on America/Chicago the cold-start window is unreachable
    and ``first_seen`` would never be stamped on any day, forever.
    """
    monkeypatch.setattr(compute, "_dt", _FrozenClockModule())
    prev = {"date": "2026-09-17", "signals_0dte": [{"id": "a", "symbol": "MU"}]}

    out = compute.merge_day_signals(prev, {"signals_0dte": []}, "2026-09-17")

    stamp = out["signals_0dte"][0]["stale_since"]
    expected = _FROZEN_UTC.astimezone(ZoneInfo("America/Chicago")).replace(
        tzinfo=None).isoformat(timespec="seconds")
    # Naive, because a tz-naive datetime in this project means Central — and
    # because _trustworthy_baseline subtracts it from a naive window_bounds
    # time, which an aware stamp would make a TypeError.
    assert stamp == expected == "2026-09-17T08:02:30"


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


def test_a_scan_at_the_slot_boundary_is_not_the_first_slot():
    # 08:15:00 belongs to the SECOND slot (autoscan_due buckets on
    # minute // 15), so the bound is exclusive.
    assert compute._trustworthy_baseline(False, "2026-09-17T08:15:00") is False


def test_a_cold_start_BEFORE_the_window_opens_claims_nothing():
    # Deliberate, and the reason is the asymmetry: inside the first slot a cold
    # start is almost certainly the day's genuine first scan. Before the window
    # opens there is no bound on how many manual scans may already have run —
    # "Run scan" is subject to no window at all — so we cannot know we are
    # first, and the safe direction is a dash rather than a claim.
    assert compute._trustworthy_baseline(False, "2026-09-17T03:00:00") is False


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


def test_an_entry_with_no_last_live_is_evicted_first_and_never_raises():
    # Reachable, which is what makes this worth pinning: merge_setups normalises
    # seen/gaps/scores off a corrupt Redis envelope but carries the entry
    # WITHOUT touching last_live, so a live map can genuinely hold one that has
    # none. An unreadable age must lose the eviction race rather than win it by
    # sorting as the literal "None" — and the sort must not raise on a mix of
    # None and str, which is what the `str(... or "")` buys.
    setups = {"nostamp|PCS|E": {"seen": 3},
              "stamped|PCS|E": {"last_live": "2026-09-17T09:00:00"}}
    kept, dropped = compute._cap_setups(setups, set(), max_entries=1)
    assert set(kept) == {"stamped|PCS|E"}
    assert dropped == 1


def test_a_non_string_last_live_does_not_crash_the_eviction_sort():
    # Reachable for the same reason: merge_setups copies last_live VERBATIM off
    # the envelope, normalising only seen/gaps/scores. Without the str() the
    # sort raises TypeError comparing int to str — which would take the whole
    # cap down rather than cost one entry its place in the order. The winner is
    # deliberately not asserted: which side of a digit-vs-digit comparison a
    # corrupt stamp lands on is an accident, not a decision.
    setups = {"numeric|PCS|E": {"last_live": 1_700_000_000},
              "stamped|PCS|E": {"last_live": "2026-09-17T09:00:00"}}
    kept, dropped = compute._cap_setups(setups, set(), max_entries=1)
    assert dropped == 1 and len(kept) == 1

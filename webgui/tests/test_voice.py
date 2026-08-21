"""The Desk's spoken alerts — the pure phrase builders and the mp3 cache.

No network anywhere in this file, but it gets there two different ways. The
``ensure`` tests monkeypatch ``_synthesize`` outright. The ``_synthesize`` tests
run the REAL function against a fake ``sys.modules['edge_tts']``, so they
exercise the temp-file/rename/cleanup dance and the bounded wait without ever
opening a socket. The one test that touches the installed ``edge_tts`` reads its
signature only, and skips when the package is absent.
"""
import asyncio
import hashlib
import logging
import os
import pathlib
import sys
import types

import pytest

import voice


# ── spell ────────────────────────────────────────────────────────────────────
def test_spell_separates_the_letters_of_a_ticker():
    assert voice.spell("SPY") == "S P Y"


def test_spell_drops_the_index_dollar_sign():
    # "$SPX" spoken as "dollar S P X" would be wrong, and as "spux" worse.
    assert voice.spell("$SPX") == "S P X"


def test_spell_upcases_and_keeps_digits():
    assert voice.spell("brk.b") == "B R K B"


def test_spell_of_nothing_is_empty_not_a_crash():
    assert voice.spell(None) == ""
    assert voice.spell("") == ""


# ── more_tail ────────────────────────────────────────────────────────────────
def test_more_tail_is_silent_when_nothing_else_arrived():
    assert voice.more_tail(0) == ""
    assert voice.more_tail(None) == ""
    assert voice.more_tail(-3) == ""


def test_more_tail_counts_the_rest():
    assert voice.more_tail(5) == "Plus 5 more."


def test_more_tail_reads_naturally_at_one():
    # Deliberately not "1 more alerts" — and deliberately not the word "alert"
    # at all, since positions use this same tail.
    assert voice.more_tail(1) == "Plus 1 more."


def test_more_tail_refuses_a_bool():
    # ``float(True) == 1.0`` has slipped through numeric guards in this repo
    # before (see the NaN/bool notes in CLAUDE.md), and pages/fmt.py's ``num``
    # rejects bool explicitly for exactly this reason. A True that arrived where
    # a count belongs is a caller bug, not "one more alert".
    assert voice.more_tail(True) == ""
    assert voice.more_tail(False) == ""


def test_more_tail_survives_a_non_finite_count():
    # ``int(inf)`` raises OverflowError, not ValueError — the module promises
    # that nothing here raises, so the guard has to cover it.
    assert voice.more_tail(float("inf")) == ""
    assert voice.more_tail(float("-inf")) == ""
    assert voice.more_tail(float("nan")) == ""


# ── flow_phrase ──────────────────────────────────────────────────────────────
# The rows below are the shape ``pages.options.flow.alert_rows`` publishes:
# ``kind`` and ``side`` are already the DISPLAY labels, not the raw keys.
def test_flow_phrase_names_the_ticker_then_the_cause():
    row = {"symbol": "SPY", "kind": "Crossover", "side": "Calls over"}
    assert voice.flow_phrase(row) == "S P Y. Crossover alert, calls over."


_FLOW_CASES = {
    ("SPY", "Crossover", "Calls over"): "S P Y. Crossover alert, calls over.",
    ("SPY", "Crossover", "Puts over"): "S P Y. Crossover alert, puts over.",
    ("NDX", "Unusual activity", "Put"): "N D X. Unusual activity alert, put.",
    ("NDX", "Unusual activity", "Call"): "N D X. Unusual activity alert, call.",
    ("QQQ", "Gamma flip", "To negative"): "Q Q Q. Gamma flip alert, to negative.",
    ("QQQ", "Gamma flip", "To positive"): "Q Q Q. Gamma flip alert, to positive.",
    ("AMD", "Big delta", "Call"): "A M D. Big delta alert, call.",
    ("AMD", "Big delta", "Put"): "A M D. Big delta alert, put.",
}


def test_flow_phrase_covers_all_four_alert_kinds():
    for (sym, kind, side), want in _FLOW_CASES.items():
        assert voice.flow_phrase(
            {"symbol": sym, "kind": kind, "side": side}) == want


def test_flow_phrase_cases_are_complete_against_the_flow_pages_own_labels():
    """The table above is hand-written; this is what stops it rotting.

    ``flow_phrase`` reads the DISPLAY labels ``pages.options.flow`` stamps, so
    the spoken vocabulary and the printed one are the same words by
    construction — but only for labels somebody remembered to test. Add a fifth
    alert kind, or relabel "Big delta", and every assertion above stays green
    while the new phrase is never once spoken aloud in a test. Drift between
    the spoken and the printed vocabulary is invisible otherwise, which is the
    documented sectors-vs-rotation failure in a new place.

    Same shape as ``test_desk.py``'s
    ``test_flow_rows_delegates_to_the_flow_pages_own_builder``: import the real
    module and compare against it, never against a copy.
    """
    from pages.options import flow
    tested_kinds = {kind for _sym, kind, _side in _FLOW_CASES}
    tested_sides = {side for _sym, _kind, side in _FLOW_CASES}
    assert tested_kinds >= set(flow._KIND_LABEL.values())
    assert tested_sides >= set(flow._SIDE_LABEL.values())


def test_flow_phrase_omits_a_missing_side_without_a_dangling_comma():
    row = {"symbol": "SPY", "kind": "Crossover", "side": ""}
    assert voice.flow_phrase(row) == "S P Y. Crossover alert."


def test_flow_phrase_folds_the_burst_count_into_the_same_sentence():
    row = {"symbol": "SPY", "kind": "Crossover", "side": "Calls over"}
    assert voice.flow_phrase(row, extra=5) == \
        "S P Y. Crossover alert, calls over. Plus 5 more."


def test_flow_phrase_survives_a_junk_row():
    # Total over a malformed row, like every other builder the Desk reads.
    assert voice.flow_phrase(None) == "Flow alert."
    assert voice.flow_phrase({}) == "Flow alert."


# ── position_phrase ──────────────────────────────────────────────────────────
def test_position_phrase_names_the_ticker_and_the_strategy():
    row = {"symbol": "SPY", "strategy": "put_credit_spread"}
    assert voice.position_phrase(row) == "S P Y. New position, put credit spread."


def test_position_phrase_without_a_strategy_still_announces_the_position():
    assert voice.position_phrase({"symbol": "QQQ"}) == "Q Q Q. New position."


def test_position_phrase_takes_the_burst_tail_too():
    row = {"symbol": "SPY", "strategy": "iron_condor"}
    assert voice.position_phrase(row, extra=2) == \
        "S P Y. New position, iron condor. Plus 2 more."


# ── cache keys ───────────────────────────────────────────────────────────────
def test_clip_name_is_stable_for_the_same_phrase():
    a = voice.clip_name("S P Y. Crossover alert.")
    assert a == voice.clip_name("S P Y. Crossover alert.")
    assert a.endswith(".mp3")


def test_clip_name_changes_with_the_voice():
    # The voice is part of the key, or switching voices in Settings would keep
    # serving clips spoken by the previous one.
    a = voice.clip_name("hello", voice_name="en-US-AriaNeural")
    b = voice.clip_name("hello", voice_name="en-US-BrianNeural")
    assert a != b


def test_clip_name_hash_is_pinned_so_warmed_clips_are_never_orphaned():
    """The digest is a CACHE KEY on disk, so changing it strands every clip.

    ``usedforsecurity=False`` documents that intent and silences FIPS/Bandit
    flags, but it must not move a single byte of output — this pins both the
    formula and the literal digest observed before the flag was added.
    """
    expected = hashlib.sha1(
        f"{voice.DEFAULT_VOICE}|{voice.RATE}|hello".encode("utf-8")).hexdigest()
    assert voice.clip_name("hello") == expected + ".mp3"
    assert voice.clip_name("hello") == "aef6ec44b2d50d96a34158c500217874aa8f5131.mp3"


def test_clip_url_is_served_from_the_voice_mount():
    url = voice.clip_url("hello")
    assert url.startswith("/voice/") and url.endswith(".mp3")


# ── ensure ───────────────────────────────────────────────────────────────────
def test_ensure_synthesizes_once_then_serves_the_cached_file(tmp_path, monkeypatch):
    monkeypatch.setattr(voice, "CACHE_DIR", tmp_path)
    calls = []

    def _fake(text, voice_name, rate, dest):
        calls.append(text)
        dest.write_bytes(b"ID3fake")

    monkeypatch.setattr(voice, "_synthesize", _fake)

    first = voice.ensure("S P Y. Crossover alert.")
    second = voice.ensure("S P Y. Crossover alert.")
    assert first == second and first.startswith("/voice/")
    assert calls == ["S P Y. Crossover alert."]      # the second call is a hit


def test_ensure_returns_none_when_synthesis_fails(tmp_path, monkeypatch):
    # No internet, no edge_tts, unwritable dir — all land here, and all must
    # degrade to silence rather than a traceback on the landing page.
    monkeypatch.setattr(voice, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(voice, "_synthesize",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("no net")))
    voice.reset_warning()
    assert voice.ensure("anything") is None


def test_ensure_ignores_an_empty_phrase(tmp_path, monkeypatch):
    monkeypatch.setattr(voice, "CACHE_DIR", tmp_path)
    assert voice.ensure("") is None
    assert voice.ensure(None) is None


def test_ensure_does_not_serve_a_zero_byte_clip(tmp_path, monkeypatch):
    # A crashed or interrupted synthesis can leave an empty file. Serving it
    # would be a silent permanent failure for that one phrase.
    monkeypatch.setattr(voice, "CACHE_DIR", tmp_path)
    dest = tmp_path / voice.clip_name("hello")
    dest.write_bytes(b"")
    monkeypatch.setattr(voice, "_synthesize",
                        lambda t, v, r, d: d.write_bytes(b"ID3real"))
    assert voice.ensure("hello") is not None
    assert dest.read_bytes() == b"ID3real"


def test_ensure_degrades_to_silence_when_synthesis_times_out(tmp_path, monkeypatch):
    """The degradation HALF of the timeout story, not the cancellation half.

    ``test_synthesize_timeout_is_bounded_and_cancels_the_request`` below is what
    proves the bounded wait actually fires and cancels; this only pins that a
    ``TimeoutError`` surfacing out of ``_synthesize`` reaches the caller as
    silence and not as a traceback on the landing page. It also pins that the
    dead ``dest`` is not served: a timed-out synthesis leaves nothing behind, so
    ``ensure``'s post-failure ``_usable`` re-check (the lost-race path) must NOT
    manufacture a URL for a file that does not exist.
    """
    monkeypatch.setattr(voice, "CACHE_DIR", tmp_path)

    def _hang(text, voice_name, rate, dest):
        raise asyncio.TimeoutError()

    monkeypatch.setattr(voice, "_synthesize", _hang)
    voice.reset_warning()
    assert voice.ensure("anything") is None


def test_ensure_survives_a_lone_surrogate_in_the_phrase(tmp_path, monkeypatch):
    """A malformed payload must not traceback the landing page.

    ``json.loads('"\\ud800"')`` yields a lone surrogate WITHOUT error, so a bad
    Redis payload is a real source of one — and ``clip_name``'s
    ``.encode("utf-8")`` refuses it. The hash of the phrase is therefore work
    that can raise, and it has to sit INSIDE ``ensure``'s guard, not above it.
    """
    monkeypatch.setattr(voice, "CACHE_DIR", tmp_path)
    voice.reset_warning()
    assert voice.ensure("SPY \udcff alert") is None


def test_ensure_serves_a_clip_that_a_lost_replace_race_already_wrote(
        tmp_path, monkeypatch):
    """Losing the rename race is not a reason to go silent.

    The prewarm daemon and a live Desk tick can synthesize the SAME phrase at
    once. Thread A wins, the browser starts streaming ``dest``, and thread B's
    ``tmp.replace(dest)`` then fails with ``PermissionError [WinError 5]``
    because the destination has an open handle. A good clip now exists — B must
    serve it rather than return ``None`` and burn the one-shot warning.
    """
    monkeypatch.setattr(voice, "CACHE_DIR", tmp_path)
    dest = tmp_path / voice.clip_name("hello")

    def _lost_the_race(text, voice_name, rate, d):
        d.write_bytes(b"ID3winner")            # the OTHER thread got there first
        raise PermissionError(5, "Access is denied")

    monkeypatch.setattr(voice, "_synthesize", _lost_the_race)
    voice.reset_warning()
    assert voice.ensure("hello") == voice.clip_url("hello")
    assert dest.read_bytes() == b"ID3winner"
    assert voice._WARNED["done"] is False      # not a failure, so not a warning


def test_ensure_warns_only_once_per_process(tmp_path, monkeypatch, caplog):
    """A page polling every two seconds would otherwise log 43,200 tracebacks."""
    monkeypatch.setattr(voice, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(voice, "_synthesize",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("no net")))
    voice.reset_warning()
    with caplog.at_level(logging.WARNING, logger="webgui"):
        assert voice.ensure("one") is None
        assert voice.ensure("two") is None
        assert voice.ensure("three") is None
    warnings = [r for r in caplog.records if "voice synthesis" in r.message]
    assert len(warnings) == 1


def test_synthesize_timeout_is_bounded_and_cancels_the_request(monkeypatch, tmp_path):
    """The timeout must CANCEL the coroutine, not orphan it in the background.

    Injecting a tiny timeout keeps the test instant — waiting the real 20 s to
    prove a 20 s wait works would be its own bug.
    """
    monkeypatch.setattr(voice, "SYNTH_TIMEOUT_SEC", 0.05)
    cancelled = {"hit": False}

    class _Hang:
        def __init__(self, *a, **k):
            pass

        async def save(self, path):
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                cancelled["hit"] = True
                raise

    monkeypatch.setitem(sys.modules, "edge_tts",
                        types.SimpleNamespace(Communicate=_Hang))
    with pytest.raises(asyncio.TimeoutError):
        voice._synthesize("hi", voice.DEFAULT_VOICE, voice.RATE, tmp_path / "x.mp3")
    assert cancelled["hit"]


def test_synthesize_leaves_no_part_file_behind(monkeypatch, tmp_path):
    """A failed synthesis must not litter the cache with .part turds."""
    class _Boom:
        def __init__(self, *a, **k):
            pass

        async def save(self, path):
            pathlib.Path(path).write_bytes(b"half")
            raise OSError("dropped")

    monkeypatch.setitem(sys.modules, "edge_tts",
                        types.SimpleNamespace(Communicate=_Boom))
    with pytest.raises(OSError):
        voice._synthesize("hi", voice.DEFAULT_VOICE, voice.RATE, tmp_path / "x.mp3")
    assert list(tmp_path.iterdir()) == []


def test_synthesize_cleanup_failure_does_not_mask_the_real_error(
        monkeypatch, tmp_path):
    """The ``finally`` must not overwrite the exception it is cleaning up after.

    Unlinking a file with an open handle raises ``PermissionError [WinError 32]``
    on Windows, and an antivirus scanner touching the ``.part`` is enough to do
    it (Malwarebytes is a documented actor on this machine). If that escapes,
    ``ensure``'s one-shot ``exc_info=True`` warning — the only diagnostic the
    feature has — reports the cleanup, never the cause.
    """
    class _Boom:
        def __init__(self, *a, **k):
            pass

        async def save(self, path):
            pathlib.Path(path).write_bytes(b"half")
            raise OSError("dropped mid-stream")

    monkeypatch.setitem(sys.modules, "edge_tts",
                        types.SimpleNamespace(Communicate=_Boom))
    monkeypatch.setattr(
        pathlib.Path, "unlink",
        lambda self, missing_ok=False: (_ for _ in ()).throw(
            PermissionError(32, "used by another process")))

    with pytest.raises(OSError, match="dropped mid-stream"):
        voice._synthesize("hi", voice.DEFAULT_VOICE, voice.RATE, tmp_path / "x.mp3")


# ── prewarm ──────────────────────────────────────────────────────────────────
def test_prewarm_texts_covers_every_symbol_and_cause():
    texts = voice.prewarm_texts(["SPY", "QQQ"])
    assert len(texts) == 2 * len(voice.FLOW_CAUSES)
    assert "S P Y. Crossover alert, calls over." in texts
    assert "Q Q Q. Big delta alert, put." in texts


def test_prewarm_texts_of_nothing_is_empty():
    assert voice.prewarm_texts(None) == []
    assert voice.prewarm_texts([]) == []


def test_prewarm_texts_survives_a_non_iterable():
    # The module's headline promise is categorical: nothing on the public
    # surface raises. ``symbols`` reaches here off a Redis payload, the same
    # source that produces the lone surrogate above — "that would be a caller
    # bug" is not a defence when the caller is a malformed cache read.
    assert voice.prewarm_texts(7) == []
    assert voice.prewarm_texts(object()) == []
    assert voice.prewarm(7) is None


def test_prewarm_returns_none_when_there_is_nothing_to_warm():
    assert voice.prewarm([]) is None
    assert voice.prewarm(None) is None


def test_prewarm_runs_on_a_daemon_thread(monkeypatch):
    """A non-daemon thread would hold the web GUI open at shutdown."""
    monkeypatch.setattr(voice, "ensure", lambda *a, **k: "/voice/x.mp3")
    t = voice.prewarm(["SPY"])
    assert t is not None and t.daemon
    t.join(timeout=5)
    assert not t.is_alive()


def test_prewarm_stops_at_the_first_failure(monkeypatch):
    """Synthesis failures come in one flavour — the endpoint is unreachable —
    so grinding through the remaining 15 phrases buys nothing but 15 timeouts."""
    calls = []

    def _fake(text, voice_name=None, **kw):
        calls.append(text)
        return None if len(calls) == 2 else "/voice/x.mp3"

    monkeypatch.setattr(voice, "ensure", _fake)
    t = voice.prewarm(["SPY", "QQQ"])
    t.join(timeout=5)
    assert len(calls) == 2                      # stopped, did not run all 16


def test_prewarm_failure_does_not_burn_the_live_paths_one_shot_warning(
        tmp_path, monkeypatch):
    """The warning slot belongs to the LIVE path, which is the one a user hears.

    A transient blip during the startup prewarm used to set ``_WARNED``
    permanently, so a genuinely broken lazy path an hour later logged nothing at
    all and the feature failed in total silence.
    """
    monkeypatch.setattr(voice, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(voice, "_synthesize",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("blip")))
    voice.reset_warning()
    t = voice.prewarm(["SPY"])
    t.join(timeout=5)
    assert voice._WARNED["done"] is False


# ── the .part sweep ──────────────────────────────────────────────────────────
def test_sweep_parts_removes_only_stale_leftovers(tmp_path, monkeypatch):
    """Orphaned ``.part`` files accumulate forever in a SERVED directory.

    A daemon prewarm thread killed at interpreter shutdown never runs its
    ``finally``, and nothing else ever looks at ``data/voice``. The sweep must
    not touch clips, and must not touch a ``.part`` that a live synthesis is
    still writing.
    """
    monkeypatch.setattr(voice, "CACHE_DIR", tmp_path)
    stale = tmp_path / "abc.mp3.123.456.part"
    fresh = tmp_path / "def.mp3.789.012.part"
    clip = tmp_path / "abc.mp3"
    for p in (stale, fresh, clip):
        p.write_bytes(b"x")
    old = os.stat(stale).st_mtime - (voice._PART_MAX_AGE_SEC + 60)
    os.utime(stale, (old, old))

    assert voice._sweep_parts() == 1
    assert not stale.exists()
    assert fresh.exists() and clip.exists()


def test_sweep_parts_never_raises_on_a_missing_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(voice, "CACHE_DIR", tmp_path / "nope")
    assert voice._sweep_parts() == 0


def test_prewarm_sweeps_stale_parts_before_it_starts(tmp_path, monkeypatch):
    monkeypatch.setattr(voice, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(voice, "ensure", lambda *a, **k: "/voice/x.mp3")
    stale = tmp_path / "abc.mp3.1.2.part"
    stale.write_bytes(b"x")
    old = os.stat(stale).st_mtime - (voice._PART_MAX_AGE_SEC + 60)
    os.utime(stale, (old, old))

    t = voice.prewarm(["SPY"])
    t.join(timeout=5)
    assert not stale.exists()


def test_flow_causes_cover_every_pair_the_flow_page_can_emit():
    """``FLOW_CAUSES`` is a deliberate COPY of pages.options.flow's labels — it
    is restated so ``voice`` stays importable with no ``pages`` package on the
    path (the prewarm runs before any page is built). A copy with no guard is
    a copy waiting to rot, so this is the guard: ``flow._TONE``'s keys enumerate
    exactly the (type, side) pairs the panel can produce, mapped through the
    page's OWN label functions rather than hand-copied a second time.
    """
    from pages.options import flow
    real = {(flow.alert_kind_label({"type": t}), flow.side_label({"side": s}))
            for t, s in flow._TONE}
    assert set(voice.FLOW_CAUSES) == real

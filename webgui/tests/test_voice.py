"""Pure speech builders for the Desk's spoken alerts.

No network anywhere in this file: synthesis is monkeypatched in the cache
tests below, and the builders here touch nothing but strings.
"""
import asyncio
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
    # edge_tts.save() is a NETWORK call with no timeout of its own; a hung
    # endpoint would otherwise pin a run.io_bound worker thread forever. The
    # bounded wait makes a hang look like every other failure: silence.
    monkeypatch.setattr(voice, "CACHE_DIR", tmp_path)

    def _hang(text, voice_name, rate, dest):
        raise asyncio.TimeoutError()

    monkeypatch.setattr(voice, "_synthesize", _hang)
    voice.reset_warning()
    assert voice.ensure("anything") is None


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


# ── prewarm ──────────────────────────────────────────────────────────────────
def test_prewarm_texts_covers_every_symbol_and_cause():
    texts = voice.prewarm_texts(["SPY", "QQQ"])
    assert len(texts) == 2 * len(voice.FLOW_CAUSES)
    assert "S P Y. Crossover alert, calls over." in texts
    assert "Q Q Q. Big delta alert, put." in texts


def test_prewarm_texts_of_nothing_is_empty():
    assert voice.prewarm_texts(None) == []
    assert voice.prewarm_texts([]) == []


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

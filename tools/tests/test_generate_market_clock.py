"""``tools/generate_market_clock.py`` -- the data half of the site's open/closed glow.

``neuralstrike.co`` is a static tree served by Caddy's ``file_server``, so
nothing on the server can know whether the NYSE is open. The "Live screens"
link's glow is therefore decided client-side, and the JavaScript that decides it
needs a holiday calendar and the session bounds.

What is pinned here is the reason this file is GENERATED rather than typed:

* the emitted holidays are this repo's derived NYSE calendar, not an eleventh
  hand-maintained copy of it,
* two years are emitted, so a file generated in December still answers on
  2 January,
* the session bounds come from ``config/sessions.toml`` (which states them in
  CT) rather than from a second pair of literals,
* and the early-close gap is written down in the file itself.
"""
import json
import re

from shared import market_calendar as mc
from tools import generate_market_clock as g


def test_the_emitted_holidays_match_the_calendar_module():
    """The whole point of generating this file. A hand-maintained list in
    JavaScript would be an eleventh copy of a calendar this repo deliberately
    derives -- and it would rot silently, because nothing renders red when a
    marketing page glows on Thanksgiving."""
    js = g.render()
    emitted = set(json.loads(re.search(r"HOLIDAYS\s*=\s*(\[[^\]]*\])", js).group(1)))
    year = g.CURRENT_YEAR
    expected = {d.isoformat() for y in (year, year + 1) for d in mc.nyse_holidays(y)}
    assert emitted == expected


def test_it_emits_two_years_so_a_january_visitor_is_covered():
    """A file generated in December that carried only that year would be blind on
    2 January -- the first trading day of the next one."""
    js = g.render()
    assert str(g.CURRENT_YEAR) in js and str(g.CURRENT_YEAR + 1) in js


def test_the_early_close_gap_is_declared_in_the_file():
    """DECIDED 2026-09-08: half-days are NOT handled. The NYSE closes at 13:00 ET
    on ~3 afternoons a year and nothing in this repo knows it -- there is no
    is_half_day anywhere. The glow is decoration with no downstream consumer, so
    the gap was accepted; this asserts it is WRITTEN DOWN rather than left for
    someone to find on Black Friday."""
    js = g.render()
    assert "13:00" in js and "early close" in js.lower()


# --- the session bounds are derived, not a second pair of literals -----------

def test_the_session_bounds_are_the_configured_ones_shifted_to_eastern():
    """``config/sessions.toml`` states the regular session in CENTRAL time
    (``08:30``/``15:00``, annotated ``# 09:30 ET`` / ``# 16:00 ET`` in the file).
    The browser works in Eastern, so the generator converts -- it does not carry
    its own 09:30/16:00, which is the second copy that config file exists to
    prevent."""
    start_ct, end_ct = mc._session_bounds("regular")
    js = g.render()
    for ct, emitted in ((start_ct, "OPEN_ET"), (end_ct, "CLOSE_ET")):
        want = f"{(ct.hour + 1) % 24:02d}:{ct.minute:02d}"
        found = re.search(rf'{emitted}\s*=\s*"([^"]+)"', js)
        assert found, f"{emitted} is not emitted"
        assert found.group(1) == want, f"{emitted} is {found.group(1)}, want {want}"


def test_a_moved_session_bound_moves_the_generated_file(monkeypatch):
    """The discriminating test: asserting the emitted value equals the config
    value proves nothing while both happen to be 09:30. Move the config and the
    output must move with it."""
    import datetime

    monkeypatch.setattr(g.mc, "_session_bounds",
                        lambda _name: (datetime.time(7, 45), datetime.time(14, 5)))
    js = g.render()
    assert 'OPEN_ET = "08:45"' in js
    assert 'CLOSE_ET = "15:05"' in js


# --- the file says what it is ------------------------------------------------

def test_the_header_names_the_generator_so_nobody_hand_edits_it():
    """A generated file with no banner gets hand-edited exactly once, and the
    next regeneration silently discards the edit."""
    head = g.render()[:1200]
    assert "generated" in head.lower()
    assert "tools/generate_market_clock.py" in head


def test_it_reaches_no_external_origin():
    """The site fetches nothing from anyone -- ``deploy/tests/test_site.py``
    pins that for the pages, and this asset must not be the exception."""
    js = g.render()
    assert "http://" not in js
    assert "https://" not in js


def test_it_emits_data_only_and_touches_no_dom():
    """YAGNI, and a scope line worth holding: this file is the calendar. Deciding
    the state and toggling the class is the consumer's job, so a DOM reference
    here means the two halves have started to merge."""
    js = g.render()
    for banned in ("document.", "window.addEventListener", "classList", "querySelector"):
        assert banned not in js, f"the generated data file references {banned}"


def test_it_is_valid_json_all_the_way_down():
    """The holidays are emitted through ``json.dumps`` rather than string
    formatting, so a stray quote cannot produce a file the browser refuses to
    parse -- and a browser that refuses it fails SILENTLY, leaving a dead glow."""
    js = g.render()
    arr = json.loads(re.search(r"HOLIDAYS\s*=\s*(\[[^\]]*\])", js).group(1))
    assert arr == sorted(arr), "emitted out of order, so a diff is noise"
    assert len(arr) == len(set(arr)), "duplicate dates"
    assert all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", d) for d in arr)


# --- the committed artifact is in step with the generator --------------------

def test_the_committed_file_matches_what_the_generator_emits():
    """This artifact is COMMITTED (unlike the rest of the generated tree), so the
    site works from a fresh clone. That only holds while the committed bytes are
    the generator's output; regenerate and commit when this fails.

    ⚠ It legitimately fails on 1 January: the file carries the year it was
    generated in and the next one. That is the reminder to regenerate, which is
    a deliberate act here rather than a timer."""
    assert g.OUTPUT_PATH.is_file(), f"{g.OUTPUT_PATH} is missing -- run the generator"
    assert g.OUTPUT_PATH.read_text(encoding="utf-8") == g.render()

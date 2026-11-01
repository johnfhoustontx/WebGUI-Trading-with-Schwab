"""The /ES and /NQ rows on the Desk's Dealer Positioning panel: their own clock, what
the second line says, the kept capture after expiry, the frame, and that the published
Desk never reads them."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from pages import desk as d
from pages import desk_feed as feed
from shared import futures_chain_config as cfg

_CT = ZoneInfo("America/Chicago")


def ct(*when):
    return datetime(*when, tzinfo=_CT).timestamp()


# Monday 2026-11-02's session, 06:00 to 15:00.
def row(captured, **over):
    return {"symbol": "FOP.ES", "spot": 6745.25, "flip": 6700.0, "call_wall": 6800.0,
            "put_wall": 6650.0, "net_gex": 2.2e9, "gex_regime": "above",
            "captured_at": captured, "expiry": "2026-11-02", "not_moving": False, **over}


def view(latest, frozen=None):
    entry = {"latest": latest}
    if frozen is not None:
        entry["frozen"] = frozen
    return {"FOP.ES": entry}


def test_a_current_capture_is_a_full_row_named_by_its_label():
    (out,) = feed.rows(view(row(ct(2026, 11, 2, 9, 0))), ct(2026, 11, 2, 9, 1))
    assert out["symbol"] == "FOP.ES" and out["label"] == "/ES"
    assert out["stale"] is False and out["as_of"] == "as of 09:00"
    assert out["call_wall"] == 6800.0 and out["put_wall"] == 6650.0
    assert out["flip_side"] == "above" and out["regime_word"] == d.regime_word("above")
    assert out["day_pct"] is None


def test_too_old_a_capture_is_stale_and_its_walls_go():
    (out,) = feed.rows(view(row(ct(2026, 11, 2, 9, 0))), ct(2026, 11, 2, 9, 12))
    assert out["stale"] is True and out["call_wall"] is None and out["put_wall"] is None
    assert out["spot"] == 6745.25 and out["as_of"] == "as of 09:00"


def test_a_feed_that_stopped_moving_is_stale_however_new():
    (out,) = feed.rows(view(row(ct(2026, 11, 2, 9, 0), not_moving=True)),
                       ct(2026, 11, 2, 9, 1))
    assert out["stale"] is True and out["call_wall"] is None


def test_after_expiry_the_row_is_the_capture_kept_before_the_last_hour():
    kept = row(ct(2026, 11, 2, 13, 55), spot=1.0)
    (out,) = feed.rows(view(row(ct(2026, 11, 2, 14, 55), spot=2.0), kept),
                       ct(2026, 11, 2, 15, 1))
    assert out["spot"] == 1.0 and out["stale"] is True and out["as_of"] == "as of 13:55"


def test_after_expiry_with_nothing_kept_there_is_no_row():
    assert feed.rows(view(row(ct(2026, 11, 2, 14, 55))), ct(2026, 11, 2, 15, 1)) == []


def test_another_days_capture_names_its_day():
    kept = row(ct(2026, 11, 2, 13, 55))
    (out,) = feed.rows(view(row(ct(2026, 11, 2, 14, 55)), kept), ct(2026, 11, 3, 5, 0))
    assert out["as_of"] == "as of Mon 13:55"


@pytest.mark.parametrize("bad", [None, [], "x", {"FOP.ES": 5}, {"FOP.ES": {"latest": 5}},
                                 {"FOP.ES": {"latest": {"expiry": "soon"}}},
                                 {"FOP.ES": {"frozen": {"expiry": "2026-11-02"}}}])
def test_a_malformed_view_is_no_row(bad):
    assert feed.rows(bad, ct(2026, 11, 2, 9, 0)) == []


def test_a_nan_capture_time_is_no_row():
    assert feed.rows(view(row(float("nan"))), ct(2026, 11, 2, 9, 0)) == []


@pytest.mark.parametrize("offered", [True, False])
def test_a_row_opens_dealer_positioning_only_while_the_list_offers_it(monkeypatch, offered):
    real = cfg.capture()
    monkeypatch.setattr(cfg, "capture", lambda: {**real, "show_in_dropdown": offered})
    (out,) = feed.rows(view(row(ct(2026, 11, 2, 9, 0))), ct(2026, 11, 2, 9, 1))
    assert out["opens"] is offered


def test_the_phase_moves_when_only_the_clock_does():
    v = view(row(ct(2026, 11, 2, 9, 0)))
    assert feed.phase(v, ct(2026, 11, 2, 9, 1)) == feed.phase(v, ct(2026, 11, 2, 9, 2))
    assert feed.phase(v, ct(2026, 11, 2, 9, 1)) != feed.phase(v, ct(2026, 11, 2, 9, 12))


# ── the published Desk ──────────────────────────────────────────────────────

def test_the_published_desk_never_reads_the_view(monkeypatch):
    monkeypatch.setattr(d._shell, "is_public", lambda: True)
    assert feed.VIEW not in d.live_views()
    assert set(d.live_views()) == set(d.VIEWS) - {feed.VIEW}


def test_the_private_desk_reads_it_and_repaints_the_panel_on_it(monkeypatch):
    monkeypatch.setattr(d._shell, "is_public", lambda: False)
    assert feed.VIEW in d.live_views()
    assert feed.VIEW in d._REGION_VIEWS["dealer"]


# ── the frame ───────────────────────────────────────────────────────────────

class _Body:
    """The classes the painter leaves on the panel's body."""
    def __init__(self):
        self.classes_now = set()

    def clear(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def classes(self, add=None, remove=None):
        self.classes_now -= set((remove or "").split())
        self.classes_now |= set((add or "").split())


@pytest.fixture
def painted(monkeypatch):
    seen = {"rows": [], "notes": [], "head": None}
    monkeypatch.setattr(d, "_dealer_row", seen["rows"].append)
    monkeypatch.setattr(d, "_grid_head", lambda g, l, extra="": seen.update(head=extra))
    monkeypatch.setattr(d.kit, "empty", lambda text: seen.update(empty=text))

    class _Label:
        def __init__(self, text):
            seen["notes"].append(text)

        def classes(self, *_):
            return self
    monkeypatch.setattr(d.ui, "label", _Label)
    return seen


def _matrix():
    return {"rows": [{"symbol": s, "spot": 100.0} for s in d.DESK_SYMBOLS]}


def _paint(matrix, feed_view, age=20):
    body = _Body()
    d._paint_dealer(body, matrix, {"age_seconds": age}, feed_view, ct(2026, 11, 2, 9, 1))
    return body.classes_now


def test_four_rows_are_the_panel_it_always_was(painted):
    assert _paint(_matrix(), None) == set()
    assert painted["head"] == "" and len(painted["rows"]) == 4


def test_a_feed_row_scrolls_inside_the_frame_after_the_four(painted):
    classes = _paint(_matrix(), view(row(ct(2026, 11, 2, 9, 0))))
    assert classes == set(d.DEALER_SCROLL.split())
    assert painted["head"] == d.DEALER_HEAD_PIN
    assert [r["symbol"] for r in painted["rows"]] == [*d.DESK_SYMBOLS, "FOP.ES"]


def test_the_note_gets_its_own_height_and_names_the_stock_rows(painted):
    classes = _paint(_matrix(), view(row(ct(2026, 11, 2, 9, 0))), age=4000)
    assert classes == set(d.DEALER_SCROLL_NOTED.split())
    assert painted["notes"][0].startswith("Stock and index walls hidden")


def test_the_feed_shows_with_the_stock_matrix_cold(painted):
    _paint(None, view(row(ct(2026, 11, 2, 9, 0))))
    assert [r["symbol"] for r in painted["rows"]] == ["FOP.ES"]


def test_nothing_at_all_still_waits(painted):
    _paint(None, None)
    assert painted["empty"] == d.WAITING_OPTIONS

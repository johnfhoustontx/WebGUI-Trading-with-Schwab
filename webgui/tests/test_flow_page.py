"""Flow Alerts page — pure builders. Tier-1 reader of cache:options:flow_alerts."""
import datetime as dt
from zoneinfo import ZoneInfo

from pages.options import flow

CT = ZoneInfo("America/Chicago")

_XO = {"type": "crossover", "side": "calls_over", "symbol": "SPY", "ts": 1754750000,
       "call_prem": 1200000.0, "put_prem": 400000.0,
       "id": "SPY|crossover|calls_over|1754750000", "text": "SPY — call premium overtook puts"}
_UOA = {"type": "uoa", "side": "call", "symbol": "QQQ", "strike": 737.0,
        "expiry": "2026-08-09", "dte": 0, "cost": 1.72, "volume": 12400, "oi": 1100,
        "vol_oi": 11.27, "premium": 2132800.0, "ts": 1754750100,
        "id": "QQQ|uoa|call|737|2026-08-09", "text": "QQQ 0DTE 737C — UNUSUAL"}
_GF = {"type": "gamma_flip", "side": "to_negative", "symbol": "$SPX", "spot": 6412.0,
       "flip": 6400.0, "ts": 1754750400,
       "id": "$SPX|gamma_flip|to_negative|1754750400", "text": "$SPX — gamma flipped NEGATIVE"}
_VIEW = {"date": "2026-08-09", "alerts": [_XO, _UOA, _GF]}


# ── rows, labels, detail ─────────────────────────────────────────────────────
def test_alert_rows_are_newest_first():
    """The service appends oldest-first; a tape reads newest-first."""
    rows = flow.alert_rows(_VIEW)
    assert [r["symbol"] for r in rows] == ["$SPX", "QQQ", "SPY"]


def test_alert_rows_survive_malformed_input():
    """render() does no validation, so the builder must be total."""
    assert flow.alert_rows(None) == []
    assert flow.alert_rows({}) == []
    assert flow.alert_rows({"alerts": "nope"}) == []
    # Non-dict entries are skipped; a bare/partial dict degrades but never raises.
    assert len(flow.alert_rows({"alerts": [None, {}, {"type": "uoa"}]})) == 2


def test_alert_rows_stamp_the_raw_kind_key_for_filtering():
    """Filters work off the raw type key, not the display label."""
    assert {r["_kind_key"] for r in flow.alert_rows(_VIEW)} == {
        "crossover", "uoa", "gamma_flip"}


def test_alert_rows_tolerate_a_uoa_alert_with_no_timestamp():
    """UOA alerts published BEFORE the service fix carry no ts — they must still
    render, just without a time."""
    stale = {k: v for k, v in _UOA.items() if k != "ts"}
    row = flow.alert_rows({"alerts": [stale]})[0]
    assert row["ts"] is None and row["time"] == "" and row["symbol"] == "QQQ"


def test_alert_rows_give_every_row_a_key_even_without_an_id():
    """row_key='id' — a duplicate/missing key would collapse rows in the table."""
    rows = flow.alert_rows({"alerts": [{"symbol": "SPY"}, {"symbol": "SPY"}]})
    assert len({r["id"] for r in rows}) == 2


def test_kind_labels_are_whole_words():
    """UI labels spell things out; 'UOA' means nothing at a glance."""
    assert flow.alert_kind_label(_XO) == "Crossover"
    assert flow.alert_kind_label(_UOA) == "Unusual activity"
    assert flow.alert_kind_label(_GF) == "Gamma flip"
    assert flow.alert_kind_label({}) == "Flow"


def test_side_labels_read_directionally():
    assert flow.side_label(_XO) == "Calls over"
    assert flow.side_label(_UOA) == "Call"
    assert flow.side_label(_GF) == "To negative"
    assert flow.side_label({}) == ""


def test_detail_cells_are_type_specific():
    assert flow.alert_detail(_XO) == "$1.20M calls vs $400k puts"
    assert flow.alert_detail(_UOA) == "0DTE 737C · 12,400 vol / 1,100 OI (11.3×) · $2.13M"
    assert flow.alert_detail(_GF) == "spot 6412 vs flip 6400"


def test_detail_is_total_over_missing_fields():
    assert flow.alert_detail({"type": "uoa"}) == ""
    assert flow.alert_detail({"type": "crossover"}) == ""
    assert flow.alert_detail({"type": "gamma_flip"}) == ""
    assert flow.alert_detail(None) == ""
    assert flow.alert_detail({"type": "who_knows"}) == ""


def test_detail_dated_expiry_when_not_zero_dte():
    a = dict(_UOA, dte=2, expiry="2026-08-11", side="put")
    assert flow.alert_detail(a).startswith("08/11 737P · ")


def test_tone_class_maps_direction_to_a_fixed_palette_class():
    """Tailwind-first: a finite (type, side) set maps to static classes, never a
    computed color or an inline style."""
    assert "emerald" in flow.tone_class(_XO)
    assert "rose" in flow.tone_class(_GF)
    assert flow.tone_class({}) == flow._TONE_NEUTRAL
    assert flow.tone_class(None) == flow._TONE_NEUTRAL


# ── time + age ───────────────────────────────────────────────────────────────
def test_fmt_time_renders_central_clock():
    """Trading times are Central everywhere in this app; ts is unix seconds."""
    ts = dt.datetime(2026, 8, 9, 9, 32, 5, tzinfo=CT).timestamp()
    assert flow.fmt_time(ts) == "09:32:05"
    assert flow.fmt_time(None) == ""
    assert flow.fmt_time("nope") == ""


def test_age_text_reads_at_a_glance():
    now = dt.datetime(2026, 8, 9, 10, 0, 0, tzinfo=CT)

    def t(**kw):
        return (now - dt.timedelta(**kw)).timestamp()

    assert flow.age_text(t(seconds=20), now) == "just now"
    assert flow.age_text(t(minutes=2), now) == "2m ago"
    assert flow.age_text(t(minutes=74), now) == "1h 14m ago"
    assert flow.age_text(None, now) == ""


def test_age_text_never_reads_negative_on_clock_skew():
    """A ts a few seconds in the future (service/GUI clock skew) must not render
    '-1m ago'."""
    now = dt.datetime(2026, 8, 9, 10, 0, 0, tzinfo=CT)
    assert flow.age_text((now + dt.timedelta(seconds=30)).timestamp(), now) == "just now"


# ── filtering + status ───────────────────────────────────────────────────────
def test_filter_rows_by_kind_and_symbol():
    rows = flow.alert_rows(_VIEW)
    assert len(flow.filter_rows(rows, {"crossover"}, None)) == 1
    assert len(flow.filter_rows(rows, {"crossover", "uoa"}, None)) == 2
    assert [r["symbol"] for r in flow.filter_rows(rows, None, "QQQ")] == ["QQQ"]
    # No kinds selected shows nothing -- an explicit empty selection, not "all".
    assert flow.filter_rows(rows, set(), None) == []
    # None means unfiltered.
    assert len(flow.filter_rows(rows, None, None)) == 3
    assert flow.filter_rows(None, None, None) == []


def test_symbol_options_are_sorted_and_deduped():
    assert flow.symbol_options(flow.alert_rows(_VIEW)) == ["$SPX", "QQQ", "SPY"]
    assert flow.symbol_options([]) == []


def test_status_text_distinguishes_quiet_from_cold():
    """'Nothing has fired' and 'the service isn't publishing' look identical on an
    empty table -- they must not read the same."""
    assert flow.status_text(None) == "Waiting for the options service…"
    assert "No flow alerts yet" in flow.status_text({"date": "2026-08-09", "alerts": []})
    assert flow.status_text(_VIEW) == "3 alerts today · 2026-08-09"
    assert flow.status_text({"date": "2026-08-09", "alerts": [_XO]}) == "1 alert today · 2026-08-09"


# ── table + handoff ──────────────────────────────────────────────────────────
def test_flow_columns_are_sortable_and_ordered():
    names = [c["name"] for c in flow.flow_columns()]
    assert names == ["time", "age", "symbol", "kind", "side", "detail", "text"]
    assert all(c["sortable"] for c in flow.flow_columns())


def test_row_fields_cover_every_column():
    """A column whose field is missing from the row dict renders blank forever."""
    row = flow.alert_rows(_VIEW)[0]
    for col in flow.flow_columns():
        assert col["field"] in row


def test_gamma_handoff_is_one_shot():
    """A stashed symbol must be consumed exactly once, or navigating back to
    Dealer Positioning later would silently re-hijack the dropdown."""
    from pages.options import handoff
    handoff.set_pending_gamma("QQQ")
    assert handoff.take_pending_gamma() == "QQQ"
    assert handoff.take_pending_gamma() is None

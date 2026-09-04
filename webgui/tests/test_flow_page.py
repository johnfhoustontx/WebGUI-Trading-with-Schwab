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
# Kept OUT of _VIEW deliberately -- several tests above hardcode _VIEW's exact
# symbol/count/order, and folding a 4th alert type in would force unrelated
# edits across all of them for no benefit (big_delta gets its own tests below).
_BD = {"type": "big_delta", "side": "call", "symbol": "SPY", "strike": 100.0,
       "expiry": "2026-08-14", "dte": 3, "delta": 0.5, "volume": 5000,
       "delta_notional": 312_000_000.0, "pct_of_gross": 0.24, "ts": 1754750500,
       "id": "SPY|big_delta|call|100|2026-08-14", "text": "SPY big Δ: $312.00M"}


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


def test_alert_rows_carry_the_contract_the_desk_speaks_aloud():
    """``strike``/``expiry``/``dte`` ride the SAME row the table already builds.

    The Desk's spoken alert names the contract, and the alternative to carrying
    it here was a second reader of the raw payload living in ``desk.py`` — two
    readers of one payload is precisely how this app's documented sectors-vs-
    rotation split happened. Additive: the table declares no column for them.
    """
    row = flow.alert_rows({"alerts": [_UOA]})[0]
    assert (row["strike"], row["expiry"], row["dte"]) == (737.0, "2026-08-09", 0)
    bd = flow.alert_rows({"alerts": [_BD]})[0]
    assert (bd["strike"], bd["expiry"], bd["dte"]) == (100.0, "2026-08-14", 3)


def test_alert_rows_leave_the_contract_empty_where_the_alert_has_none():
    """A crossover is a symbol-level fact and a gamma flip a book-level one.

    ``None`` and not ``0``: a zero strike would be spoken as a real contract,
    and a ``dte`` of 0 specifically means 0DTE — the one value that must never
    be manufactured out of an absence.
    """
    for a in (_XO, _GF):
        row = flow.alert_rows({"alerts": [a]})[0]
        assert row["strike"] is None
        assert row["expiry"] is None
        assert row["dte"] is None


def test_kind_labels_are_whole_words():
    """UI labels spell things out; 'UOA' means nothing at a glance.

    The four labels themselves are asserted by
    ``test_alert_kinds_say_what_happened_not_which_detector_fired``; what is
    unique here is that a row REACHES one, and that an unknown type still names
    itself rather than rendering blank."""
    for a in (_XO, _UOA, _GF):
        assert flow.alert_kind_label(a) == flow._KIND_LABEL[a["type"]]
        assert " " in flow.alert_kind_label(a) or len(
            flow.alert_kind_label(a)) > 4
    assert flow.alert_kind_label({}) == "Flow"


def test_side_labels_read_directionally():
    """The words are pinned by the two vocabulary tests below; this one holds
    the wiring and the EMPTY fallback — a side the map does not know renders as
    nothing, never as a made-up direction."""
    assert flow.side_label(_XO) == "Calls over"
    assert flow.side_label(_UOA) == "Call"
    assert flow.side_label(_GF) == "Now amplifying"
    assert flow.side_label({}) == ""


def test_detail_cells_are_type_specific():
    assert flow.alert_detail(_XO) == "$1.20M calls vs $400k puts"
    assert flow.alert_detail(_UOA) == "0DTE 737C · 12,400 vol / 1,100 OI (11.3×) · $2.13M"
    assert flow.alert_detail(_GF) == "spot 6412 vs flip 6400"


def test_detail_is_total_over_missing_fields():
    assert flow.alert_detail({"type": "uoa"}) == ""
    assert flow.alert_detail({"type": "crossover"}) == ""
    assert flow.alert_detail({"type": "gamma_flip"}) == ""
    assert flow.alert_detail({"type": "big_delta"}) == ""
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


# ── Task 6: big_delta on the Flow Alerts screen ──────────────────────────────
def test_kind_filter_includes_big_delta():
    """The Type multiselect is built off _KIND_LABEL -- big_delta must be a member
    so the screen can filter on it (and shows a real word, not the generic 'Flow'
    fallback that untyped rows get)."""
    assert "big_delta" in flow._KIND_LABEL
    assert flow.alert_kind_label(_BD) not in ("", "Flow")


def test_tone_class_big_delta_is_a_distinct_hue():
    """big_delta isn't bullish/bearish call-vs-put like UOA/crossover -- it gets
    its own hue, distinct from both the pos/neg palette and the neutral fallback."""
    call_cls = flow.tone_class(_BD)
    put_cls = flow.tone_class({**_BD, "side": "put"})
    assert call_cls != flow._TONE_NEUTRAL and put_cls != flow._TONE_NEUTRAL
    assert call_cls != put_cls
    assert "emerald" not in call_cls and "rose" not in call_cls
    assert "emerald" not in put_cls and "rose" not in put_cls


def test_detail_big_delta_shows_notional_and_pct_of_gross():
    d = flow.alert_detail(_BD)
    assert "of gross" in d and "24%" in d
    assert "100" in d and "C" in d


def test_alert_rows_build_end_to_end_for_big_delta():
    row = flow.alert_rows({"alerts": [_BD]})[0]
    assert row["_kind_key"] == "big_delta"
    assert row["symbol"] == "SPY"
    assert "of gross" in row["detail"]
    assert row["_tone_class"] != flow._TONE_NEUTRAL


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
    cold = flow.status_text(None)
    quiet = flow.status_text({"date": "2026-08-09", "alerts": []})
    assert cold != quiet
    assert "hasn't published" in cold          # the feed
    assert "has traded" in quiet               # the market
    assert flow.status_text(_VIEW) == "3 alerts today · 2026-08-09"
    assert flow.status_text({"date": "2026-08-09", "alerts": [_XO]}) == "1 alert today · 2026-08-09"


# ── table + handoff ──────────────────────────────────────────────────────────
def test_flow_columns_are_sortable_and_ordered():
    names = [c["name"] for c in flow.flow_columns()]
    assert names == ["time", "age", "symbol", "kind", "side", "detail", "share", "text"]
    assert all(c["sortable"] for c in flow.flow_columns())
    share = next(c for c in flow.flow_columns() if c["name"] == "share")
    assert share["field"] == "share_pct" and share["align"] == "right"


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


# ── big_delta Share column ───────────────────────────────────────────────────
def test_share_pct_is_numeric_for_big_delta_only():
    assert flow._share_pct(_BD) == 24.0                                  # 0.24 -> 24.0
    assert flow._share_pct(_UOA) is None                                 # other types
    assert flow._share_pct({"type": "big_delta"}) is None               # missing share
    assert flow._share_pct({"type": "big_delta", "pct_of_gross": None}) is None
    assert flow._share_pct(None) is None


def test_alert_rows_stamp_share_for_big_delta():
    """Share is stamped numeric for big_delta (so the column sorts by conviction)
    and left None for the other types (renders blank / sorts to one end)."""
    rows = {r["symbol"]: r for r in flow.alert_rows({"date": "d", "alerts": [_UOA, _BD]})}
    assert rows["SPY"]["share_pct"] == 24.0
    assert rows["QQQ"]["share_pct"] is None


# ── the alert vocabulary names the EVENT, not the detector ───────────────────
def test_alert_kinds_say_what_happened_not_which_detector_fired():
    """"Big delta · Call" tells a reader which of the four things options_svc
    runs produced the row. It does not tell them what the market did, which is
    the only reason the row is on screen."""
    assert flow.alert_kind_label({"type": "crossover"}) == "Premium shift"
    assert flow.alert_kind_label({"type": "uoa"}) == "Unusual volume"
    # A NOUN phrase, not "Hedging flipped": voice speaks a contract-less alert
    # as "<kind> alert", and a gamma flip always takes that path.
    assert flow.alert_kind_label({"type": "gamma_flip"}) == "Hedging flip"
    assert flow.alert_kind_label({"type": "big_delta"}) == "Outsized bet"


def test_the_gamma_sides_moved_with_their_kind():
    """"Hedging flipped · To positive" would be LESS legible than the name it
    replaced: "to positive" is only interpretable once you know the subject is
    gamma sign, and that is exactly the word the new kind name removes."""
    assert flow.side_label({"side": "to_positive"}) == "Now damping"
    assert flow.side_label({"side": "to_negative"}) == "Now amplifying"


def test_the_call_put_sides_are_untouched():
    """The "call or put, never bought or sold" caveat this page owes its reader
    depends on these staying exactly that literal — Schwab publishes no
    time-and-sales tape, so nobody here knows who initiated."""
    assert flow.side_label({"side": "calls_over"}) == "Calls over"
    assert flow.side_label({"side": "puts_over"}) == "Puts over"
    assert flow.side_label({"side": "call"}) == "Call"
    assert flow.side_label({"side": "put"}) == "Put"


def test_the_raw_payload_keys_are_NOT_renamed():
    """The keys are the options_svc contract, the config/flow_alerts.toml
    section names and _TONE's own keys. Renaming a WORD is this page's business;
    renaming a KEY would be a cross-tier migration for no reader's benefit."""
    assert set(flow._KIND_LABEL) == {"crossover", "uoa", "gamma_flip",
                                     "big_delta"}
    assert {t for t, _s in flow._TONE} == set(flow._KIND_LABEL)


# ── column headers, matched to the Desk's words ──────────────────────────────
def test_column_labels_match_the_desks_words_for_the_same_columns():
    """The Desk's flow panel prints these same two quantities. One number
    labelled two ways on two screens is the drift the Desk pass just closed."""
    labels = {c["name"]: c["label"] for c in flow.flow_columns()}
    assert labels["kind"] == "Alert type"
    assert labels["detail"] == "What traded"
    # "Alert" sat beside "Alert type" and named a different thing.
    assert labels["text"] == "Summary"
    # "Share" alone never said share OF WHAT.
    assert labels["share"] == "Share of flow"


# ── the status line ──────────────────────────────────────────────────────────
def test_the_cold_status_line_matches_the_desks_word_for_word():
    """Both now resolve to ``pages.copy.WAITING_OPTIONS``, so this holds by
    construction rather than by discipline.

    It was a guarded COPY until the Opportunity Board became the third screen
    showing this sentence — ``pages.desk`` imports this module, so importing
    back is a cycle, and a restated literal plus this test was the answer for
    two copies. Three earned a leaf both sides can reach. The assertion stays:
    it is now what catches somebody giving one of the three a literal again."""
    from pages import desk
    assert flow.status_text(None) == desk.WAITING_OPTIONS
    assert flow.status_text({}) == desk.WAITING_OPTIONS


def test_a_quiet_day_describes_the_MARKET_not_the_page():
    """"No flow alerts yet today" reads as a page that has nothing. "Nothing
    unusual has traded yet today" reads as a market that has done nothing —
    which is the true statement, and already the Desk's wording for its own
    empty flow panel."""
    line = flow.status_text({"date": "2026-08-09", "alerts": []})
    assert line.startswith("Nothing unusual has traded yet today")
    assert "2026-08-09" in line


def test_a_busy_day_still_counts_and_still_dates_itself():
    assert flow.status_text({"date": "2026-08-09", "alerts": [_XO]}) == \
        "1 alert today · 2026-08-09"
    assert flow.status_text({"date": "2026-08-09", "alerts": [_XO, _UOA]}) == \
        "2 alerts today · 2026-08-09"


def test_the_flow_help_calls_the_alerts_what_the_screen_calls_them():
    """Present-and-absent, because ``term in text`` alone cannot catch a rename
    that reached the screen and stopped at the hover guide."""
    import page_help
    text = page_help.HELP_MD["/options/flow"]
    for label in flow._KIND_LABEL.values():
        assert f"**{label}**" in text, label
    for gone in ("**Crossover**", "**Unusual activity**", "**Gamma flip**",
                 "**Big delta**", "the **Share** column"):
        assert gone not in text, gone

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
    assert flow.alert_detail(_XO) == "$1.20M calls vs $400.00k puts"
    assert flow.alert_detail(_UOA) == "0DTE 737.00C · 12,400 vol / 1,100 OI (11.27×) · $2.13M"
    assert flow.alert_detail(_GF) == "spot 6,412.00 vs flip 6,400.00"


def test_detail_is_total_over_missing_fields():
    assert flow.alert_detail({"type": "uoa"}) == ""
    assert flow.alert_detail({"type": "crossover"}) == ""
    assert flow.alert_detail({"type": "gamma_flip"}) == ""
    assert flow.alert_detail({"type": "big_delta"}) == ""
    assert flow.alert_detail(None) == ""
    assert flow.alert_detail({"type": "who_knows"}) == ""


def test_detail_dated_expiry_when_not_zero_dte():
    a = dict(_UOA, dte=2, expiry="2026-08-11", side="put")
    assert flow.alert_detail(a).startswith("08/11 737.00P · ")


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
    assert "of gross" in d and "24.00%" in d
    assert "100.00C" in d


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


# ── HIRO (the hedging-flow model) on the Flow Alerts screen ─────────────────
_HS = {"type": "hiro_surge", "side": "dealers_buying", "symbol": "$SPX", "ts": 1,
       "spot": 5712.5, "impact": 2.4e9, "mult": 3.6, "window_min": 15,
       "unclassified_share": 0.18, "id": "x1", "text": "t"}
_HF = {"type": "hiro_flip", "side": "to_selling", "symbol": "SPY", "ts": 1,
       "spot": 571.2, "cum": -3.1e8, "id": "x2", "text": "t"}


def test_hiro_labels_and_tones():
    assert flow.alert_kind_label(_HS) == "Hedging surge"
    assert flow.alert_kind_label(_HF) == "Hedging reversal"
    assert flow.side_label(_HS) == "Dealers buying"
    assert flow.side_label(_HF) == "Now selling"
    assert flow.tone_class(_HS) == "text-emerald-400"
    assert flow.tone_class(_HF) == "text-rose-400"


def test_hiro_the_other_two_sides_label_and_tone_the_other_way():
    assert flow.side_label({**_HS, "side": "dealers_selling"}) == "Dealers selling"
    assert flow.side_label({**_HF, "side": "to_buying"}) == "Now buying"
    assert flow.tone_class({**_HS, "side": "dealers_selling"}) == "text-rose-400"
    assert flow.tone_class({**_HF, "side": "to_buying"}) == "text-emerald-400"


def test_hiro_detail_cells():
    assert flow.alert_detail(_HS) == \
        "≈$2.40B in 15 min · 3.60× normal · 18.00% unlabelled · model"
    assert flow.alert_detail(_HF) == "running total ≈ -$310.00M · spot 571.20 · model"
    assert flow.alert_detail({"type": "hiro_surge"}) == ""
    assert flow.alert_detail({"type": "hiro_flip"}) == ""


def test_hiro_detail_says_model_on_every_surface():
    """The Desk flow panel and the Symbol page draw ``detail`` and not the
    alert text, so the "it is a model" qualifier has to live in the detail."""
    assert flow.alert_detail(_HS).endswith(" · model")
    assert flow.alert_detail(_HF).endswith(" · model")
    assert flow.alert_detail({**_HF, "cum": 2.0e8}) == \
        "running total ≈$200.00M · spot 571.20 · model"


def test_hiro_detail_never_prints_an_invented_zero():
    """A missing reading drops its clause; it never renders as 0% / spot 0."""
    no_share = {k: v for k, v in _HS.items() if k != "unclassified_share"}
    assert flow.alert_detail(no_share) == "≈$2.40B in 15 min · 3.60× normal · model"
    assert "0%" not in flow.alert_detail({**_HS, "unclassified_share": None})
    assert flow.alert_detail({**_HF, "spot": None}) == \
        "running total ≈ -$310.00M · model"


def test_hiro_detail_treats_non_finite_numbers_as_missing():
    nan, inf = float("nan"), float("inf")
    assert flow.alert_detail({**_HS, "impact": nan}) == ""
    assert flow.alert_detail({**_HS, "unclassified_share": nan}) == \
        "≈$2.40B in 15 min · 3.60× normal · model"
    assert flow.alert_detail({**_HF, "cum": inf}) == ""
    assert flow.alert_detail({**_HF, "spot": nan}) == \
        "running total ≈ -$310.00M · model"
    assert flow.alert_detail({**_HS, "impact": True}) == ""      # a bool is no reading


def test_hiro_money_signs_and_scales():
    assert flow._hiro_money(2.4e9) == "$2.40B"
    assert flow._hiro_money(-3.1e8) == "-$310.00M"
    assert flow._hiro_money(-4_000) == "-$4.00k"
    assert flow._hiro_money(None) == ""
    assert flow._hiro_money(float("nan")) == ""


def test_hiro_money_never_prints_minus_zero():
    """A value that rounds to zero carries no sign: "-$0.00" reads as a
    direction. At two places that is anything under half a cent."""
    assert flow._hiro_money(-0.004) == "$0.00"
    assert flow._hiro_money(-0.0) == "$0.00"
    assert flow._hiro_money(0.003) == "$0.00"
    assert flow._hiro_money(-0.4) == "-$0.40"
    assert flow._hiro_money(-0.6) == "-$0.60"


def test_alert_rows_build_end_to_end_for_hiro():
    rows = {r["_kind_key"]: r for r in flow.alert_rows({"alerts": [_HS, _HF]})}
    hs, hf = rows["hiro_surge"], rows["hiro_flip"]
    assert hs["symbol"] == "$SPX" and hs["kind"] == "Hedging surge"
    assert hs["detail"].startswith("≈$2.40B in 15 min")
    assert hs["_tone_class"] == flow._TONE_POS
    assert hf["symbol"] == "SPY" and hf["kind"] == "Hedging reversal"
    assert hf["detail"].startswith("running total ≈ -$310.00M")
    assert hf["_tone_class"] == flow._TONE_NEG
    # A model of the stock hedge, not a contract: nothing to speak as a contract.
    assert hs["strike"] is None and hs["dte"] is None and hs["share_pct"] is None


# ── the service's quiet/public flags ─────────────────────────────────────────
def _public(monkeypatch, on):
    import shell
    monkeypatch.setattr(shell, "is_public", lambda: on)


def test_rows_carry_the_quiet_flag_only_when_it_is_really_true():
    rows = {r["id"]: r for r in flow.alert_rows({"alerts": [
        {**_HS, "id": "q", "quiet": True}, {**_HS, "id": "l", "quiet": False},
        {**_HS, "id": "s", "quiet": "true"}, _XO]})}
    assert rows["q"]["quiet"] is True
    assert rows["l"]["quiet"] is False
    assert rows["s"]["quiet"] is False           # only a real True silences
    assert rows[_XO["id"]]["quiet"] is False     # no key: speaks as before


def test_public_screens_hide_an_alert_marked_not_public(monkeypatch):
    _public(monkeypatch, True)
    view = {"alerts": [{**_HS, "id": "hidden", "public": False},
                       {**_HF, "id": "shown", "public": True}, _XO]}
    assert {r["id"] for r in flow.alert_rows(view)} == {"shown", _XO["id"]}


def test_the_private_app_shows_every_alert(monkeypatch):
    _public(monkeypatch, False)
    view = {"alerts": [{**_HS, "id": "hidden", "public": False}, _XO]}
    assert {r["id"] for r in flow.alert_rows(view)} == {"hidden", _XO["id"]}


def test_the_public_status_line_counts_only_what_it_shows(monkeypatch):
    """"2 alerts today" over a table of one would leak the hidden alert."""
    view = {"date": "2026-10-01",
            "alerts": [{**_HS, "id": "hidden", "public": False}, _XO]}
    _public(monkeypatch, True)
    assert flow.status_text(view) == "1 alert today · 2026-10-01"
    only_hidden = {"date": "2026-10-01",
                   "alerts": [{**_HS, "id": "hidden", "public": False}]}
    assert flow.status_text(only_hidden).startswith("Nothing unusual has traded")
    _public(monkeypatch, False)
    assert flow.status_text(view) == "2 alerts today · 2026-10-01"


def _capture(monkeypatch, cookies):
    import shell
    monkeypatch.setattr(shell, "is_public", lambda: False)
    monkeypatch.setattr(shell, "_request_cookies", lambda: cookies)


def test_a_capture_session_hides_non_public_alerts_in_the_private_app(monkeypatch):
    """tools/capture_gallery_shots.py photographs the PRIVATE app for the public
    gallery, so its render hides what the public origin hides."""
    view = {"alerts": [{**_HS, "id": "hidden", "public": False}, _XO]}
    _capture(monkeypatch, {"ns_capture": "1"})
    assert {r["id"] for r in flow.alert_rows(view)} == {_XO["id"]}


def test_a_stray_capture_cookie_hides_nothing(monkeypatch):
    view = {"alerts": [{**_HS, "id": "hidden", "public": False}, _XO]}
    for cookies in ({"ns_capture": ""}, {"ns_capture": "0"}, None):
        _capture(monkeypatch, cookies)
        assert {r["id"] for r in flow.alert_rows(view)} == {"hidden", _XO["id"]}


def test_kind_options_drop_hidden_hiro_kinds_only_while_hiding():
    rows = flow.alert_rows({"alerts": [_XO]})
    full = flow.kind_options(rows, hiding=False)
    assert full == flow._KIND_LABEL
    hidden = flow.kind_options(rows, hiding=True)
    assert "hiro_surge" not in hidden and "hiro_flip" not in hidden
    assert set(hidden) == set(flow._KIND_LABEL) - {"hiro_surge", "hiro_flip"}


def test_kind_options_keep_a_hiro_kind_that_is_visible():
    """Once [hiro].public is on, a visible HIRO row brings its kind back."""
    rows = flow.alert_rows({"alerts": [{**_HS, "public": True}]})
    opts = flow.kind_options(rows, hiding=True)
    assert "hiro_surge" in opts and "hiro_flip" not in opts


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
                                     "big_delta", "hiro_surge", "hiro_flip"}
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


# ── the page kit (2026-09-19 consistency standard) ───────────────────────────

def test_a_row_click_no_longer_navigates_the_symbol_is_the_link():
    """The standard: a page without a detail panel links the SYMBOL cell and
    leaves the row click alone."""
    import inspect
    from pages.options import flow
    src = inspect.getsource(flow.render)
    assert '"rowClick"' not in src
    assert "GAMMA_EVENT" in src and "gamma_symbol_slot" in src


def test_the_symbol_link_is_only_drawn_where_it_can_go():
    from pages.options import flow
    assert "@click" in flow.gamma_symbol_slot(True)
    assert "@click" not in flow.gamma_symbol_slot(False)


# ── the Alert type chips, and remembering them ──────────────────────────────
def test_hidden_kinds_parse_keeps_only_known_kinds():
    assert flow.parse_hidden_kinds(["uoa", "retired_kind", 7]) == {"uoa"}
    for junk in (None, "uoa", 3, {"uoa": True}):
        assert flow.parse_hidden_kinds(junk) == frozenset()


def test_toggle_kind_flips_one_and_all_shows_everything():
    h = flow.toggle_kind(frozenset(), "uoa")
    assert h == {"uoa"}
    assert flow.toggle_kind(h, "uoa") == frozenset()
    assert flow.toggle_kind({"uoa", "crossover"}, None) == frozenset()


def test_a_kind_not_in_the_saved_set_is_shown():
    """The store holds what was switched OFF, so a kind the saved choice never
    mentioned — a new detector — arrives shown, not silently hidden."""
    shown = flow.shown_kinds(flow.parse_hidden_kinds(["uoa"]))
    assert shown == set(flow._KIND_LABEL) - {"uoa"}
    rows = flow.alert_rows(_VIEW)
    assert {r["_kind_key"] for r in flow.filter_rows(rows, shown, None)} == {
        "crossover", "gamma_flip"}


def test_kind_chips_count_per_symbol_and_keep_zero_chips():
    rows = flow.alert_rows({"alerts": [_XO, _UOA, _GF, _BD]})
    opts = flow.kind_options(rows, hiding=False)
    chips = flow.kind_chips(rows, opts, frozenset({"uoa"}), None)
    assert [c[0] for c in chips] == list(opts)            # picker order
    by = {k: (n, a) for k, _l, n, a in chips}
    assert by["crossover"] == (1, True) and by["uoa"] == (1, False)
    assert by["hiro_flip"] == (0, True)                    # zero still drawn
    spy = {k: n for k, _l, n, _a in flow.kind_chips(rows, opts, frozenset(), "SPY")}
    assert spy["crossover"] == 1 and spy["big_delta"] == 1 and spy["uoa"] == 0


def test_the_hidden_kinds_setting_defaults_to_nothing_hidden():
    import app_settings
    assert app_settings.DEFAULTS[flow.HIDDEN_KINDS_KEY] == []


def test_the_status_line_says_when_the_filter_hides_rows():
    base = "9 alerts today · 2026-10-02"
    assert flow.filtered_status(base, 9, 5) == f"{base} · 5 shown"
    assert flow.filtered_status(base, 9, 9) == base
    assert flow.filtered_status(base, 0, 0) == base

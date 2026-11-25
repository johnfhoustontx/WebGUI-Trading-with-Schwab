"""Tests for the Portfolio page pure display builders + render API.

The engine orchestration + formatting live in ``services/portfolio_svc``; this
page is a Tier-3 reader that renders the service's already-formatted rows. Only
its pure transforms (status badges, suggestion detail, column defs) and the
``render`` callable are exercised here.
"""
from pages import portfolio


def test_proxy_status():
    up_text, up_color = portfolio.proxy_status({"proxy_up": True})
    assert "up" in up_text.lower() and up_color == portfolio.UP_COLOR
    down_text, down_color = portfolio.proxy_status({"proxy_up": False})
    assert "down" in down_text.lower() and down_color == portfolio.DOWN_COLOR
    assert portfolio.proxy_status(None)[0]  # tolerates None payload


def test_stream_status():
    live_text, live_color = portfolio.stream_status({"streaming": True})
    assert "live" in live_text.lower() and live_color == portfolio.UP_COLOR
    off_text, off_color = portfolio.stream_status({"streaming": False})
    assert off_color == portfolio.MUTED_COLOR


def test_proxy_stream_status_classes():
    # proxy: up -> TXT_UP, down -> TXT_DOWN
    up_text, up_cls = portfolio.proxy_status_class({"proxy_up": True})
    assert "up" in up_text.lower() and up_cls == portfolio.TXT_UP
    down_text, down_cls = portfolio.proxy_status_class({"proxy_up": False})
    assert "down" in down_text.lower() and down_cls == portfolio.TXT_DOWN
    # stream: live -> TXT_UP, else -> TXT_MUTED
    live_text, live_cls = portfolio.stream_status_class({"streaming": True})
    assert "live" in live_text.lower() and live_cls == portfolio.TXT_UP
    _off_text, off_cls = portfolio.stream_status_class({"streaming": False})
    assert off_cls == portfolio.TXT_MUTED
    # the remove-set contains all three local text classes
    for cls in (portfolio.TXT_UP, portfolio.TXT_DOWN, portfolio.TXT_MUTED):
        assert cls in portfolio.STATUS_TEXT_CLASSES


def test_suggestion_text_joins_reasons():
    suggestions = {"AAPL": [{"action": "TRIM", "reason": "too big"},
                            {"action": "HOLD", "reason": "ok"}]}
    txt = portfolio.suggestion_text(suggestions, "AAPL")
    assert "[TRIM] too big" in txt
    assert "[HOLD] ok" in txt


def test_suggestion_text_empty_and_unselected():
    assert "no suggestions" in portfolio.suggestion_text({}, "AAPL").lower()
    assert "no suggestions" in portfolio.suggestion_text({"AAPL": []}, "AAPL").lower()
    # nothing selected -> a prompt, not a crash
    assert "select" in portfolio.suggestion_text({}, None).lower()


def test_columns_have_required_keys():
    for cols in (portfolio.HOLDINGS_COLS, portfolio.SECTOR_COLS,
                 portfolio.PERF_COLS):
        assert cols
        assert all({"name", "label", "field"} <= set(c) for c in cols)


def test_status_line_counts_holdings():
    line = portfolio.status_line({"holdings_rows": [{"symbol": "AAPL"},
                                                    {"symbol": "MSFT"}],
                                  "errors": []})
    assert "2" in line
    # an error surfaces in the status line
    err_line = portfolio.status_line({"holdings_rows": [], "errors": ["down"]})
    assert "down" in err_line


def test_status_line_tolerates_none():
    assert isinstance(portfolio.status_line(None), str)


def test_render_is_callable():
    assert callable(portfolio.render)


# ── Portfolio on the page kit (Phase 5, Task 7) ──────────────────────────────
# The page called itself "Portfolio Analyzer" while the rail and the breadcrumb
# said Portfolio; it carried a description line the hover help already gives;
# its Refresh was a raw BTN_3D button with a "Refreshing…" label beside it; and
# its three tables were raw ``ui.table``s whose unaligned columns Quasar
# rendered RIGHT by default — including four LETTER GRADES.
import inspect
import pathlib

from nicegui import ui

from pages import ui_kit as kit
from pages.options import theme as _t


def _src():
    return (pathlib.Path(__file__).resolve().parents[1] / "pages"
            / "portfolio.py").read_text(encoding="utf-8")


def _render():
    """Render Portfolio and return ONLY the elements IT built.

    ``ui.context.client.elements`` is the auto-index client the whole module
    shares, so a plain ``elements.values()`` also hands back widgets another
    test's render left behind, and an assertion about "the buttons on this
    page" then passes off someone else's page (the Phase 3 Task 1
    measurement). Diffing the ids around the render is what scopes it."""
    before = set(ui.context.client.elements)
    with ui.card():
        portfolio.render()
    return [e for i, e in ui.context.client.elements.items() if i not in before]


def _ancestors(el):
    out = []
    slot = getattr(el, "parent_slot", None)
    while slot is not None:
        out.append(slot.parent)
        slot = getattr(slot.parent, "parent_slot", None)
    return out


def _buttons(els):
    return {e.text: e for e in els if isinstance(e, ui.button)}


class TestThePortfolioFrameIsTheKits:
    def test_the_title_is_the_navs_word_and_the_blurb_is_gone(self):
        """The rail, the breadcrumb and the page help all say Portfolio; only
        the page said "Portfolio Analyzer". The description line under it is
        what ``page_help`` is for — no page in the standard carries one."""
        src = inspect.getsource(portfolio.render)
        assert "kit.page()" in src
        assert 'kit.header("Portfolio", view=VIEW, stale=False)' in src
        assert "Portfolio Analyzer" not in src
        assert "Sector breakdown, vs-sector performance" not in src

    def test_the_stamp_never_goes_amber_because_the_threshold_is_contradicted(self):
        """``stale=False`` is a decision, not a default: ``alerts.py`` measures
        this view's off-hours cadence twice and the two disagree — 620 s at
        line 116, 23 s at line 146, on the SAME Sunday. A stamp that may go
        amber nightly on a healthy stack is worse than no stamp, and the nav
        badge's threshold is where that contradiction gets settled."""
        assert "stale=False" in inspect.getsource(portfolio.render)
        assert portfolio.VIEW == "portfolio:positions"

    def test_refresh_is_a_page_action_on_the_header_line(self):
        els = _render()
        btns = _buttons(els)
        assert "Refresh" in btns
        refresh = btns["Refresh"]
        header_row = _ancestors(refresh)[1]      # actions row -> header row
        title = next(e for e in els
                     if isinstance(e, ui.label) and e.text == "Portfolio")
        assert header_row in _ancestors(title), \
            "Refresh belongs in the header's actions row, beside the stamp"

    def test_refresh_holds_its_own_spinner_instead_of_a_status_word(self):
        """The "Refreshing…" write duplicated the spinner AND overwrote the
        holdings count, which nothing put back until the next repaint."""
        src = _src()
        assert "kit.set_busy(" in src
        assert "Refreshing" not in src
        els = _render()
        refresh = _buttons(els)["Refresh"]
        kit.set_busy(refresh)
        assert not refresh.enabled and "loading" in refresh.props

    def test_the_holdings_count_is_the_kits_status_line(self):
        els = _render()
        lines = [e for e in els if isinstance(e, ui.label)
                 and " ".join(e.classes) == _t.EYEBROW]
        assert any("holding(s)" in (e.text or "") for e in lines)

    def test_the_page_builds_no_raw_button_or_table_of_its_own(self):
        src = _src()
        assert "ui.button(" not in src
        assert "ui.table(" not in src


class TestThePortfolioTablesAreTheKits:
    def test_every_data_column_sorts(self):
        """First time for all three — the Momentum-leaderboard precedent."""
        for cols, numeric in ((portfolio.HOLDINGS_COLS, portfolio.HOLDINGS_NUMERIC),
                              (portfolio.OPTION_COLS, portfolio.OPTION_NUMERIC),
                              (portfolio.SECTOR_COLS, portfolio.SECTOR_NUMERIC),
                              (portfolio.PERF_COLS, portfolio.PERF_NUMERIC)):
            for c in kit.table_columns(cols, numeric=numeric):
                assert c["sortable"] is True, c["name"]

    def test_the_numbers_are_right_and_everything_else_is_left(self):
        """Quasar's column default is RIGHT, the kit's is LEFT — so a numeric
        column not named here silently moves left on this migration."""
        for cols, numeric in ((portfolio.HOLDINGS_COLS, portfolio.HOLDINGS_NUMERIC),
                              (portfolio.OPTION_COLS, portfolio.OPTION_NUMERIC),
                              (portfolio.SECTOR_COLS, portfolio.SECTOR_NUMERIC),
                              (portfolio.PERF_COLS, portfolio.PERF_NUMERIC)):
            for c in kit.table_columns(cols, numeric=numeric):
                want = "right" if c["name"] in numeric else "left"
                assert c["align"] == want, c["name"]

    def test_the_numeric_sets_name_every_column_the_service_formats_as_a_number(self):
        """Measured against ``portfolio-analyzer/src/view_model.py``: currency,
        signed currency, signed percent, weight percent and the share count."""
        assert portfolio.HOLDINGS_NUMERIC == (
            "quantity", "price", "change_pct", "trade_price", "total_pl",
            "total_pl_pct", "day_pl")
        assert portfolio.SECTOR_NUMERIC == ("weight", "benchmark_delta")
        assert portfolio.PERF_NUMERIC == (
            "composite", "ann_return", "vs_sector", "drawdown")

    def test_the_four_letter_grades_are_text_and_move_LEFT(self):
        """⚠ The plan called all eight Performance columns numeric. Four are
        not: ``view_model._grade_cell`` renders a 0–4 score as a LETTER
        (``evaluation.grade_letter`` → F..A) or an em dash, so Return /
        Capital / Risk / Entry hold a single character and take the same left
        alignment every other text column in the app takes. ``composite`` stays
        right — it is ``"3.4 (A)"``, a magnitude with a fixed-width suffix, so
        the decimal points still line up."""
        grades = ("grade_return", "grade_capital", "grade_risk", "grade_execution")
        for name in grades:
            assert name not in portfolio.PERF_NUMERIC
        aligned = {c["name"]: c["align"] for c
                   in kit.table_columns(portfolio.PERF_COLS,
                                        numeric=portfolio.PERF_NUMERIC)}
        for name in grades:
            assert aligned[name] == "left", name
        assert aligned["composite"] == "right"

    def test_the_holdings_column_still_counts_SHARES(self):
        """Must-not-change: ``test_shared_copy`` reads these labels, and the
        equity book counts shares, not contracts."""
        labels = {c["label"] for c in portfolio.HOLDINGS_COLS}
        assert "Shares" in labels and "Contracts" not in labels

    def test_the_stream_writes_rows_into_the_element_the_kit_returned(self):
        """The service republishes every throttled stream tick and the page
        sets ``.rows``/``.update()`` in place; ``kit.table`` returns the same
        ``ui.table``, so nothing about the SSE path changes. Its
        ``ROW_CLASS_FN`` reads ``_row_class``/``_selected``, which service rows
        carry neither of — ``undefined``, dropped by ``.filter(Boolean)``."""
        els = _render()
        tables = [e for e in els if isinstance(e, ui.table)]
        # Sectors + Performance; Holdings has no table until an account arrives.
        assert len(tables) == 2
        for t in tables:
            assert t._props[":table-row-class-fn"] == kit.ROW_CLASS_FN


class TestThePortfolioStateColoursAreTheAppsSemantics:
    def test_the_indicator_colours_are_the_theme_state_tokens(self):
        """Proxy up/down and stream live/manual are a STATE reading, not P&L —
        so they take the app's semantic colours instead of three hexes of their
        own. The names survive, which is what the pure-builder tests above are
        written against."""
        assert portfolio.TXT_UP == _t.TXT_POS
        assert portfolio.TXT_DOWN == _t.TXT_NEG
        assert portfolio.TXT_MUTED == _t.MUTED

    def test_the_hex_and_the_class_cannot_drift(self):
        """``proxy_status`` returns a hex and ``proxy_status_class`` maps it to
        a class by comparing against the same constant — so the two halves have
        to be built from one value."""
        assert portfolio.TXT_UP == f"text-[{portfolio.UP_COLOR}]"
        assert portfolio.TXT_DOWN == f"text-[{portfolio.DOWN_COLOR}]"
        assert portfolio.TXT_MUTED == f"text-[{portfolio.MUTED_COLOR}]"
        assert len({portfolio.TXT_UP, portfolio.TXT_DOWN,
                    portfolio.TXT_MUTED}) == 3


def test_the_spinner_survives_the_build_repaint():
    """MUST-NOT-CHANGE, green on both sides of this migration and measured
    before it: portfolio mounted ``build_busy`` on ``panels`` and ``_repaint``
    only sets ``.rows``, so its scrim was never deleted — 1 surviving spinner,
    against the Claude Trades page's 0 on the same probe (that page was
    removed 2026-09-22). ``kit.region`` keeps that property
    by construction: the spinner lives on ``outer`` and only ``content`` is
    ever cleared."""
    els = _render()
    assert any(isinstance(e, ui.spinner) for e in els)


# ── Holdings by account (2026-11-24) ─────────────────────────────────────────

def _group(account="Trading account", shares=1, options=1, other=0):
    return {
        "account": account, "market_value": "+$1,000.00", "day_pl": "+$5.00",
        "total_pl": "-$20.00",
        "shares_rows": [{"row_id": f"{account}:S{i}", "symbol": f"S{i}"}
                        for i in range(shares)],
        "option_rows": [{"row_id": f"{account}:opt:{i}", "underlying": "SPY",
                         "strategy": "Put Credit Spread"} for i in range(options)],
        "other_rows": [{"row_id": f"{account}:O{i}", "symbol": "/ES"}
                       for i in range(other)],
    }


def test_status_line_names_the_account_count_when_there_are_several():
    one = portfolio.status_line({"holdings_rows": [{}], "account_groups": [_group()]})
    assert one == "1 holding(s)"
    two = portfolio.status_line({"holdings_rows": [{}, {}],
                                 "account_groups": [_group(), _group("IRA")]})
    assert two == "2 holding(s) in 2 accounts"


def test_account_layout_ignores_numbers_and_sees_groups_come_and_go():
    a = portfolio.account_layout([_group()])
    b = dict(_group(), market_value="+$9.00")
    assert portfolio.account_layout([b]) == a          # a tick changes no layout
    assert portfolio.account_layout([_group(options=0)]) != a
    assert portfolio.account_layout([_group(), _group("IRA")]) != a
    assert portfolio.account_layout(None) == ()


def test_account_totals_colour_each_pl_by_its_sign():
    g = dict(_group(), day_pl_tone="pos", total_pl_tone="neg")
    assert portfolio.account_totals(g) == [
        ("Free Cash", "—", ""),
        ("Net Liquidity", "—", ""),
        ("Market value", "+$1,000.00", ""),
        ("Day P/L", "+$5.00", portfolio.TXT_UP),
        ("Unrealized P/L", "-$20.00", portfolio.TXT_DOWN),
    ]


def test_account_totals_lead_with_free_cash_and_net_liquidity():
    g = dict(_group(), free_cash="$5,000.00", net_liq="$6,000.00")
    assert portfolio.account_totals(g)[:2] == [
        ("Free Cash", "$5,000.00", ""), ("Net Liquidity", "$6,000.00", "")]


def test_stamp_rows_maps_tones_and_today_to_fixed_classes():
    rows = portfolio.stamp_rows([
        {"day_pl_tone": "pos", "total_pl_tone": "neg", "expires_today": True},
        {"day_pl_tone": "flat", "total_pl_tone": "weird"},
    ])
    assert rows[0]["_day_pl_class"] == portfolio.TXT_UP
    assert rows[0]["_total_pl_class"] == portfolio.TXT_DOWN
    assert rows[0]["_expiry_class"] == portfolio.EXPIRY_TODAY_CLASS
    assert _t.BADGE_WARN in portfolio.EXPIRY_TODAY_CLASS
    # flat, an unknown tone and no flag all colour nothing
    assert (rows[1]["_day_pl_class"], rows[1]["_total_pl_class"],
            rows[1]["_expiry_class"]) == ("", "", "")
    assert portfolio.stamp_rows(None) == []


def test_the_pl_and_expiry_slots_bind_a_class_never_a_style():
    for tpl in (portfolio._PL_SLOT.format(field="day_pl"), portfolio._EXPIRY_SLOT):
        assert ":class=\"props.row._" in tpl and "style" not in tpl
    assert "props.row._day_pl_class" in portfolio._PL_SLOT.format(field="day_pl")
    assert "{{ props.value }}" in portfolio._PL_SLOT.format(field="day_pl")


def test_the_option_columns_are_the_services_keys():
    # ``view_model.OPTION_COLUMNS`` / ``SHARE_COLUMNS`` (portfolio-analyzer's
    # own test pins those headers): the page's fields are the service's keys.
    from_service = ["name", "strikes", "expiry", "contracts", "price",
                    "change_pct", "trade_price", "total_pl", "total_pl_pct",
                    "day_pl"]
    assert [c["field"] for c in portfolio.OPTION_COLS] == from_service
    assert [c["field"] for c in portfolio.HOLDINGS_COLS] == [
        "symbol", "name", "quantity", "price", "change_pct", "trade_price",
        "total_pl", "total_pl_pct", "day_pl"]
    assert [c["label"] for c in portfolio.HOLDINGS_COLS] == [
        "Symbol", "Name", "Shares", "Current price", "Change %", "Trade Price",
        "Unrealized P/L $", "Unrealized P/L %", "Day P/L"]


def _render_with(monkeypatch, payload):
    import bus_client
    monkeypatch.setattr(bus_client, "read_version", lambda view: 1)
    monkeypatch.setattr(bus_client, "read", lambda view: payload)
    return _render()


def test_each_account_is_a_card_with_its_shares_and_options_tables(monkeypatch):
    els = _render_with(monkeypatch, {
        "holdings_rows": [{}, {}],
        "account_groups": [_group("Trading account"),
                           _group("IRA", shares=2, options=0, other=1)],
    })
    texts = [e.text for e in els if isinstance(e, ui.label)]
    assert "Trading account" in texts and "IRA" in texts
    assert texts.count("Shares") == 2
    assert texts.count("Options") == 1                  # the IRA holds none
    assert texts.count("Other") == 1
    tables = [e for e in els if isinstance(e, ui.table)]
    # 2 + 2 account tables, plus Sectors and Performance
    assert len(tables) == 6
    option_tbl = next(t for t in tables if any(
        r.get("strategy") == "Put Credit Spread" for r in t.rows))
    assert [c["name"] for c in option_tbl._props["columns"]] == [
        c["name"] for c in portfolio.OPTION_COLS]
    # the P/L cells are coloured and the expiration can be highlighted
    assert {"body-cell-day_pl", "body-cell-total_pl", "body-cell-expiry"} <= set(
        option_tbl.slots)
    shares_tbl = next(t for t in tables if any(r.get("symbol") == "S0" for r in t.rows))
    assert {"body-cell-day_pl", "body-cell-total_pl"} <= set(shares_tbl.slots)
    assert "body-cell-expiry" not in shares_tbl.slots
    assert all("_day_pl_class" in r for r in option_tbl.rows)


def test_no_accounts_says_so_instead_of_an_empty_table(monkeypatch):
    els = _render_with(monkeypatch, {"holdings_rows": [], "account_groups": []})
    assert "No positions." in [e.text for e in els if isinstance(e, ui.label)]


def test_a_coloured_total_carries_one_colour_class_only(monkeypatch):
    """Two ``text-[...]`` colours on one label resolve by stylesheet order; in
    the harness the plain label colour won and every total read white."""
    g = dict(_group(), day_pl_tone="pos", total_pl_tone="neg")
    els = _render_with(monkeypatch, {"holdings_rows": [{}], "account_groups": [g]})
    by_text = {e.text: e for e in els if isinstance(e, ui.label)}
    assert portfolio.TXT_UP in by_text["+$5.00"].classes
    assert _t.LABEL not in by_text["+$5.00"].classes
    assert portfolio.TXT_DOWN in by_text["-$20.00"].classes
    assert _t.LABEL in by_text["+$1,000.00"].classes



def test_change_and_pl_percent_are_coloured_by_their_sign():
    row = portfolio.stamp_rows([{"change_pct_tone": "neg",
                                 "total_pl_pct_tone": "pos"}])[0]
    assert row["_change_pct_class"] == portfolio.TXT_DOWN
    assert row["_total_pl_pct_class"] == portfolio.TXT_UP
    assert set(portfolio.TONED_FIELDS) == {
        "change_pct", "total_pl", "total_pl_pct", "day_pl"}


def test_the_shares_and_options_columns_line_up():
    shares = portfolio.COLUMN_WIDTHS["HOLDINGS"]
    options = portfolio.COLUMN_WIDTHS["OPTION"]
    assert sum(shares.values()) == 100 and sum(options.values()) == 100
    # every column has a width, and a class for it
    assert set(shares) == {c["name"] for c in portfolio.HOLDINGS_COLS}
    assert set(options) == {c["name"] for c in portfolio.OPTION_COLS}
    assert set(shares.values()) | set(options.values()) <= set(portfolio._W)
    # the six shared figure columns end at the same place: their widths match
    tail = [c["name"] for c in portfolio.OPTION_COLS][-6:]
    assert tail == [c["name"] for c in portfolio.HOLDINGS_COLS][-6:]
    assert [shares[k] for k in tail] == [options[k] for k in tail]
    # and Shares sits over Contracts
    assert shares["quantity"] == options["contracts"]
    cols, _num = portfolio._table_spec("OPTION")
    assert cols[0]["headerClasses"] == portfolio._W[options["name"]]
    assert "table-fixed" in portfolio.ACCOUNT_TABLE_CLASS

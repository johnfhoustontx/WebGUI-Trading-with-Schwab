"""pages/idea_banner.py: the in-app prompt when a trade idea posts."""
import datetime as _dt
import pathlib

from pages import idea_banner as ib

NOW = _dt.datetime(2026, 11, 6, 15, 10, tzinfo=_dt.timezone.utc)      # 09:10 CT
PROMPT = {"banner": True, "banner_max_age_min": 30}
ROOT = pathlib.Path(__file__).resolve().parents[1]


def _posted(**kw):
    p = {"status": "posted", "at": "2026-11-06T09:00:00-06:00",
         "idea": {"label": "PLTR Bull Call Spread", "expiration": "2026-11-21"},
         "ledger": {"status": "booked", "trade_id": "ab12cd34"}}
    p.update(kw)
    return p


def test_a_booked_idea_offers_its_ticket():
    got = ib.banner_for(_posted(), NOW, mode="preview", prompt=PROMPT)
    assert got == {"trade_id": "ab12cd34", "href": "/send-order?trade=ab12cd34",
                   "text": "New trade idea: PLTR Bull Call Spread, 21 Nov 26"}


def test_no_banner_when_it_should_not_show():
    cases = [
        (_posted(), {"mode": "off"}),
        (_posted(), {"prompt": {**PROMPT, "banner": False}}),
        (_posted(status="skipped"), {}),
        (_posted(ledger={"status": "duplicate", "trade_id": None}), {}),
        (_posted(ledger={"status": "booked", "trade_id": "a b"}), {}),
        (_posted(at="2026-11-06T08:30:00-06:00"), {}),          # 40 minutes old
        (_posted(at="2026-11-06T09:00:00"), {}),                # no zone
        (_posted(), {"dismissed": {"ab12cd34"}}),
        (None, {}),
    ]
    for payload, kw in cases:
        args = {"mode": "live", "prompt": PROMPT, **kw}
        assert ib.banner_for(payload, NOW, **args) is None, (payload, kw)


def test_dismissed_ids_are_bounded():
    ib._DISMISSED.clear()
    for i in range(ib._DISMISSED_MAX + 5):
        ib.dismiss(f"t{i}")
    assert len(ib._DISMISSED) == ib._DISMISSED_MAX and "t0" not in ib._DISMISSED
    ib._DISMISSED.clear()


def test_the_banner_lives_in_the_private_layout_only():
    assert "idea_banner" in (ROOT / "main.py").read_text(encoding="utf-8")
    for name in ("live_main.py", "shell.py", "live_screens.py"):
        assert "idea_banner" not in (ROOT / name).read_text(encoding="utf-8"), name

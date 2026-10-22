"""Which options commands run on the slow lane (audit PF-04).

The scaffold gives a service's own queue a second, serial lane for the command
types the service names. The list is the service's: these are the commands that
run for many seconds to minutes (a watchlist scan, a whole-chain Strategy Finder
scan, a Claude briefing), and nothing the page is waiting on a quick answer for.
"""
import pathlib

from services.options_svc import handlers

APP = (pathlib.Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")


def test_every_slow_command_is_a_registered_command():
    assert handlers.SLOW_LANE <= set(handlers._COMMANDS)


def test_the_scans_and_the_briefings_are_on_the_slow_lane():
    assert {"rescan", "swing_scan", "gamma_analyze"} <= handlers.SLOW_LANE


def test_scalp_prob_refit_command_registered_and_slow(monkeypatch):
    import datetime as dt

    from services.options_svc import scalp_prob_fit
    from shared.bus import Bus
    from shared.bus.client import reset_fake_bus
    from shared.contracts.envelope import Command

    reset_fake_bus()
    bus = Bus(fake=True)

    assert "scalp_prob_refit" in handlers._COMMANDS
    assert "scalp_prob_refit" in handlers.SLOW_LANE
    assert "scalp_prob_refit" in handlers._REPLAY_GUARDED
    assert handlers._STALE_WHY["scalp_prob_refit"] == (
        "a replayed refit would fit and store another version for nothing")
    assert "scalp_prob_refit" in (handlers.handle_command.__doc__ or "")
    ran = []
    monkeypatch.setattr(scalp_prob_fit, "refit",
                        lambda *a, **k: ran.append(1) or {"version": 3, "rows": {"a": 5}, "seconds": 0.1})
    fresh = dt.datetime.now(dt.timezone.utc).isoformat()
    handlers.handle_command(bus, Command(type="scalp_prob_refit", args={}, ts=fresh))
    assert ran == [1]
    stale = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1)).isoformat()
    handlers.handle_command(bus, Command(type="scalp_prob_refit", args={}, ts=stale))
    assert ran == [1]                                # a stale replay never refits


def test_no_command_that_changes_a_paper_book_waits_on_the_slow_lane():
    # The whole point: these must never queue behind a scan.
    money = {name for name in handlers._COMMANDS
             if name.startswith(("paper_", "captured_", "rescue", "income_open"))}
    assert money and not (money & handlers.SLOW_LANE)


def test_a_command_whose_follow_up_reads_its_result_stays_on_the_fast_lane():
    # The Calculator and Simulator send a load and then math over what it
    # loaded; splitting them across lanes would let the math run first.
    for family in ("calc_load", "calc_load_expiry", "calc_compute", "calc_iv",
                   "sim_fetch", "sim_fetch_expiry", "sim_run", "sim_replay"):
        assert family not in handlers.SLOW_LANE, family


def test_the_app_hands_the_slow_lane_to_the_scaffold():
    assert "slow_commands=handlers.SLOW_LANE" in APP

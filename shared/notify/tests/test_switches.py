"""config/notify.toml: the per-category channel switches (Settings -> General)."""
from shared.notify import switches as sw


def _use(monkeypatch, path):
    load, _reset = sw._make_loader(path)
    monkeypatch.setattr(sw, "_load", load)


def test_shipped_file_has_every_category_on_and_the_calendar_off():
    from shared.notify.channels import ROUTE_CATEGORIES
    for cat in ROUTE_CATEGORIES:
        assert sw.enabled(cat, "discord") is True, cat
        assert sw.enabled(cat, "telegram") is True, cat
    assert sw.enabled("trade_idea", "calendar") is False


def test_off_in_the_file_reads_off_and_keeps_the_sibling(tmp_path, monkeypatch):
    f = tmp_path / "notify.toml"
    f.write_text("[channels.signals]\ndiscord = false\n")
    _use(monkeypatch, f)
    assert sw.enabled("signals", "discord") is False
    assert sw.enabled("signals", "telegram") is True


def test_malformed_or_unknown_reads_the_default(tmp_path, monkeypatch):
    f = tmp_path / "notify.toml"
    f.write_text('[channels.signals]\ndiscord = "no"\n[channels.flow_uoa]\ncalendar = 1\n')
    _use(monkeypatch, f)
    assert sw.enabled("signals", "discord") is True      # a string is not a bool
    assert sw.enabled("flow_uoa", "calendar") is False   # an int is not a bool
    assert sw.enabled("nope", "telegram") is True        # unknown category: on
    assert sw.enabled("nope", "calendar") is False       # the calendar defaults off


def test_an_unreadable_file_reads_the_defaults(tmp_path, monkeypatch):
    f = tmp_path / "notify.toml"
    f.write_text("[channels.signals\n")                  # a TOML syntax error
    _use(monkeypatch, f)
    assert sw.enabled("signals", "discord") is True


def test_a_local_override_is_seen_without_a_reload(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADING_CONFIG_OVERRIDES_IN_TESTS", "1")
    f = tmp_path / "notify.toml"
    f.write_text("[channels.signals]\ndiscord = true\n")
    _use(monkeypatch, f)
    assert sw.enabled("signals", "discord") is True
    (tmp_path / "local").mkdir()
    (tmp_path / "local" / "notify.toml").write_text("[channels.signals]\ndiscord = false\n")
    assert sw.enabled("signals", "discord") is False


def test_calendar_settings_shipped_values():
    assert sw.calendar_settings() == {"calendar_id": "", "lead_min": 5, "duration_min": 5}


def test_calendar_settings_refuse_bad_values(tmp_path, monkeypatch):
    f = tmp_path / "notify.toml"
    f.write_text('[calendar]\ncalendar_id = " abc@group.calendar.google.com "\n'
                 'lead_min = -3\nduration_min = true\n')
    _use(monkeypatch, f)
    assert sw.calendar_settings() == {"calendar_id": "abc@group.calendar.google.com",
                                      "lead_min": 5, "duration_min": 5}

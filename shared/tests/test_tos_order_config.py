"""shared/tos_order_config.py: the paste script's exits, validated for both tiers."""
import ast
import pathlib

from shared import tos_order_config as toc


def test_the_shipped_settings_are_the_defaults():
    assert toc.settings_from({}) == {**toc.DEFAULTS, "quantity": 1}
    assert toc.settings_from(None)["exit_tif"] == "GTC"


def test_bad_values_fall_back_one_at_a_time():
    got = toc.settings_from({"quantity": 0, "exit_tif": "day",
                             "debit": {"target_pct": 200, "stop_pct": 100},
                             "credit": {"target_pct": True, "stop_pct": 1001}})
    assert got["quantity"] == 1 and got["exit_tif"] == "DAY"
    assert got["debit"] == {"target_pct": 200.0, "stop_pct": 50.0, "trail_pct": 50.0}
    assert got["credit"] == {"target_pct": 50.0, "stop_pct": 100.0, "trail_pct": 100.0}


def test_the_stop_type_and_the_trail():
    got = toc.settings_from({"stop_type": " Fixed ",
                             "debit": {"trail_pct": 99.0},
                             "credit": {"trail_pct": 250}})
    assert got["stop_type"] == "fixed"
    assert got["debit"]["trail_pct"] == 99.0 and got["credit"]["trail_pct"] == 250.0
    # a debit's trail is below the mark, so 100 would put the stop at zero
    bad = toc.settings_from({"stop_type": "trail", "debit": {"trail_pct": 100}})
    assert bad["stop_type"] == "trailing" and bad["debit"]["trail_pct"] == 50.0


def test_the_page_and_the_service_validate_alike(monkeypatch):
    """The Trade panel's Copy button and the live-order service price the same exits."""
    import sys
    root = pathlib.Path(__file__).resolve().parents[2]
    if str(root / "webgui") not in sys.path:
        sys.path.insert(0, str(root / "webgui"))
    from pages.options import tos_order
    raw = {"quantity": 3, "debit": {"target_pct": 80.0, "stop_pct": 40.0}}
    monkeypatch.setattr(tos_order, "load", lambda: raw)
    assert tos_order.settings() == toc.settings_from(raw)


def test_it_imports_what_tier_1_may():
    tree = ast.parse(pathlib.Path(toc.__file__).read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            names.add(node.module)
    assert names == {"math", "repo_paths", "shared.config_toml"}

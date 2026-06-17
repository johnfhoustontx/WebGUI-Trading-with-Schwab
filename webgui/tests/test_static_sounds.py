import pathlib
import main

SOUNDS = pathlib.Path(__file__).resolve().parents[1] / "static" / "sounds"


def test_three_alert_wavs_exist_and_nonempty():
    for name in ("chime", "bell", "ping"):
        p = SOUNDS / f"{name}.wav"
        assert p.exists() and p.stat().st_size > 200, f"missing/empty {name}.wav"


def test_static_route_mounted():
    paths = {str(getattr(r, "path", "")) for r in main.app.routes}
    assert any(p.startswith("/static") for p in paths)

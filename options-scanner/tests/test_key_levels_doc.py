"""The 'What do these levels mean?' menu opens docs/KEY_LEVELS.md — verify it
carries the FlashAlpha interpretation and the new levels."""
from pathlib import Path

DOC = Path(__file__).resolve().parents[1] / "docs" / "KEY_LEVELS.md"


def test_doc_exists():
    assert DOC.exists()


def test_doc_has_max_pain_and_pin():
    text = DOC.read_text(encoding="utf-8")
    assert "Max Pain" in text
    assert "Pin" in text


def test_doc_has_wall_regime_caveat():
    text = DOC.read_text(encoding="utf-8").lower()
    # FlashAlpha's critical rule: a wall is resistance in + gamma, accelerator in -.
    assert "watch the break" in text
    assert "regime" in text

"""Guard tests for the Tailwind design-token vocabulary (Phase 0)."""
from pages.options import theme

TOKENS = ["PAGE", "CARD", "EYEBROW", "LABEL", "MUTED", "BTN", "BTN_PRIMARY", "STRATEGY_BTN"]


def test_tokens_exist_and_are_nonempty_strings():
    for name in TOKENS:
        val = getattr(theme, name)
        assert isinstance(val, str) and val.strip(), f"{name} missing/empty"


def test_tokens_are_class_strings_not_css():
    # A token is a Tailwind utility string applied via .classes() — it must not
    # contain CSS rule syntax (the whole point of the migration).
    for name in TOKENS:
        val = getattr(theme, name)
        assert "{" not in val and ";" not in val, \
            f"{name} looks like CSS, not a class string"


def test_card_token_encodes_navy_palette():
    # Convert + light polish: tokens encode the canonical hex palette.
    assert "#101a30" in theme.CARD and "#213152" in theme.CARD


def test_legacy_dashboard_css_still_present():
    # Phase 0 is additive — existing consumers (Calculator/Simulator/Trade) still
    # reference .calc-card/.cv2-btn until their phases. Do NOT remove yet.
    assert ".calc-card" in theme.DASHBOARD_CSS
    assert ".cv2-btn-primary" in theme.DASHBOARD_CSS


def test_quasar_internal_css_is_internal_only():
    css = theme.QUASAR_INTERNAL_CSS
    # MUST contain the Quasar-internal rules component classes can't reach.
    assert ".q-field__control" in css
    assert ".strat-menu-navy" in css
    # MUST NOT contain the now-tokenized semantic rules.
    assert ".calc-card{" not in css.replace(" ", "")
    assert ".cv2-btn" not in css
    assert ".calc-eyebrow" not in css


STATE_TOKENS = ["TXT_POS", "TXT_WARN", "TXT_NEG", "TXT_NEUTRAL"]


def test_state_color_tokens_exist_and_are_text_classes():
    for name in STATE_TOKENS:
        val = getattr(theme, name)
        assert isinstance(val, str) and val.startswith("text-["), f"{name} not a text-[] class"
        assert "{" not in val and ";" not in val


def test_state_color_tokens_preserve_exact_hex():
    # convert + light polish: exact colors preserved as arbitrary values.
    assert theme.TXT_POS == "text-[#66bb6a]"
    assert theme.TXT_WARN == "text-[#ffa726]"
    assert theme.TXT_NEG == "text-[#ef5350]"
    assert theme.TXT_NEUTRAL == "text-[#bdbdbd]"


BTN3D = ["BTN_3D", "BTN_3D_DANGER"]


def test_btn3d_tokens_are_class_strings():
    for n in BTN3D:
        v = getattr(theme, n)
        assert isinstance(v, str) and v.strip() and "{" not in v and ";" not in v


def test_btn3d_encodes_gradient_and_shadow():
    assert "linear-gradient(180deg" in theme.BTN_3D
    assert theme.BTN_3D.count("shadow-[") >= 1 and "active:" in theme.BTN_3D
    assert "#d33f3f" in theme.BTN_3D_DANGER  # red variant mid-stop

import matplotlib
matplotlib.use("Agg")  # Must come before pyplot import
import matplotlib.pyplot as plt
from matplotlib.colors import TwoSlopeNorm
import gamma_tool


def _sample_rows():
    rows = []
    exps = ["2026-04-29", "2026-04-30", "2026-05-02"]
    for exp in exps:
        for K in (7100.0, 7135.0, 7170.0):
            sign = 1 if K >= 7135 else -1
            rows.append({
                "expiration_date": exp,
                "strike": K,
                "call_gex_usd": 100e6 * sign,
                "put_gex_usd":  30e6,
                "net_gex_usd":  (100e6 * sign) - 30e6,
                "underlying_price": 7135.9,
            })
    return rows


def _colors():
    return dict(
        colormap_neg="#c0392b",
        colormap_mid="#ffffff",
        colormap_pos="#2980b9",
    )


def test_draw_term_heatmap_renders_quadmesh():
    fig, ax = plt.subplots()
    gamma_tool.draw_term_heatmap(ax, _sample_rows(), **_colors())
    # pcolormesh adds one QuadMesh to ax.collections
    assert len(ax.collections) >= 1
    mesh = ax.collections[0]
    # 3 strikes x 3 exps = 9 cells
    assert mesh.get_array().size == 9
    plt.close(fig)


def test_draw_term_heatmap_norm_centered_zero():
    fig, ax = plt.subplots()
    gamma_tool.draw_term_heatmap(ax, _sample_rows(), **_colors())
    norm = ax.collections[0].norm
    assert isinstance(norm, TwoSlopeNorm)
    assert norm.vcenter == 0
    plt.close(fig)


def test_draw_term_heatmap_empty_rows_shows_placeholder():
    fig, ax = plt.subplots()
    gamma_tool.draw_term_heatmap(ax, [], **_colors())
    # Empty -> no crash, placeholder text
    texts = [t.get_text() for t in ax.texts]
    assert any("No data" in t for t in texts)
    # No mesh drawn
    assert len(ax.collections) == 0
    plt.close(fig)


def test_draw_term_heatmap_labels_use_short_date():
    fig, ax = plt.subplots()
    gamma_tool.draw_term_heatmap(ax, _sample_rows(), **_colors())
    xtick_labels = [t.get_text() for t in ax.get_xticklabels()]
    # Format should be like "Apr 29", "Apr 30", "May 02"
    assert "Apr 29" in xtick_labels
    assert "May 02" in xtick_labels
    plt.close(fig)


def test_draw_term_heatmap_strike_axis_descending():
    fig, ax = plt.subplots()
    gamma_tool.draw_term_heatmap(ax, _sample_rows(), **_colors())
    ytick_labels = [t.get_text() for t in ax.get_yticklabels()]
    # Top row = highest strike
    assert ytick_labels[0] == "7170"
    assert ytick_labels[-1] == "7100"
    plt.close(fig)


def test_format_dollars_billions():
    assert gamma_tool._format_dollars(1.234e9) == "1.23 B"
    assert gamma_tool._format_dollars(-2.5e9) == "-2.50 B"


def test_format_dollars_millions():
    assert gamma_tool._format_dollars(530.34e6) == "530.34 M"
    assert gamma_tool._format_dollars(-109.27e6) == "-109.27 M"


def test_format_dollars_thousands():
    assert gamma_tool._format_dollars(500e3) == "500 K"
    assert gamma_tool._format_dollars(-50e3) == "-50 K"


def test_draw_term_heatmap_chrome_param_applies_colors():
    fig, ax = plt.subplots()
    custom_chrome = {
        "plot_bg":  "#abcdef",
        "fg_dim":   "#123456",
        "border":   "#fedcba",
    }
    gamma_tool.draw_term_heatmap(
        ax, _sample_rows(), **_colors(), chrome=custom_chrome)
    from matplotlib.colors import to_hex
    assert to_hex(ax.get_facecolor()) == "#abcdef"
    plt.close(fig)


def test_draw_term_heatmap_chrome_default_does_not_crash():
    """When chrome= isn't passed, the hardcoded fallback should keep working
    so legacy callers (and the test suite from prior tasks) still pass."""
    fig, ax = plt.subplots()
    gamma_tool.draw_term_heatmap(ax, _sample_rows(), **_colors())
    # No crash — assertion is the test passing
    plt.close(fig)

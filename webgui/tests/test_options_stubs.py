"""The Gamma and Simulator pages are stubs for now; assert their render API."""
from pages.options import gamma, simulator


def test_stub_pages_have_render():
    assert callable(gamma.render)
    assert callable(simulator.render)

"""Pure helpers behind the /trade/deepdive + /trade/deepdive-query serve routes."""
from webgui import main


def test_deepdive_html_extracts_and_falls_back():
    assert "REPORT" in main.deepdive_html({"html": "<h1>REPORT</h1>"})
    empty = main.deepdive_html(None)
    assert "Deep Dive" in empty and "<html" in empty.lower()  # placeholder page


def test_deepdive_query_html_wraps_markdown():
    page = main.deepdive_query_html({"markdown": "PASTE ME", "symbol": "OKLO"})
    assert "PASTE ME" in page           # the prompt is embedded
    assert "clipboard" in page.lower()  # a Copy button is present
    fallback = main.deepdive_query_html(None)
    assert "<html" in fallback.lower()


def test_deepdive_query_html_escapes_markdown():
    # the prompt goes into a <textarea>; angle brackets must be escaped so they
    # can't break out of the element.
    page = main.deepdive_query_html({"markdown": "a </textarea><script>x</script>", "symbol": "X"})
    assert "</textarea><script>" not in page
    assert "&lt;/textarea&gt;" in page

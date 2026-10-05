"""services/_sse.py: the two helpers an SSE-consuming service shares."""
from services import _sse


def test_parse_sse_line_data():
    tick = _sse.parse_sse_line('data: {"symbol": "SPY", "last": 500.1}')
    assert tick == {"symbol": "SPY", "last": 500.1}


def test_parse_sse_line_tolerates_no_space_and_surrounding_whitespace():
    assert _sse.parse_sse_line('  data:{"a": 1}  ') == {"a": 1}


def test_parse_sse_line_blank_comment_nondata_badjson():
    assert _sse.parse_sse_line("") is None
    assert _sse.parse_sse_line("   ") is None
    assert _sse.parse_sse_line(": keepalive") is None       # SSE comment
    assert _sse.parse_sse_line("event: ping") is None        # non-data field
    assert _sse.parse_sse_line("data: {not json") is None    # bad JSON
    assert _sse.parse_sse_line("data: 42") is None           # not an object
    assert _sse.parse_sse_line("data: [1, 2]") is None
    assert _sse.parse_sse_line(None) is None
    assert _sse.parse_sse_line(b'data: {"a": 1}') is None    # bytes: not decoded


def test_reconnect_delay_capped_exponential():
    assert _sse.reconnect_delay(0) == 3.0
    assert _sse.reconnect_delay(1) == 6.0
    assert _sse.reconnect_delay(2) == 12.0
    assert _sse.reconnect_delay(100) == 60.0     # capped
    assert _sse.reconnect_delay(100000) == 60.0  # ...and never an overflow
    assert _sse.reconnect_delay(-3) == 3.0


def test_reconnect_delay_takes_its_own_base_and_cap():
    assert _sse.reconnect_delay(0, base=1.0, cap=5.0) == 1.0
    assert _sse.reconnect_delay(3, base=1.0, cap=5.0) == 5.0

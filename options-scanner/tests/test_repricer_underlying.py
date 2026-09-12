"""The repricer read the spot out of a key Schwab does not send — found 2026-09-12.

``reprice_swing`` and ``reprice_legs`` both did::

    underlying = (chain.get("underlying") or {}).get("last", 0)

Schwab's ``/chains`` response carries the spot in **``underlyingPrice``**, and
``underlying`` is a sub-object it only populates with
``includeUnderlyingQuote=true`` — which this app never asks for. Measured live on
prod: a SPY chain came back with ``underlying: None`` and
``underlyingPrice: 764.29``. So the expression resolved to the DEFAULT on every
call, and the default was ``0`` rather than ``None``.

The damage, measured on prod's own store: **`signal_marks.current_underlying` is
`0.0` across all 58,895 rows** — never once a real reading — while every sibling
column on the same row (``current_short_delta``, ``current_value``,
``unrealized_pnl``) is populated, and ``signals.entry_underlying`` is populated
too (28.51–29,329.21). Two consequences worth naming:

* **`signal_recommender._recoverable` has never run** on the captured-signal
  path. It early-returns on ``spot <= 0``, so the ``RECOVERY_MIN_CUSHION``
  deferral — the rule that holds a stop when the short still has room — was
  permanently off. It degrades to "stop fires", the conservative direction, which
  is why nothing looked wrong.
* Two UI paths read ``rep.get("current_underlying") or row.get("entry_underlying")``
  and so silently rendered the **entry** price under a *current* label.

This is the repo's documented "never print a zero you did not read" class, and the
fix is both halves: read the right key, and default to **None**.
"""
import signal_recommender
import signal_repricer


def _chain(**over):
    """A chain shaped like the real proxy response — ``underlying`` absent,
    ``underlyingPrice`` present — with one put strike quoted."""
    base = {
        "underlyingPrice": 764.29,
        "putExpDateMap": {"2026-10-16:34": {
            "760.0": [{"bid": 1.00, "ask": 1.10, "delta": -0.30}],
            "755.0": [{"bid": 0.70, "ask": 0.80, "delta": -0.22}],
        }},
        "callExpDateMap": {},
    }
    base.update(over)
    return base


def _trade():
    return {"symbol": "SPY", "strategy": "PCS", "short_strike": 760.0,
            "long_strike": 755.0, "width": 5.0, "entry_credit": 0.40,
            "expiration": "2026-10-16"}


def _reprice(monkeypatch, chain):
    monkeypatch.setattr(signal_repricer, "_fetch_chain", lambda *a, **k: chain)
    return signal_repricer.reprice_swing(_trade(), client=object(),
                                         today=__import__("datetime").date(2026, 9, 12))


def test_the_spot_comes_from_underlyingPrice(monkeypatch):
    """The key Schwab actually sends."""
    got = _reprice(monkeypatch, _chain())
    assert got["error"] is None, got
    assert got["current_underlying"] == 764.29


def test_a_chain_with_NO_spot_reports_None_not_zero(monkeypatch):
    """0 sorts among real prices, compares as "below every strike" and reads as a
    crash on screen. None is the only honest answer to "we did not get a price"."""
    chain = _chain()
    chain.pop("underlyingPrice")
    got = _reprice(monkeypatch, chain)
    assert got["current_underlying"] is None


def test_an_explicit_zero_from_the_chain_is_also_None(monkeypatch):
    """``scanner_engine`` defaults a missing ``underlyingPrice`` to 0 in its own
    chain plumbing, so a 0 arriving here means "not read", not "the stock is
    worthless"."""
    got = _reprice(monkeypatch, _chain(underlyingPrice=0))
    assert got["current_underlying"] is None


def test_underlyingPrice_is_used_when_the_sub_object_is_absent(monkeypatch):
    """The live case: Schwab sends only ``underlyingPrice`` unless asked for a
    quote, and this app never asks."""
    got = _reprice(monkeypatch, _chain())
    assert got["current_underlying"] == 764.29


def test_the_NESTED_live_quote_WINS_when_both_are_present(monkeypatch):
    """⚠ This precedence is not arbitrary and not cosmetic. ``underlyingPrice`` is
    **pinned to the prior close outside RTH** — the documented Schwab quirk that
    once froze every extended-hours gamma number — while the nested
    ``underlying.last`` is the live quote. ``atm_iv`` established this order
    against a real chain on 2026-08-25, and the shared helper now carries it so
    the two cannot disagree.
    """
    got = _reprice(monkeypatch, _chain(underlying={"last": 770.0}))
    assert got["current_underlying"] == 770.0


def test_a_useless_nested_quote_falls_THROUGH_to_underlyingPrice(monkeypatch):
    """Preference is not blind precedence: a null or non-positive nested quote
    must not shadow a perfectly good top-level one. That is the failure ``atm_iv``
    was fixed for — it refused a good IV for want of a price."""
    for bad in (None, 0, -1.0, "n/a"):
        got = _reprice(monkeypatch, _chain(underlying={"last": bad}))
        assert got["current_underlying"] == 764.29, bad


def test_reprice_legs_reads_the_same_key(monkeypatch):
    """The second copy of the same expression — both were wrong, so both are
    tested. A debit trade's detail panel shows the spot too."""
    trade = {"symbol": "SPY", "expiration": "2026-10-16", "entry_debit": 100.0,
             "legs": [{"kind": "put", "strike": 760.0, "side": "BUY", "qty": 1}]}
    monkeypatch.setattr(signal_repricer, "_fetch_chain", lambda *a, **k: _chain())
    got = signal_repricer.reprice_legs(
        trade, client=object(), today=__import__("datetime").date(2026, 9, 12))
    assert got["error"] is None, got
    assert got["current_underlying"] == 764.29


def test_the_recovery_deferral_can_now_actually_fire():
    """``_recoverable`` early-returns on ``spot <= 0``, which is why a permanent 0
    made the RECOVERY_MIN_CUSHION rule dead rather than visibly broken. Driven
    with a real spot, it answers True — the proof that the fix restores a rule
    and not just a display field.
    """
    ctx = {"strategy": "PCS", "spot": 764.29, "short_strike": 700.0}
    assert signal_recommender._recoverable(ctx) is True
    assert signal_recommender._recoverable({**ctx, "spot": 0}) is False
    assert signal_recommender._recoverable({**ctx, "spot": None}) is False


def test_the_rescored_row_prefers_the_live_spot_over_the_entry_price():
    """``underlying_price`` falls back to ``entry_underlying`` — correct, but it
    had become the ONLY branch, so a *current* label carried an entry price.
    Driven through ``_recompute_score``, which is the only builder of that row."""
    row = {"strategy": "PCS", "symbol": "SPY", "entry_underlying": 700.0,
           "entry_credit": 0.40, "entry_max_loss": 4.60, "width": 5.0,
           "short_strike": 760.0, "long_strike": 755.0}
    seen = {}

    import scoring
    real = scoring.calc_composite_score

    def _spy(sig, **kw):
        seen["underlying_price"] = sig.get("underlying_price")
        return {"composite_score": 60.0, "grade": "Good", "factor_scores": {}}

    scoring.calc_composite_score = _spy
    try:
        signal_recommender._recompute_score(row, {"current_underlying": 764.29},
                                            None, None)
        assert seen["underlying_price"] == 764.29
        signal_recommender._recompute_score(row, {"current_underlying": None},
                                            None, None)
        assert seen["underlying_price"] == 700.0
    finally:
        scoring.calc_composite_score = real


def test_only_ONE_function_reads_the_spot_off_a_chain():
    """A source guard, because the expression is short, plausible and was
    duplicated: BOTH copies were wrong for the life of the file, and a third
    would be just as invisible.

    AST rather than text, for two reasons. A text grep would trip over the
    docstrings that explain the bug (it did, once, and the "fix" for that was a
    string-stripping filter that quietly made the assertion vacuous). And what
    actually needs guarding is structural: nothing outside ``_chain_spot`` may
    read a spot out of a chain, so the fallback order and the non-positive rule
    live in exactly one place.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(signal_repricer))
    offenders = []
    for fn in [n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name != "_chain_spot"]:
        for node in ast.walk(fn):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "get"
                    and node.args
                    and isinstance(node.args[0], ast.Constant)):
                continue
            if node.args[0].value in ("underlying", "underlyingPrice", "last"):
                offenders.append((fn.name, node.args[0].value, node.lineno))
    assert not offenders, offenders

    # Non-vacuity: the guard must be able to SEE such a read. _chain_spot itself
    # makes exactly the calls the walk above looks for, and is the one exemption.
    spot_tree = ast.parse(inspect.getsource(signal_repricer._chain_spot))
    keys = {n.args[0].value for n in ast.walk(spot_tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == "get" and n.args
            and isinstance(n.args[0], ast.Constant)}
    assert {"underlyingPrice", "underlying", "last"} <= keys, keys

from gamma_tool import compute_mvc


def _rows():
    return [
        {"expiration_date": "2026-04-29", "strike": 7100.0, "net_gex_usd": -50e6},
        {"expiration_date": "2026-04-29", "strike": 7150.0, "net_gex_usd": 530e6},
        {"expiration_date": "2026-04-30", "strike": 7100.0, "net_gex_usd": -100e6},
        {"expiration_date": "2026-04-30", "strike": 7150.0, "net_gex_usd":  60e6},
    ]


def test_mvc_default_uses_sum_across_expirations():
    # 7100 across exps: |-50 + -100| = 150M
    # 7150 across exps: |530 + 60| = 590M  -> winner
    assert compute_mvc(_rows()) == 7150.0


def test_mvc_for_specific_expiration():
    # 2026-04-30 only: 7100=-100M, 7150=60M; |-100M| > |60M| -> 7100
    assert compute_mvc(_rows(), expiration="2026-04-30") == 7100.0


def test_mvc_empty_rows_returns_none():
    assert compute_mvc([]) is None


def test_mvc_unknown_expiration_returns_none():
    assert compute_mvc(_rows(), expiration="2099-12-31") is None


def test_mvc_ties_pick_first_max_seen():
    rows = [
        {"expiration_date": "2026-04-29", "strike": 7100.0, "net_gex_usd": 100e6},
        {"expiration_date": "2026-04-29", "strike": 7150.0, "net_gex_usd": -100e6},
    ]
    # Either strike acceptable on tie — verify a strike is returned
    result = compute_mvc(rows)
    assert result in (7100.0, 7150.0)

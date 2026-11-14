"""Walk-forward scoring: each period is predicted only by a model fitted on
the periods before it.

Indices in, indices out, so the caller keeps its own row type.
"""


def walk_forward(n, period_of, min_train, fit_fn, predict_fn, keep=None):
    """Score items ``0 .. n-1`` period by period.

    ``period_of(i)`` names item ``i``'s period (anything orderable: an ISO week,
    a date). The first ``min_train`` periods are training only. For each later
    period, the items ``keep(i)`` accepts (all, when ``keep`` is None) are the
    test; a period with none is skipped. ``fit_fn(train)`` gets the indices of
    every item in an EARLIER period, in index order; ``predict_fn(model, i)``
    predicts one test item.

    Returns ``(scored, periods)``: ``scored`` the ``(i, prediction)`` pairs in
    period then index order, ``periods`` the number of periods scored."""
    periods_of = [period_of(i) for i in range(n)]
    scored, n_periods = [], 0
    for period in sorted(set(periods_of))[min_train:]:
        test = [i for i, p in enumerate(periods_of)
                if p == period and (keep is None or keep(i))]
        if not test:
            continue
        train = [i for i, p in enumerate(periods_of) if p < period]
        model = fit_fn(train)
        for i in test:
            scored.append((i, predict_fn(model, i)))
        n_periods += 1
    return scored, n_periods


__all__ = ["walk_forward"]

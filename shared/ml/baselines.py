"""Baselines a model must beat before it is believed."""
from collections import defaultdict


def usual_rates(groups, days, wins, weights) -> list:
    """Per item, the weighted win share of its group on strictly EARLIER days:
    what a reader who knew only the history would have guessed. ``None`` for an
    item whose group has no earlier day. Items of one day never see each other,
    so a day's later slots cannot learn from its earlier ones.

    The four sequences are parallel; ``days`` sort as text (ISO dates)."""
    by_group = defaultdict(list)
    for j, g in enumerate(groups):
        by_group[g].append(j)
    out = [None] * len(groups)
    for idxs in by_group.values():
        idxs.sort(key=lambda j: days[j])
        weight = won = 0.0
        i = 0
        while i < len(idxs):
            day = days[idxs[i]]
            day_weight = day_won = 0.0
            while i < len(idxs) and days[idxs[i]] == day:
                j = idxs[i]
                out[j] = won / weight if weight > 0 else None
                day_weight += weights[j]
                day_won += weights[j] * wins[j]
                i += 1
            weight += day_weight
            won += day_won
    return out


__all__ = ["usual_rates"]

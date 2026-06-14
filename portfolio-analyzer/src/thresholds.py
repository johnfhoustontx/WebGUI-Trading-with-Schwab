"""User-tunable rule thresholds for the suggestions engine.

``Thresholds`` is injected into ``src.suggestions.suggest`` so every rule
boundary is testable and user-editable (Settings dialog on the Performance
tab persists to ``data/eval_settings.json``). Persistence mirrors
``trade_store.py``: tolerant load (missing/corrupt/partial file -> defaults),
single-file JSON save.
"""
from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Thresholds:
    weight_cap: float = 0.10            # max position weight before TRIM fires
    sector_lag_pct: float = 0.05        # lag vs sector (holding window) that counts as "lagging"
    exit_loss_pct: float = 0.15         # loss since entry that, with a sector lag, triggers EXIT
    dd_trigger: float = 0.10            # drawdown-from-held-peak that triggers SET_STOP
    atr_mult: float = 2.0               # k in stop = peak - k*ATR
    take_profit_pct: float = 0.25       # gain that triggers a SCALE_OUT plan
    bottom_quartile: float = 0.25       # capital-efficiency percentile cutoff
    max_position_loss_pct: float = 0.02 # entry-based stop: cap loss at this fraction of portfolio


def load_thresholds(path) -> Thresholds:
    """Load thresholds from ``path``; any problem -> field-wise defaults."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Thresholds()
    if not isinstance(raw, dict):
        return Thresholds()
    fields = {f.name for f in dataclasses.fields(Thresholds)}
    kwargs = {
        k: v
        for k, v in raw.items()
        if k in fields and isinstance(v, (int, float)) and not isinstance(v, bool)
    }
    return Thresholds(**kwargs)


def save_thresholds(t: Thresholds, path) -> None:
    """Persist thresholds as JSON at ``path``, creating parent dirs as needed."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(dataclasses.asdict(t), indent=2), encoding="utf-8")

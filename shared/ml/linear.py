"""Weighted ridge regression on standardised inputs.

The intercept is not penalised, so a very large penalty shrinks every
prediction to the weighted mean of ``y``. A column with no spread is left at
scale one (it carries nothing and must not divide by zero).
"""
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Ridge:
    coef: np.ndarray
    intercept: float
    mean: np.ndarray
    scale: np.ndarray


def ridge(x, y, w, lam) -> Ridge:
    """Fit ``y ~ x`` minimising ``sum w (y - pred)^2 + lam * |coef|^2`` with the
    columns of ``x`` standardised by their weighted mean and spread."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    w = np.asarray(w, dtype=float)
    total = w.sum()
    mean = (w[:, None] * x).sum(axis=0) / total
    spread = np.sqrt((w[:, None] * (x - mean) ** 2).sum(axis=0) / total)
    scale = np.where(spread > 1e-12, spread, 1.0)
    z = (x - mean) / scale
    y_mean = float((w * y).sum() / total)
    yc = y - y_mean
    a = z.T @ (w[:, None] * z) + lam * np.eye(z.shape[1])
    b = z.T @ (w * yc)
    coef = np.linalg.lstsq(a, b, rcond=None)[0]
    return Ridge(coef=coef, intercept=y_mean, mean=mean, scale=scale)


def predict(model: Ridge, x) -> np.ndarray:
    z = (np.asarray(x, dtype=float) - model.mean) / model.scale
    return model.intercept + z @ model.coef


__all__ = ["Ridge", "ridge", "predict"]

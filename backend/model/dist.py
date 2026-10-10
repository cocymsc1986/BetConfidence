"""Small probability helpers (no scipy dependency)."""
from __future__ import annotations

import math


def poisson_pmf(k: int, lam: float) -> float:
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return math.exp(k * math.log(lam) - lam - math.lgamma(k + 1))


def count_pmf(k: int, mean: float, var_factor: float = 1.0) -> float:
    """P(X = k) for a count with the given mean and variance = var_factor * mean.

    var_factor == 1 is Poisson; > 1 is negative binomial (over-dispersed), which
    fits corners and cards much better than Poisson does.
    """
    if var_factor <= 1.0001 or mean <= 0:
        return poisson_pmf(k, mean)
    # NB parametrised by mean mu and size r: var = mu + mu^2 / r
    r = mean / (var_factor - 1.0)
    p = r / (r + mean)
    return math.exp(
        math.lgamma(k + r) - math.lgamma(r) - math.lgamma(k + 1) + r * math.log(p) + k * math.log1p(-p)
    )


def prob_over(line: float, mean: float, var_factor: float = 1.0) -> float:
    """P(X > line) for a half-integer line, e.g. line=9.5 -> P(X >= 10)."""
    threshold = math.floor(line) + 1
    cdf = sum(count_pmf(k, mean, var_factor) for k in range(threshold))
    return min(max(1.0 - cdf, 0.0), 1.0)


def prob_at_least_one(mean: float) -> float:
    return 1.0 - math.exp(-max(mean, 0.0))


def recency_weights(n: int, half_life: float) -> list[float]:
    """Exponential decay weights, newest first: the match `half_life` games ago counts half."""
    return [0.5 ** (i / half_life) for i in range(n)]


def clamp_prob(p: float, lo: float = 0.01, hi: float = 0.99) -> float:
    return min(max(p, lo), hi)

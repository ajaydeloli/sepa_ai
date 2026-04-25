"""
utils/math_utils.py
--------------------
Pure numeric helpers — no pandas, no external dependencies beyond the
standard library and NumPy.

These functions are deliberately kept stateless and side-effect-free so
they are easy to unit-test and safe to call from any layer of the pipeline.
"""

from __future__ import annotations

import math
from typing import Sequence


# ---------------------------------------------------------------------------
# Linear regression slope
# ---------------------------------------------------------------------------


def linear_slope(values: Sequence[float]) -> float:
    """Compute the slope of a least-squares linear regression over *values*.

    Uses the closed-form OLS formula:
    ``slope = (n·Σxy − Σx·Σy) / (n·Σx² − (Σx)²)``
    where *x* is the 0-based index position.

    Parameters
    ----------
    values:
        Sequence of floats (e.g. a rolling window of closing prices).
        Must contain at least 2 elements.

    Returns
    -------
    float
        Slope of the best-fit line.  A positive slope means the series is
        trending up; negative means trending down.

    Raises
    ------
    ValueError
        If *values* has fewer than 2 elements.

    Examples
    --------
    >>> linear_slope([1.0, 2.0, 3.0, 4.0, 5.0])
    1.0
    >>> linear_slope([5.0, 3.0, 1.0])
    -2.0
    """
    n = len(values)
    if n < 2:
        raise ValueError(
            f"linear_slope requires at least 2 data points; got {n}."
        )

    sum_x: float = 0.0
    sum_y: float = 0.0
    sum_xy: float = 0.0
    sum_x2: float = 0.0

    for i, y in enumerate(values):
        x = float(i)
        sum_x += x
        sum_y += y
        sum_xy += x * y
        sum_x2 += x * x

    denominator = n * sum_x2 - sum_x * sum_x
    if math.isclose(denominator, 0.0):
        return 0.0  # All x-values identical — degenerate case

    return (n * sum_xy - sum_x * sum_y) / denominator


# ---------------------------------------------------------------------------
# Percentage change
# ---------------------------------------------------------------------------


def pct_change(old: float, new: float) -> float:
    """Return the percentage change from *old* to *new*.

    Formula: ``(new - old) / abs(old) * 100``

    Parameters
    ----------
    old:
        Starting value.  Must not be zero.
    new:
        Ending value.

    Returns
    -------
    float
        Percentage change.  A return of ``10.0`` means +10 %.

    Raises
    ------
    ZeroDivisionError
        If *old* is zero (percentage change is undefined).

    Examples
    --------
    >>> pct_change(100.0, 110.0)
    10.0
    >>> pct_change(200.0, 180.0)
    -10.0
    """
    if math.isclose(old, 0.0):
        raise ZeroDivisionError(
            "pct_change: 'old' value is zero; percentage change is undefined."
        )
    return (new - old) / abs(old) * 100.0


# ---------------------------------------------------------------------------
# Clamp
# ---------------------------------------------------------------------------


def clamp(value: float, min_val: float, max_val: float) -> float:
    """Clamp *value* to the closed interval [*min_val*, *max_val*].

    Parameters
    ----------
    value:
        The value to clamp.
    min_val:
        Lower bound (inclusive).
    max_val:
        Upper bound (inclusive).  Must be ≥ *min_val*.

    Returns
    -------
    float
        *value* if it is within [min_val, max_val]; otherwise the nearest
        boundary.

    Raises
    ------
    ValueError
        If *min_val* > *max_val*.

    Examples
    --------
    >>> clamp(5.0, 0.0, 10.0)
    5.0
    >>> clamp(-3.0, 0.0, 10.0)
    0.0
    >>> clamp(15.0, 0.0, 10.0)
    10.0
    """
    if min_val > max_val:
        raise ValueError(
            f"clamp: min_val ({min_val}) must be <= max_val ({max_val})."
        )
    return max(min_val, min(max_val, value))

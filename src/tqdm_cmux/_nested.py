"""Composite progress for nested bars.

cmux exposes a single progress bar per workspace, so nested tqdm bars are
flattened into one fraction. A bar's ``desc`` contributes to the label, and an
inner bar's fraction is interpreted as progress within the current outer
iteration, i.e. ``f_outer = (n_outer + f_inner) / total_outer``.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Protocol

_MAX_LABEL = 120


class BarLike(Protocol):
    n: float
    total: float | None
    desc: str
    cmux_label: str | None


def _usable_total(bar: BarLike) -> float | None:
    total = getattr(bar, "total", None)
    if total is None:
        return None
    try:
        if not math.isfinite(total) or total <= 0:
            return None
    except TypeError:
        return None
    return float(total)


def composite_fraction(bars: Sequence[BarLike]) -> float | None:
    """Overall fraction for ``bars`` ordered outermost first, or None if unknown."""
    fraction: float | None = None
    for bar in reversed(bars):
        total = _usable_total(bar)
        if total is None:
            # No denominator: neither this level nor an inner one can be scaled.
            continue
        inner = 0.0 if fraction is None else fraction
        n = getattr(bar, "n", 0) or 0
        try:
            fraction = (float(n) + inner) / total
        except (TypeError, ValueError):
            continue
    if fraction is None:
        return None
    return min(1.0, max(0.0, fraction))


def composite_label(bars: Sequence[BarLike]) -> str | None:
    """Explicit ``cmux_label`` (outermost wins) or joined bar descriptions."""
    for bar in bars:
        label = getattr(bar, "cmux_label", None)
        if label:
            return str(label)[:_MAX_LABEL]
    parts: list[str] = []
    for bar in bars:
        desc = (getattr(bar, "desc", "") or "").strip().rstrip(":").strip()
        if desc and desc not in parts:
            parts.append(desc)
    if not parts:
        return None
    return " · ".join(parts)[:_MAX_LABEL] or None

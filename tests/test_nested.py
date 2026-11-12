from __future__ import annotations

from dataclasses import dataclass

from cmux_tqdm._nested import composite_fraction, composite_label


@dataclass
class Bar:
    n: float = 0
    total: float | None = 100
    desc: str = ""
    cmux_label: str | None = None


def test_single_bar():
    assert composite_fraction([Bar(n=50, total=200)]) == 0.25
    assert composite_fraction([Bar(n=0, total=0)]) is None
    assert composite_fraction([Bar(n=5, total=10)]) == 0.5


def test_nested_outer_plus_inner_fraction():
    outer = Bar(n=3, total=10, desc="Epoch")
    inner = Bar(n=50, total=100, desc="Batch")
    assert composite_fraction([outer, inner]) == 0.35
    assert composite_label([outer, inner]) == "Epoch · Batch"


def test_completed_inner_advances_outer():
    outer = Bar(n=3, total=10)
    inner = Bar(n=100, total=100)
    assert composite_fraction([outer, inner]) == 0.4


def test_indeterminate_levels_are_skipped():
    outer = Bar(n=3, total=10)
    inner = Bar(n=50, total=None)
    assert composite_fraction([outer, inner]) == 0.3
    assert composite_fraction([Bar(n=1, total=None)]) is None
    assert composite_fraction([Bar(n=1, total=float("inf"))]) is None


def test_fraction_is_clamped():
    assert composite_fraction([Bar(n=20, total=10)]) == 1.0
    assert composite_fraction([Bar(n=-5, total=10)]) == 0.0


def test_explicit_label_wins_and_descs_are_deduplicated():
    outer = Bar(n=0, total=2, desc="Train")
    inner = Bar(n=0, total=2, desc="Train")
    assert composite_label([outer, inner]) == "Train"
    assert composite_label([Bar(desc="Train: "), Bar(desc="Batch:")]) == "Train · Batch"
    assert composite_label([Bar(desc=""), Bar(desc="")]) is None
    assert composite_label([Bar(cmux_label="custom", desc="Train")]) == "custom"


def test_label_is_capped():
    label = composite_label([Bar(desc="x" * 500)])
    assert label is not None and len(label) == 120

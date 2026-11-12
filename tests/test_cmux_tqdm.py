from __future__ import annotations

import io
import sys

import pytest
import tqdm
import tqdm.auto
import tqdm.std

import cmux_tqdm as mod
from cmux_tqdm import _bridge, cmux_tqdm


class Recorder:
    def __init__(self) -> None:
        self.updates: list[tuple[float, str | None]] = []
        self.clears = 0
        self.leave = False
        self._dead = False

    def update(self, fraction: float, label: str | None) -> None:
        self.updates.append((fraction, label))

    def clear(self) -> None:
        self.clears += 1


@pytest.fixture(autouse=True)
def clean_stack():
    mod._local.stack = []
    yield
    mod._local.stack = []


@pytest.fixture
def recorder(monkeypatch) -> Recorder:
    rec = Recorder()
    monkeypatch.setattr(mod, "_bridge_for", lambda workspace: rec)
    return rec


def bar(total=4, **kwargs) -> cmux_tqdm:
    kwargs.setdefault("cmux", True)
    kwargs.setdefault("cmux_workspace", "ws")
    return cmux_tqdm(total=total, file=io.StringIO(), mininterval=0, **kwargs)


def test_updates_and_clears_on_close(recorder):
    b = bar(desc="Training")
    b.update(2)
    assert recorder.updates[-1] == (0.5, "Training")
    b.close()
    assert recorder.clears == 1


def test_iterable_closes_implicitly(recorder):
    for _ in cmux_tqdm(
        range(4), file=io.StringIO(), mininterval=0, cmux=True, cmux_workspace="ws"
    ):
        pass
    assert recorder.updates[-1][0] == 1.0
    assert recorder.clears == 1


def test_nested_bars_compose_into_one_fraction(recorder):
    outer = bar(total=2, desc="Epoch")
    inner = bar(total=10, desc="Batch")
    inner.update(5)
    # (outer.n + inner_fraction) / outer.total
    assert recorder.updates[-1] == (0.25, "Epoch · Batch")

    inner.close()
    assert recorder.clears == 0
    assert recorder.updates[-1] == (0.0, "Epoch")

    outer.update(1)
    inner2 = bar(total=10, desc="Batch")
    inner2.update(5)
    assert recorder.updates[-1] == (0.75, "Epoch · Batch")

    inner2.close()
    outer.close()
    assert recorder.clears == 1


def test_leave_keeps_final_bar(recorder):
    b = bar(cmux_leave=True)
    b.update(4)
    b.close()
    assert recorder.leave is True
    assert recorder.clears == 0
    assert recorder.updates[-1][0] == 1.0


def test_disabled_tqdm_never_touches_cmux(recorder):
    b = cmux_tqdm(total=4, disable=True, cmux=True, cmux_workspace="ws")
    b.update(1)
    b.close()
    assert recorder.updates == [] and recorder.clears == 0


def test_explicitly_disabled_cmux_never_touches_cmux(recorder):
    b = bar(cmux=False)
    b.update(1)
    b.close()
    assert recorder.updates == [] and recorder.clears == 0


def test_no_workspace_means_plain_tqdm(monkeypatch):
    for var in ("CMUX_WORKSPACE_ID", "CMUX_TQDM_WORKSPACE"):
        monkeypatch.delenv(var, raising=False)

    def boom(workspace):
        raise AssertionError("bridge must not be created without a workspace")

    monkeypatch.setattr(mod, "_bridge_for", boom)
    assert mod.cmux_available() is False
    for _ in cmux_tqdm(range(3), file=io.StringIO(), mininterval=0):
        pass


def test_install_patches_core_classes(monkeypatch, recorder):
    monkeypatch.setattr(tqdm, "tqdm", tqdm.tqdm)
    monkeypatch.setattr(tqdm.std, "tqdm", tqdm.std.tqdm)
    monkeypatch.setattr(tqdm.auto, "tqdm", tqdm.auto.tqdm)
    for name in ("tqdm.asyncio",):
        if name in sys.modules:
            module = sys.modules[name]
            monkeypatch.setattr(module, "tqdm", module.tqdm)

    mod.install()

    assert issubclass(tqdm.tqdm, mod.cmux_tqdm)
    assert issubclass(tqdm.std.tqdm, mod.cmux_tqdm)
    with tqdm.tqdm(
        total=2, file=io.StringIO(), mininterval=0, cmux=True, cmux_workspace="ws"
    ) as b:
        b.update(1)
    assert recorder.updates[-1][0] == 0.5


def test_install_is_idempotent(monkeypatch):
    monkeypatch.setattr(tqdm, "tqdm", tqdm.tqdm)
    monkeypatch.setattr(tqdm.std, "tqdm", tqdm.std.tqdm)
    monkeypatch.setattr(tqdm.auto, "tqdm", tqdm.auto.tqdm)
    mod.install()
    patched = tqdm.std.tqdm
    mod.install()
    assert tqdm.std.tqdm is patched


def test_without_cmux_binary_it_is_plain_tqdm(monkeypatch):
    monkeypatch.setattr(mod, "find_binary", lambda: None)
    monkeypatch.setenv("CMUX_WORKSPACE_ID", "ws")

    spawned: list[object] = []
    monkeypatch.setattr(_bridge.subprocess, "run", lambda *a, **k: spawned.append(a))

    assert mod.cmux_available() is False

    # Even an explicit cmux=True cannot attach without a binary.
    seen = []
    for i in cmux_tqdm(
        range(3),
        file=io.StringIO(),
        mininterval=0,
        cmux=True,
        cmux_workspace="ws",
    ):
        seen.append(i)
    assert seen == [0, 1, 2]
    assert mod._bridges == {}
    assert spawned == []


def test_install_keeps_identity_without_cmux(monkeypatch):
    original = tqdm.std.tqdm
    monkeypatch.setattr(tqdm.std, "tqdm", original)
    monkeypatch.setattr(mod, "find_binary", lambda: None)
    monkeypatch.setenv("CMUX_WORKSPACE_ID", "ws")
    mod.install()
    assert tqdm.std.tqdm is original

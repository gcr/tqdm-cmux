"""A tqdm subclass that mirrors progress into the cmux sidebar.

Usage::

    from cmux_tqdm import cmux_tqdm as tqdm

    for epoch in tqdm(range(10), desc="Training"):
        ...

Outside a cmux workspace (no ``CMUX_WORKSPACE_ID``, or no ``cmux`` binary on
PATH) the class is a plain tqdm and costs nothing extra.
"""

from __future__ import annotations

import sys
import threading
import weakref

from tqdm.std import tqdm as _std_tqdm

from ._bridge import (
    ProgressBridge,
    default_interval,
    default_workspace,
    disabled_by_env,
    find_binary,
)
from ._nested import composite_fraction, composite_label

__all__ = ["cmux_available", "cmux_tqdm", "install"]


_local = threading.local()
_bridges: dict[str, ProgressBridge] = {}
_bridges_lock = threading.Lock()


# -- per-thread nesting stack ---------------------------------------------


def _stack() -> list[weakref.ref[cmux_tqdm]]:
    stack = getattr(_local, "stack", None)
    if stack is None:
        stack = _local.stack = []
    return stack


def _live_stack() -> list[cmux_tqdm]:
    """Active bars in this thread, outermost first."""
    live: list[cmux_tqdm] = []
    for ref in list(_stack()):
        bar = ref()
        if bar is None:
            _stack().remove(ref)
        elif not bar._cmux_closed:
            live.append(bar)
    return live


def _bridge_for(workspace: str) -> ProgressBridge | None:
    binary = find_binary()
    if binary is None:
        return None
    with _bridges_lock:
        bridge = _bridges.get(workspace)
        if bridge is None or bridge._dead:
            bridge = _bridges[workspace] = ProgressBridge(
                workspace, binary, default_interval()
            )
        return bridge


def cmux_available(workspace: str | None = None) -> bool:
    """True when a cmux CLI and a target workspace are both available."""
    if disabled_by_env():
        return False
    return bool(workspace or default_workspace()) and find_binary() is not None


# -- the drop-in class ----------------------------------------------------


class cmux_tqdm(_std_tqdm):
    """tqdm, plus a throttled ``cmux set-progress`` mirror in the sidebar.

    Extra keyword arguments (all optional):

    ``cmux``           Force the mirror on (True) or off (False); default is
                       "on when running inside a cmux workspace".
    ``cmux_label``     Sidebar label. Defaults to the joined bar descriptions.
    ``cmux_workspace`` Workspace UUID to update instead of
                       ``$CMUX_WORKSPACE_ID``.
    ``cmux_leave``     Leave the final bar in the sidebar instead of clearing
                       it when the outermost bar closes.
    """

    def __init__(
        self,
        *args,
        cmux: bool | None = None,
        cmux_label: str | None = None,
        cmux_workspace: str | None = None,
        cmux_leave: bool = False,
        **kwargs,
    ):
        workspace = cmux_workspace or default_workspace()
        if cmux is None:
            cmux = cmux_available(workspace)

        self._cmux_closed = True  # no-op until we actually attach
        self._cmux_active = False
        self._cmux_bridge: ProgressBridge | None = None
        self._cmux_label = cmux_label
        self._cmux_leave = bool(cmux_leave)

        if cmux and not kwargs.get("disable") and workspace:
            bridge = _bridge_for(workspace)
            if bridge is not None:
                self._cmux_bridge = bridge
                self._cmux_active = True
                self._cmux_closed = False
                _stack().append(weakref.ref(self))

        try:
            super().__init__(*args, **kwargs)
        except BaseException:
            self._cmux_detach()
            raise

    # -- internals ---------------------------------------------------------

    def _cmux_detach(self) -> None:
        if self._cmux_closed:
            return
        self._cmux_closed = True
        refs = _stack()
        for ref in list(refs):
            bar = ref()
            if bar is None or bar is self:
                refs.remove(ref)

    def _cmux_push(self) -> None:
        if not self._cmux_active or getattr(self, "disable", False):
            return
        bars = _live_stack()
        fraction = composite_fraction(bars)
        if fraction is None:
            return
        self._cmux_bridge.update(fraction, composite_label(bars))

    def _cmux_finish(self) -> None:
        self._cmux_detach()
        bridge = self._cmux_bridge
        if bridge is None:
            return
        remaining = _live_stack()
        if remaining:
            fraction = composite_fraction(remaining)
            if fraction is not None:
                bridge.update(fraction, composite_label(remaining))
            return
        if self._cmux_leave:
            bridge.leave = True
            fraction = composite_fraction([self])
            if fraction is not None:
                bridge.update(fraction, composite_label([self]))
        else:
            bridge.clear()

    # -- tqdm hooks --------------------------------------------------------

    def display(self, msg=None, pos=None):
        self._cmux_push()
        return super().display(msg, pos)

    def close(self):
        if not self._cmux_closed:
            self._cmux_finish()
        return super().close()


# -- optional, explicit integration with third-party tqdm users -----------


_derived: dict[type, type] = {}


def _derived_class(base: type) -> type:
    """A cmux_tqdm that keeps ``base``'s frontend (notebook, asyncio, ...) in the MRO."""
    if issubclass(base, cmux_tqdm):
        # Already cmux-aware (e.g. imported after tqdm.std.tqdm was patched).
        return base
    cls = _derived.get(base)
    if cls is None:
        cls = type(f"Cmux{base.__name__}", (cmux_tqdm, base), {"__module__": __name__})
        _derived[base] = cls
    return cls


def install() -> None:
    """Replace ``tqdm.tqdm`` (and friends) with the cmux-aware subclass.

    Opt-in monkeypatching: tqdm has no plugin API. Modules that already ran
    ``from tqdm import tqdm`` keep the original class, so call this as early as
    possible — e.g. from ``sitecustomize.py`` in a dedicated venv::

        import cmux_tqdm
        cmux_tqdm.install()
    """
    if disabled_by_env() or not cmux_available():
        return

    import tqdm as _tqdm
    import tqdm.std as _std

    targets = [(_tqdm, "tqdm"), (_std, "tqdm")]
    for name in ("tqdm.asyncio", "tqdm.auto"):
        if name == "tqdm.asyncio" and name not in sys.modules:
            # Not imported yet: it will pick up the patched tqdm.std.tqdm.
            continue
        try:
            module = __import__(name, fromlist=["tqdm"])
        except ImportError:  # pragma: no cover - optional deps
            continue
        targets.append((module, "tqdm"))

    for module, attr in targets:
        base = getattr(module, attr, None)
        if isinstance(base, type) and issubclass(base, _std_tqdm):
            setattr(module, attr, _derived_class(base))

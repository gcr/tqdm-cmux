"""Throttled bridge to the cmux CLI's per-workspace progress bar.

All calls are fire-and-forget: a single daemon thread coalesces updates and
executes at most one `cmux set-progress` per ``interval`` seconds, so a hot
tqdm loop never pays subprocess cost inline.
"""

from __future__ import annotations

import atexit
import math
import os
import shutil
import subprocess
import threading
import time

_ENV_DISABLE = "CMUX_TQDM_DISABLE"
_ENV_BIN = "CMUX_TQDM_BIN"
_ENV_INTERVAL = "CMUX_TQDM_INTERVAL"
_ENV_WORKSPACE = "CMUX_TQDM_WORKSPACE"
_DEFAULT_INTERVAL = 0.5
_MAX_FAILURES = 3


def find_binary() -> str | None:
    """Locate the cmux CLI: explicit override, PATH, then cmux's bundled path."""
    explicit = os.environ.get(_ENV_BIN)
    if explicit:
        if os.path.isfile(explicit):
            return explicit
        found = shutil.which(explicit)
        if found:
            return found
    found = shutil.which("cmux")
    if found:
        return found
    bundled = os.environ.get("CMUX_BUNDLED_CLI_PATH")
    if bundled and os.path.exists(bundled):
        return bundled
    return None


def default_workspace() -> str | None:
    return os.environ.get(_ENV_WORKSPACE) or os.environ.get("CMUX_WORKSPACE_ID") or None


def default_interval() -> float:
    raw = os.environ.get(_ENV_INTERVAL)
    if not raw:
        return _DEFAULT_INTERVAL
    try:
        return max(0.0, float(raw))
    except ValueError:
        return _DEFAULT_INTERVAL


def disabled_by_env() -> bool:
    return os.environ.get(_ENV_DISABLE, "").strip().lower() in {"1", "true", "yes", "on"}


class ProgressBridge:
    """Serialises progress updates for one workspace through one worker thread."""

    def __init__(self, workspace: str, binary: str | None, interval: float = _DEFAULT_INTERVAL):
        self.workspace = workspace
        self.binary = binary or find_binary()
        self.interval = max(0.0, interval)
        # Set by cmux_tqdm(..., cmux_leave=True): keep the final bar at exit.
        self.leave = False
        self._cond = threading.Condition()
        self._pending: tuple[str, float, str | None] | None = None
        self._shutdown = False
        self._next_at = 0.0
        self._failures = 0
        self._dead = self.binary is None
        self._thread = threading.Thread(
            target=self._run, name="cmux-tqdm-progress", daemon=True
        )
        self._thread.start()
        atexit.register(self.close)

    # -- producer side -----------------------------------------------------

    def update(self, fraction: float, label: str | None) -> None:
        if self._dead:
            return
        if not math.isfinite(fraction):
            return
        fraction = min(1.0, max(0.0, fraction))
        with self._cond:
            self._pending = ("set", fraction, label)
            self._cond.notify()

    def clear(self) -> None:
        if self._dead:
            return
        with self._cond:
            self._pending = ("clear", 0.0, None)
            self._cond.notify()

    def close(self) -> None:
        """Flush the last state, then clear the bar unless ``leave`` was requested."""
        with self._cond:
            if self._shutdown:
                return
            self._shutdown = True
            pending = self._pending
            self._pending = None
            leave = self.leave
            self._cond.notify_all()
        if self._thread.is_alive() and self._thread is not threading.current_thread():
            self._thread.join(timeout=2.0)
        if pending is not None:
            self._send(pending)
        if not leave:
            self._send(("clear", 0.0, None))

    # -- worker side -------------------------------------------------------

    def _run(self) -> None:
        while True:
            with self._cond:
                while self._pending is None and not self._shutdown:
                    self._cond.wait()
                if self._pending is None:
                    return
                delay = self._next_at - time.monotonic()
                if delay > 0:
                    self._cond.wait(delay)
                    # A newer state supersedes whatever we were about to send.
                    if self._pending is not None or self._shutdown:
                        continue
                item = self._pending
                self._pending = None
                self._next_at = time.monotonic() + self.interval
            self._send(item)

    def _send(self, item: tuple[str, float, str | None]) -> None:
        if self.binary is None:
            return
        kind, fraction, label = item
        if kind == "set":
            args = [self.binary, "set-progress", f"{fraction:.3f}"]
            if label:
                args += ["--label", label]
        else:
            args = [self.binary, "clear-progress"]
        if self.workspace:
            args += ["--workspace", self.workspace]
        try:
            subprocess.run(
                args,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            self._failures += 1
            if self._failures >= _MAX_FAILURES:
                self._dead = True
        else:
            self._failures = 0

# cmux-tqdm

🤖 [AIL=5](https://danielmiessler.com/blog/ai-influence-level-ail)

A drop-in [tqdm](https://tqdm.github.io) subclass that mirrors progress into
the [cmux](https://cmuxterm.app) sidebar with `cmux set-progress`, so long
experiments are visible outside the terminal.

```python
from cmux_tqdm import cmux_tqdm as tqdm

for epoch in tqdm(range(10), desc="Training"):
    ...
```

Outside a cmux workspace (no `CMUX_WORKSPACE_ID`, or no `cmux` binary) the
class behaves exactly like `tqdm` and spawns nothing.

## Install

```sh
uv add --editable .
# or: pip install -e .
```

## Options

All normal `tqdm` arguments work. Four extras:

| kwarg | default | meaning |
|---|---|---|
| `cmux` | auto | Force the sidebar mirror on/off. Auto = on inside a workspace. |
| `cmux_label` | bar `desc` | Sidebar label. Outermost bar's value wins. |
| `cmux_workspace` | `$CMUX_WORKSPACE_ID` | Target workspace UUID. |
| `cmux_leave` | `False` | Leave the finished bar in the sidebar instead of clearing it. |

Environment overrides:

| var | meaning |
|---|---|
| `CMUX_TQDM_DISABLE=1` | Disable all mirroring. |
| `CMUX_TQDM_BIN` | Path to the `cmux` CLI. |
| `CMUX_TQDM_WORKSPACE` | Target workspace when `CMUX_WORKSPACE_ID` is absent. |
| `CMUX_TQDM_INTERVAL` | Minimum seconds between sidebar updates (default `0.5`). |

## Nested bars

cmux has one progress bar per workspace, so nested tqdm bars are flattened:

```
fraction = (n_outer + fraction_inner) / total_outer
```

e.g. outer 3/10 with inner 50/100 shows `0.35`, labelled `Epoch · Batch`.
Closing the inner bar hands the sidebar back to the outer bar; closing the
outermost bar clears it. Nesting is tracked per thread, in creation order —
two *concurrent* independent bars in one thread are indistinguishable from
nested ones, and bars in different threads overwrite each other
(last writer wins).

Bars without a `total` can't produce a fraction and are skipped while
rendering (an outer bar with a known total still reports its own progress).

## Third-party libraries that use tqdm

tqdm has no plugin API, so there is no zero-touch hook. If the importing
package does `from tqdm import tqdm`, only an early import-time patch can
reach it. `cmux_tqdm.install()` does that explicitly — it swaps
`tqdm.tqdm`, `tqdm.std.tqdm`, `tqdm.auto.tqdm` and `tqdm.asyncio.tqdm` for
cmux-aware subclasses.

Call it before the library imports tqdm, e.g. from a `sitecustomize.py` on
`PYTHONPATH` (imported by the interpreter before user code):

```python
# sitecustomize.py
import cmux_tqdm
cmux_tqdm.install()
```

Modules that already bound `from tqdm import tqdm` before `install()` keep the
original class. Without a cmux binary (or outside a workspace) `install()` is a
no-op and `cmux_tqdm` is a plain tqdm subclass: no worker thread, no
subprocess, no sidebar calls.

## How it works

`display()` (which tqdm already rate-limits) pushes the current fraction into a
per-workspace worker thread. The worker coalesces updates and runs at most one
`cmux set-progress` per interval, so a tight loop pays no subprocess cost
inline. On close — or via `atexit` if the bar is never closed — the bar is
cleared unless `cmux_leave` is set. After three consecutive CLI failures the
mirror disables itself for the rest of the process.

## Tests

```sh
uv run pytest
```

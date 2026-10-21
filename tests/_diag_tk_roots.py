"""Opt-in Tk root creation stacks; enable before discovery with TK_ROOT_DIAGNOSTICS=1.

Only weak references are kept, so tracking does not keep roots alive. Queries
must run on the Tk thread. A destroyed root's Python wrapper is not a leak.
"""
from __future__ import annotations

import functools
import os
import sys
import tkinter as tk
import traceback
import weakref


_roots: list[tuple[weakref.ReferenceType[tk.Tk], str]] = []
_installed = False


def install_tk_root_tracking() -> None:
    global _installed
    if _installed or os.environ.get("TK_ROOT_DIAGNOSTICS") != "1":
        return
    original = tk.Tk.__init__

    @functools.wraps(original)
    def tracked_init(self, *args, **kwargs):
        stack = "".join(traceback.format_stack()[:-1])
        original(self, *args, **kwargs)
        _roots.append((weakref.ref(self), stack))

    tk.Tk.__init__ = tracked_init
    _installed = True


def report_live_tk_roots(where: str) -> None:
    if not _installed:
        return
    alive = []
    for number, (ref, stack) in enumerate(_roots, 1):
        root = ref()
        if root is None:
            continue
        try:
            exists = root.winfo_exists()
        except tk.TclError:
            exists = False
        if exists:
            alive.append((number, stack))
    print(f"[Tk roots] {where}: {len(alive)} live / {len(_roots)} created", file=sys.stderr, flush=True)
    for number, stack in alive:
        print(f"[Tk roots] live root #{number} created at:\n{stack}", file=sys.stderr, flush=True)

"""Shared teardown for tests that create Tk interpreters."""

from __future__ import annotations

import functools
import gc
import threading
import tkinter as tk
import unittest
from typing import Any


def cancel_after_jobs(root: Any) -> None:
    """Cancel every Tcl ``after`` callback registered on *root*."""
    try:
        jobs = root.tk.splitlist(root.tk.call("after", "info"))
    except tk.TclError:
        return
    for job in jobs:
        # Cancel at Tcl level only. ``root.after_cancel`` would also delete the
        # Python command, which may be owned by a child widget (e.g. AdcLegend's
        # after_idle render); that child's destroy() then raises "can't delete
        # Tcl command", aborting root.destroy() and leaking a live root that
        # keeps later mainloop() calls running forever.
        try:
            root.tk.call("after", "cancel", job)
        except tk.TclError:
            pass


def join_threads_since(before: set[threading.Thread]) -> None:
    """Join newly started threads (daemon or not) without blocking indefinitely.

    Matching the Windows Tcl_AsyncDelete mitigation: wait briefly so worker
    threads are not finalizing tkinter Variables while the main thread tears
    the interpreter down.
    """
    for thread in set(threading.enumerate()) - before:
        if thread is not threading.main_thread() and thread.is_alive():
            thread.join(timeout=2.0)


def collect_tk_garbage() -> None:
    """Finalize unreachable Tk objects on the calling (normally main) thread."""
    gc.collect()


def attach_tk_cleanup(test: unittest.TestCase, root: Any, *owned: Any):
    """Register ordered, best-effort cleanup for a Tk root and owned widgets.

    Returns a zero-arg ``release()`` callable that runs the same cleanup early
    (for multi-root loops). Later automatic cleanups become no-ops after release.

    Cleanups run in this order: cancel root ``after`` jobs, destroy owned
    widgets, destroy the root, then clear test references, join new threads,
    and collect garbage on the test thread.
    """
    # Keep the attach-time snapshot required for teardown, and include workers
    # started just after prepare_tk_test (for example during app construction).
    before = set(threading.enumerate())
    before.update(getattr(test, "_tk_cleanup_threads_before", None) or ())
    state: dict[str, Any] = {"root": root, "owned": tuple(owned), "done": False}

    def finalize() -> None:
        root_obj = state.get("root")
        owned_objs = state.get("owned", ())
        object_ids = {id(item) for item in (root_obj, *owned_objs) if item is not None}
        for name, value in tuple(vars(test).items()):
            if id(value) in object_ids:
                setattr(test, name, None)
        state.clear()
        if hasattr(test, "_tk_cleanup_threads_before"):
            test._tk_cleanup_threads_before = None
        join_threads_since(before)
        collect_tk_garbage()

    def destroy_root() -> None:
        root_obj = state.get("root")
        if root_obj is not None:
            try:
                root_obj.destroy()
            except tk.TclError:
                # Never leave a live root behind: fall back to a Tcl-level destroy.
                try:
                    root_obj.tk.call("destroy", root_obj._w)
                except tk.TclError:
                    pass

    def destroy_owned() -> None:
        for widget in state.get("owned", ()):
            destroy = getattr(widget, "destroy", None)
            if destroy is not None:
                try:
                    destroy()
                except tk.TclError:
                    pass

    def cancel_jobs() -> None:
        root_obj = state.get("root")
        if root_obj is not None:
            cancel_after_jobs(root_obj)

    def release() -> None:
        if state.get("done"):
            return
        state["done"] = True
        cancel_jobs()
        destroy_owned()
        destroy_root()
        finalize()

    # unittest executes cleanups in LIFO order; single entry keeps early
    # release and automatic teardown equivalent and idempotent.
    test.addCleanup(release)
    return release


def prepare_tk_test(test: unittest.TestCase) -> None:
    """Collect leftovers on the test thread before creating another Tk root."""
    collect_tk_garbage()
    test._tk_cleanup_threads_before = set(threading.enumerate())


def install_unittest_tk_gc_safety() -> None:
    """Install an idempotent post-cleanup collection on ``TestCase``."""
    original = unittest.TestCase.doCleanups
    if getattr(original, "_tk_gc_safety_wrapper", False):
        return

    @functools.wraps(original)
    def do_cleanups_with_tk_gc(self: unittest.TestCase) -> bool:
        try:
            return original(self)
        finally:
            collect_tk_garbage()

    do_cleanups_with_tk_gc._tk_gc_safety_wrapper = True  # type: ignore[attr-defined]
    unittest.TestCase.doCleanups = do_cleanups_with_tk_gc

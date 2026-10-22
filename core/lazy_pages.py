"""Page registry: ordinary lookups build once; UI broadcasts visit built pages."""
from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any
import time


class LazyPages(dict):
    def __init__(self, install: Callable[[str, Any], None]) -> None:
        super().__init__()
        self._factories: dict[str, Callable[[], Any]] = {}
        self._order: list[str] = []
        self._building: set[str] = set()
        self._staged: set[str] = set()
        self._install = install
        self.preparation_timings_ms: list[tuple[str, float]] = []

    def register(self, name: str, factory: Callable[..., Any], *, staged: bool = False) -> None:
        if name not in self._order:
            self._order.append(name)
        self._factories[name] = factory
        if staged:
            self._staged.add(name)
        else:
            self._staged.discard(name)

    def can_prepare(self, name: str) -> bool:
        return name in self._staged and not dict.__contains__(self, name)

    def prepare(self, name: str) -> Iterator[None]:
        """Build in UI-thread steps; closing cancels and destroys unpublished work."""
        if dict.__contains__(self, name):
            return dict.__getitem__(self, name)
        if name in self._building:
            raise RuntimeError(f"Recursive page construction: {name}")
        factory = self._factories[name]
        self._building.add(name)
        page = None
        self.preparation_timings_ms = []
        try:
            started = time.perf_counter()
            page = factory(defer_build=True) if name in self._staged else factory()
            self.preparation_timings_ms.append(("factory", (time.perf_counter()-started)*1000))
            yield
            steps = iter(getattr(page, '_build_steps', ()))
            while True:
                frame = steps
                while getattr(frame, 'gi_yieldfrom', None) is not None:
                    frame = frame.gi_yieldfrom
                source = getattr(frame, 'gi_frame', None)
                label = f"{source.f_code.co_name}:{source.f_lineno}" if source else "build"
                started = time.perf_counter()
                try:
                    next(steps)
                except StopIteration:
                    self.preparation_timings_ms.append(("finalize:"+label, (time.perf_counter()-started)*1000))
                    break
                self.preparation_timings_ms.append((label, (time.perf_counter()-started)*1000))
                yield
            yield
            started = time.perf_counter()
            self._install(name, page)
            self.preparation_timings_ms.append(("install", (time.perf_counter()-started)*1000))
            dict.__setitem__(self, name, page)
            self._factories.pop(name)
            return page
        except BaseException:
            if page is not None:
                steps = getattr(page, '_build_steps', None)
                try:
                    if steps is not None and hasattr(steps, 'close'):
                        steps.close()
                finally:
                    page.destroy()
            raise
        finally:
            self._building.remove(name)

    def built_values(self) -> list[Any]:
        return list(dict.values(self))

    def get(self, name: str, default: Any = None) -> Any:
        if dict.__contains__(self, name):
            return dict.__getitem__(self, name)
        factory = self._factories.get(name)
        if factory is None:
            return default
        if name in self._building:
            raise RuntimeError(f"Recursive page construction: {name}")
        self._building.add(name)
        page = None
        try:
            page = factory()
            self._install(name, page)
            dict.__setitem__(self, name, page)
            self._factories.pop(name)
            return page
        except Exception:
            if page is not None:
                page.destroy()
            raise
        finally:
            self._building.remove(name)

    def __getitem__(self, name: str) -> Any:
        sentinel = object()
        page = self.get(name, sentinel)
        if page is sentinel:
            raise KeyError(name)
        return page

    def __contains__(self, name: object) -> bool:
        return dict.__contains__(self, name) or name in self._factories

    def __iter__(self):
        return iter(self._order)

    def __len__(self) -> int:
        return len(self._order)

    def keys(self):
        return list(self._order)

    def values(self):
        return [self[name] for name in self._order]

    def items(self):
        return [(name, self[name]) for name in self._order]

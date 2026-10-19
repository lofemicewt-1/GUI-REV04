"""Page registry: ordinary lookups build once; UI broadcasts visit built pages."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any


class LazyPages(dict):
    def __init__(self, install: Callable[[str, Any], None]) -> None:
        super().__init__()
        self._factories: dict[str, Callable[[], Any]] = {}
        self._order: list[str] = []
        self._building: set[str] = set()
        self._install = install

    def register(self, name: str, factory: Callable[[], Any]) -> None:
        if name not in self._order:
            self._order.append(name)
        self._factories[name] = factory

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

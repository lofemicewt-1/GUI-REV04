"""Thread-safe, explicit status events shared by navigation and step progress."""
from __future__ import annotations

from dataclasses import dataclass, field
import threading
import time
from typing import Any, Callable, Hashable, Iterable

from core.ui_dispatch import post_to_ui


@dataclass(frozen=True)
class StatusMessage:
    """A language-table message kept unresolved until the UI renders it."""

    key: str
    args: tuple[tuple[str, Any], ...] = ()

    @classmethod
    def make(cls, key: str, **kwargs: Any) -> "StatusMessage":
        return cls(str(key), tuple(sorted(kwargs.items())))


@dataclass(frozen=True)
class StatusContext:
    """Immutable operation context captured at the point work is started."""

    port_session: Hashable | None
    chip_addr: int | None = None
    mode: str = ""
    activation_epoch: int | None = None
    param_version: int | None = None
    operation_id: int | None = None
    page_name: str = ""
    part_keys: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.chip_addr is not None:
            object.__setattr__(self, "chip_addr", int(self.chip_addr) & 0xFF)
        object.__setattr__(self, "part_keys", tuple(str(key) for key in self.part_keys))


@dataclass(frozen=True)
class StatusToken:
    """Receipt token retaining the origin session and progress generation."""

    bus_id: int
    sequence: int
    context: Any
    event: str
    progress_token: Any = None

    @property
    def port_session(self) -> Hashable | None:
        return getattr(self.context, "port_session", None)


@dataclass(frozen=True)
class StatusUpdate:
    token: StatusToken
    state: str
    text: Any
    timestamp: float
    error: str = ""
    part_key: str | None = None
    part_state: str | None = None


@dataclass
class _Operation:
    token: StatusToken
    text: Any
    invalidated: bool = False
    terminal: str | None = None
    receipts: dict[str, str] = field(default_factory=dict)


def _freeze(value: Any) -> Any:
    """Copy common context containers into hashable immutable values."""
    if isinstance(value, dict):
        return tuple(sorted((_freeze(key), _freeze(item)) for key, item in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, set):
        return frozenset(_freeze(item) for item in value)
    try:
        hash(value)
    except Exception:
        return repr(value)
    return value


def normalized_port_session(value: Any) -> Any:
    """Normalize 192x's ``(router, port, manager, generation)`` tuple.

    1221/1018 contexts use the Router's ``(port, manager, generation)`` tuple
    directly.  Both formats therefore compare on the same transport identity.
    """
    value = _freeze(value)
    if isinstance(value, tuple) and len(value) == 4 and isinstance(value[1], str):
        return value[1:]
    return value


class StatusBus:
    """Publish explicit operation outcomes to Tk-thread subscribers.

    The bus never infers success from a worker return value. Business code must
    call ``ok`` or ``fail`` at its real completion point. ``receipt`` carries
    partial progress and completes a declared multi-part operation only after
    each expected part reports success.
    """

    def __init__(self, ui_owner: Any, *, clock: Callable[[], float] = time.time) -> None:
        self._ui_owner = ui_owner
        self._clock = clock
        self._lock = threading.RLock()
        self._bus_id = id(self)
        self._sequence = 0
        self._subscribers: dict[int, Callable[[StatusUpdate], Any]] = {}
        self._next_subscriber = 0
        self._operations: dict[int, _Operation] = {}
        self._display_sequence = 0
        self._closed = False

    @property
    def display_sequence(self) -> int:
        with self._lock:
            return self._display_sequence

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def begin(
        self,
        text: Any,
        *,
        context: Any,
        event: str,
        progress_token: Any = None,
    ) -> StatusToken:
        """Start an operation and return its immutable receipt token."""
        with self._lock:
            self._sequence += 1
            frozen_context = _freeze(context)
            # Preserve typed contexts; only mutable containers are converted.
            if not isinstance(context, (dict, list, set)):
                frozen_context = context
            token = StatusToken(self._bus_id, self._sequence, frozen_context, str(event), progress_token)
            if self._closed:
                return token
            self._operations[token.sequence] = _Operation(token=token, text=text)
            self._display_sequence = token.sequence
            update = StatusUpdate(token, "running", text, self._clock())
        self._publish(update)
        return token

    def ok(self, token: StatusToken, text: Any) -> bool:
        return self._finish(token, "success", text)

    def fail(self, token: StatusToken, text: Any) -> bool:
        error = ""
        if isinstance(text, StatusMessage):
            values = dict(text.args)
            error = str(values.get("error", ""))
        else:
            error = str(text)
        return self._finish(token, "failure", text, error=error)

    def receipt(
        self,
        token: StatusToken,
        part_key: str,
        state: str,
        *,
        text: Any = None,
        error: str = "",
    ) -> bool:
        """Publish one part of a matching operation without guessing success."""
        state = str(state)
        if state not in {"done", "error", "running", "idle"}:
            raise ValueError(f"unsupported receipt state: {state}")
        with self._lock:
            operation = self._get_operation(token)
            if operation is None or operation.terminal is not None:
                return False
            part = str(part_key)
            operation.receipts[part] = state
            update = StatusUpdate(
                token,
                "receipt",
                operation.text if text is None else text,
                self._clock(),
                str(error),
                part,
                state,
            )
            expected = tuple(getattr(token.context, "part_keys", ()) or ())
            if state == "error":
                operation.terminal = "failure"
                finish_text = text if text is not None else StatusMessage.make(
                    f"sidebar_status_{token.event}_fail", error=str(error or "")
                )
                finish_update = StatusUpdate(token, "failure", finish_text,
                                             self._clock(), str(error or text or ""))
            elif expected and all(operation.receipts.get(key) == "done" for key in expected):
                operation.terminal = "success"
                finish_text = text if text is not None else StatusMessage.make(
                    f"sidebar_status_{token.event}_ok"
                )
                finish_update = StatusUpdate(token, "success", finish_text, self._clock())
            else:
                finish_update = None
        self._publish(update)
        if finish_update is not None:
            self._publish(finish_update)
        return True

    def subscribe(self, callback: Callable[[StatusUpdate], Any]) -> Callable[[], None]:
        """Subscribe to updates; the returned closure removes the subscriber."""
        with self._lock:
            if self._closed:
                return lambda: None
            self._next_subscriber += 1
            key = self._next_subscriber
            self._subscribers[key] = callback

        def unsubscribe() -> None:
            with self._lock:
                self._subscribers.pop(key, None)

        return unsubscribe

    def accepts(self, token: StatusToken | None) -> bool:
        if token is None or token.bus_id != self._bus_id:
            return False
        with self._lock:
            operation = self._operations.get(token.sequence)
            return bool(not self._closed and operation is not None and not operation.invalidated)

    def invalidate_context(
        self,
        context: Any = None,
        *,
        session: Any = None,
        token: StatusToken | None = None,
        except_token: StatusToken | None = None,
    ) -> int:
        """Invalidate stale work by token, exact context, or port session."""
        frozen = _freeze(context) if context is not None else None
        session_key = normalized_port_session(session) if session is not None else None
        count = 0
        with self._lock:
            for operation in self._operations.values():
                if operation.invalidated:
                    continue
                item = operation.token
                if except_token is not None and item == except_token:
                    continue
                if token is not None and item == token:
                    match = True
                elif context is not None:
                    match = _freeze(item.context) == frozen or _freeze(item.progress_token) == frozen
                    progress_context = getattr(item.progress_token, "context", None)
                    if not match and progress_context is not None:
                        match = _freeze(progress_context) == frozen
                elif session is not None:
                    own_session = getattr(item.context, "port_session", None)
                    if own_session is None:
                        own_session = getattr(getattr(item.progress_token, "context", None), "port_session", None)
                    normalized_own = normalized_port_session(own_session)
                    match = normalized_own == session_key
                    if isinstance(session, str) and isinstance(normalized_own, tuple):
                        match = bool(
                            normalized_own
                            and (normalized_own[0] == session.strip()
                                 or (len(normalized_own) > 1 and normalized_own[1] == session.strip()))
                        )
                else:
                    match = False
                if match:
                    operation.invalidated = True
                    count += 1
        return count

    def invalidate_progress(self, progress_token: Any) -> int:
        """Invalidate receipts tied to one landed step-progress token."""
        count = 0
        with self._lock:
            for operation in self._operations.values():
                if not operation.invalidated and operation.token.progress_token == progress_token:
                    operation.invalidated = True
                    count += 1
        return count

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._subscribers.clear()
            self._operations.clear()

    def _get_operation(self, token: StatusToken) -> _Operation | None:
        if self._closed or token.bus_id != self._bus_id:
            return None
        operation = self._operations.get(token.sequence)
        if operation is None or operation.invalidated or operation.token != token:
            return None
        return operation

    def _finish(self, token: StatusToken, state: str, text: Any, *, error: str = "") -> bool:
        with self._lock:
            operation = self._get_operation(token)
            if operation is None or operation.terminal is not None:
                return False
            operation.terminal = state
            update = StatusUpdate(token, state, text, self._clock(), str(error))
        self._publish(update)
        return True

    def _publish(self, update: StatusUpdate) -> None:
        with self._lock:
            if self._closed:
                return
            keys = tuple(self._subscribers)
        for key in keys:
            post_to_ui(self._ui_owner, self._deliver, key, update)

    def _deliver(self, key: int, update: StatusUpdate) -> None:
        with self._lock:
            callback = self._subscribers.get(key)
            operation = self._operations.get(update.token.sequence)
            valid = bool(
                not self._closed
                and callback is not None
                and operation is not None
                and not operation.invalidated
                and operation.token == update.token
            )
        if valid and callback is not None:
            callback(update)


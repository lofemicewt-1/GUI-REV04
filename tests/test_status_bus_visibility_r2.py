from __future__ import annotations

import contextlib
import queue
import tempfile
import threading
import time
import tkinter as tk
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.ui_state_store import UiStateStore
from app.application import PiezoApplication
from app.navigation import Navigation
from core.base_tool import SerialResponse
from core.status_bus import StatusBus, StatusContext, StatusMessage
from tests.validate_responsive_layout import mocked_application


class _FakeDispatcher:
    def __init__(self) -> None:
        self.items = queue.SimpleQueue()
        self.closed = False

    def post(self, callback, *args) -> bool:
        if self.closed:
            return False
        self.items.put((callback, args))
        return True

    def drain(self) -> int:
        count = 0
        while not self.items.empty():
            callback, args = self.items.get()
            callback(*args)
            count += 1
        return count


class _FakeOwner:
    def __init__(self) -> None:
        self._piezo_ui_dispatcher = _FakeDispatcher()


class StatusBusRound2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.owner = _FakeOwner()
        self.dispatcher = self.owner._piezo_ui_dispatcher
        self.bus = StatusBus(self.owner)
        self.updates = []
        self.unsubscribe = self.bus.subscribe(self.updates.append)

    def tearDown(self) -> None:
        self.unsubscribe()
        self.bus.close()
        self.dispatcher.closed = True

    def _pump_until(self, predicate, timeout: float = 1.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not predicate():
            self.dispatcher.drain()
            time.sleep(0.002)
        self.dispatcher.drain()

    def test_worker_delivery_and_explicit_failure_only(self) -> None:
        token_box = []

        def worker() -> None:
            token = self.bus.begin(
                StatusMessage.make("running"),
                context=StatusContext(("COM4", 11, 2), chip_addr=0x50, mode="button"),
                event="waveform",
            )
            token_box.append(token)
            # A false transport response has no automatic bus meaning.
            response_ok = False
            if response_ok:
                self.bus.ok(token, StatusMessage.make("success"))

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join(1)
        self._pump_until(lambda: bool(self.updates))
        self.assertEqual([item.state for item in self.updates], ["running"])
        self.assertTrue(self.bus.accepts(token_box[0]))
        self.assertTrue(self.bus.fail(token_box[0], StatusMessage.make("explicit failure", error="fake NACK")))
        self._pump_until(lambda: len(self.updates) >= 2)
        self.assertEqual(self.updates[-1].state, "failure")
        self.assertEqual(self.updates[-1].error, "fake NACK")

    def test_receipts_aggregate_only_after_all_expected_parts(self) -> None:
        token = self.bus.begin(
            StatusMessage.make("adc begin"),
            context=StatusContext(("COM5", 21, 3), part_keys=("settings", "stage0", "stage1")),
            event="adc",
        )
        self.bus.receipt(token, "settings", "done")
        self.bus.receipt(token, "stage0", "done")
        self._pump_until(lambda: len(self.updates) >= 3)
        self.assertEqual([item.state for item in self.updates], ["running", "receipt", "receipt"])
        self.bus.receipt(token, "stage1", "done")
        self._pump_until(lambda: any(item.state == "success" for item in self.updates))
        self.assertEqual(self.updates[-1].state, "success")
        self.assertFalse(self.bus.receipt(token, "stage1", "done"))

    def test_competing_operations_context_invalidation_and_close_fence(self) -> None:
        old = self.bus.begin("old", context=StatusContext(("COM8", 1, 1)), event="discovery")
        current = self.bus.begin("new", context=StatusContext(("COM8", 1, 2)), event="discovery")
        self.assertTrue(self.bus.ok(old, "old complete"))
        self.assertEqual(self.bus.display_sequence, current.sequence)
        self.assertEqual(self.bus.invalidate_context(session="COM8"), 2)
        self.assertFalse(self.bus.accepts(current))
        self.assertFalse(self.bus.ok(current, "stale"))

        fresh = self.bus.begin("fresh", context=StatusContext(("COM9", 3, 7)), event="connect")
        self.bus.close()
        self.assertFalse(self.bus.ok(fresh, "after close"))
        before = len(self.updates)
        self.dispatcher.drain()
        self.assertEqual(len(self.updates), before)

    def test_cancelled_progress_and_stale_queued_receipts_are_fenced(self) -> None:
        progress_token = object()
        token = self.bus.begin(
            "running",
            context=StatusContext(("COM6", 12, 4), part_keys=("left", "right")),
            event="adc",
            progress_token=progress_token,
        )
        self.assertEqual(self.bus.invalidate_progress(progress_token), 1)
        self.dispatcher.drain()
        self.assertEqual(self.updates, [])
        self.assertFalse(self.bus.receipt(token, "left", "done"))
        self.assertFalse(self.bus.ok(token, "late completion"))

    def test_stale_success_timer_cannot_expire_a_newer_or_other_session_status(self) -> None:
        nav = Navigation.__new__(Navigation)
        nav._status_display_sequence = 4
        nav._status_expire_after = "timer"
        nav._status_state = "success"
        nav._status_detail = "latest"
        nav._status_error = ""
        nav.ctx = SimpleNamespace(
            status_bus=SimpleNamespace(display_sequence=4),
            state=SimpleNamespace(selected_port="COM1"),
            serial=SimpleNamespace(
                selected_port_for_page=lambda _page: "COM1",
                connection_identity=lambda port: ("COM1", 200, 3) if port == "COM1" else None,
            ),
        )
        nav._render_status = lambda: None

        nav._expire_status(3, ("COM1", 10, 2))
        self.assertEqual(nav._status_state, "success")
        nav._expire_status(4, ("COM2", 20, 3))
        self.assertEqual(nav._status_state, "success")
        nav._expire_status(4, ("COM1", 100, 2))
        self.assertEqual(nav._status_state, "success")

    def test_invalidated_operation_timer_cannot_clear_the_sidebar(self) -> None:
        token = self.bus.begin(
            "success",
            context=StatusContext(("COM1", 100, 2), page_name="awp1221"),
            event="waveform",
        )
        nav = Navigation.__new__(Navigation)
        nav._status_display_sequence = token.sequence
        nav._status_expire_after = "timer"
        nav._status_state = "success"
        nav._status_detail = "waveform complete"
        nav._status_error = ""
        nav.ctx = SimpleNamespace(
            status_bus=self.bus,
            state=SimpleNamespace(selected_port="COM1"),
            serial=SimpleNamespace(
                selected_port_for_page=lambda _page: "COM1",
                connection_identity=lambda _port: ("COM1", 100, 2),
            ),
        )
        nav._render_status = lambda: None
        self.bus.invalidate_context(token=token)

        nav._expire_status(token.sequence, token)

        self.assertEqual(nav._status_state, "success")


class DiscoveryCommitGuardTests(unittest.TestCase):
    def test_commit_requires_current_selected_port_and_matching_session(self) -> None:
        class FakeNavigation:
            def __init__(self):
                self.calls = []

            def set_chip_pages_visible(self, visible, highlight=None):
                self.calls.append(("visibility", visible, highlight))

            def set_connection_notice(self, key):
                self.calls.append(("notice", key))

        state = SimpleNamespace(extra={})
        navigation = FakeNavigation()
        serial = SimpleNamespace(
            selected_port_for_page=lambda _page: "COM7",
            connection_identity=lambda _port: ("COM7", 100, 3),
        )
        app = SimpleNamespace(serial=serial, state=state, navigation=navigation)
        apply_state = PiezoApplication.apply_chip_discovery_state

        common = dict(
            chip_name="AWP1922",
            address=0x58,
            port="COM7",
            page_name="awp1922",
            connection_detail="AWP1922 0x58",
        )
        self.assertFalse(apply_state(app, session=None, **common))
        self.assertFalse(apply_state(app, session=("COM7", 99, 2), **common))
        self.assertEqual(state.extra, {})
        self.assertEqual(navigation.calls, [])

        self.assertTrue(apply_state(app, session=("COM7", 100, 3), **common))
        self.assertEqual(state.extra["connection_detail"], "AWP1922 0x58")
        self.assertEqual(navigation.calls, [
            ("visibility", True, "awp1922"),
            ("notice", None),
        ])

        serial.selected_port_for_page = lambda _page: None
        self.assertFalse(apply_state(app, session=("COM7", 100, 3), **common))


class ChipVisibilityRound2Tests(unittest.TestCase):
    @contextlib.contextmanager
    def _mocked_application_or_skip(self):
        manager = mocked_application()
        try:
            app = manager.__enter__()
        except tk.TclError as exc:
            self.skipTest(f"Tk display unavailable: {exc}")
        try:
            yield app
        finally:
            manager.__exit__(None, None, None)

    def _pump_status(self, app) -> None:
        """Drain StatusBus post_to_ui deliveries onto the Tk sidebar."""
        dispatcher = getattr(app, "ui_dispatcher", None)
        if dispatcher is not None:
            dispatcher.drain()
        app.root.update_idletasks()
        app.root.update()

    def test_fake_connect_ready_discovery_and_manual_disconnect(self) -> None:
        with self._mocked_application_or_skip() as app:
            nav, home = app.navigation, app.navigation.pages["home"]
            connected = [False]
            session = ("COM9", 404, 8)
            home.port_var.set("COM9")
            home.port_names["COM9"] = "Fake board"

            def discover_after_ready(port, _serial):
                self.assertEqual(port, "COM9")
                self.assertTrue(app.apply_chip_discovery_state(
                    chip_name="AWP1221",
                    address=0x50,
                    port="COM9",
                    session=session,
                    page_name="home",
                    connection_detail="AWP1221 0x50",
                ))

            with (
                patch.object(app.serial, "connect", return_value=SerialResponse(True, data="READY")),
                patch.object(app.serial, "disconnect", side_effect=lambda _port: connected.__setitem__(0, False)),
                patch.object(app.serial, "is_port_connected", side_effect=lambda _port: connected[0]),
                patch.object(app.serial, "connected_ports", side_effect=lambda: ["COM9"] if connected[0] else []),
                patch.object(app.serial, "connection_identity", return_value=session),
                patch.object(app.serial, "selected_port_for_page", return_value="COM9"),
                patch.object(app.serial, "is_port_ready", return_value=True),
                patch.object(home, "_serial_for_port", return_value=SimpleNamespace(board_identity="Fake REV04")),
                patch.object(home, "_status_refresh_session", lambda *_args: None),
                patch.object(home, "_probe_status_capability_once", lambda *_args: None),
                patch.object(home, "_detect_awp192x_after_connect", side_effect=discover_after_ready),
                patch.object(home, "_status_invalidate_port", lambda *_args, **_kwargs: None),
                patch.object(home, "_cancel_continuous_read", lambda *_args, **_kwargs: None),
                patch.object(home, "_stop_flag_poll", lambda: None),
                patch.object(home, "refresh_ports", lambda: None),
            ):
                # Model the fake router's connection state without opening a port.
                original_connect = app.serial.connect

                def fake_connect(*args, **kwargs):
                    result = original_connect(*args, **kwargs)
                    connected[0] = True
                    return result

                with patch.object(app.serial, "connect", side_effect=fake_connect):
                    home.toggle_connection()
                    deadline = time.monotonic() + 2
                    while time.monotonic() < deadline and getattr(home, "_connect_attempt", None) is not None:
                        app.root.update()
                        time.sleep(0.005)
                    app.root.update()
                    self.assertIsNone(getattr(home, "_connect_attempt", None))
                    self.assertEqual(nav._visibility["highlight"], "awp1221")
                    self.assertEqual(nav._visibility["phase"], "ready_identified")

                    app.state.current_page = "awp1221"
                    home.toggle_connection()
                    app.root.update()
                    self.assertFalse(connected[0])
                    self.assertEqual(nav._visibility["phase"], "disconnected")
                    self.assertTrue(nav._page_disabled("awp1221"))
                    self.assertFalse(nav._page_visible("awp1018"))
                    self.assertEqual(nav._connection_notice_key, "sidebar_disconnected_reconnect")

    def test_explicit_discovery_map_unknown_disconnect_reconnect_and_stale_com(self) -> None:
        with self._mocked_application_or_skip() as app:
            nav = app.navigation
            home = nav.pages["home"]
            old_session = ("COM7", 101, 4)
            new_session = ("COM7", 202, 5)
            with (
                patch.object(app.serial, "selected_port_for_page", return_value="COM7"),
                patch.object(app.serial, "connection_identity", return_value=old_session),
            ):
                for chip_name, page in (
                    ("AWP1221", "awp1221"),
                    ("AWP192X", "awp1921_1923"),
                    ("AWP1018", "awp1018"),
                    ("AWP1922", "awp1922"),
                ):
                    self.assertTrue(app.apply_chip_discovery_state(
                        chip_name=chip_name,
                        address=0x50,
                        port="COM7",
                        session=old_session,
                        page_name="home",
                        connection_detail=f"{chip_name} 0x50",
                    ))
                    self.assertEqual(nav._visibility["highlight"], page)
                    self.assertTrue(nav._page_visible(page))
                    self.assertEqual(app.state.current_page, "home")

                nav.set_connection_phase("waiting_ready")
                self.assertTrue(app.apply_chip_discovery_state(
                    chip_name=None,
                    address=None,
                    port="COM7",
                    session=old_session,
                    page_name="home",
                    connection_detail="COM7: board online; supported chip not detected",
                ))
                self.assertEqual(nav._visibility["phase"], "ready_unidentified")
                self.assertTrue(all(nav._page_visible(page) for page in nav._chip_group_by_page))
                self.assertEqual(nav._connection_notice_key, "sidebar_chip_unidentified_en_hint")

            app.state.current_page = "awp1221"
            app.state.selected_port = "COM7"
            with (
                patch.object(app.serial, "manager_for_port", return_value=None),
                patch.object(app.serial, "selected_port_for_page", return_value="COM7"),
                patch.object(app.serial, "connected_ports", return_value=[]),
                patch.object(home, "_status_invalidate_port", lambda *_args, **_kwargs: None),
                patch.object(home, "_cancel_continuous_read", lambda *_args, **_kwargs: None),
                patch.object(home, "_stop_flag_poll", lambda: None),
                patch.object(home, "refresh_ports", lambda: None),
                patch.object(home, "refresh_language", lambda: None),
            ):
                home._handle_serial_connection_lost("COM7", "fake disconnect")
            self.assertEqual(nav._visibility["phase"], "disconnected")
            self.assertTrue(nav._page_visible("awp1221"))
            self.assertTrue(nav._page_disabled("awp1221"))
            self.assertFalse(nav._page_visible("awp1018"))
            self.assertEqual(nav._connection_notice_key, "sidebar_disconnected_reconnect")
            self.assertEqual(app.state.current_page, "awp1221")

            nav.set_connection_phase("waiting_ready")
            nav.set_connection_notice(None)
            with (
                patch.object(app.serial, "selected_port_for_page", return_value="COM7"),
                patch.object(app.serial, "connection_identity", return_value=new_session),
            ):
                self.assertFalse(app.apply_chip_discovery_state(
                    chip_name="AWP1018",
                    address=0x48,
                    port="COM7",
                    session=old_session,
                    page_name="home",
                    connection_detail="stale old generation",
                ))
                self.assertTrue(app.apply_chip_discovery_state(
                    chip_name="AWP1922",
                    address=0x46,
                    port="COM7",
                    session=new_session,
                    page_name="home",
                    connection_detail="AWP1922 0x46",
                ))
            self.assertTrue(nav._page_visible("awp1922"))
            self.assertEqual(nav._visibility["highlight"], "awp1922")
            self.assertEqual(app.state.current_page, "awp1221")

            app.state.selected_port = "COM7"
            before = dict(nav._visibility)
            with (
                patch.object(app.serial, "manager_for_port", return_value=None),
                patch.object(app.serial, "selected_port_for_page", return_value="COM7"),
                patch.object(app.serial, "connected_ports", return_value=["COM7"]),
                patch.object(home, "_status_invalidate_port", lambda *_args, **_kwargs: None),
                patch.object(home, "_cancel_continuous_read", lambda *_args, **_kwargs: None),
                patch.object(home, "_stop_flag_poll", lambda: None),
                patch.object(home, "refresh_ports", lambda: None),
                patch.object(home, "refresh_language", lambda: None),
            ):
                home._handle_serial_connection_lost("COM8", "unrelated fake loss")
            self.assertEqual(nav._visibility, before)
            self.assertIsNone(nav._connection_notice_key)

    def test_tk_layout_refreshes_pending_navigation_override_and_prompt_expiry(self) -> None:
        from tkinter import ttk

        with self._mocked_application_or_skip() as app:
            app.set_animations(False)
            nav, root = app.navigation, app.root
            root.update()
            chip_pages = tuple(nav._chip_group_by_page)
            self.assertTrue(all(not nav._page_visible(page) for page in chip_pages))

            nav.set_chip_pages_visible(True, highlight="AWP1221")
            nav.request_show("awp1221")
            self.assertEqual(nav._pending_page, "awp1221")
            nav.set_connection_phase("offline")
            root.update()
            self.assertIsNone(nav._pending_page)
            self.assertEqual(app.state.current_page, "home")
            nav.refresh_language()
            nav.refresh_theme()
            nav.refresh_layout()
            nav.motion.refresh()
            root.update()
            self.assertTrue(all(not nav._page_visible(page) for page in chip_pages))
            self.assertTrue(all(not nav.buttons[page].winfo_ismapped() for page in chip_pages))

            nav.set_developer_visibility_override(True)
            root.update()
            self.assertTrue(all(nav._page_visible(page) for page in chip_pages))
            nav.set_developer_visibility_override(False)
            root.update()
            self.assertTrue(all(not nav._page_visible(page) for page in chip_pages))

            # A cached page and its mode controls remain cached while hidden;
            # layout and animation refreshes cannot put them back in the rail.
            nav._chip_group_by_page["test_chip"] = "awp1221"
            nav.register_page("test_chip", "home", lambda: ttk.Frame(nav.content))
            cached = nav.pages["test_chip"]
            cached.test_cached_value = "preserve me"
            app.state.current_page = "test_chip"
            fake_mode_host = SimpleNamespace(
                page_name="test_chip",
                current_mode="button_mode",
                mode_pages={"button_mode": object(), "full_mode": object()},
                mode_buttons={},
            )
            nav.set_chip_pages_visible(True, highlight="AWP1221")
            nav._sync_mode_buttons(fake_mode_host)
            root.update()
            self.assertTrue(nav.mode_clip.winfo_ismapped())
            nav.set_connection_phase("offline")
            nav.refresh_language()
            nav.refresh_theme()
            nav.refresh_layout()
            nav.motion.refresh()
            root.update()
            self.assertFalse(nav.buttons["test_chip"].winfo_ismapped())
            self.assertFalse(nav.mode_clip.winfo_ismapped())
            self.assertIs(nav.pages["test_chip"], cached)
            self.assertEqual(cached.test_cached_value, "preserve me")
            app.state.current_page = "home"

            nav.set_connection_notice("sidebar_chip_unidentified_en_hint")
            token = app.ctx.status_bus.begin(
                StatusMessage.make("sidebar_status_waveform_begin"),
                context=StatusContext(("COM3", 33, 2)),
                event="waveform",
            )
            app.ctx.status_bus.ok(token, StatusMessage.make("sidebar_status_waveform_ok"))
            self._pump_status(app)
            self.assertEqual(nav._status_state, "success")
            self.assertLessEqual(len(nav._status_text.cget("text")), 14)
            tip = nav._status_tooltip_text()
            self.assertTrue(
                ("Waveform written" in tip) or ("波形已写入" in tip),
                tip,
            )
            nav._expire_status(token.sequence, token)
            self.assertEqual(nav._status_state, "ready")
            self.assertEqual(nav._connection_notice_key, "sidebar_chip_unidentified_en_hint")

            failing = app.ctx.status_bus.begin(
                StatusMessage.make("sidebar_status_register_write_begin"),
                context=StatusContext(("COM3", 33, 2)),
                event="register_write",
            )
            app.ctx.status_bus.fail(failing, StatusMessage.make(
                "sidebar_status_register_write_fail", error="fake NACK",
            ))
            self._pump_status(app)
            self.assertEqual(nav._status_state, "failure")
            self.assertIn("fake NACK", nav._status_tooltip_text())
            # Stale success expiry for the earlier waveform token must not
            # clear a newer failure (matches production after() args: token).
            nav._expire_status(token.sequence, token)
            self.assertEqual(nav._status_state, "failure")
            self.assertEqual(nav._connection_notice_key, "sidebar_chip_unidentified_en_hint")

    def test_visibility_override_is_persisted_locally(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ui_state.json"
            store = UiStateStore(path)
            self.assertFalse(store.get_always_show_chip_pages())
            store.set_always_show_chip_pages(True)
            self.assertTrue(UiStateStore(path).get_always_show_chip_pages())


if __name__ == "__main__":
    unittest.main()

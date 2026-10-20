from __future__ import annotations

from types import SimpleNamespace
import threading
import time
import unittest

from core.register_settings_queue import RegisterSetting, RegisterSettingsQueue
from core.serial_manager import SerialRouter
from tests.test_awp1018_show_io import SlowManager


class RegisterSettingsQueueTests(unittest.TestCase):
    def test_read_failure_or_malformed_echo_never_writes_and_later_setting_runs(self):
        for invalid in (False, True):
            with self.subTest(invalid=invalid):
                manager, tasks = SlowManager(0), RegisterSettingsQueue()
                (manager.invalid_reads if invalid else manager.fail_reads).add(0x06)
                tasks.submit("bad", RegisterSetting(manager, 0x48, 0x06, 0x4000, 0xC000, lambda: True))
                tasks.submit("good", RegisterSetting(manager, 0x48, 0x27, 7, None, lambda: True))
                self.assertTrue(tasks.wait_idle(1))
                self.assertEqual([c[2] for c in manager.calls if c[0] == "write"], [0x27])
                self.assertEqual((tasks.failed,tasks.completed),(1,1))

    def test_worker_exception_releases_transaction_and_runs_next_setting(self):
        manager,tasks=SlowManager(0),RegisterSettingsQueue()
        def fail(reg):
            raise OSError("fake disconnect")
        manager.read_hook=fail
        tasks.submit("bad",RegisterSetting(manager,0x48,0x06,0,0xC000,lambda:True))
        tasks.submit("good",RegisterSetting(manager,0x48,0x27,5,None,lambda:True))
        self.assertTrue(tasks.wait_idle(1))
        self.assertEqual((tasks.failed,tasks.completed),(1,1))
        self.assertEqual(manager.registers[0x27],5)

    def test_pending_burst_is_bounded_and_coalesces_latest_field(self):
        manager, tasks = SlowManager(0), RegisterSettingsQueue(capacity=1)
        entered, release = threading.Event(), threading.Event()
        manager.write_hook=lambda reg: (entered.set(),release.wait(2)) if reg==0x27 else None
        tasks.submit("block",RegisterSetting(manager,0x48,0x27,1,None,lambda:True))
        self.assertTrue(entered.wait(1))
        try:
            for value in range(200):
                self.assertTrue(tasks.submit("field",RegisterSetting(manager,0x48,0x22,value,0x3FF,lambda:True)))
            self.assertFalse(tasks.submit("overflow",RegisterSetting(manager,0x48,0x06,0,0xC000,lambda:True)))
        finally:
            release.set()
        self.assertTrue(tasks.wait_idle(1))
        self.assertEqual(manager.registers[0x22],0xF8C7)
        self.assertEqual(tasks.coalesced,199)
        self.assertEqual([c[2] for c in manager.calls if c[0]=="write"],[0x27,0x22])

    def test_close_during_read_cancels_stale_write_without_joining(self):
        manager,tasks=SlowManager(0),RegisterSettingsQueue()
        entered,release=threading.Event(),threading.Event()
        manager.read_hook=lambda reg:(entered.set(),release.wait(2))
        tasks.submit("rmw",RegisterSetting(manager,0x48,0x06,0x4000,0xC000,lambda:True))
        self.assertTrue(entered.wait(1))
        try:
            tasks.submit("next",RegisterSetting(manager,0x48,0x27,2,None,lambda:True))
            started=time.perf_counter()
            tasks.close()
            self.assertLess(time.perf_counter()-started,.05)
        finally:
            release.set()
        self.assertTrue(tasks.wait_idle(1))
        self.assertFalse([c for c in manager.calls if c[0]=="write"])
        self.assertEqual(tasks.cancelled,2)


class RoutedRegisterSettingsTests(unittest.TestCase):
    def make_router(self):
        state=SimpleNamespace(current_page="awp1018",selected_port="FAKE_A",serial_connected=True,extra={"awp1018_device_addr":0x48})
        router=SerialRouter(state)
        manager=SlowManager(0)
        router._managers["FAKE_A"]=manager
        router.set_page_port("awp1018","FAKE_A")
        self.addCleanup(router.disconnect_all)
        return router,manager,state

    def test_changing_current_chip_does_not_retarget_captured_request(self):
        router,manager,state=self.make_router()
        other=SlowManager(0)
        router._managers["FAKE_B"]=other
        entered,release=threading.Event(),threading.Event()
        manager.read_hook=lambda reg:(entered.set(),release.wait(2))
        router.queue_register_setting("awp1018",0x48,0x06,0x4000,clear_mask=0xC000,address_state_key="awp1018_device_addr")
        self.assertTrue(entered.wait(1))
        try:
            state.current_page="awp1221"
            state.selected_port="FAKE_B"
        finally:
            release.set()
        self.assertTrue(router._register_settings_queue.wait_idle(1))
        self.assertEqual(manager.registers[0x06],0x7FFF)
        self.assertFalse(other.calls)

    def test_connection_replacement_generation_port_and_address_cancel_stale_rmw(self):
        for change in ("generation","generation_shadow","manager","port","address","disconnect"):
            with self.subTest(change=change):
                router,manager,state=self.make_router()
                if change=="generation_shadow":
                    router._manager_connection_generations[id(manager)]=10
                entered,release=threading.Event(),threading.Event()
                manager.read_hook=lambda reg:(entered.set(),release.wait(2))
                router.queue_register_setting("awp1018",0x48,0x06,0x4000,clear_mask=0xC000,address_state_key="awp1018_device_addr")
                self.assertTrue(entered.wait(1))
                try:
                    if change in {"generation","generation_shadow"}:manager.connection_generation+=1
                    elif change=="manager":router._managers["FAKE_A"]=SlowManager(0)
                    elif change=="port":router.set_page_port("awp1018","FAKE_B")
                    elif change=="address":state.extra["awp1018_device_addr"]=0x49
                    else:manager.is_connected=False
                finally:
                    release.set()
                self.assertTrue(router._register_settings_queue.wait_idle(1))
                self.assertFalse([c for c in manager.calls if c[0]=="write"])
                self.assertEqual(router._register_settings_queue.cancelled,1)

    def test_reconnect_just_before_locked_write_drops_old_read_result(self):
        from contextlib import contextmanager
        router,manager,state=self.make_router()
        guards=[]
        @contextmanager
        def connection_guard():
            with manager.lock:
                guards.append(True)
                manager.connection_generation+=1
                yield
        manager.connection_guard=connection_guard
        router.queue_register_setting("awp1018",0x48,0x06,0x4000,clear_mask=0xC000)
        self.assertTrue(router._register_settings_queue.wait_idle(1))
        self.assertEqual(guards,[True],"write did not enter the transport connection guard")
        self.assertFalse([c for c in manager.calls if c[0]=="write"])
        self.assertEqual(router._register_settings_queue.cancelled,1)

    def test_later_explicit_write_is_not_overwritten_by_old_pending_default(self):
        router,manager,state=self.make_router()
        entered,release=threading.Event(),threading.Event()
        manager.write_hook=lambda reg:(entered.set(),release.wait(2)) if reg==0x27 else None
        router.queue_register_setting("awp1018",0x48,0x27,1)
        self.assertTrue(entered.wait(1))
        try:
            router.queue_register_setting("awp1018",0x48,0x22,100,clear_mask=0x3FF)
            response=router.write_register_value(0x48,0x22,0xABCD,bits=16)
            self.assertFalse(response.ok)
        finally:
            release.set()
        self.assertTrue(router._register_settings_queue.wait_idle(1))
        self.assertEqual(manager.registers[0x22],0xF800,"stale default was not cancelled after explicit intent")
        self.assertTrue(router.write_register_value(0x48,0x22,0xABCD,bits=16).ok)
        self.assertEqual(manager.registers[0x22],0xABCD)

    def test_bulk_write_cancels_pending_defaults_only_for_its_register_range(self):
        router,manager,state=self.make_router()
        entered,release=threading.Event(),threading.Event()
        manager.write_hook=lambda reg:(entered.set(),release.wait(2)) if reg==0x27 else None
        router.queue_register_setting("awp1018",0x48,0x27,1)
        self.assertTrue(entered.wait(1))
        try:
            router.queue_register_setting("awp1018",0x48,0x22,100,clear_mask=0x3FF)
            router.queue_register_setting("awp1018",0x48,0x06,0x4000,clear_mask=0xC000)
            self.assertFalse(router.write_register_values(0x48,0x21,[0x2222,0x3333],bits=16).ok)
        finally:
            release.set()
        self.assertTrue(router._register_settings_queue.wait_idle(1))
        self.assertEqual(manager.registers[0x22],0xF800)
        self.assertTrue(router.write_register_values(0x48,0x21,[0x2222,0x3333],bits=16).ok)
        self.assertEqual(manager.registers[0x22],0x3333)
        self.assertEqual(manager.registers[0x06],0x7FFF)

    def test_action_cannot_overtake_pending_mode_setting(self):
        router,manager,state=self.make_router()
        entered,release=threading.Event(),threading.Event()
        manager.write_hook=lambda reg:(entered.set(),release.wait(2)) if reg==0x27 else None
        router.queue_register_setting("awp1018",0x48,0x27,1)
        self.assertTrue(entered.wait(1))
        try:
            router.queue_register_setting("awp1018",0x48,0x34,0,clear_mask=8)
            response=router.write_register_value(0x48,0x02,1,bits=16)
        finally:
            release.set()
        self.assertTrue(router._register_settings_queue.wait_idle(1))
        self.assertFalse(response.ok,"action overtook an unapplied chip mode")
        self.assertIn("busy",response.error.lower())
        self.assertNotIn(0x02,manager.registers)
        self.assertTrue(router.write_register_value(0x48,0x02,1,bits=16).ok)

    def test_existing_transaction_owner_can_finish_while_new_setting_waits(self):
        router,manager,state=self.make_router()
        with manager.snapshot_transaction():
            router.queue_register_setting("awp1018",0x48,0x27,1)
            self.assertTrue(router.write_register_value(0x48,0x22,0xABCD,bits=16).ok)
        self.assertTrue(router._register_settings_queue.wait_idle(1))
        self.assertEqual(manager.registers[0x22],0xABCD)

    def test_explicit_unconnected_page_does_not_fall_back_to_another_board(self):
        router,manager,state=self.make_router()
        router.set_page_port("awp1018","FAKE_MISSING")
        router.queue_register_setting("awp1018",0x48,0x27,9)
        self.assertIsNone(router._register_settings_queue)
        self.assertFalse(manager.calls)


if __name__ == "__main__":
    unittest.main()

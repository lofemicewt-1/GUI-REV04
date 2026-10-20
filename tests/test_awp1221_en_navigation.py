from __future__ import annotations
from types import SimpleNamespace
from unittest.mock import Mock, patch
import unittest
from pages.awp1221_page import AWP1221Page
from pages.base_page import BasePage

class Value:
    def __init__(self, value=None): self.value=value
    def get(self): return self.value
    def set(self, value): self.value=value

class EnNavigationTests(unittest.TestCase):
    def setUp(self):
        self.identity=('COM14',123,1)
        self.serial=SimpleNamespace(is_connected=True,
            selected_port_for_page=lambda name: self.identity[0] if self.identity else None,
            page_connection_identity=lambda name:self.identity,
            configure_gpio=Mock(side_effect=lambda pin,mode:SimpleNamespace(ok=True,data=f'GPIOCFG {pin} {mode}')))
        self.page=object.__new__(AWP1221Page)
        self.page.ctx=SimpleNamespace(serial=self.serial)
        self.page.tr=lambda key:key
        self.page.en_enabled_var=Value(False)
        self.page.en_status_var=Value('')
        self.page._en_state=None
        self.page._shared_d4_panel_identity=None
        self.page._shared_state_owner=SimpleNamespace(invalidate_all=Mock())
        self.page._invalidate_gpio_settings=Mock()
        self.page._notify_shared_d4_gpio_panel=Mock()
        self.page._set_en_high_adc_load=Mock()
        self.enter_patch=patch.object(BasePage,'on_show',lambda page:None)
        self.enter_patch.start(); self.addCleanup(self.enter_patch.stop)

    def confirm(self,enabled=True):
        self.page.en_enabled_var.set(enabled)
        self.page._on_en_toggled()
        self.assertIs(self.page._en_state,enabled)

    def test_acknowledged_high_and_low_survive_reentry_without_more_io(self):
        for enabled in (True,False):
            with self.subTest(enabled=enabled):
                self.confirm(enabled)
                calls=self.serial.configure_gpio.call_count
                self.page.on_show(); self.page.on_show()
                self.assertIs(self.page._en_state,enabled)
                self.assertEqual(self.page.en_enabled_var.get(),enabled)
                self.assertEqual(self.serial.configure_gpio.call_count,calls)

    def test_new_port_manager_generation_or_disconnect_clears_confirmation(self):
        for identity in (('COM15',123,1),('COM14',456,1),('COM14',123,2),None):
            with self.subTest(identity=identity):
                self.identity=('COM14',123,1); self.confirm()
                self.identity=identity
                self.page.on_show()
                self.assertIsNone(self.page._en_state)
                self.assertEqual(self.page.en_status_var.get(),'awp1221_en_unknown')

    def test_bad_ack_does_not_resurrect_previous_high_on_reentry(self):
        self.confirm()
        self.serial.configure_gpio.side_effect=None
        self.serial.configure_gpio.return_value=SimpleNamespace(ok=True,data='GPIOCFG D4 LOW')
        with patch('pages.awp1221_page.messagebox.showerror'):
            self.page._on_en_toggled()
        self.page.on_show()
        self.assertIsNone(self.page._en_state)

    def test_shared_d4_update_is_retained_and_hiz_stays_unknown(self):
        for mode,state in (('HIGH',True),('LOW',False),('HIZ',None)):
            self.page._sync_shared_d4_gpio_mode(mode)
            self.page.on_show()
            self.assertIs(self.page._en_state,state)


class RealEnNavigationTests(unittest.TestCase):
    def test_acknowledged_en_survives_actual_page_round_trip_and_expires_on_reconnect(self):
        from tests.validate_responsive_layout import mocked_application, _flush
        from unittest.mock import PropertyMock
        with mocked_application() as app:
            app.set_animations(False)
            app.navigation.show('awp1221'); _flush(app.root)
            page=app.navigation.pages['awp1221']
            router=app.ctx.serial
            identity=['COM14',123,1]
            with (patch.object(type(router),'is_connected',new_callable=PropertyMock,return_value=True),
                  patch.object(router,'selected_port_for_page',return_value='COM14'),
                  patch.object(router,'page_connection_identity',side_effect=lambda name:tuple(identity)),
                  patch.object(router,'configure_gpio',side_effect=lambda pin,mode:SimpleNamespace(ok=True,data=f'GPIOCFG {pin} {mode}')) as writes,
                  patch.object(page,'_set_en_high_adc_load')):
                for enabled in (True,False):
                    page.en_enabled_var.set(enabled); page._on_en_toggled()
                    before=writes.call_count
                    app.navigation.show('home'); _flush(app.root)
                    app.navigation.show('awp1221'); _flush(app.root)
                    self.assertIs(page._en_state,enabled)
                    self.assertEqual(writes.call_count,before)
                identity[2]+=1
                app.navigation.show('home'); app.navigation.show('awp1221'); _flush(app.root)
                self.assertIsNone(page._en_state)

if __name__=='__main__': unittest.main()

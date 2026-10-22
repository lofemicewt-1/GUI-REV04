import unittest
import time
from unittest.mock import patch, PropertyMock
from core.lazy_pages import LazyPages


class PagePreparationTests(unittest.TestCase):
    def test_preparation_is_unpublished_until_complete_and_cancel_destroys_it(self):
        installed, built = [], []
        class Page:
            def __init__(self, defer_build=False):
                self.destroyed = False
                self._build_steps = iter(range(4))
                built.append(self)
            def destroy(self):
                self.destroyed = True
        pages = LazyPages(lambda name, page: installed.append(name))
        pages.register('target', Page, staged=True)
        job = pages.prepare('target')
        next(job)
        self.assertEqual(installed, [])
        self.assertEqual(pages.built_values(), [])
        job.close()
        self.assertTrue(built[0].destroyed)
        self.assertEqual(installed, [])
        for _ in pages.prepare('target'):
            pass
        self.assertEqual(installed, ['target'])
        self.assertIs(pages['target'], built[1])

    def test_failed_stage_does_not_cache_partial_page_and_allows_retry(self):
        destroyed = []
        class Page:
            def __init__(self, defer_build=False):
                def steps():
                    yield
                    raise ValueError('failed stage')
                self._build_steps = steps()
            def destroy(self):
                destroyed.append(True)
        pages = LazyPages(lambda *_: None)
        pages.register('target', Page, staged=True)
        with self.assertRaisesRegex(ValueError, 'failed stage'):
            list(pages.prepare('target'))
        self.assertEqual(destroyed, [True])
        self.assertFalse(dict.__contains__(pages, 'target'))
        with self.assertRaisesRegex(ValueError, 'failed stage'):
            list(pages.prepare('target'))


class LayoutCoalescingTests(unittest.TestCase):
    def test_unchanged_adc_flags_do_not_restart_native_grid_layout(self):
        from unittest.mock import Mock
        from pages.awp1221_page import AWP1221ModePage
        page = object.__new__(AWP1221ModePage)
        page._button_adc_fields = [(Mock(), Mock())] * 6
        page._button_adc_columns = 3
        page._button_adc_decision_width = lambda section: 600
        controls = [Mock() for _ in range(6)]
        for control in controls:
            control.winfo_reqwidth.return_value = 20
        page._button_adc_zps_frame = Mock()
        page._button_adc_zps_frame.winfo_children.return_value = controls
        section = Mock()
        page._reflow_button_adc_settings(section, columns=3)
        for control in controls:
            control.grid_configure.assert_called_once()
            control.grid_configure.reset_mock()
        page._reflow_button_adc_settings(section, columns=3)
        for control in controls:
            control.grid_configure.assert_not_called()


class RealPagePreparationTests(unittest.TestCase):
    def test_restoring_shared_auto_serial_does_not_refresh_inactive_mode_mid_transaction(self):
        from tests.validate_responsive_layout import mocked_application
        with mocked_application() as app:
            app.set_animations(False)
            host = app.navigation.pages['awp1221']
            button = host.mode_pages['button_mode']
            full = host.mode_pages['full_mode']
            model = host._settings_model
            model.shared['adc_settings']['auto_serial'] = not button.auto_serial_var.get()
            with patch.object(button, '_refresh_waveform_controls', wraps=button._refresh_waveform_controls) as refresh:
                full._apply_settings_model_to_view(model)
            refresh.assert_not_called()
            self.assertEqual(full.auto_serial_var.get(), model.shared['adc_settings']['auto_serial'])
            self.assertEqual(host._settings_sync_depth, 0)
            self.assertEqual(full._settings_sync_depth, 0)

    def test_prepared_commit_yields_to_cancellation_and_can_retry(self):
        from tkinter import ttk
        from tests.validate_responsive_layout import mocked_application
        with mocked_application() as app:
            nav, root = app.navigation, app.root
            root.update()
            pages = LazyPages(lambda *_: None)
            pages.register('target', lambda defer_build=False: ttk.Frame(nav.content), staged=True)
            committed = []
            nav._start_preparation(pages, 'target', lambda: committed.append(True))
            self.assertEqual(committed, [], 'commit ran in the generator completion callback')
            self.assertIsNotNone(nav._pending_navigation)
            self.assertGreaterEqual(len(nav._last_preparation_steps_ms), 3)
            nav.cancel_pending_navigation()
            root.update()
            self.assertEqual(committed, [])
            self.assertEqual(app.state.current_page, 'home')
            nav._start_preparation(pages, 'target', lambda: committed.append(True))
            root.after(20, root.quit)
            root.mainloop()
            self.assertEqual(committed, [True])
            self.assertIsNone(nav._pending_navigation)
            pages['target'].destroy()

    def test_connected_router_receives_no_writes_during_complete_preparation(self):
        from tests.validate_responsive_layout import mocked_application
        from core.base_tool import SerialResponse
        for name in ('awp1921_1923', 'awp1018', 'awp1221'):
            with self.subTest(page=name), mocked_application() as app:
                app.set_animations(False)
                router = app.ctx.serial
                with patch.object(type(router), 'is_connected', new_callable=PropertyMock, return_value=True), patch.object(router, 'write_register_value', return_value=SerialResponse(True, [])) as writes, patch.object(router, 'queue_register_setting', return_value=True) as queued:
                    list(app.navigation.pages.prepare(name))
                    host = app.navigation.pages[name]
                    list(host.mode_pages.prepare('full_mode'))
                    if name == 'awp1921_1923':
                        list(host.mode_pages.prepare('pump_mode'))
                    self.assertEqual(writes.call_args_list, [], 'hidden preparation wrote to the connected route')
                    self.assertEqual(queued.call_args_list, [], 'hidden preparation queued hardware settings')
                    if name == 'awp1921_1923':
                        host.mode_pages['button_mode'].on_show()
                        self.assertEqual(len(writes.call_args_list), 1)
                        self.assertEqual(writes.call_args.args[1], 0x15)
                        host.mode_pages['button_mode'].on_show()
                        self.assertEqual(len(writes.call_args_list), 1, 'initial duration was replayed')

    def test_unready_target_rolls_back_without_activation(self):
        from tkinter import ttk
        from tests.validate_responsive_layout import mocked_application
        from tests.test_motion import pump
        with mocked_application() as app:
            root, nav = app.root, app.navigation
            pump(root, .15)
            activated = []
            def factory():
                page = ttk.Frame(nav.content)
                page.on_show = lambda: activated.append(True)
                return page
            nav.register_page('unready', 'home', factory)
            change = app.transitions.change
            def short_wait(*args, **kwargs):
                kwargs['wait_s'] = .05
                return change(*args, **kwargs)
            with patch.object(app.transitions, '_owner_is_foreground', return_value=True), patch.object(app.transitions, 'change', side_effect=short_wait), patch.object(nav, '_page_layout_ready', return_value=False):
                nav.request_show('unready')
                pump(root, .4)
            self.assertEqual(activated, [])
            self.assertEqual(app.state.current_page, 'home')
            self.assertFalse(nav._transition_pending)
            self.assertFalse(app.transitions.active)

    def test_all_chip_preparations_cancel_late_without_io_or_leaked_children(self):
        from tests.validate_responsive_layout import mocked_application
        for name in ('awp1221', 'awp1018', 'awp1921_1923'):
            for count in (20, 40):
                with self.subTest(page=name, steps=count), mocked_application() as app:
                    app.set_animations(False)
                    root, nav = app.root, app.navigation
                    root.update()
                    before = set(nav.content.winfo_children())
                    errors = []
                    root.report_callback_exception = lambda k,v,tb: errors.append(repr(v))
                    with patch.object(app.ctx.serial, 'queue_register_setting', side_effect=AssertionError('prepare queues hardware')), patch.object(app.ctx.serial, 'write_register_value', side_effect=AssertionError('prepare writes hardware')):
                        job = nav.pages.prepare(name)
                        for _ in range(count):
                            try:
                                next(job)
                            except StopIteration:
                                self.fail('fixture did not cancel an unfinished page')
                            root.update()
                        job.close()
                    root.update()
                    self.assertEqual(set(nav.content.winfo_children()), before)
                    self.assertEqual(errors, [])

    def test_cold_build_keeps_old_page_until_ready_without_transport_io(self):
        from tests.validate_responsive_layout import mocked_application
        with mocked_application() as app:
            root, nav = app.root, app.navigation
            app.set_animations(False)
            root.update()
            errors, observations = [], []
            root.report_callback_exception = lambda k,v,tb: errors.append(repr(v))
            original = nav._run_preparation
            def observe():
                original()
                if nav._preparation is not None:
                    observations.append((app.state.current_page, dict.__contains__(nav.pages, 'awp1221')))
            nav._run_preparation = observe
            # Item-1 visibility hides chip pages while offline; READY-identified
            # is required before request_show may start cold preparation.
            nav.set_chip_pages_visible(True, highlight='AWP1221')
            root.update()
            with patch.object(app.ctx.serial, 'write_register_value', side_effect=AssertionError('preparation writes hardware')), patch.object(app.ctx.serial, 'read_register_value', side_effect=AssertionError('preparation reads hardware'), create=True):
                nav.request_show('awp1221')
                deadline = time.perf_counter()+5
                while app.state.current_page != 'awp1221' and time.perf_counter()<deadline:
                    root.update()
                    time.sleep(.001)
            self.assertEqual(app.state.current_page, 'awp1221')
            self.assertTrue(observations)
            self.assertTrue(all(value==('home', False) for value in observations), observations)
            self.assertEqual(errors, [])
            self.assertIsNone(nav._preparation)

    def test_cancel_partial_actual_page_cleans_up_and_can_retry(self):
        from tests.validate_responsive_layout import mocked_application
        with mocked_application() as app:
            app.set_animations(False)
            root, nav = app.root, app.navigation
            root.update()
            errors = []
            root.report_callback_exception = lambda k,v,tb: errors.append(repr(v))
            pages = nav.pages
            before = set(nav.content.winfo_children())
            job = pages.prepare('awp1221')
            for _ in range(8):
                next(job)
            job.close()
            root.update()
            self.assertEqual(set(nav.content.winfo_children()), before)
            self.assertFalse(dict.__contains__(pages, 'awp1221'))
            self.assertNotIn('awp1221', pages._building)
            for _ in pages.prepare('awp1221'):
                root.update()
            self.assertEqual(errors, [])


if __name__ == '__main__':
    unittest.main()

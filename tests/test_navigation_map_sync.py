"""Mapping must repair sidebar geometry without reentering Tk's idle loop."""
import tkinter as tk
import unittest
from unittest import mock

from tests.validate_responsive_layout import mocked_application


class NavigationMapSyncTests(unittest.TestCase):
    def test_startup_and_remap_without_nested_idle(self):
        with mocked_application() as app:
            root, nav = app.root, app.navigation
            errors = []
            root.report_callback_exception = lambda *args: errors.append(str(args[1]))

            def pump():
                root.after(450, root.quit)
                root.mainloop()
                self.assertFalse(errors, errors)
                self.assertTrue(nav.sidebar_canvas.winfo_ismapped())
                self.assertGreater(nav.sidebar_canvas.winfo_width(), 1)
                self.assertTrue(nav.buttons['home'].winfo_ismapped())

            with mock.patch.object(root, 'update_idletasks', side_effect=AssertionError('nested idle')):
                pump()
                root.withdraw()
                root.after(20, root.deiconify)
                pump()
                root.iconify()
                root.after(50, root.deiconify)
                pump()

    def test_unmap_cancels_pending_repair(self):
        with mocked_application() as app:
            root, nav = app.root, app.navigation
            root.after(400, root.quit)
            root.mainloop()
            nav._on_root_unmap()
            nav._on_root_map()
            self.assertIsNotNone(nav._map_sync_job)
            nav._on_root_unmap()
            self.assertIsNone(nav._map_sync_job)
            nav._on_root_map()
            self.assertIsNotNone(nav._map_sync_job)
        self.assertIsNone(nav._map_sync_job)


if __name__ == '__main__':
    unittest.main()

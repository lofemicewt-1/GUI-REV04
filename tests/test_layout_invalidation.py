from __future__ import annotations

import unittest
from unittest.mock import patch

from tests.test_motion import pump
from tests.validate_responsive_layout import mocked_application


class LayoutInvalidationTests(unittest.TestCase):
    def test_warm_chip_switch_does_not_resize_an_unchanged_canvas_window(self):
        with mocked_application() as app:
            app.set_animations(False)
            nav = app.navigation
            app.root.geometry("1700x980+0+0")
            nav.show("awp1221")
            pump(app.root, .3)
            nav.show("awp1018")
            pump(app.root, .3)
            before = tuple(nav.content_canvas.itemcget(nav.content_window, key) for key in ("width", "height"))
            with patch.object(nav.content_canvas, "itemconfigure", wraps=nav.content_canvas.itemconfigure) as resize:
                nav.show("awp1221")
                pump(app.root, .3)
            after = tuple(nav.content_canvas.itemcget(nav.content_window, key) for key in ("width", "height"))
            self.assertEqual(before, after)
            self.assertFalse(resize.call_args_list, "switching equally sized pages invalidated the entire canvas window")

    def test_unchanged_scroll_layout_does_not_invalidate_its_canvas(self):
        with mocked_application() as app:
            app.set_animations(False)
            app.navigation.show("awp1221")
            pump(app.root, .3)
            area = app.navigation.pages["awp1221"].mode_scroll
            area._sync_scroll_region()
            pump(app.root, .1)
            with patch.object(area.canvas, "configure", wraps=area.canvas.configure) as redraw:
                area._sync_scroll_region()
                area._sync_scroll_region()
            writes = [call for call in redraw.call_args_list if "scrollregion" in call.kwargs]
            self.assertFalse(writes, "unchanged layout repeatedly invalidated the scroll canvas")


if __name__ == "__main__":
    unittest.main()

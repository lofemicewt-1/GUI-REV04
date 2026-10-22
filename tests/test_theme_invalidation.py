"""Exercise real ttk style broadcasts and page readiness in the application."""
from tkinter import ttk
import unittest
import time
from tests.validate_responsive_layout import mocked_application

def pump(root,ms=250):root.after(ms,root.quit);root.mainloop()

class ThemeInvalidationTests(unittest.TestCase):
    def test_unchanged_waveform_labels_do_not_broadcast_theme_changes(self):
        with mocked_application() as app:
            # Let initial map/theme work finish before observing local updates.
            pump(app.root,700)
            for name in ('awp1221','awp1921_1923'):
                with self.subTest(page=name):
                    host=app.navigation.pages[name]
                    page=host.mode_pages['button_mode']
                    page._refresh_waveform_choice_labels();pump(app.root,400)
                    # Observe real Tcl theme events rather than replacing style operations.
                    events=[]
                    binding=app.root.bind('<<ThemeChanged>>',lambda event:events.append(event.widget),add='+')
                    page._refresh_waveform_choice_labels()
                    pump(app.root)
                    app.root.unbind('<<ThemeChanged>>',binding)
                    self.assertEqual(len(events),0,f'{name}: unchanged labels broadcast theme events')
                    app.set_theme('dark' if app.state.theme=='light' else 'light');pump(app.root)
                    page._refresh_waveform_choice_labels()
                    self.assertEqual(ttk.Style(app.root).lookup('R2cWaveform.TCombobox','foreground'),app.ctx.colors['text'])
                    self.assertEqual(ttk.Style(app.root).lookup('R2cUnsupportedWaveform.TCombobox','foreground'),app.ctx.colors['muted'])

    def test_active_mode_must_be_mapped_before_ready(self):
        with mocked_application() as app:
            app.set_animations(False)
            app.navigation.show('awp1921_1923')
            deadline=time.monotonic()+10
            while (app.state.current_page!='awp1921_1923' or not app.navigation._page_layout_ready()) and time.monotonic()<deadline:
                pump(app.root,50)
            nav=app.navigation;host=nav.pages['awp1921_1923'];active=host.mode_pages[host.current_mode]
            self.assertTrue(nav._page_layout_ready())
            # The host viewport still has its old allocation at this boundary.
            active.grid_remove()
            self.assertFalse(nav._page_layout_ready(),'Unmapped target mode was published as ready')
            active.grid()
            deadline=time.monotonic()+10
            pump(app.root)
            while not nav._page_layout_ready() and time.monotonic()<deadline:pump(app.root,50)
            self.assertTrue(nav._page_layout_ready())

if __name__=='__main__':unittest.main()

"""Current-source, hardware-fenced checks for the shared page animation rollout."""
import time
from tkinter import ttk
import unittest

from tests.validate_responsive_layout import mocked_application
from pages.base_page import BasePage


class SharedPageMotionTests(unittest.TestCase):
    LOG_CASES = (('awp1018', 'awp1018_normal_mode'), ('awp1018', 'awp1018_diff_mode'),
                 ('awp1921_1923', 'button_mode'), ('awp1921_1923', 'pump_mode'), ('awp1922', None))

    @classmethod
    def setUpClass(cls):
        cls.fixture = mocked_application()
        cls.app = cls.fixture.__enter__()
        cls.root = cls.app.root
        cls.root.geometry('1700x980+0+0')
        cls.root.deiconify()
        cls.errors = []
        cls.root.report_callback_exception = lambda kind, value, tb: cls.errors.append(str(value))
        cls.app.set_animations(False)

    @classmethod
    def tearDownClass(cls):
        cls.fixture.__exit__(None, None, None)

    def wait(self, ready, seconds=15):
        deadline, stable = time.perf_counter()+seconds, 0
        def poll():
            nonlocal stable
            stable = stable+1 if ready() else 0
            if stable >= 3 or time.perf_counter() >= deadline:
                self.root.quit()
            else:
                self.root.after(20, poll)
        self.root.after(20, poll)
        self.root.mainloop()
        self.assertGreaterEqual(stable, 3, 'real page geometry did not settle')
        self.assertFalse(self.errors, self.errors)

    def display(self, chip, mode):
        self.app.set_animations(False)
        self.app.navigation.request_show(chip)
        self.wait(lambda:self.app.state.current_page==chip and self.app.navigation._preparation is None
                  and not self.app.transitions.active and self.app.navigation._page_layout_ready())
        host = self.app.navigation.pages[chip]
        if mode:
            self.app.navigation.mode_buttons[mode].invoke()
            self.wait(lambda:host.current_mode==mode and self.app.navigation._preparation is None
                      and not self.app.transitions.active and self.app.navigation._page_layout_ready())
        page = host.mode_pages[mode] if mode else host
        self.wait(lambda: page.winfo_ismapped() and not getattr(page,'_preparing',False)
                  and self.app.navigation._page_layout_ready()
                  and self.app._resize_after is None and not self.app.transitions.active
                  and not getattr(page, '_selection_refresh_job', None)
                  and not (getattr(page, '_serial_log_motion', None) and page._serial_log_motion.busy))
        return host, page

    def align(self, motion):
        def ready():
            if motion.busy:
                return False
            box = motion.canvas.bbox('selection')
            x, y, width, height = motion._rect_for(motion._selected)
            return box is not None and abs((box[0]+box[2])/2-x-width/2) <= 1
        self.wait(ready, 5)

    def test_logs_animate_real_allocations_on_all_other_collapsible_pages(self):
        for chip, mode in self.LOG_CASES:
            with self.subTest(chip=chip, mode=mode):
                host, page = self.display(chip, mode)
                motion = page._serial_log_motion
                self.assertIsNotNone(motion)
                page._serial_log_surface().canvas.yview_moveto(1)
                self.wait(lambda: self.app.navigation._page_layout_ready() and not self.app.transitions.active)
                if motion.expanded:
                    page._serial_log_toggle.invoke()
                    self.wait(lambda: not motion.busy)
                samples = []
                binding = motion.clip.bind('<Configure>', lambda e: samples.append(e.height), add='+')
                self.app.set_animations(True)
                try:
                    page._serial_log_toggle.invoke()
                    self.wait(lambda: not motion.busy and motion.clip.winfo_ismapped()
                              and motion.clip.winfo_height() == motion.content.winfo_reqheight())
                    full = motion.content.winfo_reqheight()
                    self.assertTrue(any(1 < value < full-1 for value in samples), (chip, mode, full, samples))
                    self.assertEqual(int(page._serial_log_text.cget('height')), 8)
                    self.assertEqual(page._serial_log_text.winfo_height(), page._serial_log_text.winfo_reqheight())
                    self.wait(lambda: self.app.navigation._page_layout_ready() and not self.app.transitions.active)
                    viewport = page._serial_log_surface().canvas
                    self.assertLessEqual(motion.clip.winfo_rooty()+motion.clip.winfo_height(),
                                         viewport.winfo_rooty()+viewport.winfo_height()+1,
                                         'expanded log/scrollbar is clipped by the viewport footer')
                    page._serial_log_toggle.invoke()
                    self.root.after(35, page._serial_log_toggle.invoke)
                    self.root.after(90, page._serial_log_toggle.invoke)
                    self.wait(lambda: not motion.busy and not motion.clip.winfo_manager())
                finally:
                    motion.clip.unbind('<Configure>', binding)

    def test_selection_backgrounds_keep_native_commands_and_retarget(self):
        cases = [('awp1018', mode) for mode in ('awp1018_normal_mode', 'awp1018_diff_mode', 'full_mode')]
        cases.append(('awp1921_1923', 'button_mode'))
        for chip, mode in cases:
            with self.subTest(chip=chip, mode=mode):
                host, page = self.display(chip, mode)
                target = page.waveform_page if chip == 'awp1921_1923' else page
                motion = (target._source_selection_motion if chip == 'awp1921_1923'
                          else target._stage_selection_motion)
                buttons = motion._buttons
                self.assertIs(motion._motion.animator, self.app.animator)
                if page.step_progress is not None:
                    page.step_progress.scroll_to_step('waveform' if chip == 'awp1921_1923' else 'adc')
                buttons[0].invoke()
                self.align(motion)
                start = motion.canvas.bbox('selection')[0]
                positions = []
                binding = motion._indicator.bind('<Configure>',
                    lambda e: positions.append(motion.canvas.bbox('selection')[0]), add='+')
                self.app.set_animations(True)
                try:
                    buttons[-1].invoke()
                    self.align(motion)
                finally:
                    motion._indicator.unbind('<Configure>', binding)
                end = motion.canvas.bbox('selection')[0]
                self.assertTrue(any(min(start,end) < x < max(start,end) for x in positions), positions)
                for button in (buttons[0], buttons[-1], buttons[0], buttons[-1]):
                    button.invoke()
                self.app.set_animations(False)
                self.align(motion)
                self.assertIs(motion._selected, buttons[-1])
                for item in motion.canvas.find_all():
                    if motion.canvas.type(item) in ('polygon','rectangle','line'):
                        self.assertEqual(motion.canvas.itemcget(item, 'dash'), '')
                self.app.navigation.show('home')
                self.wait(lambda: not self.app.transitions.active)
                self.assertFalse(motion.busy)
                self.assertFalse(motion.canvas.winfo_ismapped())

    def test_logs_survive_theme_language_resize_and_page_hide(self):
        for language, theme, geometry in (('en','dark','1400x850+20+20'), ('zh','light','1700x980+0+0')):
            self.app.set_animations(False)
            self.app.apply_settings(language, theme, self.app.state.font_size)
            self.root.geometry(geometry)
            for chip, mode in self.LOG_CASES:
                with self.subTest(chip=chip,mode=mode,language=language,theme=theme):
                    host, page = self.display(chip,mode)
                    motion=page._serial_log_motion
                    motion.set_expanded(False,animate=False)
                    self.wait(lambda:not motion.busy)
                    page._serial_log_surface().canvas.yview_moveto(1)
                    self.wait(lambda:not self.app.transitions.active)
                    self.app.set_animations(True)
                    page._serial_log_toggle.invoke()
                    self.app.navigation.show('home')
                    self.wait(lambda:not motion.busy and not self.app.transitions.active)
                    self.assertFalse(page._serial_log_scroll_requested)
                    self.display(chip,mode)
                    self.assertTrue(motion.expanded)
                    self.wait(lambda:not motion.busy and motion.clip.winfo_height()==motion.content.winfo_reqheight())
                    self.assertEqual(int(page._serial_log_text.cget('height')),8)
                    self.assertIsNotNone(page._serial_log_text.cget('xscrollcommand'))

    def test_selection_theme_language_keyboard_and_owner_destruction(self):
        groups = []
        for chip, mode in (('awp1018','awp1018_normal_mode'), ('awp1018','awp1018_diff_mode'),
                           ('awp1018','full_mode'), ('awp1921_1923','button_mode')):
            host, page = self.display(chip, mode)
            motion = (page.waveform_page._source_selection_motion if chip == 'awp1921_1923'
                      else page._stage_selection_motion)
            groups.append((chip, mode, motion))
        for language, theme in (('en','dark'), ('zh','light')):
            self.app.apply_settings(language, theme, self.app.state.font_size)
            for chip, mode, motion in groups:
                with self.subTest(chip=chip, mode=mode, language=language, theme=theme):
                    self.display(chip, mode)
                    self.align(motion)
                    for button, label in zip(motion._buttons, motion._labels):
                        self.assertEqual(motion.canvas.itemcget(label,'text'), button.cget('text'))
                    other=next(button for button in motion._buttons if button is not motion._selected)
                    item=motion._bases[motion._buttons.index(other)]
                    x,y,width,height=motion._rect_for(other)
                    selected_fill=motion.canvas.itemcget(motion._fill,'fill')
                    motion.canvas.event_generate('<Motion>',x=x+width//2,y=y+height//2)
                    self.wait(lambda:motion.canvas.itemcget(item,'fill')==motion._hover_bg)
                    self.assertNotEqual(motion._hover_bg,selected_fill)
                    self.assertEqual(motion.canvas.itemcget(motion._fill,'fill'),selected_fill)
                    motion.canvas.event_generate('<Leave>')
                    self.wait(lambda:motion.canvas.itemcget(item,'fill')==motion._background)
                    first, second = motion._buttons[:2]
                    self.root.focus_force()
                    first.focus_set()
                    first.event_generate('<KeyPress-Tab>')
                    self.wait(lambda: self.root.focus_get() is second)
                    second.event_generate('<KeyPress-space>')
                    second.event_generate('<KeyRelease-space>')
                    self.wait(lambda: motion._selected is second)
                    self.align(motion)
        page = BasePage(self.root, self.app.ctx)
        page.place(x=300,y=100,width=400,height=80)
        row = ttk.Frame(page)
        row.pack(fill='x')
        buttons = [ttk.Button(row, text=str(i)) for i in range(2)]
        for index, button in enumerate(buttons):
            row.columnconfigure(index,weight=1)
            button.grid(row=0,column=index,sticky='ew')
        motion = page.make_segmented_selection(buttons)
        motion.select(buttons[0],animate=False)
        self.align(motion)
        self.app.set_animations(True)
        motion.select(buttons[1])
        page._queue_selection_refresh()
        page.destroy()
        self.wait(lambda: motion._closed and not motion.busy)

    def test_other_chip_legend_selection_survives_refresh_and_differs_from_hover(self):
        for chip,mode in (('awp1018','awp1018_normal_mode'),('awp1018','awp1018_diff_mode'),
                          ('awp1921_1923','button_mode')):
            with self.subTest(chip=chip,mode=mode):
                host,page=self.display(chip,mode)
                legend=page.adc_legend
                legend._rule_rows[2][0].event_generate('<Button-1>')
                self.wait(lambda:legend._preview_stage=='Stage1')
                if chip=='awp1921_1923':
                    page._refresh_button_adc_legend_rules()
                else:
                    page._refresh_adc_legend_rules()
                self.wait(lambda:legend._preview_stage=='Stage1')
                selected,other=legend._rule_rows[2][0],legend._rule_rows[1][0]
                self.assertTrue(selected.cget('text').startswith('✓ '))
                other.event_generate('<Enter>')
                self.wait(lambda:not self.app.transitions.active)
                self.assertFalse(other.cget('text').startswith('✓ '))
                self.assertNotEqual(selected.cget('background'),other.cget('background'))
                self.assertEqual(legend._rule_borders[2].cget('highlightbackground'),page.colors['primary'])
                self.assertEqual(legend._rule_borders[1].cget('highlightbackground'),page.colors['border'])
                other.event_generate('<Leave>')

    def test_full_logs_and_shared_dropdown_advanced_controls_keep_their_contract(self):
        dropdown = self.app.micro_anim._dropdown_motion
        self.assertIs(dropdown.animator, self.app.animator)
        for chip in ('awp1018', 'awp1921_1923', 'awp1221'):
            with self.subTest(full=chip):
                host, page = self.display(chip, 'full_mode')
                self.assertIsNone(page._serial_log_motion)
                self.assertIsNone(getattr(page, '_serial_log_toggle', None))
                self.wait(lambda:page._serial_log_text.winfo_ismapped())
        for chip, mode in (('awp1018','awp1018_normal_mode'), ('awp1018','awp1018_diff_mode'),
                           ('awp1921_1923','button_mode'), ('awp1921_1923','pump_mode')):
            with self.subTest(advanced=chip, mode=mode):
                host, page = self.display(chip, mode)
                trap = host.trapezoid
                self.app.set_animations(True)
                trap.set_expanded(True, persist=False)
                self.root.after(35, lambda: trap.set_expanded(False, persist=False))
                self.root.after(90, lambda: trap.set_expanded(True, persist=False))
                self.wait(lambda: not self.app.animator.is_running(trap._motion_key)
                          and not self.app.transitions.active and trap._layout_job is None)
                self.assertGreater(trap._reveal_height, 0)
                trap.set_expanded(False, persist=False)
                self.app.set_animations(False)
                self.wait(lambda: not self.app.transitions.active and trap._reveal_height == 0)


if __name__ == '__main__':
    unittest.main()

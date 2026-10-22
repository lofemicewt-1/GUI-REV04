"""Keep an existing scroll endpoint when layout changes the viewport."""
import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace
import unittest
from pages.base_page import VerticalScrollArea
from styles.theme import apply_theme
from tests.tk_cleanup import prepare_tk_test,attach_tk_cleanup

class ScrollEndpointTests(unittest.TestCase):
    def setUp(self):
        prepare_tk_test(self)
        self.root=tk.Tk();attach_tk_cleanup(self,self.root)
        self.root.geometry('640x480+0+0')
        colors=apply_theme(self.root,'light',10)
        ctx=SimpleNamespace(colors=colors,state=SimpleNamespace(ui_scale=1.0))
        self.area=VerticalScrollArea(self.root,ctx,horizontal=True)
        self.area.pack(fill='both',expand=True)
        self.body=ttk.Frame(self.area.content,width=1200,height=1000)
        self.body.grid(row=0,column=0)
        self.pump()

    def pump(self):
        self.root.after(100,self.root.quit);self.root.mainloop()

    def test_resize_keeps_both_existing_endpoints(self):
        self.area.canvas.xview_moveto(1);self.area.canvas.yview_moveto(1);self.pump()
        self.root.geometry('500x360+0+0');self.pump()
        self.assertAlmostEqual(self.area.canvas.xview()[1],1)
        self.assertAlmostEqual(self.area.canvas.yview()[1],1)

    def test_user_can_leave_endpoint_during_resize(self):
        self.area.canvas.xview_moveto(1);self.area.canvas.yview_moveto(1);self.pump()
        self.root.geometry('500x360+0+0')
        self.area.canvas.xview_moveto(.2);self.area.canvas.yview_moveto(.2);self.pump()
        self.assertAlmostEqual(self.area.canvas.xview()[0],.2,places=2)
        self.assertAlmostEqual(self.area.canvas.yview()[0],.2,places=2)

    def test_content_growth_before_scroll_callback_keeps_endpoint(self):
        self.area.canvas.xview_moveto(1)
        self.area.canvas.yview_moveto(1)
        # Tk coalesces the scroll callback with the subsequent region update.
        self.body.configure(width=1400,height=1200)
        self.area.canvas.configure(scrollregion=(0,0,1400,1216))
        self.pump()
        self.assertAlmostEqual(self.area.canvas.xview()[1],1)
        self.assertAlmostEqual(self.area.canvas.yview()[1],1)

if __name__=='__main__':unittest.main()

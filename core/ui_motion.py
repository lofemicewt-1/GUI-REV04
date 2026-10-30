"""Reusable Tk motion, driven by the application's shared Animator.

UnderlineMotion: select(widget), refresh(), finish(), hide(), close(); busy
includes both queued layout work and an active tween. The indicator fills its
parent track vertically and follows the selected widget horizontally.

DropdownReveal: construct once per root to animate all ttk Combobox popdowns;
reset() finishes open entrances, close() also removes this instance's bindings.
Windows reveals by clipping the full native surface; other platforms fade it.

SegmentedButtonMotion: the same selection lifecycle as an underline, rendered
as a full filled button background while preserving native button commands.

GridReveal: set_expanded(bool), refresh(), finish(), close(); busy. Animate a
grid-managed clipping frame while its inner content retains its natural size.

Dependencies: Python's standard library and core.animation. No application,
chip, transport, settings-model, or theme imports. See examples/ui_motion_demo.py.
All methods belong on the Tk thread; owners must close controllers before stop().
"""
from __future__ import annotations

import ctypes
import sys
import time
import tkinter as tk
from tkinter import ttk
from collections.abc import Callable, MutableMapping

from core import animation as anim

__all__ = ["UnderlineMotion", "DropdownReveal", "SegmentedButtonMotion", "GridReveal"]


class UnderlineMotion:
    """Own one underline's geometry, redirection, and lifetime."""

    def __init__(self, indicator: tk.Widget, animator: anim.Animator | None = None, *,
                 color: Callable[[], str] | None = None, owner: tk.Misc | None = None) -> None:
        self.indicator = indicator
        self.animator = animator if animator is not None else anim.animator_for(indicator)
        self._track = indicator.master
        self._color = color
        self._target: tk.Widget | None = None
        self._rect: tuple[float, float] | None = None
        self._target_rect: tuple[float, float] | None = None
        self._key = ("underline", id(self))
        self._job: str | None = None
        self._animate_pending = False
        self._waiting_frame = False
        self._catchups = 0
        self._generation = 0
        self._closed = False
        self._bindings = []
        self._target_bindings = []
        for sequence in ("<Map>", "<Configure>"):
            self._bind(self._bindings, self._track, sequence, self.refresh)
        self._bind(self._bindings, self._track, "<Unmap>", self.hide)
        self._bind(self._bindings, indicator, "<Configure>", self._frame_ready)
        self._bind(self._bindings, indicator, "<Destroy>", lambda _event: self.close())
        if owner is not None and owner is not self._track:
            self._bind(self._bindings, owner, "<Map>", self.refresh)
            self._bind(self._bindings, owner, "<Unmap>", self.hide)
            self._bind(self._bindings, owner, "<Destroy>", lambda _event: self.close())

    @property
    def busy(self) -> bool:
        return self._waiting_frame or self._job is not None or (self.animator is not None and self.animator.is_running(self._key))

    @staticmethod
    def _bind(bindings, widget, sequence, callback) -> None:
        bindings.append((widget, sequence, widget.bind(sequence, callback, add="+")))

    @staticmethod
    def _unbind(bindings) -> None:
        for widget, sequence, binding in bindings:
            try:
                widget.unbind(sequence, binding)
            except tk.TclError:
                pass
        bindings.clear()

    def select(self, target: tk.Widget | None, *, animate: bool = True) -> None:
        """Coalesce to the latest selection; passing None clears the underline."""
        if self._closed:
            return
        if target is not self._target:
            if self._waiting_frame and self.indicator.winfo_ismapped():
                self._rect = (float(self.indicator.winfo_x()), float(self.indicator.winfo_width()))
            self._waiting_frame = False
            self._catchups = 0
            self._unbind(self._target_bindings)
            self._target = target
            if target is not None:
                for sequence in ("<Map>", "<Configure>"):
                    self._bind(self._target_bindings, target, sequence, self.refresh)
                self._bind(self._target_bindings, target, "<Destroy>", lambda _e: self.select(None))
        if target is None:
            self.hide()
        elif animate and (self.animator is None or not self.animator.enabled):
            self.finish()
        else:
            self._queue(animate)

    def refresh(self, _event=None) -> None:
        """Coalesce a theme/geometry refresh without restarting equal targets."""
        self._queue(False)

    def _queue(self, animate: bool) -> None:
        if self._closed or self._target is None:
            return
        self._animate_pending |= animate
        if self._job is None:
            self._job = self._track.after_idle(self._run)

    def _run(self) -> None:
        self._job = None
        if self._waiting_frame and self.animator is not None and self.animator.enabled:
            return
        self._waiting_frame = False
        animate, self._animate_pending = self._animate_pending, False
        self._place(animate)

    def _frame_ready(self, _event=None) -> None:
        if self._closed or not self._waiting_frame or self._rect is None:
            return
        actual = (self.indicator.winfo_x(), self.indicator.winfo_width())
        if actual == (round(self._rect[0]), max(1, round(self._rect[1]))):
            self._waiting_frame = False
            self._queue(True)

    def _cancel_job(self) -> None:
        if self._job is not None:
            try:
                self._track.after_cancel(self._job)
            except tk.TclError:
                pass
            self._job = None
        self._animate_pending = False

    def _place(self, animate: bool) -> None:
        target_widget = self._target
        if self._closed or target_widget is None:
            return
        target = (float(target_widget.winfo_rootx() - self._track.winfo_rootx()),
                  float(target_widget.winfo_width()))
        start = self._rect
        moving = self.animator is not None and self.animator.is_running(self._key)
        if self._color is not None:
            self.indicator.configure(bg=self._color())
        if target == self._target_rect and moving:
            return
        if moving and self.indicator.winfo_ismapped():
            # A queued place() may not have reached Tk geometry yet. Redirect
            # from the position actually on screen, rather than an unseen step.
            start = (float(self.indicator.winfo_x()), float(self.indicator.winfo_width()))
        self._target_rect = target
        self._generation += 1
        generation = self._generation

        def step(progress: float) -> None:
            if self._closed or generation != self._generation or self._waiting_frame:
                return
            rect = target if start is None else tuple(a + (b-a)*progress for a, b in zip(start, target))
            middle = target if start is None else tuple(a + (b-a)*.35 for a, b in zip(start, target))
            if ((animate or moving) and self.animator is not None and self.animator.enabled and self.animator._in_tick
                    and start is not None and round(start[0]) != round(target[0])
                    and progress > .5 and self.indicator.winfo_x() == round(start[0])
                    and round(middle[0]) not in (round(start[0]), round(target[0]))
                    and self._catchups < 3):
                # Commit a real intermediate geometry before restarting the
                # remaining motion. Replaying step(0) under load never paints
                # any displacement and can otherwise end in a visible jump.
                self._catchups += 1
                self._waiting_frame = True
                self.animator.cancel(self._key)
                rect = middle
            elif progress >= 1:
                self._catchups = 0
            self._rect = rect
            self.indicator.place(x=round(rect[0]), y=0, width=max(1, round(rect[1])), relheight=1)

        if ((animate or moving) and self.animator is not None and self._track.winfo_viewable()
                and start is not None and start != target):
            self.animator.animate(self._key, anim.DUR_SNAP, step, ease=anim.EASE_SNAP)
        else:
            if self.animator is not None:
                self.animator.cancel(self._key)
            step(1)

    def finish(self) -> None:
        """Synchronously commit the latest selection, including queued work."""
        if self._closed:
            return
        self._cancel_job()
        self._waiting_frame = False
        self._catchups = 0
        if self.animator is not None:
            self.animator.cancel(self._key)
        self._place(False)

    def hide(self, _event=None) -> None:
        """Cancel queued/running work, keeping the selection for the next Map."""
        self._generation += 1
        self._cancel_job()
        self._waiting_frame = False
        self._catchups = 0
        if self.animator is not None:
            self.animator.cancel(self._key)
        self._rect = self._target_rect = None
        try:
            if self.indicator.winfo_exists():
                self.indicator.place_forget()
        except tk.TclError:
            pass

    def close(self) -> None:
        if self._closed:
            return
        self.hide()
        self._closed = True
        self._unbind(self._target_bindings)
        self._unbind(self._bindings)
        self._target = None


class SegmentedButtonMotion:
    """Slide a filled selection while retaining native buttons and keyboard input.

    Buttons share one parent. Their commands still own the selection: call
    select(button) after updating business state. A Canvas paints the moving
    background and labels; pointer input is forwarded to the original buttons.
    """

    def __init__(self, buttons, animator: anim.Animator | None = None, *,
                 owner: tk.Misc | None = None, selected_style="Selected.TButton") -> None:
        self._buttons = tuple(buttons)
        self._parent = self._buttons[0].master
        self._style = ttk.Style(self._parent)
        self._selected_style = selected_style
        self._normal_styles = {b: str(b.cget("style")) or "TButton" for b in self._buttons}
        self._closed = False
        self._draw_job = None
        self._pressed = None
        self._selected = None
        self._hovered = None
        self._keyboard_focus = False
        self._bindings = []
        # The native marker supplies the same shared motion/geometry lifecycle
        # as an underline. The Canvas presents it as a full-height rounded fill.
        self._indicator = tk.Frame(self._parent, bd=0, highlightthickness=0, takefocus=False)
        self._motion = UnderlineMotion(self._indicator, animator, owner=owner)
        self.canvas = tk.Canvas(self._parent, width=1, height=1, bd=0,
                                highlightthickness=0, takefocus=False)
        self._bases = [self.canvas.create_polygon(0, 0, 0, 0, smooth=True) for _ in self._buttons]
        self._fill = self.canvas.create_polygon(0, 0, 0, 0, smooth=True, tags="selection")
        self._labels = [self.canvas.create_text(0, 0, text="") for _ in self._buttons]
        bind = UnderlineMotion._bind
        for sequence in ("<Map>", "<Configure>", "<<ThemeChanged>>"):
            bind(self._bindings, self._parent, sequence, self.refresh)
        bind(self._bindings, self._parent, "<Unmap>", self.hide)
        bind(self._bindings, self._indicator, "<Configure>", self._draw)
        bind(self._bindings, self._indicator, "<Map>", self._raise_surface)
        bind(self._bindings, self.canvas, "<Map>", self._queue_draw)
        bind(self._bindings, self.canvas, "<Configure>", self._queue_draw)
        bind(self._bindings, self.canvas, "<Enter>", self._hover)
        bind(self._bindings, self.canvas, "<Motion>", self._hover)
        bind(self._bindings, self.canvas, "<Leave>", self._leave)
        bind(self._bindings, self.canvas, "<ButtonPress-1>", self._press)
        bind(self._bindings, self.canvas, "<ButtonRelease-1>", self._release)
        bind(self._bindings, self.canvas, "<Destroy>", lambda _e: self.close())
        for sequence in ("<KeyPress-Tab>", "<<PrevWindow>>"):
            bind(self._bindings, self._parent.winfo_toplevel(), sequence, self._keyboard)
        for button in self._buttons:
            for sequence in ("<Configure>", "<FocusIn>", "<FocusOut>"):
                bind(self._bindings, button, sequence, self.refresh)
            bind(self._bindings, button, "<KeyPress>", self._keyboard)
            bind(self._bindings, button, "<Destroy>", lambda _e: self.close())
        if owner is not None and owner is not self._parent:
            bind(self._bindings, owner, "<Map>", self.refresh)
            bind(self._bindings, owner, "<Unmap>", self.hide)
        self.refresh()

    @property
    def busy(self):
        return self._motion.busy or self._draw_job is not None

    def select(self, button, *, animate=True):
        if self._closed:
            return
        self._selected = button
        self.refresh()
        self._motion.select(button, animate=animate)

    def refresh(self, _event=None):
        if self._closed:
            return
        # Map events can arrive before every ancestor is viewable. Prepare the
        # child surface now; Tk maps it with the parent and draws on its Map.
        style = self._style
        parent_style = str(self._parent.cget("style")) or "TFrame"
        self._background = style.lookup(parent_style, "background") or "#FFFFFF"
        self._blue = style.lookup(self._selected_style, "background", ("active",)) or "#2563EB"
        self._edge = style.lookup(self._selected_style, "bordercolor", ("active",)) or self._blue
        self._white = style.lookup(self._selected_style, "foreground") or "#FFFFFF"
        panel_rgb = self.canvas.winfo_rgb(self._background)
        panel_hex = "#%02X%02X%02X" % tuple(value//257 for value in panel_rgb)
        self._hover_bg = anim.lerp_color(panel_hex, self._blue, .08)
        self._looks = [(str(b.cget("text")),
                        style.lookup(self._normal_styles[b], "foreground") or "#111827",
                        style.lookup(self._normal_styles[b], "font") or "TkDefaultFont",
                        style.lookup(self._normal_styles[b], "bordercolor", ("active",)) or "#CBD5E1")
                       for b in self._buttons]
        if self.canvas.cget("bg") != self._background:
            self.canvas.configure(bg=self._background)
        if self.canvas.winfo_manager() != "place":
            self.canvas.place(x=0, y=0, relwidth=1, relheight=1)
        if self._draw_job is None:
            self._draw_job = self.canvas.after_idle(self._draw)

    @staticmethod
    def _rounded(rect):
        x, y, width, height = rect
        # Canvas strokes straddle their path. Keep all four edges inside the
        # button's pixel bounds so the surface cannot clip the bottom/right.
        x, y, width, height = x+1, y+1, max(0, width-2), max(0, height-2)
        right, bottom = x+width, y+height
        # Tk's smoothed polygon rounds halfway between neighbouring points.
        # Double the control distance to retain the buttons' ~8 px corners.
        radius = max(1, min(16, height*.36, width/2))
        return (x+radius, y, right-radius, y, right, y, right, y+radius,
                right, bottom-radius, right, bottom, right-radius, bottom,
                x+radius, bottom, x, bottom, x, bottom-radius, x, y+radius, x, y)

    def _raise_surface(self, _event=None):
        if not self._closed:
            tk.Misc.tkraise(self.canvas)
            self._queue_draw()

    def _queue_draw(self, _event=None):
        if not self._closed and self._draw_job is None:
            self._draw_job = self.canvas.after_idle(self._draw)

    def _rect_for(self, button):
        return (button.winfo_rootx()-self.canvas.winfo_rootx(),
                button.winfo_rooty()-self.canvas.winfo_rooty(),
                button.winfo_width(), button.winfo_height())

    def _draw(self, _event=None):
        if self._draw_job is not None:
            self.canvas.after_cancel(self._draw_job)
            self._draw_job = None
        if self._closed or not hasattr(self, "_looks"):
            return
        fill_x, fill_width = self._indicator.winfo_x(), self._indicator.winfo_width()
        # Retain the prepared drawing even while an ancestor is still hidden;
        # its native Map may follow the last child Configure/idle callback.
        placed = self._indicator.winfo_manager() == "place"
        if self._selected is not None and placed:
            _, y, _, height = self._rect_for(self._selected)
            self.canvas.coords(self._fill, *self._rounded((fill_x, y, fill_width, height)))
            self.canvas.itemconfigure(self._fill, fill=self._blue, outline=self._edge, width=2, state="normal")
        else:
            self.canvas.itemconfigure(self._fill, state="hidden")
        try:
            focused = self._parent.focus_get()
        except (tk.TclError, KeyError):
            focused = None
        for index, button in enumerate(self._buttons):
            rect = self._rect_for(button)
            x, y, width, height = rect
            text, foreground, font, border = self._looks[index]
            hovered = button is self._hovered and button is not self._selected and not button.instate(("disabled",))
            keyboard_focus = self._keyboard_focus and focused is button and button is not self._selected
            self.canvas.coords(self._bases[index], *self._rounded(rect))
            self.canvas.itemconfigure(self._bases[index],
                                      fill=self._hover_bg if hovered else self._background,
                                      outline=self._blue if hovered or keyboard_focus else border,
                                      width=2 if hovered or keyboard_focus else 1)
            covered = placed and fill_x <= x+width/2 <= fill_x+fill_width
            color = self._white if covered else foreground
            self.canvas.coords(self._labels[index], x+width/2, y+height/2)
            self.canvas.itemconfigure(self._labels[index], text=text, fill=color, font=font)

    def _hit(self, event):
        for button in self._buttons:
            x, y, width, height = self._rect_for(button)
            if x <= event.x < x+width and y <= event.y < y+height and not button.instate(("disabled",)):
                return button
        return None

    def _press(self, event):
        self._keyboard_focus = False
        self._hover(event)
        self._queue_draw()
        self._pressed = self._hit(event)
        if self._pressed is not None:
            self._pressed.focus_set()
        return "break"

    def _hover(self, event):
        hovered = self._hit(event)
        self.canvas.configure(cursor="hand2" if hovered is not None else "")
        if hovered is not self._hovered:
            self._hovered = hovered
            self._queue_draw()

    def _leave(self, _event=None):
        self._hovered = None
        self.canvas.configure(cursor="")
        self._queue_draw()

    def _keyboard(self, _event=None):
        self._keyboard_focus = True
        self._queue_draw()

    def _release(self, event):
        button, self._pressed = self._pressed, None
        if button is not None and self._hit(event) is button:
            button.invoke()
        return "break"

    def finish(self):
        self._motion.finish()
        self.refresh()
        self._draw()

    def hide(self, _event=None):
        self._motion.hide()
        self._pressed = None
        self._hovered = None
        self._keyboard_focus = False
        if self._draw_job is not None:
            self.canvas.after_cancel(self._draw_job)
            self._draw_job = None
        if not self._closed:
            self.canvas.place_forget()

    def close(self):
        if self._closed:
            return
        self.hide()
        self._closed = True
        self._motion.close()
        UnderlineMotion._unbind(self._bindings)
        for widget in (self.canvas, self._indicator):
            try:
                widget.destroy()
            except tk.TclError:
                pass


class GridReveal:
    """Disclose grid-managed content using the shared Animator and a clip frame.

    Grid the clip frame first (so grid_remove remembers its row), then grid
    content inside it with sticky='new'. The frame controls only visible height;
    its content keeps natural height, fonts, scrollbars and input bindings.
    """

    def __init__(self, clip, content, animator=None, *, expanded=False, owner=None, on_settled=None,
                 present=None):
        self.clip, self.content = clip, content
        self.animator = animator if animator is not None else anim.animator_for(clip)
        self.expanded = bool(expanded)
        self.on_settled = on_settled
        self.present = present
        self._height = self._target = 0.0
        self._key = ("grid-reveal", id(self))
        self._job = None
        self._geometry_job = None
        self._geometry_pending = False
        self._settle_notify_pending = False
        self._waiting_frame = False
        self._catchups = 0
        self._animate_pending = False
        self._generation = 0
        self._closed = False
        self._bindings = []
        clip.grid_propagate(False)
        bind = UnderlineMotion._bind
        bind(self._bindings, content, "<Configure>", self.refresh)
        bind(self._bindings, clip, "<Map>", self.refresh)
        bind(self._bindings, clip, "<Configure>", self._frame_ready)
        bind(self._bindings, clip, "<Destroy>", lambda _e: self.close())
        if owner is not None:
            bind(self._bindings, owner, "<Map>", self.refresh)
            bind(self._bindings, owner, "<Unmap>", self.finish)
            bind(self._bindings, owner, "<Destroy>", lambda _e: self.close())
        self.finish()
        self.refresh()

    @property
    def busy(self):
        return (self._job is not None or self._geometry_job is not None or self._geometry_pending
                or self._settle_notify_pending or self._waiting_frame
                or (self.animator is not None and self.animator.is_running(self._key)))

    def set_expanded(self, value, *, animate=True):
        if not self._closed:
            self._settle_notify_pending = False
            self._waiting_frame = False
            self._catchups = 0
            self.expanded = bool(value)
            self._queue(animate)

    def refresh(self, _event=None):
        self._queue(False)

    def _queue(self, animate):
        if self._closed:
            return
        self._animate_pending |= animate
        if self._job is None:
            self._job = self.clip.after_idle(self._run)

    def _write(self, height):
        changed = (max(1, round(height)) != int(str(self.clip.cget("height")))
                   or bool(height > 0) != bool(self.clip.winfo_manager()))
        if self.present is not None and changed:
            generation = self._generation
            def apply():
                if not self._closed and generation == self._generation:
                    self._apply_height(height)
            return self.present(self.clip.master, self._key, apply)
        self._apply_height(height)

    def _apply_height(self, height):
        self._height = height
        requested = max(1, round(height))
        if int(str(self.clip.cget("height"))) != requested:
            self.clip.configure(height=requested)
        if height > 0:
            if self.clip.winfo_manager() != "grid":
                self.clip.grid()
        elif self.clip.winfo_manager() == "grid":
            self.clip.grid_remove()
        self._geometry_pending = bool(height > 0 and self.clip.master.winfo_viewable()
                                      and self.clip.winfo_height() != requested)
        if self.clip.winfo_manager() == "grid" and self._geometry_job is None:
            self._geometry_job = self.clip.after_idle(self._sync_geometry)

    def _sync_geometry(self):
        self._geometry_job = None
        if self._closed or not self.clip.master.winfo_viewable():
            self._geometry_pending = False
            return
        self._geometry_pending = (self.clip.winfo_manager() == 'grid'
                                  and self.clip.winfo_height() != max(1, round(self._height)))
        if self._geometry_pending:
            # A propagated grid request can leave an already-allocated parent
            # unchanged, without another Configure to arrange its children.
            # Re-arrange after propagation, without nesting update_idletasks.
            parent = self.clip.master
            parent.event_generate("<Configure>", width=parent.winfo_width(), height=parent.winfo_height(),
                                  x=parent.winfo_x(), y=parent.winfo_y())
        self._notify_settled()

    def _notify_settled(self):
        if (self._settle_notify_pending and not self._geometry_pending and self._geometry_job is None
                and self._job is None and not self._waiting_frame
                and (self.animator is None or not self.animator.is_running(self._key))):
            self._settle_notify_pending = False
            if self.on_settled is not None:
                self.on_settled()

    def _run(self):
        self._job = None
        animate, self._animate_pending = self._animate_pending, False
        if self._waiting_frame:
            return
        target = float(self.content.winfo_reqheight()) if self.expanded else 0.0
        moving = self.animator is not None and self.animator.is_running(self._key)
        if moving and target == self._target:
            return
        if not animate and not moving and target == self._target == self._height:
            self._notify_settled()
            return
        if (animate and self.animator is not None and self.animator.enabled and target > 0
                and self.clip.master.winfo_viewable() and not self.clip.winfo_ismapped()):
            # Map the clipping surface and let native geometry catch up before
            # spending the entrance budget on a cold nested page layout.
            self._write(1)
            self._queue(True)
            return
        start = (float(self.clip.winfo_height()) if self.clip.winfo_ismapped()
                 else self._height)
        self._target = target
        self._generation += 1
        generation = self._generation

        def complete():
            if self._closed or generation != self._generation:
                return
            self._write(target)
            self._settle_notify_pending = start != target and self.on_settled is not None
            self._notify_settled()

        def step(progress):
            if self._closed or generation != self._generation:
                return
            visible = self.clip.winfo_height() if self.clip.winfo_manager() == "grid" else 0
            middle = start+(target-start)*.35
            if (self.animator is not None and self.animator.enabled and self.animator._in_tick
                    and progress > .5 and start != target and visible == round(start) and self._catchups < 3
                    and round(middle) not in {round(start), round(target)}):
                # Native geometry can lag behind a busy page's timer callbacks.
                # Commit a meaningful middle frame, release the timer, then
                # resume only when Tk actually configures that visible height.
                self._catchups += 1
                self._waiting_frame = True
                self.animator.cancel(self._key)
                self._write(middle)
                return
            self._write(start+(target-start)*progress)

        if ((animate or moving) and self.animator is not None and start != target
                and self.clip.master.winfo_viewable()):
            self.animator.animate(self._key, anim.DUR_SHEET, step, ease=anim.EASE_OUT, done=complete)
        else:
            if self.animator is not None:
                self.animator.cancel(self._key)
            step(1)
            complete()

    def _frame_ready(self, _event=None):
        if not self._closed and self.clip.winfo_height() == max(1, round(self._height)):
            self._geometry_pending = False
        if (not self._closed and self._waiting_frame
                and self.clip.winfo_height() == max(1, round(self._height))):
            self._waiting_frame = False
            self._queue(True)
        self._notify_settled()

    def _cancel(self):
        self._generation += 1
        self._waiting_frame = False
        self._geometry_pending = False
        self._settle_notify_pending = False
        if self._job is not None:
            self.clip.after_cancel(self._job)
            self._job = None
        if self._geometry_job is not None:
            self.clip.after_cancel(self._geometry_job)
            self._geometry_job = None
        self._animate_pending = False
        if self.animator is not None:
            self.animator.cancel(self._key)

    def finish(self, _event=None):
        if self._closed:
            return
        self._cancel()
        self._target = float(self.content.winfo_reqheight()) if self.expanded else 0.0
        self._write(self._target)

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._cancel()
        UnderlineMotion._unbind(self._bindings)


if sys.platform == "win32":
    from ctypes import wintypes
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    _user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    _user32.SetWindowRgn.argtypes = [wintypes.HWND, wintypes.HRGN, wintypes.BOOL]
    _gdi32.CreateRectRgn.argtypes = [ctypes.c_int] * 4
    _gdi32.CreateRectRgn.restype = wintypes.HRGN
    _gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]


def _clip_popdown(call, path: str, progress: float) -> None:
    if sys.platform != "win32":
        return
    hwnd = wintypes.HWND(int(str(call("wm", "frame", path)), 0))
    if progress >= 1:
        _user32.SetWindowRgn(hwnd, None, True)
        return
    rect = wintypes.RECT()
    setter = getattr(_user32, "SetThreadDpiAwarenessContext", None)
    previous = None
    if setter is not None:
        setter.argtypes, setter.restype = [ctypes.c_void_p], ctypes.c_void_p
        previous = setter(ctypes.c_void_p(-3))
    try:
        if not _user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return
    finally:
        if previous:
            setter(previous)
    region = _gdi32.CreateRectRgn(0, 0, rect.right-rect.left, max(1, round((rect.bottom-rect.top)*progress)))
    if region and not _user32.SetWindowRgn(hwnd, region, True):
        _gdi32.DeleteObject(region)  # Successful sets transfer ownership to Windows.


class DropdownReveal:
    """Root-owned native Combobox entrances; one instance per Tk root."""

    def __init__(self, root: tk.Misc, animator: anim.Animator, *,
                 metrics: MutableMapping[str, float] | None = None) -> None:
        self.root, self.animator, self._metrics = root, animator, metrics
        self._paths: set[str] = set()
        self._closed = False
        self._bindings = []
        for sequence, callback in (("<Map>", self._on_map), ("<Unmap>", self._on_unmap),
                                   ("<Destroy>", self._on_unmap)):
            binding = root.bind_class("ComboboxPopdown", sequence, callback, add="+")
            self._bindings.append((sequence, binding))
        self._root_binding = root.bind("<Destroy>", self._on_destroy, add="+")

    @property
    def active(self) -> bool:
        return bool(self._paths)

    @staticmethod
    def _fix_view(call, path: str) -> None:
        # ttk computes `see current` before the final list height is assigned.
        lb = f"{path}.f.l"
        try:
            count = int(call(lb, "size"))
            lines = max(1, int(call(lb, "cget", "-height")))
            selection = call(lb, "curselection")
            current = (int(selection[0]) if isinstance(selection, tuple) and selection
                       else int(selection or 0) if str(selection).strip() else 0)
            call(lb, "yview", 0 if count <= lines or current < lines else min(current, count-lines))
        except (tk.TclError, ValueError, TypeError, IndexError):
            pass

    def _on_map(self, event) -> None:
        if self._closed:
            return
        path, call = str(event.widget), self.root.tk.call
        try:
            if call("winfo", "toplevel", path) != path or not int(call("winfo", "ismapped", path)):
                return
            self._fix_view(call, path)
            if not self.animator.enabled:
                return
            call("wm", "attributes", path, "-alpha", 1)
        except tk.TclError:
            return
        self._paths.add(path)
        if self._metrics is not None:
            self._metrics["popdown"] = self._metrics.get("popdown", 0) + 1

        def step(progress: float) -> None:
            started = time.perf_counter()
            call("wm", "attributes", path, "-alpha", progress)
            _clip_popdown(call, path, progress)
            if self._metrics is not None:
                elapsed = (time.perf_counter()-started)*1000
                self._metrics["frames"] = self._metrics.get("frames", 0) + 1
                self._metrics["frame_ms"] = self._metrics.get("frame_ms", 0) + elapsed
                self._metrics["frame_max"] = max(self._metrics.get("frame_max", 0), elapsed)

        self.animator.animate(("popdown", path), anim.DUR_BASE, step, ease=anim.EASE_OUT,
                              done=lambda: self._paths.discard(path))

    def _on_unmap(self, event) -> None:
        path = str(event.widget)
        if path in self._paths:
            self.animator.cancel(("popdown", path), finish=True)
            self._paths.discard(path)

    def reset(self) -> None:
        for path in list(self._paths):
            self.animator.cancel(("popdown", path), finish=True)

    def _on_destroy(self, event) -> None:
        if event.widget is self.root:
            self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.reset()
        for sequence, binding in self._bindings:
            try:
                script = str(self.root.tk.call("bind", "ComboboxPopdown", sequence))
                remaining = "\n".join(line for line in script.splitlines() if f"[{binding} " not in line)
                self.root.tk.call("bind", "ComboboxPopdown", sequence, remaining)
                self.root.deletecommand(binding)
            except tk.TclError:
                pass
        self._bindings.clear()
        try:
            self.root.unbind("<Destroy>", self._root_binding)
        except tk.TclError:
            pass

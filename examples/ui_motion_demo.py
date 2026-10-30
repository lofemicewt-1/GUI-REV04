"""Run `python -m examples.ui_motion_demo`; add --smoke for an automatic check."""
import argparse
import json
import tkinter as tk
from tkinter import ttk

from core.animation import Animator
from core.ui_motion import DropdownReveal, UnderlineMotion


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    root = tk.Tk()
    root.title("Tkinter 动画复用示例")
    root.geometry("520x260")
    animator = Animator(root)
    dropdown = DropdownReveal(root, animator)  # Once per root, covers every ttk Combobox.
    body = ttk.Frame(root, padding=24)
    body.pack(fill="both", expand=True)
    ttk.Label(body, text="点击选项，或打开下拉列表", font=("Microsoft YaHei UI", 12)).pack(anchor="w")
    header = ttk.Frame(body)
    header.pack(fill="x", pady=(18, 0))
    track = ttk.Frame(body, height=3)
    track.pack(fill="x", pady=(4, 16))
    indicator = tk.Frame(track, bg="#2563EB", bd=0)
    motion = UnderlineMotion(indicator, animator)
    buttons = []
    for index, label in enumerate(("选项 A", "选项 B", "选项 C")):
        header.columnconfigure(index, weight=1, uniform="tabs")
        button = ttk.Button(header, text=label)
        button.configure(command=lambda target=button: motion.select(target))
        button.grid(row=0, column=index, sticky="ew", padx=(0, 4))
        buttons.append(button)
    combo = ttk.Combobox(body, values=[f"列表选项 {i+1}" for i in range(20)], state="readonly", height=5)
    combo.current(17)
    combo.pack(fill="x")
    enabled = tk.BooleanVar(value=True)

    def toggle():
        animator.set_enabled(enabled.get())
        if not enabled.get():
            motion.finish()
            dropdown.reset()

    ttk.Checkbutton(body, text="启用动画", variable=enabled, command=toggle).pack(anchor="w", pady=12)

    def close():
        motion.close()
        dropdown.close()
        animator.stop()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", close)
    motion.select(buttons[0], animate=False)
    result = {}
    if args.smoke:
        errors, samples = [], []
        root.report_callback_exception = lambda k, value, tb: errors.append(str(value))
        indicator.bind("<Configure>", lambda event: samples.append(event.x), add="+")
        root.after(200, lambda: motion.select(buttons[2]))
        root.after(500, lambda: root.tk.call("ttk::combobox::Post", str(combo)))
        root.after(800, lambda: root.tk.call("ttk::combobox::Unpost", str(combo)))
        root.after(850, lambda: motion.select(buttons[1]))

        def finish_smoke():
            enabled.set(False)
            toggle()
            root.update_idletasks()
            expected = buttons[1].winfo_rootx()-track.winfo_rootx()
            result.update(ok=not errors and indicator.winfo_x() == expected and not motion.busy
                          and any(0 < x < buttons[2].winfo_rootx()-track.winfo_rootx() for x in samples),
                          intermediate_positions=samples, errors=errors,
                          application_imported="app.application" in __import__("sys").modules)
            close()

        root.after(1400, finish_smoke)
    root.mainloop()
    if args.smoke:
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result.get("ok") and not result["application_imported"] else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

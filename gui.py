"""Graphical controls for the calibrated emulator tasks."""

import math
import re
from datetime import datetime
import queue
import subprocess
import sys
import threading
import tkinter as tk
from collections import OrderedDict
from pathlib import Path
from tkinter import messagebox
from PIL import Image, ImageTk


ROOT = Path(__file__).resolve().parent
ADB = ROOT / ".tools/platform-tools/adb"
BG = "#EDF3FF"
WHITE = "#FFFFFF"
FIELD = "#F8FAFF"
TEXT = "#111B36"
MUTED = "#667493"
BORDER = "#DCE5F8"
BLUE = "#306AF0"
RED = "#E7474F"
GREEN = "#2DC46D"
TRACK = "#CBD5E8"


def timestamp_line(line):
    return line if re.match(r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\]", line) else f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {line}"


def exception_reason(lines, code):
    for line in reversed(lines):
        if "已停止：" in line:
            return line.split("已停止：", 1)[1]
    for line in reversed(lines):
        if re.match(r"^(?:\w+\.)*\w*(?:Error|Exception):", line):
            return line
    return f"程序异常退出（退出码 {code}）"


def px(widget, value):
    return round(value)


def asset_options(name, size, master):
    root = master.winfo_toplevel()
    if not hasattr(root, "_assets"):
        root._assets = OrderedDict()
    key = (name, size)
    if key not in root._assets:
        with Image.open(ROOT / "assets" / name) as source:
            photo = ImageTk.PhotoImage(source.convert("RGBA").resize(size, Image.Resampling.LANCZOS),
                                       master=master)
        root._assets[key] = {"image": photo}
        if len(root._assets) > 512:
            root._assets.popitem(last=False)
    else:
        root._assets.move_to_end(key)
    return root._assets[key]


def draw_asset(canvas, name, size, x, y, **kwargs):
    scaled_size = tuple(px(canvas, length) for length in size)
    return canvas.create_image(x, y, **asset_options(name, scaled_size, canvas), **kwargs)


TASKS = (
    ("help", "自动联盟互助"),
    ("explore", "自动领取探险经验"),
    ("donate", "自动捐献"),
    ("recruit", "自动免费招募"),
    ("warehouse", "自动领取仓库补给"),
    ("tree", "自动收集生命结晶"),
    ("dawn", "自动收集晨曦回礼"),
    ("pet", "宠物技能"),
    ("reconnect", "强制下线后自动重连"),
)
UNITS = (("shield", "盾兵"), ("spear", "矛兵"), ("archer", "射手"))


def discover_emulators():
    output = subprocess.run([str(ADB), "devices", "-l"], check=True, capture_output=True,
                            text=True, timeout=15).stdout
    found = []
    for line in output.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 2 or parts[1] != "device":
            continue
        serial = parts[0]
        is_emulator = serial.startswith("emulator-")
        if not is_emulator:
            try:
                for prop in ("ro.kernel.qemu", "ro.boot.qemu"):
                    result = subprocess.run([str(ADB), "-s", serial, "shell", "getprop", prop],
                                            check=True, capture_output=True, text=True, timeout=4)
                    if result.stdout.strip() == "1":
                        is_emulator = True
                        break
            except (OSError, subprocess.SubprocessError):
                continue
        if is_emulator:
            model = next((item[6:].replace("_", " ") for item in parts[2:]
                          if item.startswith("model:")), "模拟器")
            found.append((serial, f"{model} ({serial})"))
    return found


class Switch(tk.Canvas):
    def __init__(self, parent, variable, command=None):
        super().__init__(parent, width=42, height=24, bg=WHITE, bd=0,
                         highlightthickness=0, takefocus=1, cursor="arrow")
        self.variable = variable
        self.command = command
        self.enabled = True
        self.variable.trace_add("write", lambda *_: self.draw())
        self.bind("<Button-1>", self.toggle)
        self.bind("<space>", self.toggle)
        self.bind("<Return>", self.toggle)
        self.draw()

    def toggle(self, _event=None):
        if self.enabled:
            self.focus_set()
            self.variable.set(not self.variable.get())
            if self.command:
                self.command()

    def set_enabled(self, enabled):
        self.enabled = enabled
        self.draw()

    def draw(self):
        self.delete("all")
        on = self.variable.get()
        fill = GREEN if on else TRACK
        if not self.enabled:
            fill = "#A6E8BF" if on else BORDER
        self.create_line(*(px(self, value) for value in (12, 12, 30, 12)),
                         width=px(self, 24), capstyle="round", fill=fill)
        x = 20 if on else 2
        self.create_oval(*(px(self, value) for value in (x, 2, x + 20, 22)),
                         fill=WHITE, outline="")



class HintEntry(tk.Canvas):
    def __init__(self, parent, initial, hint):
        super().__init__(parent, height=38, bg=WHITE, bd=0, highlightthickness=0)
        self.value = tk.StringVar(value=initial)
        self.entry = tk.Entry(self, textvariable=self.value, bg=FIELD, fg=TEXT, bd=0,
                              highlightthickness=0, insertbackground=TEXT,
                              disabledbackground=FIELD, font=("Helvetica Neue", 13))
        self.entry_id = self.create_window(13, 19, window=self.entry, anchor="w", height=26)
        self.entry.bind("<Up>", lambda _event: self.step_value(1))
        self.entry.bind("<Down>", lambda _event: self.step_value(-1))
        self.entry.bind("<Button-1>", self.click, add="+")
        self.enabled = True
        self.hint = hint
        self.pressed_step = 0
        self.press_timer = None
        self.value.trace_add("write", lambda *_: self.draw())
        self.bind("<Configure>", lambda _event: self.draw())
        self.bind("<Button-1>", self.click)
        self.draw()

    def get(self):
        return self.value.get().strip()

    def set_enabled(self, enabled):
        self.enabled = enabled
        self.entry.configure(state="normal" if enabled else "disabled")
        self.draw()

    def click(self, event):
        x = event.x + (self.entry.winfo_x() if event.widget is self.entry else 0)
        y = event.y + (self.entry.winfo_y() if event.widget is self.entry else 0)
        if x >= self.winfo_width() - px(self, 35):
            self.press_step(1 if y < px(self, 19) else -1)
        else:
            self.entry.focus_set()

    def press_step(self, change):
        if not self.enabled:
            return
        if self.press_timer is not None:
            self.after_cancel(self.press_timer)
        self.pressed_step = change
        self.step_value(change)
        self.press_timer = self.after(150, self.clear_press)

    def clear_press(self):
        self.pressed_step = 0
        self.press_timer = None
        self.draw()

    def step_value(self, change):
        if not self.enabled:
            return "break"
        try:
            current = float(self.get())
        except ValueError:
            current = 1.0
        value = max(1.0, current + change)
        self.value.set(str(int(value)) if value.is_integer() else str(value))
        return "break"

    def draw(self):
        self.delete("panel")
        self.delete("hint")
        width = max(self.winfo_width(), px(self, 80))
        p = lambda value: px(self, value)
        round_rect(self, p(1), p(1), width - p(1), p(37), p(8), BORDER)
        round_rect(self, p(2), p(2), width - p(2), p(36), p(7), FIELD)
        round_rect(self, width - p(25), p(7), width - p(7), p(31), p(6), "#F0F4FB")
        if self.pressed_step:
            top, bottom = (p(7), p(19)) if self.pressed_step > 0 else (p(19), p(31))
            self.create_rectangle(width - p(25), top, width - p(7), bottom,
                                  fill="#DCE8FF", outline="", tags="panel")
        draw_asset(self, "ui/stepper.png", (12, 18), width - p(16), p(19), tags="panel")
        self.tag_lower("panel")
        self.coords(self.entry_id, p(13), p(19))
        self.itemconfigure(self.entry_id, width=max(1, width - p(60)), height=p(26))
        if not self.value.get():
            self.create_text(p(13), p(19), text=self.hint, anchor="w", fill=MUTED,
                             font=("Helvetica Neue", p(10)), tags="hint")
            self.tag_bind("hint", "<Button-1>", lambda _event: self.entry.focus_set())


class DevicePicker(tk.Canvas):
    def __init__(self, parent, variable):
        super().__init__(parent, width=340, height=36, bg=WHITE, bd=0,
                         highlightthickness=0, takefocus=1, cursor="arrow")
        self.variable = variable
        self.values = ()
        self.enabled = False
        self.popup = None
        self.variable.trace_add("write", lambda *_: self.draw())
        self.bind("<Configure>", lambda _event: self.draw())
        self.bind("<Button-1>", self.open_menu)
        self.bind("<space>", self.open_menu)
        self.bind("<Return>", self.open_menu)
        self.bind("<Down>", self.open_menu)
        self.bind_all("<Button-1>", self.dismiss_outside, add="+")
        self.draw()

    def set_values(self, values):
        self.close_menu()
        self.values = tuple(values)

    def set_enabled(self, enabled):
        self.enabled = enabled
        if not enabled:
            self.close_menu()
        self.draw()

    def draw(self):
        self.delete("all")
        p = lambda value: px(self, value)
        width = max(self.winfo_width(), p(80))
        round_rect(self, 0, 0, width, p(36), p(8), BORDER)
        round_rect(self, p(1), p(1), width - p(1), p(35), p(7), FIELD)
        self.create_text(p(12), p(18), anchor="w", text=self.variable.get(),
                         fill=TEXT if self.enabled else MUTED,
                         font=("Helvetica Neue", p(12)))
        # Keep long device names away from the arrow.
        self.create_rectangle(width - p(32), p(3), width - p(8), p(33), fill=FIELD, outline=FIELD)
        draw_asset(self, "ui/chevron.png", (12, 8), width - p(19), p(18))

    def close_menu(self, _event=None):
        if self.popup is not None:
            self.popup.destroy()
            self.popup = None

    def choose(self, _event=None):
        selection = self.choices.curselection()
        if selection:
            self.variable.set(self.values[selection[0]])
        self.close_menu()
        self.focus_set()
        return "break"

    def open_menu(self, _event=None):
        if self.popup is not None:
            self.close_menu()
            return "break"
        if not self.enabled or not self.values:
            return "break"
        self.popup = tk.Toplevel(self, bg=WHITE, bd=0)
        self.popup.overrideredirect(True)
        self.popup.transient(self.winfo_toplevel())
        self.choices = tk.Listbox(self.popup, bg=WHITE, fg=TEXT, bd=0,
                                  highlightthickness=0, selectborderwidth=0,
                                  selectbackground="#E8F0FF", selectforeground=BLUE,
                                  activestyle="none", exportselection=False,
                                  font=("Helvetica Neue", 12), height=min(6, len(self.values)))
        self.choices.pack(fill="both", expand=True, padx=1, pady=6)
        for value in self.values:
            self.choices.insert("end", "  " + value)
        selected = self.values.index(self.variable.get()) if self.variable.get() in self.values else 0
        self.choices.selection_set(selected)
        self.choices.activate(selected)
        self.choices.see(selected)
        self.popup.update_idletasks()
        height = self.popup.winfo_reqheight()
        y = self.winfo_rooty() + self.winfo_height() + 4
        if y + height > self.winfo_screenheight():
            y = self.winfo_rooty() - height - 4
        self.popup.geometry(f"{self.winfo_width()}x{height}+{self.winfo_rootx()}+{y}")
        self.choices.bind("<ButtonRelease-1>", self.choose)
        self.choices.bind("<Return>", self.choose)
        self.choices.bind("<Escape>", self.close_menu)
        self.choices.bind("<FocusOut>", self.close_menu)
        self.choices.focus_set()
        return "break"

    def dismiss_outside(self, event):
        if self.popup is None:
            return
        inside_popup = (self.popup.winfo_rootx() <= event.x_root < self.popup.winfo_rootx() + self.popup.winfo_width()
                        and self.popup.winfo_rooty() <= event.y_root < self.popup.winfo_rooty() + self.popup.winfo_height())
        if not inside_popup:
            self.close_menu()


def round_rect(canvas, x1, y1, x2, y2, radius, color):
    """Draw a rounded surface directly in Tk, without resampling an image on resize."""
    radius = min(radius, (x2 - x1) / 2, (y2 - y1) / 2)
    return canvas.create_polygon(
        x1 + radius, y1, x2 - radius, y1, x2, y1, x2, y1 + radius,
        x2, y2 - radius, x2, y2, x2 - radius, y2, x1 + radius, y2,
        x1, y2, x1, y2 - radius, x1, y1 + radius, x1, y1,
        smooth=True, splinesteps=12, fill=color, outline="", tags="panel")


class Panel(tk.Canvas):
    def __init__(self, parent, inset=20):
        super().__init__(parent, bg=BG, bd=0, highlightthickness=0)
        self.inset = inset
        self.body = tk.Frame(self, bg=WHITE)
        self.body_id = self.create_window(inset, inset, window=self.body, anchor="nw")
        self.bind("<Configure>", self.redraw)

    def redraw(self, event):
        self.delete("panel")
        inset = px(self, 1)
        round_rect(self, inset, inset, event.width - inset, event.height - inset,
                   px(self, 14), BORDER)
        round_rect(self, 2 * inset, 2 * inset, event.width - 2 * inset,
                   event.height - 2 * inset, px(self, 13), WHITE)
        self.tag_lower("panel")
        self.coords(self.body_id, px(self, self.inset), px(self, self.inset))
        self.itemconfigure(self.body_id, width=max(1, event.width - 2 * px(self, self.inset)),
                           height=max(1, event.height - 2 * px(self, self.inset)))


def image_label(parent, name, size, background):
    label = tk.Label(parent, **asset_options(name, (size, size), parent),
                     bg=background, bd=0, padx=0, pady=0)
    root = parent.winfo_toplevel()
    if not hasattr(root, "_native_asset_labels"):
        root._native_asset_labels = []
    root._native_asset_labels.append((label, name, size))
    return label


def badge(parent, symbol, size=34, background=WHITE):
    return image_label(parent, f"ui/{symbol}.png", size, background)


class ActionButton(tk.Canvas):
    def __init__(self, parent, command):
        super().__init__(parent, width=138, height=44, bg=WHITE, bd=0, highlightthickness=0,
                         takefocus=1, cursor="arrow")
        self.command = command
        self.mode = "start"
        self.bind("<Configure>", lambda _event: self.draw())
        self.bind("<Button-1>", self.press)
        self.bind("<space>", self.press)
        self.bind("<Return>", self.press)
        self.draw()

    def press(self, _event=None):
        if self.mode != "stopping":
            self.focus_set()
            self.command()

    def set_mode(self, mode):
        self.mode = mode
        self.configure(cursor="arrow")
        self.draw()

    def draw(self):
        self.delete("all")
        p = lambda value: px(self, value)
        width = max(self.winfo_width(), p(138))
        color = BLUE if self.mode == "start" else RED
        round_rect(self, p(1), p(1), width - p(1), p(43), p(11), color)
        label = {"start": "启动", "stop": "停止", "stopping": "正在停止…"}[self.mode]
        if self.mode == "start":
            draw_asset(self, "ui/play.png", (14, 16), p(41), p(22))
        self.create_text(width / 2 + (p(10) if self.mode == "start" else 0), p(22),
                         text=label, fill=WHITE, font=("Helvetica Neue", p(14), "bold"))


class RefreshButton(tk.Canvas):
    def __init__(self, parent, command):
        super().__init__(parent, width=94, height=36, bg=WHITE, bd=0,
                         highlightthickness=0, cursor="arrow", takefocus=1)
        self.command = command
        self.enabled = True
        self.bind("<Button-1>", self.press)
        self.bind("<space>", self.press)
        self.bind("<Return>", self.press)
        self.draw()

    def press(self, _event=None):
        if self.enabled:
            self.focus_set()
            self.command()

    def set_enabled(self, enabled):
        self.enabled = enabled
        self.configure(cursor="arrow" if enabled else "arrow")
        self.draw()

    def draw(self):
        self.delete("all")
        p = lambda value: px(self, value)
        round_rect(self, 0, 0, p(94), p(36), p(8), BORDER)
        round_rect(self, p(1), p(1), p(93), p(35), p(7), FIELD)
        draw_asset(self, "ui/refresh.png", (18, 18), p(29), p(18))
        self.create_text(p(62), p(18), text="刷新", fill=TEXT if self.enabled else MUTED,
                         font=("Helvetica Neue", p(12)))


class BotWindow:
    def __init__(self, root):
        self.root = root
        root.title("无尽冬日 GameBot")
        root.geometry("1040x800")
        root.minsize(560, 560)
        root.resizable(True, True)
        root.configure(bg=BG)
        icon_path = ROOT / "assets/gamebot-dock.png"
        self.app_icon = tk.PhotoImage(file=icon_path)
        root.iconphoto(True, self.app_icon)
        if sys.platform == "darwin":
            try:
                from AppKit import NSApplication, NSImage
                NSApplication.sharedApplication().setApplicationIconImage_(
                    NSImage.alloc().initWithContentsOfFile_(str(icon_path)))
            except ImportError:
                pass
        self.options = {name: tk.BooleanVar(value=True) for name, _ in (*TASKS, *UNITS)}
        self.options["train"] = tk.BooleanVar(value=True)
        self.device = tk.StringVar()
        self.device_lookup = {}
        self.device_results = queue.Queue()
        self.scanning = False
        self.process = None
        self.reader = None
        self.lines = queue.Queue()
        self.output = []
        self.stopping = False
        self.switches = {}

        self.viewport = tk.Canvas(root, bg=BG, bd=0, highlightthickness=0)
        self.viewport.pack(fill="both", expand=True)
        self.scrollbar = tk.Scrollbar(root, orient="vertical", command=self.scroll_to)
        self.viewport.configure(yscrollcommand=self.scrollbar.set)
        frame = tk.Frame(self.viewport, bg=BG, padx=22, pady=4)
        self.content = frame
        self.content_id = self.viewport.create_window(0, 0, window=frame, anchor="nw")
        self.viewport.bind("<Configure>", self.resize_content)
        frame.bind("<Configure>", self.update_scrollregion)
        root.bind_all("<MouseWheel>", self.scroll_content)
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(4, weight=1, minsize=180)

        header = tk.Frame(frame, bg=BG, height=58)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        header.pack_propagate(False)
        self.logo_label = image_label(header, "gamebot-icon.png", 46, BG)
        self.logo_label.pack(side="left", padx=(8, 14))
        self.native_images = []
        branding = tk.Frame(header, bg=BG)
        branding.pack(side="left", anchor="center")
        tk.Label(branding, text="无尽冬日 GameBot", bg=BG, fg=TEXT,
                 font=("Helvetica Neue", 22, "bold")).pack(anchor="w")
        tk.Label(branding, text="自动任务控制台", bg=BG, fg=MUTED,
                 font=("Helvetica Neue", 11)).pack(anchor="w")

        self.device_panel = device_panel = Panel(frame, inset=17)
        device_panel.configure(height=118)
        device_panel.grid(row=1, column=0, sticky="ew", pady=(0, 9))
        device = device_panel.body
        device.grid_columnconfigure(1, weight=1)
        badge(device, "monitor", size=42).grid(row=0, column=0, sticky="n", padx=(2, 15))
        self.device_group = device_group = tk.Frame(device, bg=WHITE)
        device_group.grid(row=0, column=1, sticky="ew")
        tk.Label(device_group, text="模拟器", bg=WHITE, fg=TEXT,
                 font=("Helvetica Neue", 13, "bold")).pack(anchor="w", pady=(0, 4))
        device_row = tk.Frame(device_group, bg=WHITE)
        device_row.pack(fill="x")
        self.device_box = DevicePicker(device_row, self.device)
        self.device_box.pack(side="left", fill="x", expand=True)
        self.refresh_button = RefreshButton(device_row, self.refresh_devices)
        self.refresh_button.pack(side="left", padx=(10, 0))
        self.device_status = tk.Label(device_group, text="正在查找模拟器…", bg=WHITE, fg=MUTED,
                                      font=("Helvetica Neue", 10))
        self.device_status.pack(anchor="w", pady=(4, 0))
        self.device_divider = tk.Frame(device, bg=BORDER, width=1)
        self.device_divider.grid(row=0, column=2, sticky="ns", padx=18)
        self.action_group = action_group = tk.Frame(device, bg=WHITE)
        action_group.grid(row=0, column=3, sticky="e")
        self.status_label = tk.Label(action_group, text="● 未运行", bg=WHITE, fg=MUTED,
                                     font=("Helvetica Neue", 11))
        self.status_label.pack(side="left", padx=(0, 12))
        self.start_button = ActionButton(action_group, self.toggle)
        self.start_button.pack(side="left")

        heading = tk.Frame(frame, bg=BG)
        heading.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        badge(heading, "gear", size=38, background=BG).pack(side="left", padx=(2, 10))
        tk.Label(heading, text="自动任务", bg=BG, fg=TEXT,
                 font=("Helvetica Neue", 18, "bold")).pack(side="left")
        tk.Label(heading, text="到期自动检查，重连后立即复查", bg=BG, fg=MUTED,
                 font=("Helvetica Neue", 10)).pack(side="right")
        badge(heading, "clock", size=15, background=BG).pack(side="right", padx=(0, 7))

        self.cards = cards = tk.Frame(frame, bg=BG)
        cards.grid(row=3, column=0, sticky="nsew")
        cards.grid_rowconfigure(0, weight=1)
        for column in range(3):
            cards.grid_columnconfigure(column, weight=1, uniform="cards")
        daily_card, daily = self.card(cards, "日常任务", "calendar")
        daily_card.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        for name, label in (("help", "联盟互助"), ("explore", "探险经验"),
                            ("warehouse", "仓库补给"), ("dawn", "晨曦回礼"), ("pet", "宠物技能")):
            self.option_row(daily, name, label)

        training_card, training = self.card(cards, "练兵与联盟", "people")
        training_card.grid(row=0, column=1, sticky="nsew", padx=6)
        self.option_row(training, "train", "自动练兵", self.training_changed)
        for name, label in UNITS:
            self.option_row(training, name, label, indent=True)
        self.option_row(training, "donate", "联盟捐献")
        self.option_row(training, "recruit", "免费招募")

        connection_card, connection = self.card(cards, "结晶与连接", "diamond")
        connection_card.grid(row=0, column=2, sticky="nsew", padx=(6, 0))
        self.option_row(connection, "tree", "自动收集生命结晶", self.dependent_changed)
        self.threshold_entry = self.input_row(connection, "收集阈值 · 大于 0", "1000", "阈值必须大于 0")
        self.option_row(connection, "reconnect", "强制下线后自动重连", self.dependent_changed)
        self.reconnect_entry = self.input_row(connection, "点击重连前等待 · 分钟", "10", "等待时间必须大于 0")

        self.task_cards = (daily_card, training_card, connection_card)
        self.log_panel = log_panel = Panel(frame, inset=14)
        log_panel.configure(height=180)
        log_panel.grid(row=4, column=0, sticky="nsew", pady=(9, 0))
        self.log_heading = log_heading = tk.Frame(log_panel.body, bg=WHITE)
        log_heading.pack(fill="x", pady=(0, 6))
        badge(log_heading, "terminal", size=26).pack(side="left", padx=(2, 10))
        tk.Label(log_heading, text="运行日志", bg=WHITE, fg=TEXT,
                 font=("Helvetica Neue", 15, "bold")).pack(side="left")
        tk.Label(log_heading, text="异常会弹窗提示", bg=WHITE, fg=MUTED,
                 font=("Helvetica Neue", 10)).pack(side="right")
        badge(log_heading, "info", size=15).pack(side="right", padx=(0, 7))
        log_area = tk.Frame(log_panel.body, bg=FIELD, highlightthickness=1,
                            highlightbackground=BORDER)
        log_area.pack(fill="both", expand=True)
        self.log = tk.Text(log_area, state="disabled", wrap="char", width=1, height=5, bg=FIELD,
                           fg=TEXT, bd=0, padx=14, pady=9, highlightthickness=0,
                           font=("Menlo", 11))
        self.log.tag_configure("task_disabled", foreground=RED)
        self.log_scrollbar = tk.Scrollbar(log_area, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=self.log_scrollbar.set)
        self.log_placeholder = tk.Frame(log_area, bg=FIELD)
        self.log_placeholder.pack(fill="both", expand=True)
        empty_content = tk.Frame(self.log_placeholder, bg=FIELD)
        empty_content.place(relx=.5, rely=.5, anchor="center")
        badge(empty_content, "document", size=30, background=FIELD).pack(pady=(0, 3))
        tk.Label(empty_content, text="暂无日志", bg=FIELD, fg=MUTED,
                 font=("Helvetica Neue", 11, "bold")).pack()
        tk.Label(empty_content, text="启动后将在这里显示运行记录", bg=FIELD, fg=MUTED,
                 font=("Helvetica Neue", 9)).pack()
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(0, self.refresh_devices)
        if sys.platform == "darwin":
            root.after_idle(self.install_native_images)
        self._layout_columns = 3
        self._laying_out = False
        self._layout_pending = False
        root.bindtags((*root.bindtags(), "GameBotResize"))
        root.bind_class("GameBotResize", "<Configure>", self.schedule_layout)
        root.after_idle(self.apply_layout)

    def schedule_layout(self, event):
        # Cocoa live resize can defer Tk timers until the mouse is released.
        self.apply_layout()

    def apply_layout(self):
        if self._laying_out:
            self._layout_pending = True
            return
        self._laying_out = True
        try:
            self.layout_content()
        finally:
            self._laying_out = False
            if self._layout_pending:
                self._layout_pending = False
                self.root.after_idle(self.apply_layout)

    def layout_content(self):
        width = self.root.winfo_width()
        if width <= 1:
            return
        columns = 3 if width >= 1040 else 2 if width >= 740 else 1
        if columns != self._layout_columns:
            self._layout_columns = columns
            for column in range(3):
                self.cards.grid_columnconfigure(column, weight=1 if column < columns else 0,
                                                uniform="cards" if column < columns else "", minsize=0)
            for row in range(3):
                self.cards.grid_rowconfigure(row, weight=0, minsize=0)
            for index, card in enumerate(self.task_cards):
                card.configure(width=1, height=300)
                card.grid(row=index // columns, column=index % columns, sticky="nsew",
                          padx=(0 if index % columns == 0 else 6,
                                0 if index % columns == columns - 1 else 6), pady=(0, 10))
            if columns < 3:
                self.device_divider.grid_remove()
                self.action_group.grid(row=1, column=0, columnspan=4, sticky="e", pady=(10, 0))
                self.device_panel.configure(height=174)
            else:
                self.device_divider.grid()
                self.action_group.grid(row=0, column=3, columnspan=1, sticky="e", pady=0)
                self.device_panel.configure(height=118)
            self.device_box.configure(width=180 if columns == 1 else 340)
        self.device_status.configure(wraplength=max(200, self.device_group.winfo_width()))
        # Settle grid requests before sizing the canvas window; otherwise a narrow
        # layout can retain the old height and make its bottom unreachable.
        self.root.update_idletasks()
        self.viewport.itemconfigure(self.content_id,
                                    height=max(self.content.winfo_reqheight(), self.viewport.winfo_height()))
        self.root.update_idletasks()
        self.update_scrollregion()
        self.position_native_images()

    def install_native_images(self):
        # Tk 8.6 raster images render at 1× on Retina; AppKit keeps the source pixels.
        try:
            from AppKit import NSApplication, NSImage, NSImageView
        except ImportError:
            return
        windows = NSApplication.sharedApplication().windows()
        native_window = next((window for window in windows if window.title() == self.root.title()), None)
        if native_window is None:
            return
        for label, name, size in self.root._native_asset_labels:
            pixels = px(label, size)
            view = NSImageView.alloc().initWithFrame_(((0, 0), (pixels, pixels)))
            view.setImage_(NSImage.alloc().initWithContentsOfFile_(str(ROOT / "assets" / name)))
            native_window.contentView().addSubview_(view)
            # Reserve the same layout space without drawing a second, low-res copy underneath.
            label.blank_image = ImageTk.PhotoImage(Image.new("RGBA", (pixels, pixels)), master=label)
            label.configure(image=label.blank_image)
            self.native_images.append((label, view, size))
            label.bind("<Configure>", self.schedule_native_images, add="+")
        self.position_native_images()

    def schedule_native_images(self, _event=None):
        if not getattr(self, "_native_position_pending", False):
            self._native_position_pending = True
            self.root.after_idle(self.position_native_images)

    def position_native_images(self):
        self._native_position_pending = False
        for label, view, _size in self.native_images:
            size = label.winfo_width()
            x = label.winfo_rootx() - self.root.winfo_rootx()
            y = label.winfo_rooty() - self.root.winfo_rooty()
            parent = view.superview()
            view.setHidden_(not label.winfo_ismapped() or y < 0 or y + size > self.root.winfo_height())
            native_y = y if parent.isFlipped() else parent.bounds().size.height - y - size
            view.setFrame_(((x, native_y), (size, size)))

    def resize_content(self, event):
        self.viewport.itemconfigure(self.content_id, width=max(1, event.width - 14))
        self.schedule_layout(event)

    def update_scrollregion(self, _event=None):
        self.viewport.configure(scrollregion=self.viewport.bbox("all"))
        if self.content.winfo_height() > self.viewport.winfo_height() + 2:
            self.scrollbar.place(relx=1, rely=0, relheight=1, anchor="ne")
        else:
            self.scrollbar.place_forget()
            self.viewport.yview_moveto(0)

    def scroll_to(self, *args):
        self.viewport.yview(*args)
        self.position_native_images()

    def scroll_content(self, event):
        if event.widget is self.log:
            return
        self.viewport.yview_scroll(-1 if event.delta > 0 else 1, "units")
        self.position_native_images()

    def card(self, parent, title, icon):
        card = Panel(parent, inset=17)
        header = tk.Frame(card.body, bg=WHITE)
        header.pack(fill="x", pady=(0, 6))
        badge(header, icon, size=34).pack(side="left", padx=(0, 10))
        tk.Label(header, text=title, bg=WHITE, fg=TEXT,
                 font=("Helvetica Neue", 14, "bold")).pack(side="left")
        body = tk.Frame(card.body, bg=WHITE)
        body.pack(fill="both", expand=True)
        return card, body

    def option_row(self, parent, name, label, command=None, indent=False):
        row = tk.Frame(parent, bg=WHITE, height=35)
        row.pack(fill="x")
        row.pack_propagate(False)
        switch = Switch(row, self.options[name], command)
        switch.pack(side="right", pady=3)
        text = tk.Label(row, text=label, bg=WHITE, fg=MUTED if indent else TEXT,
                        font=("Helvetica Neue", 11), cursor="arrow")
        text.pack(side="left", padx=(18 if indent else 2, 0))
        text.bind("<Button-1>", switch.toggle)
        tk.Frame(row, bg=BORDER, height=1).place(relx=0, rely=1, relwidth=1, anchor="sw")
        self.switches[name] = switch

    def input_row(self, parent, label, initial, hint):
        row = tk.Frame(parent, bg=WHITE)
        row.pack(fill="x", pady=(4, 6))
        tk.Label(row, text=label, bg=WHITE, fg=MUTED,
                 font=("Helvetica Neue", 10)).pack(anchor="w", pady=(0, 5))
        entry = HintEntry(row, initial, hint)
        entry.pack(fill="x")
        return entry

    def refresh_devices(self):
        if self.scanning or self.process is not None:
            return
        self.scanning = True
        self.refresh_button.set_enabled(False)
        self.device_box.set_enabled(False)
        self.device_status.configure(text="正在查找模拟器…")
        threading.Thread(target=self.scan_devices, daemon=True).start()
        self.root.after(100, self.poll_devices)

    def scan_devices(self):
        try:
            self.device_results.put((discover_emulators(), None))
        except (OSError, subprocess.SubprocessError) as exc:
            self.device_results.put(([], str(exc)))

    def poll_devices(self):
        try:
            devices, error = self.device_results.get_nowait()
        except queue.Empty:
            self.root.after(100, self.poll_devices)
            return
        previous = self.device_lookup.get(self.device.get())
        self.device_lookup = {label: serial for serial, label in devices}
        labels = list(self.device_lookup)
        self.device_box.set_values(labels)
        self.device_box.set_enabled(bool(labels))
        chosen = next((label for serial, label in devices if serial == previous), None)
        self.device.set(chosen or (labels[0] if labels else ""))
        self.device_status.configure(text=error or (f"已找到 {len(labels)} 台模拟器" if labels else "未发现已连接的模拟器"))
        self.scanning = False
        self.refresh_button.set_enabled(True)

    def training_changed(self):
        for name, _ in UNITS:
            self.switches[name].set_enabled(self.process is None and self.options["train"].get())

    def dependent_changed(self):
        self.threshold_entry.set_enabled(self.process is None and self.options["tree"].get())
        self.reconnect_entry.set_enabled(self.process is None and self.options["reconnect"].get())

    def set_running(self, running):
        for name, _ in (*TASKS, ("train", "")):
            self.switches[name].set_enabled(not running)
        self.training_changed()
        self.dependent_changed()
        self.device_box.set_enabled(not running and bool(self.device_lookup))
        self.refresh_button.set_enabled(not running)

    def command(self):
        serial = self.device_lookup.get(self.device.get())
        if not serial:
            raise ValueError("请先连接并选择一台模拟器")
        cmd = [sys.executable, "-u", str(ROOT / "help_bot.py"), "--device", serial,
               "--daily-tasks", "--forever"]
        for name, _ in (*TASKS, *UNITS):
            if not self.options[name].get():
                cmd.append(f"--no-{name}")
        if not self.options["train"].get():
            cmd.append("--no-train")
        if self.options["tree"].get():
            try:
                value = int(self.threshold_entry.get())
            except ValueError:
                raise ValueError("生命结晶阈值必须是大于 0 的整数") from None
            if value <= 0:
                raise ValueError("生命结晶阈值必须大于 0")
            cmd += ["--tree-threshold", str(value)]
        if self.options["reconnect"].get():
            try:
                minutes = float(self.reconnect_entry.get())
            except ValueError:
                raise ValueError("重连等待时间必须是大于 0 的数字") from None
            if not math.isfinite(minutes) or minutes <= 0:
                raise ValueError("重连等待时间必须大于 0")
            cmd += ["--reconnect-minutes", str(minutes)]
        return cmd

    def toggle(self):
        if self.process is not None:
            self.stopping = True
            self.process.terminate()
            self.start_button.set_mode("stopping")
            self.status_label.configure(text="● 正在停止", fg=RED)
            return
        try:
            command = self.command()
            self.process = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE,
                                            stderr=subprocess.STDOUT, text=True, bufsize=1)
        except (OSError, ValueError) as exc:
            messagebox.showerror("无法启动", str(exc), parent=self.root)
            return
        self.output = []
        self.stopping = False
        self.append_log("已启动。")
        self.set_running(True)
        self.start_button.set_mode("stop")
        self.status_label.configure(text="● 运行中", fg=GREEN)
        self.reader = threading.Thread(target=self.read_output, daemon=True)
        self.reader.start()
        self.root.after(150, self.poll)

    def read_output(self):
        process = self.process
        for line in process.stdout:
            self.lines.put(line.rstrip())
        process.stdout.close()

    def poll(self):
        while not self.lines.empty():
            line = self.lines.get_nowait()
            self.output.append(line)
            self.append_log(line)
        if self.process.poll() is None or self.reader.is_alive():
            self.root.after(150, self.poll)
            return
        # The reader can enqueue the final error between the drain and its exit check.
        if not self.lines.empty():
            self.root.after(0, self.poll)
            return
        code = self.process.returncode
        stopped = self.stopping
        self.process = None
        self.set_running(False)
        self.start_button.set_mode("start")
        self.status_label.configure(text="● 未运行", fg=MUTED)
        if stopped:
            self.append_log("已停止。")
        elif code:
            messagebox.showerror("程序异常", exception_reason(self.output, code),
                                 parent=self.root)
        else:
            self.append_log("运行已结束。")

    def append_log(self, line):
        self.log_placeholder.pack_forget()
        self.log_scrollbar.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True)
        self.position_native_images()
        self.log.configure(state="normal")
        tag = ("task_disabled",) if "⚠️" in line else ()
        self.log.insert("end", timestamp_line(line) + "\n", tag)
        self.log.see("end")
        self.log.configure(state="disabled")

    def close(self):
        if self.process is not None:
            self.process.terminate()
        self.root.destroy()


if __name__ == "__main__":
    BotWindow(tk.Tk()).root.mainloop()

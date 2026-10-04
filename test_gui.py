"""Check simulator discovery without opening a desktop window."""

import subprocess
import os
import unittest
from unittest.mock import Mock, patch

import gui
from tkinter import font as tkfont


class DeviceDiscoveryTest(unittest.TestCase):
    def test_disabled_task_warning_uses_red_log_tag(self):
        window = object.__new__(gui.BotWindow)
        window.log_placeholder = Mock()
        window.log_scrollbar = Mock()
        window.log = Mock()
        window.error_log = Mock()
        window.error_area = Mock()
        window.error_area.winfo_manager.return_value = ""
        window.log_panel = Mock()
        window.log_panel.winfo_height.return_value = 180
        window.error_area.winfo_reqheight.return_value = 80
        window.root = Mock()
        window.root.winfo_width.return_value = 1040
        window.root.winfo_height.return_value = 800
        window.root.maxsize.return_value = (1600, 1000)
        window.apply_layout = Mock()
        window.log.tag_ranges.return_value = ()
        window.error_log.tag_ranges.return_value = ()
        window.position_native_images = Mock()
        window.append_log("普通日志")
        self.assertEqual(window.log.insert.call_args.args[2], ())
        window.error_log.insert.assert_not_called()
        window.error_area.pack.assert_not_called()
        window.append_log("⚠️ 当前已出现三个任务连续执行异常因此自动中断脚本")
        self.assertEqual(window.log.insert.call_args.args[2], ("task_disabled",))
        self.assertEqual(window.error_log.insert.call_args.args[0], "end")
        window.error_area.pack.assert_called_once_with(
            fill="x", before=window.log.master, pady=(4, 10))
        window.root.after_idle.assert_called_once_with(window.apply_layout)
        window.root.geometry.assert_called_once_with("1040x894")
        window.error_area.winfo_manager.return_value = "pack"
        window.append_log("⚠️ 生命结晶收集 已连续执行失败3次，后续不再执行这个任务")
        self.assertEqual(window.log.insert.call_args.args[2], ("task_disabled",))
        window.append_log("已停止：未确认主城")
        self.assertIn("未确认主城", window.error_log.insert.call_args.args[1])
        window.error_area.pack.assert_called_once()
        window.root.geometry.assert_called_once()

    def test_lists_only_online_emulators(self):
        devices = """List of devices attached
emulator-5554 device product:sdk model:Pixel_5
127.0.0.1:62001 device model:Nox_Player
other-device device model:unknown
emulator-5556 offline
"""

        def fake_run(command, **_kwargs):
            if command[1:3] == ["devices", "-l"]:
                return subprocess.CompletedProcess(command, 0, devices)
            return subprocess.CompletedProcess(command, 0,
                                               "1\n" if command[2] == "127.0.0.1:62001" else "0\n")

        with patch.object(gui.subprocess, "run", side_effect=fake_run):
            self.assertEqual(gui.discover_emulators(), [
                ("emulator-5554", "Pixel 5 (emulator-5554)"),
                ("127.0.0.1:62001", "Nox Player (127.0.0.1:62001)"),
            ])


@unittest.skipUnless(os.environ.get("GAMEBOT_GUI_TEST") == "1", "requires a desktop session")
class WindowInteractionTest(unittest.TestCase):
    @staticmethod
    def settle_resize(root):
        # Win32 delivers native resize messages via the event queue; Cocoa can
        # service live resize via idle events alone.
        if gui.sys.platform == "win32":
            root.update()
        else:
            root.update_idletasks()

    def test_error_moves_normal_log_and_grows_window_without_shrinking_it(self):
        for geometry, compact in (("1040x650", False), ("560x560", False),
                                   ("1300x650", False), ("1300x800", False),
                                   ("1300x650", True)):
            with self.subTest(geometry=geometry, compact=compact):
                root = gui.tk.Tk()
                try:
                    with patch.object(gui.BotWindow, "refresh_devices"):
                        window = gui.BotWindow(root)
                        root.geometry(geometry)
                        if compact:
                            window.cards.grid_remove()
                        window.append_log("普通日志")
                        root.update()
                    old_height = window.log.winfo_height()
                    old_y = window.log.master.winfo_y()
                    old_size = (root.winfo_width(), root.winfo_height())
                    old_panel_height = window.log_panel.winfo_height()
                    max_height = root.maxsize()[1]
                    window.append_log("⚠️ 异常信息")
                    root.update()
                    extra_height = window.error_area.winfo_height() + 14
                    expected_height = min(old_size[1] + extra_height, max_height)
                    self.assertEqual(window.log.winfo_height(), old_height)
                    self.assertEqual(window.log.master.winfo_y(), old_y + extra_height)
                    self.assertEqual(window.log_panel.winfo_height(), old_panel_height + extra_height)
                    self.assertEqual((root.winfo_width(), root.winfo_height()),
                                     (old_size[0], expected_height))
                    for _ in range(20):
                        window.append_log("⚠️ 后续异常")
                    root.update()
                    self.assertEqual(window.log.winfo_height(), old_height)
                    self.assertEqual(root.winfo_height(), expected_height)
                finally:
                    root.destroy()

    def test_error_before_first_layout_preserves_normal_log_height(self):
        root = gui.tk.Tk()
        try:
            with patch.object(gui.BotWindow, "refresh_devices"):
                window = gui.BotWindow(root)
                window.append_log("普通日志")
                for _ in range(20):
                    window.append_log("⚠️ 异常信息")
                root.update()
            for geometry in ("1040x800", "560x560", "1300x850"):
                root.geometry(geometry)
                self.settle_resize(root)
                self.assertTrue(window.log.winfo_ismapped())
                self.assertTrue(window.error_area.winfo_ismapped())
                self.assertGreaterEqual(window.log.winfo_height(), window.log.winfo_reqheight())
                self.assertGreaterEqual(window.error_log.winfo_height(), window.error_log.winfo_reqheight())
        finally:
            root.destroy()

    def test_wilderness_controls(self):
        root = gui.tk.Tk()
        try:
            with patch.object(gui.BotWindow, "refresh_devices"):
                window = gui.BotWindow(root)
                root.update()
            wilderness_card = window.task_cards[3]
            self.assertEqual((int(wilderness_card.grid_info()["row"]),
                              int(wilderness_card.grid_info()["column"])), (1, 1))
            self.assertTrue(any(name == "ui/intelligence.png" for _, name, _ in root._native_asset_labels))
            self.assertEqual(window.stamina_entry.get(), "0")
            for change, expected in ((-1, "0"), (1, "1"), (-1, "0")):
                window.stamina_entry.step_value(change)
                self.assertEqual(window.stamina_entry.get(), expected)
            window.scroll_to("moveto", 1)
            root.update()
            icon = window.stamina_entry.info_icon
            text = icon.master.grid_slaves(row=0, column=0)[0]
            height = tkfont.Font(root=root, font=text.cget("font")).metrics("linespace")
            self.assertIn((icon, "ui/info.png", height), root._native_asset_labels)
            icon.event_generate("<Enter>")
            root.update()
            self.assertEqual(icon.tooltip.winfo_children()[0].cget("text"),
                             "值为0时不限制，非0时判断体力大于该值时才执行操作")
            icon.event_generate("<Leave>")
            self.assertIsNone(icon.tooltip)
            window.switches["bounty"].toggle()
            self.assertTrue(window.options["bounty"].get())
            window.switches["intelligence"].toggle()
            self.assertFalse(window.switches["bounty"].enabled)
            self.assertEqual(window.stamina_entry.entry.cget("state"), "disabled")
            window.switches["bounty"].toggle()
            self.assertTrue(window.options["bounty"].get())
            window.switches["intelligence"].toggle()
            self.assertTrue(window.switches["bounty"].enabled)
            self.assertEqual(window.stamina_entry.entry.cget("state"), "normal")
            for index, (name, label) in enumerate(gui.RESOURCES):
                check = window.resource_checks[name]
                self.assertEqual((int(check.grid_info()["row"]),
                                  int(check.grid_info()["column"])), divmod(index, 2))
                self.assertEqual(check.cget("text"), label)
                with patch.object(gui.messagebox, "showwarning") as warning:
                    check.invoke()
                self.assertEqual(window.options[name].get(), index == len(gui.RESOURCES) - 1)
                self.assertEqual(warning.call_count, int(index == len(gui.RESOURCES) - 1))
            window.switches["gather"].toggle()
            for name, check in window.resource_checks.items():
                self.assertEqual(check.cget("state"), "disabled")
                check.invoke()
                self.assertEqual(window.options[name].get(), name == "iron")
            window.switches["gather"].toggle()
            window.set_running(True)
            for name in ("intelligence", "bounty", "gather"):
                self.assertFalse(window.switches[name].enabled)
            self.assertFalse(window.stamina_entry.enabled)
            self.assertTrue(all(check.cget("state") == "disabled"
                                for check in window.resource_checks.values()))
            window.set_running(False)
            self.assertTrue(window.stamina_entry.enabled)
            self.assertTrue(all(check.cget("state") == "normal"
                                for check in window.resource_checks.values()))
        finally:
            root.destroy()

    def test_controls_layout_and_log_view(self):
        root = gui.tk.Tk()
        try:
            with patch.object(gui.BotWindow, "refresh_devices"):
                window = gui.BotWindow(root)
                root.update()
            self.assertEqual((root.winfo_width(), root.winfo_height()), (1040, 800))
            self.assertEqual(window._layout_columns, 2)
            self.assertEqual([int(card.grid_info()["column"]) for card in window.task_cards], [0, 1, 0, 1])
            daily_card, training_card, _, wilderness_card = window.task_cards
            self.assertEqual(int(wilderness_card.grid_info()["row"]), 1)
            self.assertGreaterEqual(wilderness_card.winfo_y(),
                                    daily_card.winfo_y() + daily_card.winfo_height() + 10)
            for name in ("donate", "recruit", "treasure"):
                self.assertIs(window.switches[name].master.master.master, daily_card.body)
            self.assertGreater(daily_card.winfo_height(), training_card.winfo_height())
            self.assertEqual(bool(window.scrollbar.winfo_ismapped()),
                             window.content.winfo_height() > window.viewport.winfo_height() + 2)
            left = window.device_panel.winfo_rootx() - root.winfo_rootx()
            right = root.winfo_rootx() + root.winfo_width() - (
                window.device_panel.winfo_rootx() + window.device_panel.winfo_width())
            self.assertEqual(left, right)
            self.assertEqual(left, 22)
            self.assertEqual(window.subtitle.winfo_height(), window.subtitle.winfo_reqheight())
            self.assertLessEqual(window.subtitle.winfo_rooty() + window.subtitle.winfo_height(),
                                 window.header.winfo_rooty() + window.header.winfo_height())
            self.assertEqual(tuple(root.resizable()), (1, 1))
            self.assertFalse(window.log.winfo_ismapped())
            self.assertTrue(window.log_placeholder.winfo_ismapped())
            self.assertFalse(window.error_area.winfo_ismapped())
            self.assertEqual(window.log.master.winfo_width(), window.log_panel.body.winfo_width())
            for icon, name, size in root._native_asset_labels:
                if name != "ui/info.png":
                    continue
                text = icon.master.grid_slaves(row=0, column=0)[0]
                height = tkfont.Font(root=root, font=text.cget("font")).metrics("linespace")
                self.assertEqual(size, height)
                self.assertEqual((icon.winfo_width(), icon.winfo_height()), (height, height))
                self.assertEqual(icon.winfo_x() - text.winfo_x() - text.winfo_width(), 2)
                # Chinese glyphs sit below the center of Tk's font line box.
                self.assertLessEqual(abs(icon.winfo_y() + icon.winfo_height() / 2 -
                                         text.winfo_y() - text.winfo_height() / 2 - 2), 0.5)
            for switch in window.switches.values():
                self.assertEqual((switch.winfo_width(), switch.winfo_height()), (42, 24))
                self.assertLessEqual(switch.winfo_y() + switch.winfo_height(),
                                     switch.master.winfo_height() - 1)
            group = window.device_status.master
            self.assertLessEqual(group.winfo_reqheight(), group.master.winfo_height())

            picker = window.device_box
            picker.set_values(["Emulator A", "Emulator B"])
            picker.set_enabled(True)
            window.device.set("Emulator A")
            picker.open_menu()
            root.update()
            self.assertEqual(tuple(picker.combo.cget("values")), ("Emulator A", "Emulator B"))
            self.assertEqual(str(picker.combo.cget("state")), "readonly")
            picker.combo.current(1)
            self.assertEqual(window.device.get(), "Emulator B")
            picker.set_enabled(False)
            self.assertEqual(str(picker.combo.cget("state")), "disabled")

            window.device_lookup = {"Emulator B": "emulator-test"}
            self.assertFalse(window.options["upgrade"].get())
            self.assertNotIn("--upgrade", window.command())
            window.switches["upgrade"].toggle()
            self.assertIn("--upgrade", window.command())
            window.options["train"].set(False)
            window.training_changed()
            self.assertFalse(window.switches["upgrade"].enabled)
            self.assertNotIn("--upgrade", window.command())
            window.options["train"].set(True)
            window.training_changed()
            window.upgrade_hint.event_generate("<Enter>")
            root.update()
            self.assertEqual(window.upgrade_hint.tooltip.winfo_children()[0].cget("text"),
                             "开启后不再训练新兵而是将低级兵种升到当前最高级")
            window.upgrade_hint.event_generate("<Leave>")
            self.assertIsNone(window.upgrade_hint.tooltip)
            self.assertIn("--intelligence", window.command())
            threshold_index = window.command().index("--stamina-threshold")
            self.assertEqual(window.command()[threshold_index + 1], "0")
            self.assertIn("--gather", window.command())
            resources_index = window.command().index("--resources")
            self.assertEqual(window.command()[resources_index + 1:resources_index + 5],
                             [name for name, _ in gui.RESOURCES])
            window.options["wood"].set(False)
            self.assertNotIn("wood", window.command())
            window.options["gather"].set(False)
            self.assertNotIn("--gather", window.command())
            self.assertNotIn("--resources", window.command())
            window.options["gather"].set(True)
            self.assertNotIn("--bounty", window.command())
            window.options["bounty"].set(True)
            self.assertIn("--bounty", window.command())
            window.stamina_entry.value.set("100")
            self.assertIn("100", window.command())
            window.stamina_entry.value.set("-1")
            with self.assertRaisesRegex(ValueError, "体力触发阈值"):
                window.command()
            window.options["intelligence"].set(False)
            self.assertNotIn("--intelligence", window.command())
            self.assertNotIn("--bounty", window.command())
            window.options["intelligence"].set(True)
            window.options["bounty"].set(False)
            window.stamina_entry.value.set("0")
            self.assertTrue(window.options["treasure"].get())
            self.assertNotIn("--no-treasure", window.command())
            window.switches["treasure"].toggle()
            self.assertIn("--no-treasure", window.command())
            self.assertNotIn("--no-pet", window.command())
            window.switches["pet"].toggle()
            self.assertIn("--no-pet", window.command())
            window.set_running(True)
            window.switches["pet"].toggle()
            self.assertFalse(window.options["pet"].get())
            window.set_running(False)
            window.switches["pet"].toggle()
            self.assertTrue(window.options["pet"].get())

            window.threshold_entry.value.set("1")
            window.threshold_entry.step_value(-1)
            self.assertEqual(window.threshold_entry.get(), "1")
            window.reconnect_entry.value.set("1.5")
            window.reconnect_entry.step_value(1)
            self.assertEqual(window.reconnect_entry.get(), "2.5")
            first_line = gui.timestamp_line("First log line")
            window.append_log(first_line)
            root.update()
            self.assertFalse(window.log_placeholder.winfo_ismapped())
            self.assertTrue(window.log.winfo_ismapped())
            self.assertEqual(window.log.get("1.0", "end-1c"), first_line + "\n")
            self.assertFalse(window.error_area.winfo_ismapped())
            window.append_log("⚠️ 当前已出现三个任务连续执行异常因此自动中断脚本")
            root.update()
            self.assertEqual(window.log.tag_cget("task_disabled", "foreground"), gui.RED)
            self.assertIn("task_disabled", window.log.tag_names("2.0"))
            self.assertNotIn("task_disabled", window.log.tag_names("1.0"))
            self.assertNotIn("First log line", window.error_log.get("1.0", "end"))
            self.assertIn("⚠️", window.error_log.get("1.0", "end"))
            self.assertEqual(window.error_log.cget("fg"), gui.RED)
            self.assertTrue(window.error_area.winfo_ismapped())
            self.assertEqual(window.log.master.winfo_width(), window.log_panel.body.winfo_width())
            self.assertEqual(window.log.master.winfo_width(), window.error_area.winfo_width())
            self.assertGreaterEqual(window.error_area.winfo_y(),
                                    window.log_heading.winfo_y() + window.log_heading.winfo_height() + 10)
            self.assertGreaterEqual(window.log.master.winfo_y(),
                                    window.error_area.winfo_y() + window.error_area.winfo_height() + 10)
            for label, view, _size in window.native_images:
                if not label.winfo_ismapped():
                    self.assertTrue(view.isHidden())
            window.log.tag_add("sel", "1.0", "1.end")
            self.assertEqual(window.log.get("sel.first", "sel.last"), first_line)
            root.geometry("940x560")
            root.update()
            self.assertEqual((window.switches["help"].winfo_width(),
                              window.switches["help"].winfo_height()), (42, 24))
            self.assertEqual(window.logo_label.winfo_width(), 46)
            self.assertEqual(tkfont.Font(root=root, font=window.device_status.cget("font")).actual()["size"], 10)
            if not window.scrollbar.winfo_ismapped():
                self.assertLessEqual(window.log_placeholder.winfo_rooty() +
                                     window.log_placeholder.winfo_height(),
                                     root.winfo_rooty() + root.winfo_height() + 2)
            self.assertTrue(window.device_box.winfo_ismapped())
            root.geometry("1080x700")
            root.update()
            self.assertEqual((window.switches["help"].winfo_width(),
                              window.switches["help"].winfo_height()), (42, 24))
            for geometry in ("560x560", "800x650", "1080x700", "1300x850", "560x560"):
                root.geometry(geometry)
                root.update()
                columns = 3 if root.winfo_width() >= 1200 else 2 if root.winfo_width() >= 740 else 1
                self.assertEqual(window._layout_columns, columns)
                self.assertEqual(window.content.winfo_width(), window.viewport.winfo_width())
                for index, card in enumerate(window.task_cards):
                    self.assertEqual(int(card.grid_info()["row"]), index // columns)
                    self.assertEqual(int(card.grid_info()["column"]), index % columns)
                    self.assertEqual(card.winfo_height(), card.winfo_reqheight())
                    self.assertGreaterEqual(card.body.winfo_height(), card.body.winfo_reqheight())
                    for child in card.body.winfo_children():
                        self.assertLessEqual(child.winfo_y() + child.winfo_height(), card.body.winfo_height())
                def bounds(widget):
                    return (widget.winfo_rootx(), widget.winfo_rooty(),
                            widget.winfo_rootx() + widget.winfo_width(),
                            widget.winfo_rooty() + widget.winfo_height())
                a, b = bounds(window.start_button), bounds(window.refresh_button)
                self.assertTrue(a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])
                self.assertLessEqual(b[2], root.winfo_rootx() + root.winfo_width())
                self.assertEqual(window.log.cget("wrap"), "char")
                self.assertEqual(window.log.master.winfo_width(), window.log_panel.body.winfo_width())
                self.assertEqual(window.error_area.winfo_width(), window.log.master.winfo_width())
                for label, view, _size in window.native_images:
                    frame = view.frame()
                    parent = view.superview()
                    self.assertEqual(frame.origin.x, label.winfo_rootx() - root.winfo_rootx())
                    self.assertEqual(frame.size.width, label.winfo_width())
                    self.assertEqual(frame.size.height, label.winfo_height())
                    y = label.winfo_rooty() - root.winfo_rooty()
                    expected_y = y if parent.isFlipped() else parent.bounds().size.height - y - label.winfo_height()
                    self.assertEqual(frame.origin.y, expected_y)
            # Only service geometry/idle events: no timer or manual layout call.
            root.geometry("1300x850")
            self.settle_resize(root)
            wide_height = window.log_panel.winfo_reqheight()
            window.log.tag_remove("sel", "1.0", "end")
            window.append_log("日志内容自动换行。" * 35)
            for _ in range(100):
                window.append_log("持续输出日志")
            root.update_idletasks()
            self.assertEqual(window.log_panel.winfo_reqheight(), wide_height)
            self.assertGreater(window.log.yview()[0], 0)
            self.assertEqual(window.log.yview()[1], 1)
            # Drag beyond either edge, including repeated timer ticks with no motion.
            for text in (window.log, window.error_log):
                text.configure(state="normal")
                text.insert("end", "选中行\n" * 100)
                text.configure(state="disabled")
                for start, outside, direction in ((0, text.winfo_height() + 40, 1),
                                                   (1, -40, -1)):
                    text.yview_moveto(start)
                    root.update()
                    text.event_generate("<Button-1>", x=30, y=text.winfo_height() // 2)
                    before = text.yview()[0]
                    text.event_generate("<B1-Motion>", x=30, y=outside)
                    root.after(180, root.quit)
                    root.mainloop()
                    text.event_generate("<ButtonRelease-1>", x=30, y=outside)
                    self.assertGreater((text.yview()[0] - before) * direction, 0)
                    self.assertTrue(text.tag_ranges("sel"))
                selected_top = text.index("@0,0")
                window.append_log("⚠️ 选中文本期间的新日志")
                self.assertEqual(text.index("@0,0"), selected_top)
            root.geometry("560x560")
            self.settle_resize(root)
            self.assertEqual(window._layout_columns, 1)
            self.assertEqual(window.log_panel.winfo_reqheight(), wide_height)
            self.assertTrue(window.scrollbar.winfo_ismapped())
            window.scroll_to("moveto", 1)
            root.update_idletasks()
            self.assertLessEqual(window.log_panel.winfo_rooty() + window.log_panel.winfo_height(),
                                 window.viewport.winfo_rooty() + window.viewport.winfo_height())
            self.assertGreater(window.log.winfo_height(), 0)
            root.geometry("1300x850")
            self.settle_resize(root)
            self.assertEqual(window._layout_columns, 3)
            self.assertEqual(window.log_panel.winfo_reqheight(), wide_height)
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main()

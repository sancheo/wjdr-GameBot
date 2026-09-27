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
        window.position_native_images = Mock()
        window.append_log("普通日志")
        self.assertEqual(window.log.insert.call_args.args[2], ())
        window.append_log("⚠️ 当前已出现三个任务连续执行异常因此自动中断脚本")
        self.assertEqual(window.log.insert.call_args.args[2], ("task_disabled",))
        window.append_log("⚠️ 生命结晶收集 已连续执行失败3次，后续不再执行这个任务")
        self.assertEqual(window.log.insert.call_args.args[2], ("task_disabled",))

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
    def test_controls_layout_and_log_view(self):
        root = gui.tk.Tk()
        try:
            with patch.object(gui.BotWindow, "refresh_devices"):
                window = gui.BotWindow(root)
                root.update()
            self.assertEqual((root.winfo_width(), root.winfo_height()), (1040, 800))
            self.assertEqual(window._layout_columns, 3)
            self.assertEqual([int(card.grid_info()["column"]) for card in window.task_cards], [0, 1, 2])
            self.assertFalse(window.scrollbar.winfo_ismapped())
            self.assertEqual(tuple(root.resizable()), (1, 1))
            self.assertFalse(window.log.winfo_ismapped())
            self.assertTrue(window.log_placeholder.winfo_ismapped())
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
            self.assertIsNotNone(picker.popup)
            picker.choices.selection_clear(0, "end")
            picker.choices.selection_set(1)
            picker.choose()
            self.assertEqual(window.device.get(), "Emulator B")
            self.assertIsNone(picker.popup)
            picker.open_menu()
            picker.set_enabled(False)
            self.assertIsNone(picker.popup)

            window.device_lookup = {"Emulator B": "emulator-test"}
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
            window.append_log("⚠️ 当前已出现三个任务连续执行异常因此自动中断脚本")
            self.assertEqual(window.log.tag_cget("task_disabled", "foreground"), gui.RED)
            self.assertIn("task_disabled", window.log.tag_names("2.0"))
            self.assertNotIn("task_disabled", window.log.tag_names("1.0"))
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
                columns = 3 if root.winfo_width() >= 1040 else 2 if root.winfo_width() >= 740 else 1
                self.assertEqual(window._layout_columns, columns)
                for index, card in enumerate(window.task_cards):
                    self.assertEqual(int(card.grid_info()["row"]), index // columns)
                    self.assertEqual(int(card.grid_info()["column"]), index % columns)
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
            root.update_idletasks()
            wide_height = window.log_panel.winfo_reqheight()
            window.append_log("日志内容自动换行。" * 35)
            for _ in range(100):
                window.append_log("持续输出日志")
            root.update_idletasks()
            self.assertEqual(window.log_panel.winfo_reqheight(), wide_height)
            self.assertGreater(window.log.yview()[0], 0)
            self.assertEqual(window.log.yview()[1], 1)
            root.geometry("560x560")
            root.update_idletasks()
            self.assertEqual(window._layout_columns, 1)
            self.assertEqual(window.log_panel.winfo_reqheight(), wide_height)
            self.assertTrue(window.scrollbar.winfo_ismapped())
            window.scroll_to("moveto", 1)
            root.update_idletasks()
            self.assertLessEqual(window.log_panel.winfo_rooty() + window.log_panel.winfo_height(),
                                 window.viewport.winfo_rooty() + window.viewport.winfo_height())
            self.assertGreater(window.log.winfo_height(), 0)
            root.geometry("1300x850")
            root.update_idletasks()
            self.assertEqual(window._layout_columns, 3)
            self.assertEqual(window.log_panel.winfo_reqheight(), wide_height)
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main()

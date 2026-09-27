# Windows 开发接续说明

本压缩包保留了 Mac 和 Windows 共用的源码、素材、测试截图，以及现有的 macOS Android Platform Tools。当前项目只在 macOS 上验证，Windows 移植尚未完成。

1. 在 Windows 安装 Python 3（含 Tkinter）、Pillow，以及 Android 模拟器。模拟器需保持游戏在前台，分辨率为 1080×2340。
2. 从 Android 官方下载 Windows 版 Platform Tools，将 `adb.exe` 等 Windows 文件放在 `.tools/platform-tools/`，保留已有的 Mac 文件。不要将 Mac 的 `adb` 改名为 `adb.exe`。
3. 修改 `gui.py` 和 `help_bot.py` 中的 `ADB` 路径，使 Windows 使用 `adb.exe`，macOS 继续使用 `adb`。
4. `help_bot.py` 的 `recognize_text()` 使用 macOS Vision。Windows 需要替换或增加 OCR 实现，保持返回格式为 `[(文字, (x, y, 宽, 高)), ...]`，并验证中文、英文和倒计时识别。
5. 在 Windows 上先检查 `adb devices`，再运行 `python gui.py` 或 `python help_bot.py --device <序列号> --duration 60 --dry-run`。完成 OCR 移植后，再运行日常任务与测试。

`artifacts/` 是测试使用的真实模拟器截图，也可能包含角色或聊天信息。分享压缩包前请自行核对。

# Windows 开发补充

便携版安装、模拟器配置和首次启动见 [README 的安装与使用](README.md#安装与使用)，版本发布见 [README 的发布说明](README.md#发布新版本维护者)。本文保留 Windows 平台的工具查找、编译环境与实现约定。开发验证使用项目 `.venv` 运行 `python run_tests.py --suite fast`；完整离线回归使用 `--suite all`，固定截图样本见 `tests/fixtures/README.md`。

## ADB 查找与配置

`platform_support.py` 按以下顺序选择 Windows ADB：

1. 环境变量 `GAMEBOT_ADB` 指定的可执行文件。
2. 项目 `.tools/platform-tools/adb.exe`。
3. `ANDROID_HOME` 下的 `platform-tools/adb.exe`，然后是 `ANDROID_SDK_ROOT` 下的同名文件。
4. Windows 默认 SDK 路径 `%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe`。
5. 系统 `PATH` 中的 `adb.exe`。

`GAMEBOT_ADB` 设置后直接使用指定路径；路径无效时不会继续查找其他位置。其余候选路径只在文件存在时选用。全部找不到时返回项目工具目录的路径，由后续调用报告错误。

在 PowerShell 中临时指定 ADB：

```powershell
$env:GAMEBOT_ADB = 'D:\Android SDK\platform-tools\adb.exe'
```

Windows 版 `adb.exe` 和相关 DLL 未纳入 Git。可使用 Android Studio 的 SDK，或下载 [Android 官方 Platform Tools](https://developer.android.com/tools/releases/platform-tools)。放入项目工具目录时，`adb.exe`、`AdbWinApi.dll`、`AdbWinUsbApi.dll` 必须来自同一份 Windows Platform Tools。

## 桌面程序与编译环境

Windows 界面由 `desktop/GameBot.cs` 编译的 EXE 承载 WebView2，页面资源位于 `assets/desktop.html` 和 `assets/desktop/`，Python 后台位于 `gui_web.py`。源码开发使用项目 `.venv`；便携发行版使用 `runtime/python` 内置的独立 Python、`runtime/webview-sdk` 和 `runtime/webview2`，无需用户安装 Python 或 WebView2。EXE 应保留在完整程序目录中。便携包构建命令见 [README 的发布说明](README.md#发布新版本维护者)，使用方法见 [安装与使用](README.md#安装与使用)。

`build_desktop.ps1` 使用 Windows 自带的 .NET Framework x64 C# 编译器，WebView2 SDK DLL 来自 `pywebview` 的 `webview/lib` 目录。编译无需安装 Visual Studio、Rust 或 Node。运行需要 [Microsoft Edge WebView2 Runtime](https://developer.microsoft.com/en-us/microsoft-edge/webview2/)。

修改 `desktop/GameBot.cs` 后，在项目目录重新编译：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\build_desktop.ps1
```

若 `GameBot.exe` 正在运行、无法覆盖，编译脚本会生成 `GameBot.next.exe`。`start_windows.cmd` 调用 `launch_desktop.ps1`，在 EXE 缺失或 C# 源码更新时自动编译，并选择 `GameBot.exe` 与 `GameBot.next.exe` 中较新的版本启动。更新后先停止旧窗口中的任务，再通过启动脚本打开新窗口。

修改 HTML、CSS、JS 或 Python 脚本后，停止任务并重新打开桌面程序即可。依赖变更时按 README 重新安装 `requirements.txt`。

## OCR 实现约定

`ocr_backend.py` 提供统一的 `recognize_text(image)` 接口，返回 `[(文字, (x, y, 宽, 高)), ...]`，坐标以传入图片左上角为原点。

Windows 使用固定版本 `rapidocr-onnxruntime==1.4.4`，模型随依赖安装，在本机 CPU 上识别。引擎首次使用时加载并缓存复用，截图不上传。macOS 使用 Vision。

Windows 识别流程将图片转换为 RGB、宽高各放大两倍，再以 PNG 字节送入引擎；输出坐标还原到原图，并裁剪到图片范围。`merge_text_lines` 合并同一行的相邻文字框，以兼容“1天 + 时分秒”“冷却中 + 倒计时”等拆分结果。修改 OCR 时需保留上述返回格式与坐标约定，避免影响倒计时解析和目标定位。

## 主要模块职责

| 文件 | 职责 |
| --- | --- |
| `platform_support.py` | ADB 路径查找、Windows 子进程处理与 UTF-8 输出配置。 |
| `gui_web.py` | 桌面界面的 Python 后台接口与任务进程管理。 |
| `app_updates.py`、`app_version.py` | GitHub 正式版本检查、下载校验、更新准备和版本标识。 |
| `desktop/Updater.cs` | 独立更新器，替换程序与运行环境，执行启动检查并在失败时回滚。 |
| `task_scheduler.py` | 日常任务调度。 |
| `runtime_control.py` | 停止及单项重试协议。 |
| `device_lease.py` | 系统级模拟器互斥，防止多个任务进程同时控制同一模拟器。 |
| `task_status.py` | 任务状态消息校验。 |
| `image_matching.py` | 模板缓存、平均绝对差和 Dice 匹配；保持既有阈值与同分时的坐标顺序。 |
| `help_bot.py` | 任务入口与兼容接口。 |
| `ocr_backend.py` | Windows RapidOCR 与 macOS Vision 的统一识别接口。 |

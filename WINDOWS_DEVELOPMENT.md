# Windows 安装与开发

项目支持 Windows 和 macOS，共用任务逻辑、素材与截图测试。Windows 使用 `adb.exe` 和本地 RapidOCR；macOS 继续使用 `adb` 和 Vision。模拟器需保持游戏在前台，分辨率为 **1080×2340**，游戏界面布局需与现有模板一致。

第一次安装请优先按照 [README.md](README.md) 中“安装与环境配置”的 7 个步骤操作。本文补充 Windows 平台的启动、OCR 实现和开发验证信息。

## 安装

建议使用 64 位 Python 3.12（包含 Tcl/Tk 和 pip）。在项目目录打开 PowerShell：

```powershell
py -3.12 --version
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

如果 `py` 不可用，用已安装的 `python` 或 Python 完整路径代替。`.venv` 只适用于创建它的电脑和项目路径；移动项目或换电脑后需要删除旧环境并重新创建，不能复制复用。

ADB 按下列顺序查找，无需修改源码：

1. 环境变量 `GAMEBOT_ADB` 指定的可执行文件。
2. 项目 `.tools/platform-tools/adb.exe`（macOS 为 `adb`）。
3. `ANDROID_HOME` 或 `ANDROID_SDK_ROOT` 下的 `platform-tools`。
4. Windows 默认 SDK 路径 `%LOCALAPPDATA%\Android\Sdk\platform-tools`。
5. 系统 `PATH`。

Windows 版 `adb.exe` 和相关 DLL 未纳入 Git。新机器可复用 Android Studio 的 SDK，或从 [Android 官方 Platform Tools](https://developer.android.com/tools/releases/platform-tools) 下载。若放入项目工具目录，`adb.exe`、`AdbWinApi.dll`、`AdbWinUsbApi.dll` 必须来自同一份 Windows Platform Tools；不要把 macOS 的 `adb` 改名为 `adb.exe`。

自定义 ADB 示例：

```powershell
$env:GAMEBOT_ADB = 'D:\Android SDK\platform-tools\adb.exe'
```

## 启动

先启动模拟器和游戏，并确认游戏停留在主城。然后双击项目目录中的 `start_windows.cmd`，或运行：

```powershell
.\.venv\Scripts\python.exe gui.py
```

GUI 会列出已连接模拟器。序列号可能随模拟器重启变化，请点击“刷新”。如需在命令行查询与验证：

```powershell
.\.venv\Scripts\python.exe -c "from gui import discover_emulators; print(discover_emulators())"
.\.venv\Scripts\python.exe help_bot.py --self-test
.\.venv\Scripts\python.exe help_bot.py --duration 60 --dry-run
```

只有一台已连接模拟器时无需指定序列号；多台时添加 `--device emulator-5556`（替换为实际序列号）。只观察模式不操作页面，需要手动停留在主城；普通运行会执行已有的主城恢复逻辑。

需要持续运行日常任务时，在 GUI 中选择任务后点击“启动”，或运行：

```powershell
.\.venv\Scripts\python.exe help_bot.py --daily-tasks --forever --reconnect-minutes 1
```

点击“停止”或在命令行按 Ctrl-C 结束。ADB 与后台任务在 Windows 隐藏控制台窗口，GUI 与子进程统一使用 UTF-8，支持中文和警告符号。

## OCR 与验证

`ocr_backend.py` 保持 `[(文字, (x, y, 宽, 高)), ...]` 返回格式，坐标相对于传入图片左上角。Windows 使用固定版本 `rapidocr-onnxruntime==1.4.4`，模型随依赖安装，识别时在本机 CPU 运行，不上传截图。引擎首次使用时加载并复用。输入放大两倍后识别，输出坐标还原，并合并同一行的相邻文字框，以兼容“1天 + 时分秒”“冷却中 + 倒计时”等拆行结果。参考 [RapidOCR API 文档](https://rapidai.github.io/RapidOCRDocs/v1.4.4/install_usage/api/RapidOCR/)。

```powershell
.\.venv\Scripts\python.exe -X utf8 -m unittest test_help_bot test_schedule test_platform_support -v
$env:GAMEBOT_GUI_TEST = '1'
.\.venv\Scripts\python.exe -X utf8 -m unittest test_gui -v
```

2026-09-29 本机验证：Python 3.12.14、Pillow 11.3.0、RapidOCR 1.4.4；94 项任务/调度/平台测试通过。此前的 3 项 GUI 测试也已通过。测试覆盖真实截图中的中文、跨天倒计时、宠物冷却、建筑升级，以及英文 OCR、坐标转换、ADB 查找和 UTF-8 日志。`--self-test` 和已连接模拟器的 60 秒只观察运行通过。实时打开、滚动和收起任务抽屉，识别到兵种状态、联盟捐献、免费招募与跨天倒计时，并确认返回主城；此次实时检查没有领取或消耗资源。macOS 分支保留原实现，本次未在 macOS 实机重跑；长期自动日常任务仍需实际运行观察。

`artifacts/` 是测试使用的真实模拟器截图，也可能包含角色或聊天信息。分享压缩包前请自行核对。

# 无尽冬日GameBot

目前实现《无尽冬日》安卓模拟器上的**联盟互助、探险经验领取、士兵训练、联盟科技捐献、免费英雄招募、仓库补给、生命之树、晨曦回礼和宠物技能**，
并支持**强制下线后重连**。支持 macOS 和 Windows，通过 ADB 连接本机 Android 模拟器。

## 使用前须知

本项目通过 ADB 截图、识别画面并向模拟器发送点击操作。请先确认以下条件：

- 运行电脑为 Windows 或 macOS；Linux 暂无 OCR 实现。
- 使用的是 Android **模拟器**，不是实体手机。
- 模拟器选择 Pixel 5 ，游戏通过taptap下载。
- 《无尽冬日》已进入主城，游戏在模拟器内保持前台；电脑、模拟器和游戏在任务期间不能休眠或退出。
- 第一次使用必须先执行只观察模式，确认识别正常后再允许程序点击。

程序会在点击前核对主城、页面标题和目标按钮；无法确认时会停止或重试。

## 需要安装的软件

推荐新手只安装下面三项：

| 要安装的内容 | 它的作用 | 从哪里获得 |
| --- | --- | --- |
| Python 3.12（64 位） | 运行 GameBot | [Python 官网](https://www.python.org/downloads/) |
| Android Studio | 安装和管理安卓模拟器，同时提供程序连接模拟器所需的 ADB 工具 | [Android Studio 官网](https://developer.android.com/studio) |
| 《无尽冬日》国服 | GameBot 要操作的游戏 | 安装到 Android Studio 创建的模拟器中 |

### Android 模拟器和 Platform Tools 要不要单独安装？

一般**不需要另外下载安装包**。安装 Android Studio 后，可以在 Android Studio 里面安装这两个组件：

- **Android Emulator**：安卓模拟器本体，用来运行游戏。
- **Android SDK Platform-Tools**：包含 `adb`。GameBot 依靠 `adb` 获取游戏画面并执行点击。

它们是 Android Studio 管理的组件，并不代表装完 Android Studio 就一定已经下载完成。请按照下方第 2 步检查这两个组件是否已勾选并安装。

### “项目依赖”是什么？需要自己寻找吗？

不需要。项目依赖不是另一个需要手工寻找的软件，而是 GameBot 使用的 Python 功能包：

- Pillow：读取和比较游戏截图。
- RapidOCR：Windows 上识别截图中的中文和倒计时。
- PyObjC：macOS 上调用系统自带的文字识别功能。

这些内容已经写在项目的 `requirements.txt` 文件中。后面只需要复制执行一条安装命令，系统会自动选择并下载当前电脑需要的内容。第一次安装依赖时需要联网，游戏截图只在本机处理，不会上传。

Git 不是必需软件。如果拿到的是项目文件夹或压缩包，不用安装 Git。

## 安装与环境配置

请按照下面的顺序操作。不要跳过“只观察模式”，它用于确认程序不会点错位置。

### 第 1 步：安装 Python 3.12

Windows：

1. 打开 [Python 官网](https://www.python.org/downloads/)，下载 Windows 版 64 位 Python 3.12。
2. 打开安装程序。
3. 在安装界面底部勾选 **Add python.exe to PATH**。
4. 点击 **Install Now**。默认安装会同时安装本项目需要的 pip 和 Tcl/Tk。
5. 安装完成后关闭原有 PowerShell 窗口，后面重新打开一个。

macOS：

1. 打开 [Python 官网](https://www.python.org/downloads/)，下载并安装 Python 3.12 的 macOS 安装包。
2. 使用默认选项完成安装。官网安装包包含 pip 和图形界面所需的 Tk。

如果电脑已经安装 Python 3.12，可以直接进入下一步。

### 第 2 步：安装 Android Studio、模拟器和 ADB

1. 从 [Android Studio 官网](https://developer.android.com/studio) 下载并安装 Android Studio。
2. 第一次启动时选择 **Standard（标准）**设置，让安装向导下载推荐的 Android SDK 组件。
3. 进入 Android Studio 欢迎页的 **More Actions → SDK Manager**。如果已经打开项目，则使用 **Tools → SDK Manager**。
4. 打开 **SDK Tools** 标签页。
5. 勾选 **Android SDK Platform-Tools** 和 **Android Emulator**。
6. 点击 **Apply**，等待下载和安装完成，再点击 **Finish**。

如果 Android Studio 提示需要开启虚拟化或安装模拟器加速组件，请按它给出的提示操作，然后重启电脑。没有完成虚拟化配置时，模拟器可能无法启动或运行很慢。

### 第 3 步：创建 Pixel 5 模拟器

1. 在 Android Studio 欢迎页打开 **More Actions → Virtual Device Manager**；如果已经打开项目，则选择 **Tools → Device Manager**。
2. 点击 **Create Device**。
3. 在 **Phone** 分类中选择 **Pixel 5**，然后点击 **Next**。
4. 选择 **API 34**的 Android 系统镜像。旁边有下载图标时，先点击下载，完成后再选择它。
5. 继续点击 **Next**。保持 Pixel 5 默认的竖屏 **1080×2340** 分辨率，不要修改成其他尺寸。
6. 点击 **Finish**，再点击设备右侧的启动按钮，等待安卓桌面完全出现。

Android Studio 创建的模拟器会自动连接 ADB，不需要再打开“USB 调试”。如果使用其他品牌的安卓模拟器，则需要自行开启它的“ADB 调试”或“本地调试”，并把分辨率调整为 1080×2340；对完全没有经验的用户，建议直接使用 Android Studio 模拟器。

### 第 4 步：在模拟器中准备游戏

1. 如果创建模拟器时选择了带 Google Play 的系统镜像，可以打开模拟器中的 Play 商店并按正常方式安装游戏。
2. 如果使用《无尽冬日》国服的官方 APK 安装包，可以把 `.apk` 文件直接拖入已经启动的模拟器窗口，等待安装完成。请只使用可信的官方来源。
3. 打开游戏，登录账号并进入主城。
4. 让游戏一直显示在模拟器最前面，不要切换到安卓桌面或其他应用。
5. 关闭电脑和模拟器的自动休眠。长时间运行时，电脑、模拟器和游戏都必须保持开启。

GameBot 不会自动安装模拟器、安装游戏或登录游戏账号。

### 第 5 步：打开项目目录中的命令窗口

如果拿到的是 `.zip` 压缩包，请先完整解压，不能直接在压缩包预览窗口里运行。解压后，找到同时包含 `gui.py`、`help_bot.py` 和 `requirements.txt` 的文件夹，这个文件夹就是“项目目录”。

Windows PowerShell：

1. 在文件资源管理器中打开项目目录。
2. 点击窗口顶部的地址栏，输入 `powershell`，按回车。
3. 系统会打开一个蓝色或黑色的 PowerShell 窗口。后面的 Windows 命令都在这里逐行输入，每输入一行就按一次回车。

macOS 终端：

1. 打开“终端”应用。
2. 输入 `cd` 和一个空格。
3. 把项目文件夹从 Finder 拖到终端窗口中，终端会自动填入路径。
4. 按回车。后面的 macOS 命令都在这个终端窗口中逐行输入。

不确定目录是否正确时，可以查看窗口当前目录中是否存在 `gui.py`、`help_bot.py` 和 `requirements.txt`。缺少其中任何一个文件都不要继续。

### 第 6 步：创建 GameBot 专用环境并安装项目依赖

“专用环境”可以理解为 GameBot 自己的 Python 工具箱，文件会保存在项目目录下的 `.venv` 文件夹中，不会干扰电脑里的其他程序。

Windows PowerShell：

```powershell
py -3.12 --version
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

第一行应该显示 `Python 3.12.x`。最后一行会自动安装 Pillow 和 Windows 需要的 RapidOCR，可能需要等待几分钟。看到 `Successfully installed` 或命令重新出现，且没有红色 `ERROR`，即表示安装完成。

如果提示找不到 `py`，请回到第 1 步重新安装 Python，并确认勾选 **Add python.exe to PATH**。无需执行“激活虚拟环境”之类的额外命令。

macOS 终端：

```sh
python3 --version
python3 -m venv .venv
./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/python -m pip install -r requirements.txt
```

第一行应该显示 `Python 3.12.x`。最后一行会自动安装 Pillow 和 macOS 需要的 PyObjC。安装成功的判断方法与 Windows 相同。

以后移动项目文件夹或换电脑时，需要在新位置重新执行本步骤，不能直接复制旧的 `.venv` 使用。

### 第 7 步：确认 GameBot 能找到模拟器

确认 Android Studio 的 Pixel 5 模拟器已经启动，并且游戏停留在主城，然后运行下面与自己系统对应的命令。

Windows PowerShell：

```powershell
.\.venv\Scripts\python.exe -c "from gui import discover_emulators; print(discover_emulators())"
```

macOS 终端：

```sh
./.venv/bin/python -c 'from gui import discover_emulators; print(discover_emulators())'
```

正常情况下会输出类似：

```text
[('emulator-5554', 'sdk_gphone64_x86_64 (emulator-5554)')]
```

括号中的名称可能不同，只要不是空列表 `[]` 就表示找到了模拟器。Windows 版程序会自动寻找 Android Studio 默认安装的 ADB；macOS 项目中也带有可用的 ADB。

如果输出 `[]`，先确认模拟器已经完全启动，再关闭并重新打开模拟器，然后重试。仍然找不到时查看下方“GUI 中没有模拟器”。

找到模拟器后，先执行项目自检：

Windows PowerShell：

```powershell
.\.venv\Scripts\python.exe help_bot.py --self-test
```

macOS 终端：

```sh
./.venv/bin/python help_bot.py --self-test
```

最后看到“主城和强制下线样本识别正确”表示自检通过。

接着执行 60 秒只观察模式。它只读取当前模拟器画面，不会点击游戏：

Windows PowerShell：

```powershell
.\.venv\Scripts\python.exe help_bot.py --duration 60 --dry-run
```

macOS 终端：

```sh
./.venv/bin/python help_bot.py --duration 60 --dry-run
```

只观察模式识别到联盟互助按钮或强制下线弹窗时可能提前结束，这是正常情况。如果出现屏幕尺寸、游戏前台或主城识别错误，请先解决错误，不要直接启动自动点击。

### 第 8 步：打开 GameBot

Windows 用户回到项目文件夹，双击 `start_windows.cmd`。如果窗口没有出现，则回到 PowerShell 运行：

```powershell
.\.venv\Scripts\python.exe gui.py
```

macOS 终端：

```sh
./.venv/bin/python gui.py
```

在界面中完成以下操作：

1. 查看“模拟器”下拉框是否已经选中刚创建的 Pixel 5；没有时点击“刷新”。
2. 第一次使用时，先关闭其他任务，只保留“自动联盟互助”。
3. 点击蓝色“启动”，同时观察 GameBot 日志和模拟器画面。
4. 确认识别与点击位置正常后，点击红色“停止”。
5. 再根据需要逐项开启其他任务。练兵可以分别关闭盾兵、矛兵或射手。
6. 生命结晶阈值和强制下线等待分钟数必须填写大于 0 的数字。

至此，普通用户需要完成的安装和启动流程已经结束。

## 可选：不用图形界面运行

普通用户使用第 8 步的图形界面即可，不需要本节。如果只想通过命令窗口运行，可以使用下面的示例。

Windows PowerShell：

```powershell
# 只运行联盟互助 10 分钟，最多点击 20 次
.\.venv\Scripts\python.exe help_bot.py --duration 600 --max-clicks 20

# 长期运行全部日常任务；按键盘 Ctrl+C 停止
.\.venv\Scripts\python.exe help_bot.py --daily-tasks --forever --reconnect-minutes 1
```

macOS 终端：

```sh
# 只运行联盟互助 10 分钟，最多点击 20 次
./.venv/bin/python help_bot.py --duration 600 --max-clicks 20

# 长期运行全部日常任务；按键盘 Control+C 停止
./.venv/bin/python help_bot.py --daily-tasks --forever --reconnect-minutes 1
```

以上命令默认电脑只连接一台模拟器。如果同时启动了多台模拟器，请直接使用图形界面选择设备。长期运行时，电脑、模拟器和游戏必须一直保持开启。

## 常见问题

### 找不到 ADB 或提示无法启动 ADB

重新打开 Android Studio 的 **SDK Manager → SDK Tools**，确认 **Android SDK Platform-Tools** 已勾选。没有勾选时，勾选它并点击 **Apply**；已经勾选时，可以先取消、应用，再重新勾选并安装。安装完成后关闭 GameBot 和模拟器，重新启动 Android Studio 模拟器，再执行第 7 步。

不要从网上单独寻找来历不明的 `adb.exe`，也不要把 macOS 的 `adb` 文件改名成 Windows 的 `adb.exe`。

### GUI 中没有模拟器

先打开 Android Studio 的 **Device Manager**，确认 Pixel 5 右侧显示为正在运行，并且模拟器窗口已经出现安卓桌面。然后回到 GameBot 点击“刷新”。仍然没有时，关闭模拟器后从 Device Manager 重新启动，再执行第 7 步的检测命令。

GameBot 只显示安卓模拟器，不会显示通过 USB 连接的实体手机。

### 提示屏幕尺寸不正确

GameBot 收到的游戏截图必须是 1080×2340。拖大或缩小模拟器在电脑上的窗口没有作用。请删除尺寸错误的虚拟设备，然后按照第 3 步重新创建 **Pixel 5**，不要修改它的默认分辨率。

### 提示游戏不在前台或无法确认主城

进入模拟器，确认屏幕上显示的是《无尽冬日》国服主城，而不是安卓桌面、登录页面或其他应用。第一次运行前，建议手动关闭游戏内活动弹窗并返回主城，然后重新执行只观察模式。

如果使用的不是国服、不是中文界面或游戏界面已经大幅改版，现有识别图片可能无法使用。

### 安装项目依赖时出现红色 ERROR

先检查电脑能否正常上网，然后重新执行第 6 步最后两条 `pip` 命令。仍然失败时，请从错误信息最下面开始查看；通常可以看到是网络连接失败，还是 Python 版本不正确。请确认第一条版本命令显示的是 `Python 3.12.x`。

### OCR 加载失败

重新执行第 6 步中自己系统对应的依赖安装命令。不要使用系统自带的其他 Python 启动 GameBot。Windows 会自动安装 RapidOCR，macOS 会自动安装 PyObjC；Linux 目前不受支持。

### 双击 `start_windows.cmd` 一闪而过

这通常表示第 6 步没有完成。回到项目目录打开 PowerShell，重新执行第 6 步的四条 Windows 命令，然后执行：

```powershell
.\.venv\Scripts\python.exe gui.py
```

使用这条命令启动时，错误信息会保留在 PowerShell 窗口中，方便判断是哪一步没有安装成功。

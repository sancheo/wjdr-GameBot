using System;
using System.Diagnostics;
using System.Collections.Generic;
using System.Drawing;
using System.IO;
using System.Reflection;
using System.Runtime.CompilerServices;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

internal static class Program
{
    internal static readonly string Root = AppDomain.CurrentDomain.BaseDirectory;
    internal static readonly bool Portable = Directory.Exists(Path.Combine(Root, "runtime"));
    internal static readonly string Python = Portable
        ? Path.Combine(Root, "runtime", "python", "python.exe")
        : Path.Combine(Root, ".venv", "Scripts", "python.exe");
    internal static readonly string Sdk = Portable ? Path.Combine(Root, "runtime", "webview-sdk")
        : Path.Combine(Root, ".venv", "Lib", "site-packages", "webview", "lib");
    internal static readonly string Browser = Portable ? Path.Combine(Root, "runtime", "webview2") : null;
    internal static bool CheckUi;
    internal static bool CheckStartup;

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern bool SetDllDirectory(string path);
    [DllImport("user32.dll")]
    private static extern bool SetProcessDPIAware();

    [STAThread]
    private static void Main(string[] args)
    {
        CheckStartup = Array.IndexOf(args, "--check-startup") >= 0;
        CheckUi = CheckStartup || Array.IndexOf(args, "--check-ui") >= 0;
        if (CheckUi)
        {
            Console.SetOut(new StreamWriter(Console.OpenStandardOutput(), new UTF8Encoding(false)) { AutoFlush = true });
            Console.SetError(new StreamWriter(Console.OpenStandardError(), new UTF8Encoding(false)) { AutoFlush = true });
        }
        try
        {
            if (!File.Exists(Python) ||
                !File.Exists(Path.Combine(Sdk, "Microsoft.Web.WebView2.Core.dll")))
                throw new InvalidOperationException(Portable ? "便携包文件不完整，请重新解压完整压缩包。"
                    : "请先按 WINDOWS_DEVELOPMENT.md 创建 .venv 并安装 requirements.txt。\n请将 GameBot.exe 保留在项目目录中。");
            if (Portable && !File.Exists(Path.Combine(Browser, "msedgewebview2.exe")))
                throw new InvalidOperationException("便携包缺少 WebView2 运行环境，请重新解压完整压缩包。");
            if (Portable) PreparePortableRuntime();
            AppDomain.CurrentDomain.AssemblyResolve += delegate(object sender, ResolveEventArgs e)
            {
                string name = new AssemblyName(e.Name).Name;
                if (name != "Microsoft.Web.WebView2.Core" && name != "Microsoft.Web.WebView2.WinForms") return null;
                return Assembly.LoadFrom(Path.Combine(Sdk, name + ".dll"));
            };
            SetDllDirectory(Path.Combine(Sdk, "runtimes", "win-x64", "native"));
            SetProcessDPIAware();
            Run();
        }
        catch (Exception e)
        {
            Environment.ExitCode = 1;
            if (CheckUi) Console.Error.WriteLine(e);
            else MessageBox.Show(e.Message, "GameBot 启动失败", MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
    }

    private static void PreparePortableRuntime()
    {
        // Keep a user's Python/WebView2 environment from overriding this package.
        Environment.SetEnvironmentVariable("PYTHONHOME", null);
        Environment.SetEnvironmentVariable("PYTHONPATH", null);
        Environment.SetEnvironmentVariable("PYTHONNOUSERSITE", "1");
        Environment.SetEnvironmentVariable("WEBVIEW2_BROWSER_EXECUTABLE_FOLDER", Browser);
        // Microsoft requires these read/execute grants for unpackaged Fixed
        // Version runtimes on Windows 10 (v120+). Only this bundled folder changes.
        int build;
        string currentBuild = Convert.ToString(Microsoft.Win32.Registry.GetValue(
            @"HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows NT\CurrentVersion", "CurrentBuildNumber", "0"));
        if (Int32.TryParse(currentBuild, out build) && build >= 22000) return;
        var start = new ProcessStartInfo(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System), "icacls.exe"),
            "\"" + Browser + "\" /grant *S-1-15-2-2:(OI)(CI)(RX) *S-1-15-2-1:(OI)(CI)(RX)");
        start.UseShellExecute = false;
        start.CreateNoWindow = true;
        start.RedirectStandardOutput = true;
        start.RedirectStandardError = true;
        using (var process = Process.Start(start))
        {
            string output = process.StandardOutput.ReadToEnd();
            string error = process.StandardError.ReadToEnd();
            process.WaitForExit();
            if (process.ExitCode != 0) throw new InvalidOperationException("无法准备界面运行环境，请解压到当前用户可写的本地目录。\n" + output + error);
        }
    }

    [MethodImpl(MethodImplOptions.NoInlining)]
    private static void Run()
    {
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        using (var form = new DesktopWindow()) Application.Run(form);
    }
}

internal sealed class DesktopWindow : Form
{
    private readonly WebView2 view = new WebView2();
    private readonly JavaScriptSerializer json = new JavaScriptSerializer();
    private Process backend;
    private StreamWriter backendInput;
    private bool closing;
    private readonly StringBuilder backendError = new StringBuilder();

    internal DesktopWindow()
    {
        Text = "无尽冬日 GameBot";
        ClientSize = new Size(1040, 800);
        MinimumSize = new Size(560, 560);
        StartPosition = FormStartPosition.CenterScreen;
        BackColor = Color.FromArgb(237, 243, 255);
        AutoScaleMode = AutoScaleMode.Dpi;
        view.Dock = DockStyle.Fill;
        view.DefaultBackgroundColor = BackColor;
        Controls.Add(view);
        string iconPath = Path.Combine(Program.Root, "assets", "gamebot-icon.png");
        using (var image = new Bitmap(iconPath))
        {
            IntPtr handle = image.GetHicon();
            using (var icon = Icon.FromHandle(handle)) Icon = (Icon)icon.Clone();
            DestroyIcon(handle);
        }
        Shown += async delegate { await Initialize(); };
        FormClosing += async delegate(object sender, FormClosingEventArgs e)
        {
            if (closing) return;
            e.Cancel = true;
            closing = true;
            await Task.Run(new Action(StopBackend));
            Close();
        };
    }

    [DllImport("user32.dll")]
    private static extern bool DestroyIcon(IntPtr handle);

    private async Task Initialize()
    {
        try
        {
            StartBackend();
            var options = new CoreWebView2EnvironmentOptions("--disable-features=ElasticOverscroll");
            var environment = await CoreWebView2Environment.CreateAsync(Program.Browser,
                Path.Combine(Program.Root, ".desktop-cache", Program.CheckUi ? "ui-check" : ""), options);
            await view.EnsureCoreWebView2Async(environment);
            if (closing) return;
            view.CoreWebView2.Settings.AreDefaultContextMenusEnabled = false;
            view.CoreWebView2.Settings.AreDevToolsEnabled = Program.CheckUi;
            view.CoreWebView2.Settings.IsZoomControlEnabled = false;
            view.CoreWebView2.Settings.IsStatusBarEnabled = false;
            view.CoreWebView2.SetVirtualHostNameToFolderMapping("gamebot.local",
                Path.Combine(Program.Root, "assets"), CoreWebView2HostResourceAccessKind.DenyCors);
            ulong pageNavigationId = 0;
            view.CoreWebView2.NavigationStarting += delegate(object sender, CoreWebView2NavigationStartingEventArgs e)
            {
                if (e.Uri != "https://gamebot.local/desktop.html") e.Cancel = true;
                else pageNavigationId = e.NavigationId;
            };
            view.CoreWebView2.NewWindowRequested += delegate(object sender, CoreWebView2NewWindowRequestedEventArgs e) { e.Handled = true; };
            view.CoreWebView2.WebMessageReceived += ReceiveRequest;
            var loaded = new TaskCompletionSource<bool>();
            view.CoreWebView2.NavigationCompleted += delegate(object sender, CoreWebView2NavigationCompletedEventArgs e)
            {
                if (e.NavigationId != pageNavigationId) return;
                if (e.IsSuccess) loaded.TrySetResult(true);
                else loaded.TrySetException(new InvalidOperationException("界面加载失败：" + e.WebErrorStatus));
            };
            view.CoreWebView2.Navigate("https://gamebot.local/desktop.html");
            if (Program.CheckUi)
            {
                if (await Task.WhenAny(loaded.Task, Task.Delay(15000)) != loaded.Task)
                    throw new TimeoutException("WebView2 页面加载超时");
                await loaded.Task;
                if (Program.CheckStartup) await CheckStartupInterface();
                else await CheckInterface();
                Close();
            }
        }
        catch (Exception e)
        {
            Environment.ExitCode = 1;
            if (Program.CheckUi) Console.Error.WriteLine(e);
            else MessageBox.Show(this, "无法打开桌面界面：\n" + e.Message +
                (Program.Portable ? "\n\n请将完整便携包解压到可写的本地目录后重试。"
                    : "\n\n请确认已安装 Microsoft Edge WebView2 Runtime，并已安装项目 Python 依赖。"),
                "GameBot 启动失败", MessageBoxButtons.OK, MessageBoxIcon.Error);
            Close();
        }
    }

    private void StartBackend()
    {
        var start = new ProcessStartInfo(Program.Python,
            "-X utf8 -u gui_web.py --bridge" + (Program.CheckUi ? " --check-ui" : ""));
        start.WorkingDirectory = Program.Root;
        start.UseShellExecute = false;
        start.CreateNoWindow = true;
        start.RedirectStandardInput = true;
        start.RedirectStandardOutput = true;
        start.RedirectStandardError = true;
        start.StandardOutputEncoding = Encoding.UTF8;
        start.StandardErrorEncoding = Encoding.UTF8;
        backend = new Process { StartInfo = start, EnableRaisingEvents = true };
        backend.OutputDataReceived += delegate(object sender, DataReceivedEventArgs e)
        {
            if (!String.IsNullOrEmpty(e.Data)) SendResponse(e.Data);
        };
        backend.ErrorDataReceived += delegate(object sender, DataReceivedEventArgs e)
        {
            if (e.Data == null) return;
            lock (backendError)
            {
                backendError.AppendLine(e.Data);
                if (backendError.Length > 4000) backendError.Remove(0, backendError.Length - 4000);
            }
        };
        backend.Exited += delegate
        {
            if (closing) return;
            string message;
            lock (backendError) message = "Python 后台已退出。\n" + backendError.ToString();
            SendResponse(json.Serialize(new { fatal = message }));
        };
        backend.Start();
        // .NET Framework otherwise uses the Windows code page (e.g. GB2312),
        // but Python's -X utf8 bridge requires UTF-8 JSON without a BOM.
        backendInput = new StreamWriter(backend.StandardInput.BaseStream, new UTF8Encoding(false)) { AutoFlush = true };
        backend.BeginOutputReadLine();
        backend.BeginErrorReadLine();
    }

    private void ReceiveRequest(object sender, CoreWebView2WebMessageReceivedEventArgs e)
    {
        if (closing || e.Source != "https://gamebot.local/desktop.html") return;
        try
        {
            if (e.WebMessageAsJson.Length > 65536) throw new InvalidOperationException("界面请求过大");
            var request = json.Deserialize<Dictionary<string, object>>(e.WebMessageAsJson);
            if (request.ContainsKey("method") && Convert.ToString(request["method"]) == "install_update")
            {
                if (Program.CheckUi) throw new InvalidOperationException("自检模式不能安装更新");
                foreach (var process in Process.GetProcesses())
                {
                    using (process)
                    {
                        if (process.Id == Process.GetCurrentProcess().Id || !process.ProcessName.StartsWith("GameBot")) continue;
                        try
                        {
                            if (String.Equals(Path.GetDirectoryName(process.MainModule.FileName), Program.Root.TrimEnd(Path.DirectorySeparatorChar), StringComparison.OrdinalIgnoreCase))
                            {
                                SendResponse(json.Serialize(new { id = request["id"], error = "请先关闭此目录中其他 GameBot 窗口，再安装更新。" }));
                                return;
                            }
                        }
                        catch (System.ComponentModel.Win32Exception) { }
                        catch (InvalidOperationException) { }
                    }
                }
            }
            backendInput.WriteLine(e.WebMessageAsJson);
        }
        catch (Exception error)
        {
            SendResponse(json.Serialize(new { fatal = "Python 后台连接失败：" + error.Message }));
        }
    }

    private void SendResponse(string response)
    {
        if (closing || IsDisposed || !IsHandleCreated) return;
        try
        {
            BeginInvoke(new Action(delegate
            {
                if (!closing && view.CoreWebView2 != null)
                {
                    view.CoreWebView2.PostWebMessageAsJson(response);
                    if (response.Contains("install_plan"))
                    {
                        var message = json.Deserialize<Dictionary<string, object>>(response);
                        object result;
                        if (message.TryGetValue("result", out result))
                        {
                            var data = result as Dictionary<string, object>;
                            if (data != null && data.ContainsKey("install_plan")) BeginUpdate(Convert.ToString(data["install_plan"]));
                        }
                    }
                }
            }));
        }
        catch (InvalidOperationException) { }
    }

    private async void BeginUpdate(string plan)
    {
        try
        {
            string stage = Path.GetDirectoryName(Path.GetFullPath(plan));
            if (!Program.Portable || Program.CheckUi || Path.GetDirectoryName(stage) != Program.Root.TrimEnd(Path.DirectorySeparatorChar) ||
                !Path.GetFileName(stage).StartsWith(".update-stage-") || Path.GetFileName(plan) != "update-plan.json")
                throw new InvalidOperationException("更新计划目录不正确。");
            var start = new ProcessStartInfo(Path.Combine(stage, "GameBot.Updater.exe"),
                "\"" + plan + "\" " + Process.GetCurrentProcess().Id) { UseShellExecute = false, WorkingDirectory = stage };
            Process.Start(start);
            await Task.Run(new Action(StopBackend));
            Close();
        }
        catch (Exception error)
        {
            MessageBox.Show(this, "无法启动更新程序，现有程序文件未被修改。\n" + error.Message, "GameBot 更新失败");
            Close();
        }
    }

    private void StopBackend()
    {
        closing = true;
        if (backend == null) return;
        try
        {
            if (!backend.HasExited)
            {
                backendInput.WriteLine("{\"method\":\"close\"}");
                backendInput.Close();
                if (!backend.WaitForExit(25000))
                {
                    var stop = new ProcessStartInfo(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System), "taskkill.exe"),
                        "/PID " + backend.Id + " /T /F") { UseShellExecute = false, CreateNoWindow = true };
                    using (var process = Process.Start(stop)) process.WaitForExit(5000);
                }
            }
        }
        catch (InvalidOperationException) { }
        catch (IOException) { }
        finally { backend.Dispose(); }
    }

    private async Task<bool> JsBool(string expression)
    {
        return await view.CoreWebView2.ExecuteScriptAsync(expression) == "true";
    }

    private async Task CheckStartupInterface()
    {
        for (int i = 0; i < 150; i++)
        {
            if (await JsBool("connected && document.getElementById('app-version').textContent.startsWith('v') && !document.getElementById('refresh').disabled && Array.from(document.images).every(i => i.complete && i.naturalWidth > 0)"))
            {
                Console.WriteLine("GameBot startup check passed.");
                return;
            }
            await Task.Delay(100);
        }
        throw new InvalidOperationException("新版界面、资源或后台未就绪。");
    }

    private async Task CheckInterface()
    {
        // Real EXE/WebView2/pipe integration. Test backend does not enumerate ADB.
        for (int i = 0; i < 100; i++)
        {
            if (await JsBool("connected && !document.getElementById('refresh').disabled && Array.from(document.images).every(i => i.complete && i.naturalWidth > 0)")) break;
            if (i == 99) throw new InvalidOperationException("图片或 Python 桥接未就绪");
            await Task.Delay(50);
        }
        if (!await JsBool("document.querySelectorAll('[data-option]').length === 31 && document.querySelector('#daily #option-task_rewards') !== null && document.querySelector('#alt-account #option-help_clicker') !== null && document.querySelector('#training #option-heal') !== null && document.querySelector('#wilderness #option-hunt') !== null && document.querySelector('#hunt-level-beast').options.length === 30 && JSON.stringify(Array.from(document.querySelector('#hunt-level-polar_beast').options, option => option.value)) === JSON.stringify(['2级','3级','4级','5级','6级'])"))
            throw new InvalidOperationException("任务控件不完整");
        await view.CoreWebView2.ExecuteScriptAsync(@"
            window.checkedUpdates = false;
            (async () => {
                const api = window.pywebview.api;
                const original = await api.get_state();
                const saved = {get_state:api.get_state, check_updates:api.check_updates,
                    download_update:api.download_update, install_update:api.install_update};
                const fixture = {...original, update:{version:'1.0.0', phase:'idle', message:'尚未检查更新',
                    installable:true, new_version:'', notes:'', downloaded:0, total:100}};
                let checked = false, downloaded = false, installed = false;
                try {
                    api.get_state = async () => fixture;
                    api.check_updates = async () => {checked=true; Object.assign(fixture.update, {
                        phase:'available', new_version:'1.0.1', notes:'<script>unsafe()</script>', message:'发现新版本'}); return fixture;};
                    api.download_update = async () => {downloaded=true; fixture.update.phase='downloading'; return fixture;};
                    api.install_update = async () => {installed=true; fixture.update.phase='installing'; return {};};
                    render(fixture);
                    document.getElementById('check-updates').click(); await mutation;
                    if (!checked || document.getElementById('app-version').textContent !== 'v1.0.0' ||
                        document.getElementById('update-action').hidden || document.querySelector('#update-notes script') ||
                        document.getElementById('update-notes').textContent !== '<script>unsafe()</script>')
                        throw new Error('版本、更新说明或检查按钮错误');
                    document.getElementById('update-action').click(); await mutation;
                    fixture.update.downloaded=50; render(fixture);
                    if (!downloaded || document.getElementById('update-progress').hidden ||
                        document.getElementById('update-progress').value !== 50 || !document.getElementById('check-updates').disabled)
                        throw new Error('下载按钮、进度或重复检查保护错误');
                    fixture.update.phase='ready'; render(fixture);
                    if (document.getElementById('update-action').textContent !== '安装并重启') throw new Error('安装按钮缺失');
                    document.getElementById('update-action').click(); await mutation; render(fixture);
                    if (!installed || !document.getElementById('update-action').hidden) throw new Error('安装操作或重复安装保护错误');
                    fixture.update.phase='available'; fixture.update.installable=false; render(fixture);
                    if (!document.getElementById('update-action').disabled) throw new Error('源码模式允许自动安装');
                    window.checkedUpdates=true;
                } finally {Object.assign(api, saved); render(original);}
            })().catch(error => {window.checkedUpdates=String(error);});");
        for (int i = 0; i < 100 && !await JsBool("window.checkedUpdates === true"); i++) await Task.Delay(50);
        if (!await JsBool("window.checkedUpdates === true"))
            throw new InvalidOperationException("更新界面自检失败：" + await view.CoreWebView2.ExecuteScriptAsync("window.checkedUpdates"));
        Console.WriteLine("Update UI checks passed: version, check/download/install buttons, notes as text, progress, duplicate request and source mode guards.");
        await view.CoreWebView2.ExecuteScriptAsync(@"
            window.checkedBear = false;
            (async () => {
                const original = formConfig();
                const input = document.getElementById('option-bear');
                const drive = document.getElementById('option-bear_drive');
                const follow = document.getElementById('option-bear_follow');
                const settle = async () => { await new Promise(resolve => setTimeout(resolve, 180)); await mutation; };
                try {
                    if (input.checked || drive.checked || !follow.checked || !drive.disabled || !follow.disabled ||
                        document.querySelector('#wilderness .info').title !== '开启时其他任务不执行, 只专注打熊')
                        throw new Error('打熊默认值或提示错误');
                    input.click();
                    await settle();
                    const state = await window.pywebview.api.get_state();
                    if (!state.config.options.bear || state.config.options.bear_drive || !state.config.options.bear_follow ||
                        drive.disabled || follow.disabled || !document.getElementById('option-intelligence').disabled ||
                        !document.getElementById('option-heal').disabled || !document.getElementById('option-help').disabled ||
                        document.getElementById('option-help_clicker').disabled || document.getElementById('option-reconnect').disabled)
                        throw new Error('打熊配置同步或独占任务依赖错误');
                    drive.click();
                    follow.click();
                    await settle();
                    const saved = (await window.pywebview.api.get_state()).config;
                    loadConfig(saved);
                    if (!drive.checked || follow.checked || !saved.options.bear_drive || saved.options.bear_follow)
                        throw new Error('打熊子选项未同步或恢复');
                    running = true;
                    dependencies();
                    if (!input.disabled || !drive.disabled || !follow.disabled) throw new Error('运行时仍可修改打熊选项');
                    running = false;
                    dependencies();
                    input.click();
                    await settle();
                    if (!drive.disabled || !follow.disabled || document.getElementById('option-intelligence').disabled ||
                        document.getElementById('option-help').disabled ||
                        document.getElementById('option-help').checked !== original.options.help)
                        throw new Error('关闭打熊后未恢复原任务设置');
                    window.checkedBear = true;
                } finally {
                    running = false;
                    clearTimeout(syncTimer);
                    await mutation;
                    await window.pywebview.api.update_config(original);
                    loadConfig(original);
                }
            })().catch(error => {window.checkedBear = String(error);});");
        for (int i = 0; i < 100 && !await JsBool("window.checkedBear === true"); i++) await Task.Delay(50);
        if (!await JsBool("window.checkedBear === true"))
            throw new InvalidOperationException("自动打熊界面自检失败：" + await view.CoreWebView2.ExecuteScriptAsync("window.checkedBear"));
        await view.CoreWebView2.ExecuteScriptAsync(@"
            window.checkedHelpClicker = false;
            (async () => {
                const original = formConfig();
                const input = document.getElementById('option-help_clicker');
                const bear = document.getElementById('option-bear');
                const dialog = document.getElementById('help-clicker-confirm');
                const hint = '打开时其他功能不会执行, 只会进入联盟互助界面快速点击互助';
                const settle = async () => { await new Promise(resolve => setTimeout(resolve, 180)); await mutation; };
                try {
                    if (input.checked || document.querySelector('#alt-account .info').title !== hint ||
                        !document.querySelector('#alt-account h3').textContent.includes('小号模式'))
                        throw new Error('小号模式默认值或悬停说明错误');
                    bear.click();
                    await settle();
                    if (!bear.checked || input.disabled) throw new Error('开启打熊后无法切换互助连点');
                    input.click();
                    if (!dialog.open || input.checked || document.getElementById('help-clicker-confirm-message').textContent !== hint)
                        throw new Error('打开开关前未要求确认');
                    dialog.querySelector('button[value=no]').click();
                    await settle();
                    const declined = (await window.pywebview.api.get_state()).config.options;
                    if (dialog.open || input.checked || declined.help_clicker || !bear.checked || !declined.bear)
                        throw new Error('选择否后互助连点或打熊状态改变');
                    input.click();
                    dialog.querySelector('button[value=yes]').click();
                    await settle();
                    const confirmed = (await window.pywebview.api.get_state()).config.options;
                    if (dialog.open || !input.checked || !confirmed.help_clicker || bear.checked || confirmed.bear)
                        throw new Error('确认互助连点后未关闭打熊');
                    if (!document.getElementById('option-hunt').disabled || !document.getElementById('option-heal').disabled ||
                        !document.getElementById('entry-tree_threshold').disabled || bear.disabled || document.getElementById('option-reconnect').disabled ||
                        document.getElementById('entry-reconnect_minutes').disabled)
                        throw new Error('互助连点任务依赖错误');
                    loadConfig((await window.pywebview.api.get_state()).config);
                    if (!input.checked || dialog.open) throw new Error('当前窗口确认状态不能直接加载');
                    running = true;
                    dependencies();
                    if (!input.disabled || !bear.disabled) throw new Error('运行时仍可切换互斥模式');
                    running = false;
                    dependencies();
                    bear.click();
                    await settle();
                    const switched = (await window.pywebview.api.get_state()).config.options;
                    if (!bear.checked || !switched.bear || input.checked || switched.help_clicker || input.disabled)
                        throw new Error('开启打熊后未关闭互助连点');
                    input.click();
                    dialog.querySelector('button[value=yes]').click();
                    await settle();
                    const switchedBack = (await window.pywebview.api.get_state()).config.options;
                    if (!input.checked || !switchedBack.help_clicker || bear.checked || switchedBack.bear)
                        throw new Error('再次确认互助连点后未关闭打熊');
                    input.click();
                    await settle();
                    input.click();
                    const cancelled = new Event('cancel', {cancelable:true});
                    if (dialog.dispatchEvent(cancelled)) dialog.close();
                    await settle();
                    if (input.checked || (await window.pywebview.api.get_state()).config.options.help_clicker)
                        throw new Error('取消确认后仍开启互助连点');
                    window.checkedHelpClicker = true;
                } finally {
                    running = false;
                    if (dialog.open) { dialog.close('no'); await settle(); }
                    clearTimeout(syncTimer);
                    await mutation;
                    await window.pywebview.api.update_config(original);
                    loadConfig(original);
                }
            })().catch(error => {window.checkedHelpClicker = String(error);});");
        for (int i = 0; i < 100 && !await JsBool("window.checkedHelpClicker === true"); i++) await Task.Delay(50);
        if (!await JsBool("window.checkedHelpClicker === true"))
            throw new InvalidOperationException("互助连点界面自检失败：" + await view.CoreWebView2.ExecuteScriptAsync("window.checkedHelpClicker"));
        await view.CoreWebView2.ExecuteScriptAsync(@"
            window.checkedLevels = false;
            (async () => {
                const original = formConfig();
                window.checkedLevelDetails = JSON.stringify({original, options: Array.from(document.getElementById('level-meat').options, option => option.value)});
                let verified = false;
                try {
                    for (const [name] of resources) {
                        const input = document.getElementById('level-' + name);
                        input.value = '4级矿';
                        input.dispatchEvent(new Event('change', {bubbles: true}));
                    }
                    await new Promise(resolve => setTimeout(resolve, 180));
                    await mutation;
                    const state = await window.pywebview.api.get_state();
                    window.checkedLevelDetails = JSON.stringify({backend: state.config.resource_levels, form: formConfig().resource_levels});
                    verified = resources.every(([name]) =>
                        state.config.resource_levels[name] === '4级矿' &&
                        formConfig().resource_levels[name] === '4级矿');
                } finally {
                    clearTimeout(syncTimer);
                    await window.pywebview.api.update_config(original);
                    loadConfig(original);
                }
                window.checkedLevels = verified;
            })().catch(error => {window.checkedLevels = 'failed'; window.checkedLevelDetails += String(error);});");
        for (int i = 0; i < 100 && !await JsBool("window.checkedLevels === true"); i++) await Task.Delay(50);
        if (!await JsBool("window.checkedLevels === true")) throw new InvalidOperationException("资源下拉框4级选择或 Python 等级同步失败：" + await view.CoreWebView2.ExecuteScriptAsync("window.checkedLevelDetails"));
        await view.CoreWebView2.ExecuteScriptAsync(@"
            window.checkedHunt = false;
            (async () => {
                const original = formConfig();
                try {
                    document.getElementById('hunt-level-beast').value = '30级';
                    document.getElementById('hunt-level-polar_beast').value = '6级';
                    document.getElementById('hunt-level-beast').dispatchEvent(new Event('change', {bubbles:true}));
                    await new Promise(resolve => setTimeout(resolve, 180));
                    await mutation;
                    const state = await window.pywebview.api.get_state();
                    if (state.config.hunt_levels.beast !== '30级' || state.config.hunt_levels.polar_beast !== '6级')
                        throw new Error('打怪等级同步失败');
                    document.getElementById('option-beast').checked = false;
                    document.getElementById('option-beast').dispatchEvent(new Event('change', {bubbles:true}));
                    document.getElementById('option-polar_beast').checked = false;
                    document.getElementById('option-polar_beast').dispatchEvent(new Event('change', {bubbles:true}));
                    if (!document.getElementById('option-polar_beast').checked || !document.getElementById('notice').open)
                        throw new Error('允许取消最后一个打怪目标');
                    document.getElementById('notice').close();
                    document.getElementById('option-hunt').checked = false;
                    dependencies();
                    if (!document.getElementById('option-beast').disabled || !document.getElementById('hunt-level-polar_beast').disabled)
                        throw new Error('打怪开关依赖失败');
                    window.checkedHunt = true;
                } finally {
                    clearTimeout(syncTimer);
                    await mutation;
                    await window.pywebview.api.update_config(original);
                    loadConfig(original);
                }
            })().catch(error => {window.checkedHunt = String(error);});");
        for (int i = 0; i < 100 && !await JsBool("window.checkedHunt === true"); i++) await Task.Delay(50);
        if (!await JsBool("window.checkedHunt === true")) throw new InvalidOperationException("自动打怪控件自检失败：" + await view.CoreWebView2.ExecuteScriptAsync("window.checkedHunt"));
        await view.CoreWebView2.ExecuteScriptAsync("window.checkedBridge=false; window.pywebview.api.start(formConfig()).then(()=>window.checkedBridge='unsafe').catch(e=>window.checkedBridge=e.message.includes('模拟器'))");
        for (int i = 0; i < 60 && !await JsBool("window.checkedBridge === true"); i++) await Task.Delay(50);
        if (!await JsBool("window.checkedBridge === true")) throw new InvalidOperationException("启动校验或 Python 返回通道失败");
        await view.CoreWebView2.ExecuteScriptAsync(@"
            window.checkedEmulatorPicker = false;
            (async () => {
                const api = window.pywebview.api;
                const original = await api.get_state();
                const saved = {select_device: api.select_device, launch_emulator: api.launch_emulator, restart_emulator: api.restart_emulator,
                    start: api.start, refresh_devices: api.refresh_devices, update_config: api.update_config};
                const fixture = {...original, selected: 'avd:Pixel_5', devices: [
                    {id:'avd:Pixel_5', key:'avd:Pixel_5', name:'Pixel_5', avd:'Pixel_5', serial:'emulator-test', status:'running'},
                    {id:'avd:Pixel_5_2', key:'avd:Pixel_5_2', name:'Pixel_5_2', avd:'Pixel_5_2', serial:'', status:'stopped'}]};
                let launched = '', restarted = '', refreshed = false, updated = false;
                try {
                    api.select_device = async id => { fixture.selected = id; return fixture; };
                    api.start = async () => { throw new Error('模拟器 Pixel_5_2 未启动'); };
                    api.launch_emulator = async id => { launched = id; fixture.devices[1].status = 'starting'; return fixture; };
                    api.restart_emulator = async id => { restarted = id; fixture.devices[0].status = 'restarting'; return fixture; };
                    api.refresh_devices = async () => { refreshed = true; return fixture; };
                    api.update_config = async () => { updated = true; return fixture; };
                    render(fixture);
                    document.getElementById('device').click();
                    if (document.getElementById('device-list').hidden || document.querySelectorAll('.device-choice').length !== 2 ||
                        document.querySelectorAll('.device-launch').length !== 1 ||
                        document.querySelectorAll('.device-restart').length !== 1 ||
                        !document.getElementById('device-list').textContent.includes('未启动')) throw new Error('列表或启动按钮缺失');
                    document.querySelectorAll('.device-choice')[1].click();
                    await mutation;
                    if (selected !== 'avd:Pixel_5_2' || document.getElementById('run').disabled) throw new Error('未启动模拟器选择失败');
                    document.getElementById('run').click();
                    await mutation;
                    if (!document.getElementById('notice').open || !document.getElementById('notice-message').textContent.includes('未启动'))
                        throw new Error('未启动提示缺失');
                    document.getElementById('notice').close();
                    document.getElementById('device').click();
                    document.querySelector('.device-launch').click();
                    await mutation;
                    if (launched !== 'avd:Pixel_5_2' || document.querySelector('.device-launch') ||
                        !document.getElementById('device-label').textContent.includes('启动中')) throw new Error('启动操作或状态更新失败');
                    document.querySelector('.device-restart').click();
                    await mutation;
                    if (restarted !== 'avd:Pixel_5' || selected !== 'avd:Pixel_5_2' || document.querySelector('.device-restart') ||
                        !document.getElementById('device-list').textContent.includes('重启中')) throw new Error('重启操作或状态更新失败');
                    fixture.devices[0].status = 'running';
                    fixture.running = true;
                    render(fixture);
                    if (!document.querySelector('.device-restart').disabled) throw new Error('任务运行中仍允许重启');
                    if (document.getElementById('refresh').disabled) throw new Error('运行中刷新不可用');
                    document.getElementById('refresh').click();
                    await mutation;
                    if (!refreshed || updated) throw new Error('运行中刷新修改了任务配置');
                    window.checkedEmulatorPicker = true;
                } finally {
                    Object.assign(api, saved);
                    document.getElementById('notice').close();
                    closeDevices();
                    render(original);
                }
            })().catch(error => {window.checkedEmulatorPicker = String(error);});");
        for (int i = 0; i < 100 && !await JsBool("window.checkedEmulatorPicker === true"); i++) await Task.Delay(50);
        if (!await JsBool("window.checkedEmulatorPicker === true")) throw new InvalidOperationException("模拟器下拉列表自检失败：" + await view.CoreWebView2.ExecuteScriptAsync("window.checkedEmulatorPicker"));
        await view.CoreWebView2.ExecuteScriptAsync("window.checkedLogs=false; window.pywebview.api.get_state().then(s=>{s.logs=[{id:1,text:'<script>unsafe()</script>',error:false},{id:2,text:'异常日志',error:true}];s.last_log=2;render(s);render(s);window.checkedLogs=document.getElementById('log-lines').childElementCount===2&&document.getElementById('error-lines').childElementCount===1&&!document.querySelector('#log-lines script');s.logs=[];s.last_log=0;s.log_reset=true;render(s)})");
        for (int i = 0; i < 60 && !await JsBool("window.checkedLogs === true"); i++) await Task.Delay(50);
        if (!await JsBool("window.checkedLogs === true")) throw new InvalidOperationException("日志去重或文本显示失败");
        await view.CoreWebView2.ExecuteScriptAsync(@"
            window.checkedTaskStatus = false;
            (async () => {
                const api = window.pywebview.api;
                const original = await api.get_state();
                const savedRetry = api.retry_task;
                const fixture = {...original, running:true, stopping:false, task_status:[
                    {name:'train',label:'自动练兵',status:'waiting',next_run:Date.now()/1000+600,reason:''},
                    {name:'treasure',label:'宠物寻宝',status:'disabled',next_run:null,reason:'<script>unsafe()</script>',retry_requested:false}
                ]};
                let retried = '';
                try {
                    api.retry_task = async name => { retried = name; fixture.task_status[1].retry_requested = true; return fixture; };
                    render(fixture);
                    const rows = document.getElementById('task-status-rows');
                    if (rows.children.length !== 2 || rows.querySelector('script') ||
                        !rows.textContent.includes('下次执行') || !rows.textContent.includes('已停用'))
                        throw new Error('状态、时间或异常文本显示失败');
                    rows.querySelector('.task-retry').click();
                    await mutation;
                    if (retried !== 'treasure' || !rows.querySelector('.task-retry').disabled ||
                        !rows.textContent.includes('已安排重试')) throw new Error('单项重试或重复请求保护失败');
                    fixture.stopping = true; fixture.task_status[1].retry_requested = false;
                    render(fixture);
                    if (!rows.querySelector('.task-retry').disabled) throw new Error('停止期间仍可重试');
                    window.checkedTaskStatus = true;
                } finally { api.retry_task = savedRetry; render(original); }
            })().catch(error => {window.checkedTaskStatus = String(error);});");
        for (int i = 0; i < 100 && !await JsBool("window.checkedTaskStatus === true"); i++) await Task.Delay(50);
        if (!await JsBool("window.checkedTaskStatus === true"))
            throw new InvalidOperationException("任务状态界面自检失败：" + await view.CoreWebView2.ExecuteScriptAsync("window.checkedTaskStatus"));
        await view.CoreWebView2.ExecuteScriptAsync("document.getElementById('option-train').checked=false; dependencies()");
        if (!await JsBool("document.getElementById('option-upgrade').disabled && document.getElementById('option-shield').disabled && !document.getElementById('option-heal').disabled"))
            throw new InvalidOperationException("任务依赖禁用失败");
        await view.CoreWebView2.ExecuteScriptAsync("document.getElementById('option-train').checked=true; dependencies(); window.uiFrames=[]; window.uiLast=performance.now(); window.uiTracking=true; function uiFrame(t){if(!window.uiTracking)return;window.uiFrames.push(t-window.uiLast);window.uiLast=t;requestAnimationFrame(uiFrame)}; requestAnimationFrame(uiFrame)");
        var clock = Stopwatch.StartNew();
        for (int i = 0; i < 120; i++)
        {
            ClientSize = new Size(600 + (i % 60) * 13, 620 + (i % 30) * 5);
            await Task.Delay(16);
        }
        foreach (int width in new[] { 560, 1040, 1280 })
        {
            ClientSize = new Size(width, 800);
            await Task.Delay(100);
            string expected = width < 740 ? "1" : width < 1200 ? "2" : "3";
            if (!await JsBool(@"(() => {
                const cards = document.querySelector('.cards');
                if (getComputedStyle(cards).columnCount !== '" + expected + @"' || document.documentElement.scrollWidth > innerWidth) return false;
                const columns = new Map();
                for (const card of cards.children) {
                    if (card.getClientRects().length !== 1) return false;
                    const rect = card.getBoundingClientRect();
                    const left = Math.round(rect.left);
                    if (!columns.has(left)) columns.set(left, []);
                    columns.get(left).push(rect);
                }
                return columns.size === " + expected + @" && Array.from(columns.values()).every(column => {
                    column.sort((a,b) => a.top - b.top);
                    return column.every((rect,i) => i === 0 || Math.abs(rect.top - column[i-1].bottom - 10) < 1);
                });
            })()"))
                throw new InvalidOperationException("响应式布局或横向溢出检查失败：" + width);
        }
        await view.CoreWebView2.ExecuteScriptAsync("window.uiTracking=false");
        string frames = await view.CoreWebView2.ExecuteScriptAsync("JSON.stringify({frames:uiFrames.length,p95:uiFrames.slice().sort((a,b)=>a-b)[Math.floor(uiFrames.length*.95)]})");
        Console.WriteLine("Desktop EXE checks passed: images, 31 controls, bear defaults/tooltip/checkboxes/Python sync/restore/exclusive dependencies, help-clicker default/tooltip/yes/no/cancel/window state/exclusive dependencies, independent auto-healing switch, monster levels/Python sync/last selection guard, level-4 dropdown/Python sync, Python bridge, emulator inventory/status/launch/restart/refresh, validation, log deduplication, task status/deadlines/single-task retry/stop guard, dependencies, 120 resizes, 1/2/3 waterfall columns with 10px gaps. Resize elapsed ms: " + clock.ElapsedMilliseconds + "; frame samples: " + frames);
    }
}

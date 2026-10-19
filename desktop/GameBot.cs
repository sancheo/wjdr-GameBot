using System;
using System.Diagnostics;
using System.Collections.Generic;
using System.Drawing;
using System.IO;
using System.Reflection;
using System.Runtime.CompilerServices;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
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
    internal static bool CheckLogs;

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern bool SetDllDirectory(string path);
    [DllImport("user32.dll")]
    private static extern bool SetProcessDPIAware();

    [STAThread]
    private static void Main(string[] args)
    {
        AppContext.SetSwitch("Switch.System.IO.UseLegacyPathHandling", false);
        AppContext.SetSwitch("Switch.System.IO.BlockLongPaths", false);
        CheckStartup = Array.IndexOf(args, "--check-startup") >= 0;
        CheckLogs = Array.IndexOf(args, "--check-logs") >= 0;
        CheckUi = CheckStartup || CheckLogs || Array.IndexOf(args, "--check-ui") >= 0;
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
    private int rejectedBackendMessages;

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
                if (Program.CheckLogs) { await CheckStartupInterface(); await CheckLogInterface(); }
                else if (Program.CheckStartup) await CheckStartupInterface();
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
            RecordBackendDiagnostic(e.Data);
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
        Dictionary<string, object> message;
        try
        {
            message = new JavaScriptSerializer { MaxJsonLength = 16 * 1024 * 1024 }
                .Deserialize<Dictionary<string, object>>(response);
            if (message == null || (!message.ContainsKey("id") && !message.ContainsKey("fatal")))
                throw new ArgumentException("后台消息不是 JSON RPC 响应");
        }
        catch (ArgumentException)
        {
            Interlocked.Increment(ref rejectedBackendMessages);
            RecordBackendDiagnostic(response);
            return;
        }
        catch (InvalidOperationException)
        {
            Interlocked.Increment(ref rejectedBackendMessages);
            RecordBackendDiagnostic(response);
            return;
        }
        try
        {
            BeginInvoke(new Action(delegate
            {
                // BeginInvoke runs later: an outer catch cannot catch failures
                // here, and the window may have closed since it was queued.
                if (closing || IsDisposed || view.IsDisposed || view.CoreWebView2 == null) return;
                try
                {
                    view.CoreWebView2.PostWebMessageAsJson(response);
                    object result;
                    if (message.TryGetValue("result", out result))
                    {
                        var data = result as Dictionary<string, object>;
                        if (data != null && data.ContainsKey("install_plan")) BeginUpdate(Convert.ToString(data["install_plan"]));
                    }
                }
                catch (ArgumentException error) { RecordBackendDiagnostic(error.Message); }
                catch (InvalidOperationException error) { RecordBackendDiagnostic(error.Message); }
                catch (COMException error) { RecordBackendDiagnostic(error.Message); }
            }));
        }
        catch (InvalidOperationException) { }
    }

    private void RecordBackendDiagnostic(string message)
    {
        lock (backendError)
        {
            backendError.AppendLine(message);
            if (backendError.Length > 4000) backendError.Remove(0, backendError.Length - 4000);
        }
    }

    private async void BeginUpdate(string plan)
    {
        try
        {
            string stage = Path.GetDirectoryName(Path.GetFullPath(plan));
            if (!Program.Portable || Program.CheckUi || Path.GetDirectoryName(stage) != Program.Root.TrimEnd(Path.DirectorySeparatorChar) ||
                !(Path.GetFileName(stage).StartsWith(".update-stage-") ||
                  System.Text.RegularExpressions.Regex.IsMatch(Path.GetFileName(stage), @"^\.u-[0-9a-f]{12}$")) ||
                Path.GetFileName(plan) != "update-plan.json")
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

    private async Task CheckLogInterface()
    {
        await view.CoreWebView2.ExecuteScriptAsync(@"
            window.checkedLogs=false;
            window.pywebview.api.get_state().then(async s=>{
                s.logs=[];s.last_log=0;s.log_reset=true;render(s);
                s.log_reset=false;
                s.logs=Array.from({length:4010},(_,index)=>({id:index+1,
                    text:index===0?'<script>unsafe()</script>':'日志 '+index,error:index%300===0}));
                s.last_log=4010;s.running=true;render(s);
                s.running=false;render(s);render(s);
                const retained=document.getElementById('log-lines').childElementCount===4010
                    &&document.getElementById('error-lines').childElementCount===14
                    &&!document.querySelector('#log-lines script');
                const path = 'C:/异常截图/模拟器 A/失败 <截图>.png';
                const text = '失败截图：'+path+'；故障截图清理失败：测试';
                s.logs=[{id:4011,text,error:true}];s.last_log=4011;render(s);
                const link = document.querySelector('#error-lines .screenshot-link');
                const api = window.pywebview.api;
                const original = api.open_screenshot;
                let opened = null;
                try {
                    api.open_screenshot = async value => {opened=value;return true;};
                    if (link) link.click();
                    await Promise.resolve();
                    window.checkedLogs = retained && !!link && link.textContent===path
                        && link.parentNode.textContent===text && opened===path
                        && !link.querySelector('截图');
                } finally {api.open_screenshot=original;}
                s.logs=[];s.last_log=0;s.log_reset=true;render(s);
            });");
        for (int i = 0; i < 60 && !await JsBool("window.checkedLogs === true"); i++) await Task.Delay(50);
        if (!await JsBool("window.checkedLogs === true")) throw new InvalidOperationException("日志去重或文本显示失败");
        Console.WriteLine("Log UI checks passed: 4010 runtime lines and 14 exceptions retained after stop and repeated snapshots; text escaped; screenshot link opens the exact path.");
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
        await view.CoreWebView2.ExecuteScriptAsync(@"
            window.bridgeProbe = '';
            window.chrome.webview.addEventListener('message', event => {
                if (event.data.id === -1) window.bridgeProbe = event.data.result;
            });");
        int rejectedBefore = rejectedBackendMessages;
        SendResponse("[2026-10-16 08:00:00] 屏幕适配：1176×2400 → 1080×2340");
        SendResponse("{invalid JSON");
        SendResponse("[]");
        SendResponse(json.Serialize(new { id = -1, result = "中文响应与日志隔离正常" }));
        for (int i = 0; i < 100 && !await JsBool("window.bridgeProbe === '中文响应与日志隔离正常'"); i++)
            await Task.Delay(50);
        if (rejectedBackendMessages != rejectedBefore + 3 ||
            !await JsBool("window.bridgeProbe === '中文响应与日志隔离正常' && connected"))
            throw new InvalidOperationException("后台诊断隔离或 JSON 响应验证失败");
        Console.WriteLine("Bridge validation passed: plain diagnostics, malformed JSON and arrays rejected; valid Unicode response delivered.");
        if (!await JsBool("document.querySelectorAll('[data-option]').length === 30 && document.querySelector('#daily #option-task_rewards') !== null && document.querySelector('#alt-account #option-help_clicker') !== null && document.querySelector('#training #option-heal') !== null && document.querySelector('#wilderness #option-hunt') !== null && document.querySelector('#hunt-level-beast').options.length === 30 && JSON.stringify(Array.from(document.querySelector('#hunt-level-polar_beast').options, option => option.value)) === JSON.stringify(['2级','3级','4级','5级','6级'])"))
            throw new InvalidOperationException("任务控件不完整");
        await view.CoreWebView2.ExecuteScriptAsync(@"
            window.checkedUpdates = false;
            (async () => {
                const api = window.pywebview.api;
                const original = await api.get_state();
                const saved = {get_state:api.get_state, check_updates:api.check_updates,
                    download_update:api.download_update, cancel_update_download:api.cancel_update_download, install_update:api.install_update};
                const fixture = {...original, update:{version:'1.0.0', phase:'idle', message:'尚未检查更新',
                    installable:true, new_version:'', notes:'', downloaded:0, total:100}};
                let checked = false, downloaded = false, cancelled = false, installed = false;
                try {
                    api.get_state = async () => fixture;
                    api.check_updates = async () => {checked=true; Object.assign(fixture.update, {
                        phase:'available', new_version:'1.0.1', notes:'<script>unsafe()</script>', message:'发现新版本'}); return fixture;};
                    api.download_update = async () => {downloaded=true; fixture.update.phase='downloading'; return fixture;};
                    api.cancel_update_download = async () => {cancelled=true; fixture.update.phase='available'; return fixture;};
                    api.install_update = async () => {installed=true; fixture.update.phase='installing'; return {};};
                    render(fixture);
                    const badge = document.getElementById('check-updates');
                    const label = document.getElementById('app-version');
                    const height = badge.getBoundingClientRect().height;
                    if (document.getElementById('new-version') || document.getElementById('update-dialog').open)
                        throw new Error('旧的标题栏更新入口仍存在');
                    let finishCheck;
                    api.check_updates = () => {checked=true; return new Promise(resolve => {finishCheck=resolve;});};
                    badge.click();
                    if (!badge.disabled || !label.hidden || badge.querySelector('.version-spinner').hidden ||
                        getComputedStyle(badge.querySelector('.version-spinner')).animationName !== 'version-spin' ||
                        badge.getBoundingClientRect().height !== height) throw new Error('检查中动画或高度错误');
                    Object.assign(fixture.update, {phase:'current', message:'当前已是最新正式版本'});
                    finishCheck(fixture); await new Promise(resolve => setTimeout(resolve, 0));
                    if (label.textContent !== '已是最新版本' || label.hidden || badge.getBoundingClientRect().height !== height)
                        throw new Error('最新版本提示或高度错误');
                    await new Promise(resolve => setTimeout(resolve, 3100)); render(fixture);
                    if (label.textContent !== 'v1.0.0') throw new Error('三秒后未恢复版本号');
                    fixture.update.phase='error'; fixture.update.message='网络连接失败'; render(fixture); render(fixture);
                    if (label.textContent !== '网络连接失败' || getComputedStyle(badge).color !== 'rgb(231, 71, 79)')
                        throw new Error('异常红字未持续显示');
                    api.check_updates = async () => {throw new Error('桥接请求异常');};
                    await checkUpdates(); render(fixture);
                    if (label.textContent !== '桥接请求异常' || badge.disabled) throw new Error('请求异常未显示或不可重试');
                    api.check_updates = async () => {checked=true; Object.assign(fixture.update, {
                        phase:'available', new_version:'1.0.1', notes:'<script>unsafe()</script>', message:'发现新版本'}); return fixture;};
                    await checkUpdates(); render(fixture);
                    if (!checked || label.textContent !== '有新版本：v1.0.1' ||
                        document.getElementById('update-dialog').open || document.querySelector('#update-notes script') ||
                        document.getElementById('update-notes').textContent !== '<script>unsafe()</script>' ||
                        getComputedStyle(badge).color !== 'rgb(231, 71, 79)') throw new Error('新版本红字或更新说明错误');
                    const recentCheck = lastUpdateCheck;
                    checked=false; checkUpdatesIfDue();
                    if (checked) throw new Error('未到每日间隔却重复检查');
                    const savedVisibility = Object.getOwnPropertyDescriptor(document, 'visibilityState');
                    try {
                        lastUpdateCheck = Date.now() - autoUpdateInterval;
                        Object.defineProperty(document, 'visibilityState', {configurable:true, value:'hidden'});
                        checkUpdatesIfDue();
                        if (checked) throw new Error('窗口后台仍自动检测');
                        Object.defineProperty(document, 'visibilityState', {configurable:true, value:'visible'});
                        checkUpdatesIfDue(); await new Promise(resolve => setTimeout(resolve, 0));
                        if (!checked) throw new Error('窗口可见时每日检测未触发');
                    } finally {
                        if (savedVisibility) Object.defineProperty(document, 'visibilityState', savedVisibility);
                        else delete document.visibilityState;
                        lastUpdateCheck = recentCheck;
                    }
                    badge.click();
                    if (!document.getElementById('update-dialog').open || document.getElementById('update-version').textContent !== 'v1.0.1' ||
                        document.getElementById('update-action').textContent !== '更新') throw new Error('新版弹窗或更新按钮缺失');
                    document.getElementById('update-cancel').click(); render(fixture);
                    if (document.getElementById('update-dialog').open || downloaded || installed || label.textContent !== '有新版本：v1.0.1')
                        throw new Error('取消弹窗触发了更新或隐藏了新版入口');
                    badge.click();
                    document.getElementById('update-action').click(); await mutation;
                    fixture.update.downloaded=50; render(fixture);
                    if (!downloaded || document.getElementById('update-progress').hidden ||
                        document.getElementById('update-progress').value !== 50 || !document.getElementById('check-updates').disabled)
                        throw new Error('下载按钮、进度或重复检查保护错误');
                    document.getElementById('update-cancel').click(); await mutation;
                    if (!cancelled || document.getElementById('update-dialog').open || fixture.update.phase !== 'available')
                        throw new Error('取消下载未传递到后台');
                    badge.click(); document.getElementById('update-action').click(); await mutation;
                    fixture.update.phase='error'; fixture.update.message='下载连接中断'; render(fixture);
                    if (document.getElementById('update-message').hidden || document.getElementById('update-action').disabled)
                        throw new Error('下载错误或重试按钮缺失');
                    fixture.update.phase='ready'; render(fixture);
                    if (document.getElementById('update-action').textContent !== '安装并重启') throw new Error('安装按钮缺失');
                    document.getElementById('update-action').click(); await mutation; render(fixture);
                    if (!installed || !document.getElementById('update-action').disabled) throw new Error('安装操作或重复安装保护错误');
                    fixture.update.phase='available'; fixture.update.installable=false; render(fixture);
                    if (!document.getElementById('update-action').disabled) throw new Error('源码模式允许自动安装');
                    window.checkedUpdates=true;
                } finally {document.getElementById('update-dialog').close(); Object.assign(api, saved); render(original);}
            })().catch(error => {window.checkedUpdates=String(error);});");
        for (int i = 0; i < 200 && !await JsBool("window.checkedUpdates === true"); i++) await Task.Delay(50);
        if (!await JsBool("window.checkedUpdates === true"))
            throw new InvalidOperationException("更新界面自检失败：" + await view.CoreWebView2.ExecuteScriptAsync("window.checkedUpdates"));
        Console.WriteLine("Update UI checks passed: spinner and fixed height, 3-second current status, persistent errors/new version, daily visible-window checks, modal, download/cancel/retry/install and source mode guards.");
        await view.CoreWebView2.ExecuteScriptAsync(@"
            window.checkedBear = false;
            (async () => {
                const original = formConfig();
                const input = document.getElementById('option-bear');
                const drive = document.getElementById('option-bear_drive');
                const follow = document.getElementById('option-bear_follow');
                const noHeroes = document.getElementById('option-bear_follow_no_heroes');
                const settle = async () => { await new Promise(resolve => setTimeout(resolve, 180)); await mutation; };
                try {
                    if (input.checked || drive.checked || follow.checked || !noHeroes.checked ||
                        !drive.disabled || !follow.disabled || !noHeroes.disabled ||
                        document.querySelector('#wilderness .info').title !== '开启时其他任务不执行, 只专注打熊')
                        throw new Error('打熊默认值或提示错误');
                    input.click();
                    follow.click();
                    await settle();
                    const state = await window.pywebview.api.get_state();
                    if (!state.config.options.bear || state.config.options.bear_drive || !state.config.options.bear_follow ||
                        state.config.options.bear_follow_no_heroes || noHeroes.checked ||
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
                let verified = false;
                try {
                    renderGatherSteps([{resource:'wood',level:'7级'}, {resource:'iron',level:'8级'}, {resource:'wood',level:'4级'}]);
                    syncConfig();
                    while (gatherSteps().length < 6) resourceList.querySelector('[data-gather-action=add]').click();
                    if (resourceList.querySelector('[data-gather-action=add]') ||
                        resourceList.querySelectorAll('.gather-step').length !== 6 ||
                        resourceList.querySelectorAll('.gather-arrow').length !== 5)
                        throw new Error('六项上限或箭头失败');
                    while (gatherSteps().length > 1) resourceList.querySelector('[data-gather-action=remove]').click();
                    if (resourceList.querySelector('[data-gather-action=remove]') ||
                        resourceList.querySelector('.gather-arrow') || gatherSteps()[0].level !== '7级')
                        throw new Error('最少一项或删除箭头失败');
                    resourceList.querySelector('[data-gather-action=add]').click();
                    const input = resourceList.querySelectorAll('[data-gather-level]')[1];
                    input.value = '4级';
                    input.dispatchEvent(new Event('change', {bubbles:true}));
                    await new Promise(resolve => setTimeout(resolve, 180));
                    await mutation;
                    const state = await window.pywebview.api.get_state();
                    const expected = [{resource:'wood',level:'7级'}, {resource:'meat',level:'4级'}];
                    window.checkedLevelDetails = JSON.stringify({backend:state.config.gather_steps, form:gatherSteps()});
                    const matches = steps => steps.length === expected.length && steps.every((step, index) =>
                        step.resource === expected[index].resource && step.level === expected[index].level);
                    verified = matches(state.config.gather_steps) && matches(gatherSteps());
                } finally {
                    clearTimeout(syncTimer);
                    await window.pywebview.api.update_config(original);
                    loadConfig(original);
                }
                window.checkedLevels = verified;
            })().catch(error => {window.checkedLevels = 'failed'; window.checkedLevelDetails += String(error);});");
        for (int i = 0; i < 100 && !await JsBool("window.checkedLevels === true"); i++) await Task.Delay(50);
        if (!await JsBool("window.checkedLevels === true")) throw new InvalidOperationException("资源采集动态子选项或 Python 顺序同步失败：" + await view.CoreWebView2.ExecuteScriptAsync("window.checkedLevelDetails"));
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
                const saved = {select_device: api.select_device,
                    start: api.start, refresh_devices: api.refresh_devices, update_config: api.update_config, set_device_note: api.set_device_note};
                const fixture = {...original, selected: 'serial:127.0.0.1:62001', devices: [
                    {id:'serial:127.0.0.1:62001', key:'serial:127.0.0.1:62001', name:'夜神', avd:'', serial:'emulator-test', status:'running'},
                    {id:'serial:127.0.0.1:62025', key:'serial:127.0.0.1:62025', name:'夜神 2', avd:'', serial:'', status:'stopped'},
                    {id:'serial:PHONE123', key:'serial:PHONE123', name:'Pixel 7', kind:'physical', avd:'', serial:'PHONE123', status:'running'}]};
                let launched = '', restarted = '', refreshed = false, updated = false;
                try {
                    api.select_device = async id => { fixture.selected = id; return fixture; };
                    api.start = async () => { throw new Error('模拟器 Pixel_5_2 未启动'); };
                    api.refresh_devices = async () => { refreshed = true; return fixture; };
                    api.update_config = async () => { updated = true; return fixture; };
                    api.set_device_note = async (id, note) => { fixture.devices.find(d => d.id === id).note = note.trim(); return fixture; };
                    render(fixture);
                    document.getElementById('device').click();
                    if (document.getElementById('device-list').hidden || document.querySelectorAll('.device-choice').length !== 3 ||
                        document.querySelectorAll('.device-launch').length !== 0 ||
                        document.querySelectorAll('.device-restart').length !== 0 ||
                        !document.getElementById('device-list').textContent.includes('未启动')) throw new Error('列表或启动按钮缺失');
                    const choices = document.querySelectorAll('.device-choice');
                    if (!choices[0].textContent.includes('模拟器') || !choices[2].textContent.includes('真机') ||
                        !choices[2].textContent.includes('已连接') || choices[2].parentElement.querySelector('.device-restart'))
                        throw new Error('真机类型、状态或按钮错误');
                    document.querySelectorAll('.device-note')[2].click();
                    if (!document.getElementById('device-note-dialog').open) throw new Error('备注窗口未打开');
                    document.getElementById('device-note-input').value = '小号 <手机>';
                    document.getElementById('device-note-save').click();
                    await mutation;
                    if (document.getElementById('device-note-dialog').open ||
                        document.querySelectorAll('.device-choice')[2].querySelector('.device-note-text').textContent !== '小号 <手机>' ||
                        selected !== 'serial:127.0.0.1:62001' || updated) throw new Error('备注保存修改了选择或任务配置');
                    fixture.devices[2].status = 'unauthorized'; render(fixture);
                    if (!document.querySelectorAll('.device-choice')[2].textContent.includes('待授权')) throw new Error('真机授权状态缺失');
                    fixture.devices[2].status = 'offline'; render(fixture);
                    if (!document.querySelectorAll('.device-choice')[2].textContent.includes('离线')) throw new Error('真机离线状态缺失');
                    document.querySelectorAll('.device-choice')[1].click();
                    await mutation;
                    if (selected !== 'serial:127.0.0.1:62025' || document.getElementById('run').disabled) throw new Error('未启动模拟器选择失败');
                    document.getElementById('run').click();
                    await mutation;
                    if (!document.getElementById('notice').open || !document.getElementById('notice-message').textContent.includes('未启动'))
                        throw new Error('未启动提示缺失');
                    document.getElementById('notice').close();
                    document.getElementById('device').click();
                    fixture.devices[0].status = 'running';
                    fixture.running = true;
                    render(fixture);
                    if (document.querySelector('.device-launch') || document.querySelector('.device-restart')) throw new Error('旧模拟器操作仍然存在');
                    if (document.getElementById('refresh').disabled) throw new Error('运行中刷新不可用');
                    document.getElementById('refresh').click();
                    await mutation;
                    if (!refreshed || updated) throw new Error('运行中刷新修改了任务配置');
                    window.checkedEmulatorPicker = true;
                } finally {
                    Object.assign(api, saved);
                    document.getElementById('notice').close();
                    document.getElementById('device-note-dialog').close();
                    closeDevices();
                    render(original);
                }
            })().catch(error => {window.checkedEmulatorPicker = String(error);});");
        for (int i = 0; i < 100 && !await JsBool("window.checkedEmulatorPicker === true"); i++) await Task.Delay(50);
        if (!await JsBool("window.checkedEmulatorPicker === true")) throw new InvalidOperationException("模拟器下拉列表自检失败：" + await view.CoreWebView2.ExecuteScriptAsync("window.checkedEmulatorPicker"));
        await CheckLogInterface();
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
        Console.WriteLine("Desktop EXE checks passed: images, 30 controls, bear defaults/tooltip/checkboxes/Python sync/restore/exclusive dependencies, help-clicker default/tooltip/yes/no/cancel/window state/exclusive dependencies, independent auto-healing switch, monster levels/Python sync/last selection guard, dynamic gather steps/limits/arrows/Python order sync, Python bridge, emulator inventory/status/launch/restart/refresh, validation, log deduplication, task status/deadlines/single-task retry/stop guard, dependencies, 120 resizes, 1/2/3 waterfall columns with 10px gaps. Resize elapsed ms: " + clock.ElapsedMilliseconds + "; frame samples: " + frames);
    }
}

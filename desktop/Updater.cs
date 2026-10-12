using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;
using System.Windows.Forms;

internal sealed class UpdateTransaction
{
    internal readonly string Target, Stage, Payload;
    internal readonly string Version;
    private readonly string backup;
    private readonly List<string> names = new List<string>();
    private readonly HashSet<string> originals = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
    private readonly List<string> installed = new List<string>();
    private readonly JavaScriptSerializer json = new JavaScriptSerializer { MaxJsonLength = 8 * 1024 * 1024 };

    internal static bool Allowed(string name)
    {
        return new[] { "runtime", "assets", ".tools", "scripts", "GameBot.exe", "GameBot.Updater.exe",
                       "README.md", "requirements-bundled.txt", "release.json" }.Contains(name) ||
               System.Text.RegularExpressions.Regex.IsMatch(name, @"^[A-Za-z_]\w*\.py$");
    }

    internal UpdateTransaction(string planPath)
    {
        planPath = Path.GetFullPath(planPath);
        Stage = Path.GetDirectoryName(planPath);
        var plan = json.Deserialize<Dictionary<string, object>>(File.ReadAllText(planPath, Encoding.UTF8));
        Target = Path.GetFullPath(Convert.ToString(plan["target"])).TrimEnd(Path.DirectorySeparatorChar);
        Payload = Path.GetFullPath(Convert.ToString(plan["payload"]));
        Version = Convert.ToString(plan["version"]);
        if (Path.GetDirectoryName(Stage) != Target || !Path.GetFileName(Stage).StartsWith(".update-stage-") ||
            Payload != Path.Combine(Stage, "payload") || Convert.ToString(plan["repository"]) != "sancheo/wjdr-GameBot" ||
            Convert.ToInt32(plan["update_protocol"]) != 1 ||
            !System.Text.RegularExpressions.Regex.IsMatch(Version, @"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$"))
            throw new InvalidOperationException("更新计划不属于当前程序目录或版本协议不正确。");
        backup = Path.Combine(Stage, "backup");
        if (Directory.Exists(backup)) throw new InvalidOperationException("这个更新已开始过，请勿重复执行。");
        var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        var items = (System.Collections.IEnumerable)plan["files"];
        int count = 0;
        foreach (Dictionary<string, object> item in items)
        {
            if (++count > 30000) throw new InvalidOperationException("更新文件数量超限。");
            string relative = Convert.ToString(item["path"]);
            string[] parts = relative.Split('/');
            if (!Allowed(parts[0]) || (parts.Length > 1 && !new[] { "runtime", "assets", ".tools", "scripts" }.Contains(parts[0])) ||
                parts.Any(p => p.Length == 0 || p == "." || p == ".." || p.EndsWith(".") || p.EndsWith(" ") || p.IndexOfAny(Path.GetInvalidFileNameChars()) >= 0) ||
                !seen.Add(relative)) throw new InvalidOperationException("更新计划包含不安全或重复的文件路径。");
            string source = Path.GetFullPath(Path.Combine(Payload, relative.Replace('/', Path.DirectorySeparatorChar)));
            if (!source.StartsWith(Payload + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase))
                throw new InvalidOperationException("更新文件超出准备目录。");
            for (string path = source; path != Payload; path = Path.GetDirectoryName(path))
                if ((File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0)
                    throw new InvalidOperationException("更新目录不能包含链接。");
            using (var stream = File.OpenRead(source))
            using (var sha = SHA256.Create())
            {
                string hash = BitConverter.ToString(sha.ComputeHash(stream)).Replace("-", "").ToLowerInvariant();
                if (stream.Length != Convert.ToInt64(item["size"]) || hash != Convert.ToString(item["sha256"]).ToLowerInvariant())
                    throw new InvalidOperationException("更新文件校验失败：" + relative);
            }
            if (!names.Contains(parts[0])) names.Add(parts[0]);
        }
        if (!seen.Contains("GameBot.exe") || !seen.Contains("release.json") || !seen.Contains("runtime/python/python.exe"))
            throw new InvalidOperationException("更新计划缺少入口或运行环境。");
        var metadata = json.Deserialize<Dictionary<string, object>>(File.ReadAllText(Path.Combine(Payload, "release.json"), Encoding.UTF8));
        if (Convert.ToString(metadata["version"]) != Version || Convert.ToString(metadata["repository"]) != "sancheo/wjdr-GameBot" ||
            Convert.ToString(metadata["platform"]) != "windows-x64" || Convert.ToInt32(metadata["update_protocol"]) != 1)
            throw new InvalidOperationException("更新包身份与计划不一致。");
        names.Sort(StringComparer.OrdinalIgnoreCase);
    }

    private static bool Exists(string path) { return File.Exists(path) || Directory.Exists(path); }
    private static void Move(string source, string target)
    {
        for (int attempt = 0; ; attempt++)
        {
            try
            {
                if (Directory.Exists(source)) Directory.Move(source, target);
                else File.Move(source, target);
                return;
            }
            catch (IOException) { if (attempt == 9) throw; Thread.Sleep(500); }
        }
    }
    private static void Remove(string path)
    {
        for (int attempt = 0; ; attempt++)
        {
            try
            {
                if (Directory.Exists(path)) Directory.Delete(path, true);
                else if (File.Exists(path)) File.Delete(path);
                return;
            }
            catch (IOException) { if (attempt == 9) throw; Thread.Sleep(500); }
            catch (UnauthorizedAccessException) { if (attempt == 9) throw; Thread.Sleep(500); }
        }
    }

    internal void Apply(Action validateInstalled)
    {
        Directory.CreateDirectory(backup);
        try
        {
            foreach (string name in names)
            {
                string target = Path.Combine(Target, name);
                if (Exists(target))
                {
                    Move(target, Path.Combine(backup, name));
                    originals.Add(name);
                }
                Move(Path.Combine(Payload, name), target);
                installed.Add(name);
            }
            validateInstalled();
        }
        catch (Exception original)
        {
            try
            {
                foreach (string name in names.AsEnumerable().Reverse())
                {
                    string target = Path.Combine(Target, name);
                    if (installed.Contains(name)) Remove(target);
                    if (originals.Contains(name)) Move(Path.Combine(backup, name), target);
                }
            }
            catch (Exception recovery)
            {
                throw new InvalidOperationException("更新失败，恢复旧版也失败。旧文件保存在：" + backup + "\n" + recovery.Message, original);
            }
            throw new InvalidOperationException("更新失败，已恢复旧版：" + original.Message, original);
        }
    }
}

internal static class UpdaterProgram
{
    [STAThread]
    private static void Main(string[] args)
    {
        Application.EnableVisualStyles();
        if (args.Length != 2) { MessageBox.Show("请在 GameBot 界面点击安装更新。", "GameBot 更新"); return; }
        UpdateTransaction transaction = null;
        bool parentExited = false;
        using (var window = new Form { Text = "GameBot 更新", Width = 420, Height = 145,
            StartPosition = FormStartPosition.CenterScreen, FormBorderStyle = FormBorderStyle.FixedDialog,
            MaximizeBox = false, MinimizeBox = false, ControlBox = false })
        {
            var label = new Label { Dock = DockStyle.Fill, TextAlign = System.Drawing.ContentAlignment.MiddleCenter,
                Text = "正在校验更新文件并等待程序退出…" };
            window.Controls.Add(label);
            window.Shown += async delegate
            {
                try
                {
                    await System.Threading.Tasks.Task.Run(delegate
                    {
                        int pid = Int32.Parse(args[1]);
                        transaction = new UpdateTransaction(args[0]);
                        try
                        {
                            using (var parent = Process.GetProcessById(pid))
                            {
                                if (!String.Equals(Path.GetDirectoryName(parent.MainModule.FileName), transaction.Target, StringComparison.OrdinalIgnoreCase))
                                    throw new InvalidOperationException("等待退出的进程不属于当前程序目录。");
                                if (!parent.WaitForExit(45000)) throw new InvalidOperationException("原程序未退出，未修改文件。");
                            }
                        }
                        catch (ArgumentException) { /* The parent has already exited. */ }
                        parentExited = true;
                        transaction.Apply(delegate { CheckStartup(transaction.Target); Restart(transaction.Target); });
                    });
                    File.WriteAllText(Path.Combine(transaction.Stage, "completed"), transaction.Version);
                    window.Close();
                }
                catch (Exception error)
                {
                    Environment.ExitCode = 1;
                    MessageBox.Show(window, error.Message, "GameBot 更新失败", MessageBoxButtons.OK, MessageBoxIcon.Error);
                    if (parentExited && transaction != null && File.Exists(Path.Combine(transaction.Target, "GameBot.exe")))
                        try { Restart(transaction.Target); } catch (Exception) { }
                    window.Close();
                }
            };
            Application.Run(window);
        }
    }

    internal static void CheckStartup(string root)
    {
        var start = new ProcessStartInfo(Path.Combine(root, "GameBot.exe"), "--check-startup") {
            WorkingDirectory = root, UseShellExecute = false, CreateNoWindow = true,
            RedirectStandardOutput = true, RedirectStandardError = true,
            StandardOutputEncoding = Encoding.UTF8, StandardErrorEncoding = Encoding.UTF8 };
        start.EnvironmentVariables.Remove("GAMEBOT_RESUME_KEY");
        using (var process = Process.Start(start))
        {
            var output = process.StandardOutput.ReadToEndAsync();
            var error = process.StandardError.ReadToEndAsync();
            if (!process.WaitForExit(60000)) { process.Kill(); throw new InvalidOperationException("新版启动检查超时。"); }
            if (process.ExitCode != 0 || !output.Result.Contains("GameBot startup check passed."))
                throw new InvalidOperationException("新版启动检查失败：" + error.Result);
        }
    }
    private static void Restart(string root)
    {
        var start = new ProcessStartInfo(Path.Combine(root, "GameBot.exe")) { WorkingDirectory = root, UseShellExecute = false };
        start.EnvironmentVariables.Remove("GAMEBOT_RESUME_KEY");
        Process.Start(start);
    }
}

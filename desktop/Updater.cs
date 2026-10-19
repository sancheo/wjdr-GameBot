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
    private readonly FileTransaction fileTransaction;

    static UpdateTransaction() { LongIO.Enable(); }
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
        var plan = json.Deserialize<Dictionary<string, object>>(LongIO.ReadAllText(planPath, Encoding.UTF8));
        Target = Path.GetFullPath(Convert.ToString(plan["target"])).TrimEnd(Path.DirectorySeparatorChar);
        Payload = Path.GetFullPath(Convert.ToString(plan["payload"]));
        Version = Convert.ToString(plan["version"]);
        if (Path.GetDirectoryName(Stage) != Target || !(Path.GetFileName(Stage).StartsWith(".update-stage-") ||
                System.Text.RegularExpressions.Regex.IsMatch(Path.GetFileName(Stage), @"^\.u-[0-9a-f]{12}$")) ||
            Payload != Path.Combine(Stage, "payload") || Convert.ToString(plan["repository"]) != "sancheo/wjdr-GameBot" ||
            Convert.ToInt32(plan["update_protocol"]) != 1 ||
            !System.Text.RegularExpressions.Regex.IsMatch(Version, @"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$"))
            throw new InvalidOperationException("更新计划不属于当前程序目录或版本协议不正确。");
        backup = Path.Combine(Stage, "backup");
        if (LongIO.DirectoryExists(backup)) throw new InvalidOperationException("这个更新已开始过，请勿重复执行。");
        if (plan.ContainsKey("mode") && Convert.ToString(plan["mode"]) == "files")
        { fileTransaction = new FileTransaction(plan, Target, Payload, backup, Version); return; }
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
                if ((LongIO.GetAttributes(path) & FileAttributes.ReparsePoint) != 0)
                    throw new InvalidOperationException("更新目录不能包含链接。");
            using (var stream = LongIO.OpenRead(source))
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
        var metadata = json.Deserialize<Dictionary<string, object>>(LongIO.ReadAllText(Path.Combine(Payload, "release.json"), Encoding.UTF8));
        if (Convert.ToString(metadata["version"]) != Version || Convert.ToString(metadata["repository"]) != "sancheo/wjdr-GameBot" ||
            Convert.ToString(metadata["platform"]) != "windows-x64" || Convert.ToInt32(metadata["update_protocol"]) != 1)
            throw new InvalidOperationException("更新包身份与计划不一致。");
        names.Sort(StringComparer.OrdinalIgnoreCase);
    }

    private static bool Exists(string path) { return LongIO.FileExists(path) || LongIO.DirectoryExists(path); }
    private static void Move(string source, string target)
    {
        for (int attempt = 0; ; attempt++)
        {
            try
            {
                if (LongIO.DirectoryExists(source)) LongIO.MoveDirectory(source, target);
                else LongIO.MoveFile(source, target);
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
                if (LongIO.DirectoryExists(path)) LongIO.DeleteDirectory(path, true);
                else if (LongIO.FileExists(path)) LongIO.DeleteFile(path);
                return;
            }
            catch (IOException) { if (attempt == 9) throw; Thread.Sleep(500); }
            catch (UnauthorizedAccessException) { if (attempt == 9) throw; Thread.Sleep(500); }
        }
    }

    internal void Apply(Action validateInstalled)
    {
        if (fileTransaction != null) { fileTransaction.Apply(validateInstalled); return; }
        LongIO.CreateDirectory(backup);
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

internal static class LongIO
{
    internal static void Enable()
    {
        AppContext.SetSwitch("Switch.System.IO.UseLegacyPathHandling", false);
        AppContext.SetSwitch("Switch.System.IO.BlockLongPaths", false);
    }
    private static string Native(string path)
    {
        if (path.StartsWith(@"\\?\")) return path;
        string full = Path.GetFullPath(path);
        return full.StartsWith(@"\\") ? @"\\?\UNC\" + full.Substring(2) : @"\\?\" + full;
    }
    internal static string ReadAllText(string p, Encoding encoding) { return File.ReadAllText(Native(p), encoding); }
    internal static FileAttributes GetAttributes(string p) { return File.GetAttributes(Native(p)); }
    internal static FileStream OpenRead(string p) { return File.OpenRead(Native(p)); }
    internal static bool FileExists(string p) { return File.Exists(Native(p)); }
    internal static bool DirectoryExists(string p) { return Directory.Exists(Native(p)); }
    internal static void CreateDirectory(string p) { Directory.CreateDirectory(Native(p)); }
    internal static void MoveDirectory(string a, string b) { Directory.Move(Native(a), Native(b)); }
    internal static void MoveFile(string a, string b) { File.Move(Native(a), Native(b)); }
    internal static void DeleteDirectory(string p, bool recursive) { Directory.Delete(Native(p), recursive); }
    internal static void DeleteFile(string p) { File.Delete(Native(p)); }
    internal static void WriteAllText(string p, string text) { File.WriteAllText(Native(p), text); }
}

internal sealed class FileTransaction
{
    private sealed class Item
    {
        internal string Path, Source, Hash;
        internal long Size;
    }
    private readonly string target, payload, backup;
    private readonly List<Item> changed = new List<Item>(), unchanged = new List<Item>();
    private readonly List<string> deleted = new List<string>(), originals = new List<string>(), installed = new List<string>();
    private readonly List<string> createdDirectories = new List<string>();
    private readonly Dictionary<string, string> backups = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
    private readonly HashSet<string> seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);

    internal FileTransaction(Dictionary<string, object> plan, string root, string source, string recovery, string version)
    {
        target = root; payload = source; backup = recovery;
        foreach (string key in new[] { "files", "unchanged" })
        {
            foreach (Dictionary<string, object> raw in (System.Collections.IEnumerable)plan[key])
            {
                string relative = SafePath(Convert.ToString(raw["path"]));
                if (seen.Count >= 30000 || !seen.Add(relative)) throw new InvalidOperationException("文件清单重复或数量超限。");
                var item = new Item { Path = relative, Size = Convert.ToInt64(raw["size"]), Hash = Convert.ToString(raw["sha256"]) };
                if (item.Size < 0 || !System.Text.RegularExpressions.Regex.IsMatch(item.Hash, "^[0-9a-f]{64}$"))
                    throw new InvalidOperationException("文件大小或校验值不正确。");
                if (key == "files")
                {
                    string staged = Convert.ToString(raw["staged"]);
                    if (!System.Text.RegularExpressions.Regex.IsMatch(staged, "^[0-9]{1,5}$"))
                        throw new InvalidOperationException("更新暂存文件名不正确。");
                    item.Source = Path.Combine(payload, staged);
                    changed.Add(item);
                }
                else { item.Source = Path.Combine(target, relative.Replace('/', '\\')); unchanged.Add(item); }
                CheckParents(target, relative);
                Verify(item, key == "files" ? payload : target);
            }
        }
        foreach (object value in (System.Collections.IEnumerable)plan["delete"])
        {
            string relative = SafePath(Convert.ToString(value));
            if (seen.Count >= 30000 || !seen.Add(relative)) throw new InvalidOperationException("删除路径与更新文件冲突。");
            CheckParents(target, relative);
            deleted.Add(relative);
        }
        // File/directory aliases must be rejected before any originals move.
        foreach (string path in seen)
        {
            int slash = path.IndexOf('/');
            while (slash >= 0)
            {
                if (seen.Contains(path.Substring(0, slash))) throw new InvalidOperationException("更新文件路径层级冲突。");
                slash = path.IndexOf('/', slash + 1);
            }
        }
        string[] required = { "GameBot.exe", "GameBot.Updater.exe", "release.json", "app_updates.py", "gui_web.py",
            "help_bot.py", "app_version.py", "README.md", "runtime/python/python.exe", "runtime/webview2/msedgewebview2.exe",
            "runtime/webview-sdk/Microsoft.Web.WebView2.Core.dll", "assets/desktop.html", ".tools/platform-tools/adb.exe",
            ".tools/platform-tools/AdbWinApi.dll", ".tools/platform-tools/AdbWinUsbApi.dll" };
        var incoming = new HashSet<string>(changed.Concat(unchanged).Select(i => i.Path), StringComparer.OrdinalIgnoreCase);
        if (required.Any(p => !incoming.Contains(p))) throw new InvalidOperationException("文件清单缺少程序入口或运行环境。");
        var metadataFile = changed.Concat(unchanged).Single(i => i.Path == "release.json");
        var metadata = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(LongIO.ReadAllText(metadataFile.Source, Encoding.UTF8));
        if (Convert.ToString(metadata["version"]) != version || Convert.ToString(metadata["repository"]) != "sancheo/wjdr-GameBot" ||
            Convert.ToString(metadata["platform"]) != "windows-x64" || Convert.ToInt32(metadata["update_protocol"]) != 1)
            throw new InvalidOperationException("文件更新版本或身份不一致。");
    }

    private static string SafePath(string path)
    {
        string[] parts = path.Split('/');
        if (!UpdateTransaction.Allowed(parts[0]) ||
            (parts.Length > 1 && !new[] { "runtime", "assets", ".tools", "scripts" }.Contains(parts[0])) ||
            parts.Any(p => p.Length == 0 || p == "." || p == ".." || p.EndsWith(".") || p.EndsWith(" ") ||
                p.IndexOfAny(Path.GetInvalidFileNameChars()) >= 0 ||
                System.Text.RegularExpressions.Regex.IsMatch(p, @"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\..*)?$", System.Text.RegularExpressions.RegexOptions.IgnoreCase)))
            throw new InvalidOperationException("文件更新包含不安全的路径。");
        return path;
    }

    private static void CheckParents(string root, string relative)
    {
        string path = Path.Combine(root, relative.Replace('/', '\\'));
        for (string current = path; current != root; current = Path.GetDirectoryName(current))
        {
            if (LongIO.FileExists(current) || LongIO.DirectoryExists(current))
            {
                if ((LongIO.GetAttributes(current) & FileAttributes.ReparsePoint) != 0 ||
                    (current == path ? LongIO.DirectoryExists(current) : LongIO.FileExists(current)))
                    throw new InvalidOperationException("文件更新目录包含链接或文件类型冲突：" + relative);
            }
        }
        if ((LongIO.GetAttributes(root) & FileAttributes.ReparsePoint) != 0)
            throw new InvalidOperationException("程序或暂存根目录不能是链接。");
    }

    private static void Verify(Item item, string root)
    {
        CheckParents(root, item.Source.Substring(root.Length + 1).Replace('\\', '/'));
        using (var stream = LongIO.OpenRead(item.Source))
        using (var sha = SHA256.Create())
            if (stream.Length != item.Size || BitConverter.ToString(sha.ComputeHash(stream)).Replace("-", "").ToLowerInvariant() != item.Hash)
                throw new InvalidOperationException("文件已变化或校验失败：" + item.Path);
    }

    private void EnsureDirectory(string directory)
    {
        if (LongIO.DirectoryExists(directory)) return;
        EnsureDirectory(Path.GetDirectoryName(directory));
        LongIO.CreateDirectory(directory);
        createdDirectories.Add(directory);
    }

    internal void Apply(Action validateInstalled)
    {
        // Existing files may have changed since download, or while waiting for exit.
        foreach (Item item in unchanged) Verify(item, target);
        foreach (Item item in changed) { CheckParents(target, item.Path); Verify(item, payload); }
        foreach (string path in deleted) CheckParents(target, path);
        LongIO.CreateDirectory(backup);
        try
        {
            foreach (string relative in changed.Select(i => i.Path).Concat(deleted))
            {
                string destination = Path.Combine(target, relative.Replace('/', '\\'));
                if (LongIO.FileExists(destination))
                {
                    string saved = Path.Combine(backup, backups.Count.ToString());
                    LongIO.MoveFile(destination, saved); backups.Add(relative, saved); originals.Add(relative);
                }
            }
            foreach (Item item in changed)
            {
                string destination = Path.Combine(target, item.Path.Replace('/', '\\'));
                EnsureDirectory(Path.GetDirectoryName(destination));
                LongIO.MoveFile(item.Source, destination); installed.Add(item.Path);
            }
            validateInstalled();
        }
        catch (Exception error)
        {
            try
            {
                foreach (string relative in installed.AsEnumerable().Reverse())
                    LongIO.DeleteFile(Path.Combine(target, relative.Replace('/', '\\')));
                foreach (string relative in originals.AsEnumerable().Reverse())
                    LongIO.MoveFile(backups[relative], Path.Combine(target, relative.Replace('/', '\\')));
                foreach (string directory in createdDirectories.AsEnumerable().Reverse())
                    LongIO.DeleteDirectory(directory, false);
            }
            catch (Exception recovery)
            { throw new InvalidOperationException("更新失败，恢复旧版也失败。备份目录：" + backup + "\n" + recovery.Message, error); }
            throw new InvalidOperationException("更新失败，已恢复旧版：" + error.Message, error);
        }
    }
}

internal static class UpdaterProgram
{
    [STAThread]
    private static void Main(string[] args)
    {
        // WinForms can cache the legacy path switches during initialization.
        // Enable long paths before any WinForms or filesystem initialization.
        LongIO.Enable();
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
                    LongIO.WriteAllText(Path.Combine(transaction.Stage, "completed"), transaction.Version);
                    window.Close();
                }
                catch (Exception error)
                {
                    Environment.ExitCode = 1;
                    MessageBox.Show(window, error.Message, "GameBot 更新失败", MessageBoxButtons.OK, MessageBoxIcon.Error);
                    if (parentExited && transaction != null && LongIO.FileExists(Path.Combine(transaction.Target, "GameBot.exe")))
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

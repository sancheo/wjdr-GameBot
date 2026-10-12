"""Background GitHub Releases updates using only Python's standard library."""

from copy import deepcopy
import hashlib
import hmac
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import threading
from urllib.error import HTTPError
from urllib.parse import urlsplit, quote
from urllib.request import Request, build_opener, HTTPRedirectHandler
import uuid
import zipfile

from app_version import VERSION, REPOSITORY, UPDATE_PROTOCOL


MAX_DOWNLOAD = 2 * 1024**3
MAX_EXTRACTED = 4 * 1024**3
DATA_FOLDERS = {"configs", "logs", "artifacts", ".desktop-cache"}
PAYLOAD_FOLDERS = {"runtime", "assets", ".tools", "scripts"}
PAYLOAD_FILES = {"GameBot.exe", "GameBot.Updater.exe", "README.md",
                 "requirements-bundled.txt", "release.json"}
REQUIRED = {"GameBot.exe", "GameBot.Updater.exe", "gui_web.py", "help_bot.py", "app_updates.py",
            "app_version.py", "README.md", "release.json", "runtime/python/python.exe",
            "runtime/webview2/msedgewebview2.exe", "runtime/webview-sdk/Microsoft.Web.WebView2.Core.dll",
            "assets/desktop.html", ".tools/platform-tools/adb.exe", ".tools/platform-tools/AdbWinApi.dll",
            ".tools/platform-tools/AdbWinUsbApi.dll"}


def version_tuple(value):
    if not isinstance(value, str) or not re.fullmatch(r"v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", value):
        raise ValueError("版本号必须使用 v1.2.3 格式的正式版本")
    return tuple(map(int, value.removeprefix("v").split(".")))


def payload_name(name):
    return name in PAYLOAD_FILES or name in PAYLOAD_FOLDERS or bool(re.fullmatch(r"[A-Za-z_]\w*\.py", name))


def trusted_url(url):
    parsed = urlsplit(url)
    return (parsed.scheme == "https" and not parsed.username and not parsed.password
            and parsed.port in (None, 443) and parsed.hostname in
            {"api.github.com", "github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"})


class GithubRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, newurl):
        if not trusted_url(newurl):
            raise ValueError("下载地址跳转到了不支持的站点")
        return super().redirect_request(request, response, code, message, headers, newurl)


def open_url(url):
    if not trusted_url(url):
        raise ValueError("更新地址必须是 GitHub 的 HTTPS 地址")
    return build_opener(GithubRedirects()).open(Request(url, headers={
        "User-Agent": "GameBot-Updater", "Accept": "application/vnd.github+json" if
        urlsplit(url).hostname == "api.github.com" else "application/octet-stream",
        "X-GitHub-Api-Version": "2026-03-10",
    }), timeout=25)


def asset_url(asset, tag):
    url = asset.get("browser_download_url", "")
    prefix = f"https://github.com/{REPOSITORY}/releases/download/{quote(tag, safe='')}/"
    if not isinstance(url, str) or not url.startswith(prefix) or not trusted_url(url):
        raise ValueError("Release 附件不属于配置的 GitHub 仓库")
    return url


def choose_release(data, current):
    if not isinstance(data, dict):
        raise ValueError("GitHub 版本信息格式不正确")
    if data.get("draft") or data.get("prerelease"):
        return None
    tag = data.get("tag_name")
    version = version_tuple(tag)
    if version <= version_tuple(current):
        return None
    name = f"GameBot-Windows-x64-v{'.'.join(map(str, version))}.zip"
    assets = data.get("assets", [])
    matches = [a for a in assets if isinstance(a, dict) and a.get("name") == name and a.get("state") == "uploaded"]
    if len(matches) != 1:
        raise ValueError(f"新版本缺少便携更新包 {name}，请发布完整打包附件")
    asset = matches[0]
    size = asset.get("size")
    if not isinstance(size, int) or isinstance(size, bool) or not 0 < size <= MAX_DOWNLOAD:
        raise ValueError("更新包大小不正确或超过 2 GiB")
    digest = asset.get("digest")
    sha = digest[7:] if isinstance(digest, str) and re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest) else None
    checksum = next((a for a in assets if isinstance(a, dict) and a.get("name") == name + ".sha256"
                     and a.get("state") == "uploaded"), None)
    if not sha and checksum is None:
        raise ValueError("更新包缺少 SHA-256 校验信息，请同时上传 .zip.sha256 附件")
    return {"version": '.'.join(map(str, version)), "tag": tag, "name": name, "size": size,
            "url": asset_url(asset, tag), "sha256": sha,
            "checksum_url": asset_url(checksum, tag) if checksum else None,
            "notes": str(data.get("body") or "该版本未提供更新说明")[:6000]}


def extract_package(archive, destination, expected_version):
    """Validate the entire ZIP before extracting; user data may never be payload."""
    with zipfile.ZipFile(archive) as source:
        entries = source.infolist()
        if not entries or len(entries) > 30000 or sum(e.file_size for e in entries) > MAX_EXTRACTED:
            raise ValueError("更新包文件数量或解压大小不正确")
        roots, seen, files = set(), set(), []
        for entry in entries:
            # ZipInfo normalizes Windows separators and truncates NULs in
            # filename. Validate the original ZIP name before that conversion.
            raw = entry.orig_filename
            path = PurePosixPath(raw)
            parts = raw.rstrip('/').split('/')
            if (path.is_absolute() or '\\' in raw or len(parts) < 2 or
                    any(p in ('', '.', '..') or p.endswith((' ', '.')) or
                        any(ord(c) < 32 or c in ':<>"|?*' for c in p) or
                        re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", p) for p in parts)):
                raise ValueError("更新包包含不安全的文件路径")
            if stat.S_ISLNK(entry.external_attr >> 16):
                raise ValueError("更新包不能包含符号链接")
            roots.add(parts[0])
            relative = '/'.join(parts[1:])
            key = relative.casefold()
            if key in seen:
                raise ValueError("更新包包含重复文件路径")
            seen.add(key)
            if not payload_name(parts[1]) or (len(parts) > 2 and parts[1] not in PAYLOAD_FOLDERS):
                raise ValueError("更新包包含用户数据或不支持的顶层文件")
            if not entry.is_dir():
                files.append((entry, relative))
        if len(roots) != 1 or not next(iter(roots)).startswith('GameBot-Windows-x64-'):
            raise ValueError("更新包目录结构不正确")
        if not REQUIRED.issubset({relative for _, relative in files}):
            raise ValueError("更新包缺少程序或运行环境")
        metadata_entry = next(entry for entry, relative in files if relative == 'release.json')
        if metadata_entry.file_size > 16384:
            raise ValueError("版本元数据过大")
        metadata = json.loads(source.read(metadata_entry))
        if metadata != {"version": expected_version, "repository": REPOSITORY,
                        "platform": "windows-x64", "update_protocol": UPDATE_PROTOCOL}:
            raise ValueError("包内版本、平台或更新协议与 Release 不一致")
        destination.mkdir(parents=True, exist_ok=False)
        inventory = []
        for entry, relative in files:
            target = destination.joinpath(*relative.split('/'))
            target.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            with source.open(entry) as reader, target.open('xb') as writer:
                while chunk := reader.read(1024 * 1024):
                    writer.write(chunk)
                    digest.update(chunk)
            inventory.append({"path": relative, "sha256": digest.hexdigest(), "size": entry.file_size})
        return inventory


class UpdateManager:
    def __init__(self, root, *, enabled=True):
        self.root = Path(root).resolve()
        self.lock = threading.RLock()
        self.cancelled = threading.Event()
        self.release = None
        self.stage = None
        self.plan = None
        self.thread = None
        self.enabled = enabled
        self.installable = (self.root / 'runtime/python/python.exe').is_file() and (self.root / 'GameBot.Updater.exe').is_file()
        self.version = VERSION
        metadata_path = self.root / 'release.json'
        if metadata_path.is_file():
            try:
                metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
                version_tuple(metadata['version'])
                self.version = metadata['version']
            except (OSError, ValueError, KeyError, TypeError):
                self.installable = False
        self.state = {"version": self.version, "repository": REPOSITORY, "phase": "idle", "new_version": "",
                      "message": "尚未检查更新", "notes": "", "downloaded": 0, "total": 0,
                      "installable": self.installable}

    def snapshot(self):
        with self.lock:
            return deepcopy(self.state)

    def cleanup_completed(self):
        for stage in self.root.glob('.update-stage-*'):
            if not re.fullmatch(r'\.update-stage-[0-9a-f]{32}', stage.name) or stage.is_symlink():
                continue
            if not (stage / 'completed').is_file() and not (stage / 'cancelled').is_file():
                continue
            try:
                plan_path = stage / 'update-plan.json'
                plan = json.loads(plan_path.read_text(encoding='utf-8')) if plan_path.exists() else None
                if ((plan and Path(plan['target']).resolve() == self.root and plan['repository'] == REPOSITORY)
                        or (plan is None and (stage / 'cancelled').is_file())):
                    shutil.rmtree(stage, ignore_errors=True)
            except (OSError, ValueError, KeyError, TypeError):
                pass

    def _set(self, **values):
        with self.lock:
            self.state.update(values)

    def check(self):
        with self.lock:
            if not self.enabled or self.cancelled.is_set() or self.state['phase'] in ('checking', 'downloading', 'ready', 'installing'):
                return self.snapshot()
            self.state.update(phase='checking', message='正在检查 GitHub 正式版本…')
            self.thread = threading.Thread(target=self._check, daemon=True)
            self.thread.start()
            return self.snapshot()

    def _check(self):
        try:
            with open_url(f'https://api.github.com/repos/{REPOSITORY}/releases/latest') as response:
                raw = response.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise ValueError('GitHub 版本响应过大')
            release = choose_release(json.loads(raw), self.version)
            with self.lock:
                self.release = release
                self.state.update(phase='available' if release else 'current',
                                  message=f"发现新版本 v{release['version']}" if release else '当前已是最新正式版本',
                                  new_version=release['version'] if release else '', notes=release['notes'] if release else '')
        except HTTPError as error:
            message = '仓库暂无可用的正式 Release，或仓库为私有仓库' if error.code == 404 else (
                'GitHub 请求受限，请稍后再试' if error.code in (403, 429) else f'GitHub 检查失败（HTTP {error.code}）')
            self._set(phase='error', message=message)
        except Exception as error:
            self._set(phase='error', message=f'检查更新失败：{error}')

    def download(self):
        with self.lock:
            if not self.enabled or not self.installable:
                raise ValueError('自动安装更新仅支持完整的 Windows 便携发行包')
            if self.state['phase'] not in ('available', 'error') or not self.release or self.cancelled.is_set():
                raise ValueError('请先检查并选择可用的新版本')
            self.state.update(phase='downloading', message='正在下载更新，现有任务继续运行…', downloaded=0,
                              total=self.release['size'])
            self.thread = threading.Thread(target=self._download, args=(deepcopy(self.release),), daemon=True)
            self.thread.start()
            return self.snapshot()

    def _download(self, release):
        stage = self.root / ('.update-stage-' + uuid.uuid4().hex)
        try:
            if shutil.disk_usage(self.root).free < release['size'] * 4 + 512 * 1024**2:
                raise ValueError('磁盘空间不足，请至少保留更新包大小四倍加 512 MiB 的空间')
            stage.mkdir()
            self.stage = stage
            expected = release['sha256']
            if not expected:
                with open_url(release['checksum_url']) as response:
                    checksum = response.read(4097)
                if len(checksum) > 4096:
                    raise ValueError('校验附件过大')
                expected = checksum.decode('ascii').split()[0]
                if not re.fullmatch('[0-9a-fA-F]{64}', expected):
                    raise ValueError('SHA-256 校验附件格式不正确')
            archive = stage / 'download.zip'
            digest, count = hashlib.sha256(), 0
            with open_url(release['url']) as response, archive.open('xb') as target:
                while chunk := response.read(1024 * 1024):
                    if self.cancelled.is_set():
                        raise RuntimeError('下载已取消')
                    count += len(chunk)
                    if count > release['size']:
                        raise ValueError('下载大小超过 Release 声明')
                    target.write(chunk)
                    digest.update(chunk)
                    self._set(downloaded=count)
            if count != release['size'] or not hmac.compare_digest(digest.hexdigest(), expected.lower()):
                raise ValueError('更新包大小或 SHA-256 校验失败，未修改现有程序')
            self._set(message='下载完成，正在校验和准备更新文件…')
            files = extract_package(archive, stage / 'payload', release['version'])
            if self.cancelled.is_set():
                raise RuntimeError('更新已取消')
            # Run the currently installed updater from a separate folder so its
            # installed copy can itself be replaced by the incoming release.
            shutil.copy2(self.root / 'GameBot.Updater.exe', stage / 'GameBot.Updater.exe')
            plan = {"target": str(self.root), "payload": str(stage / 'payload'), "version": release['version'],
                    "repository": REPOSITORY, "update_protocol": UPDATE_PROTOCOL, "files": files}
            self.plan = stage / 'update-plan.json'
            self.plan.write_text(json.dumps(plan, ensure_ascii=False), encoding='utf-8')
            archive.unlink()
            self._set(phase='ready', message='更新包已校验。点击“安装并重启”将停止任务并更新程序。')
        except Exception as error:
            self._set(phase='error', message=f'下载或准备更新失败：{error}')
            if stage.exists() and stage.parent.resolve() == self.root:
                shutil.rmtree(stage, ignore_errors=True)
            self.plan = None

    def prepare_install(self):
        with self.lock:
            if self.state['phase'] != 'ready' or self.plan is None or self.cancelled.is_set():
                raise ValueError('更新包尚未准备完成')
            self.state.update(phase='installing', message='正在停止任务并安装更新…')
            return str(self.plan)

    def close(self):
        self.cancelled.set()
        if self.state['phase'] != 'installing' and self.stage and self.stage.is_dir():
            try:
                (self.stage / 'cancelled').write_text('cancelled', encoding='ascii')
            except OSError:
                pass
        # A ready package can be deleted on normal exit, but an installation
        # handoff owns its stage until the external updater has finished.
        if self.state['phase'] != 'installing' and self.thread and self.thread.is_alive():
            return
        if self.state['phase'] != 'installing' and self.stage and self.stage.is_dir():
            shutil.rmtree(self.stage, ignore_errors=True)

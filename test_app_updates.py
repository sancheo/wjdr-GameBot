"""Release selection, hostile packages, download failures and native rollback."""

import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import app_updates as updates
from app_version import REPOSITORY, UPDATE_PROTOCOL
import gui_web


def release(version='1.0.1', payload=b'test', digest=True):
    name = f'GameBot-Windows-x64-v{version}.zip'
    asset = {'name': name, 'state': 'uploaded', 'size': len(payload),
             'browser_download_url': f'https://github.com/{REPOSITORY}/releases/download/v{version}/{name}'}
    if digest:
        asset['digest'] = 'sha256:' + hashlib.sha256(payload).hexdigest()
    return {'tag_name': 'v' + version, 'draft': False, 'prerelease': False,
            'body': '<script>release notes are text</script>', 'assets': [asset]}


def package(extra=None, version='1.0.1'):
    files = {name: b'fixture' for name in updates.REQUIRED}
    files['release.json'] = json.dumps({'version': version, 'repository': REPOSITORY,
                                      'platform': 'windows-x64', 'update_protocol': UPDATE_PROTOCOL}).encode()
    files.update(extra or {})
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            info = zipfile.ZipInfo('GameBot-Windows-x64-v1.0.1/' + name)
            # ZipInfo's Windows constructor normalizes backslashes. Preserve
            # the actual hostile name in the stored ZIP for this regression.
            info.filename = 'GameBot-Windows-x64-v1.0.1/' + name
            z.writestr(info, data)
    return output.getvalue()


class UpdateTests(unittest.TestCase):
    def test_numeric_versions_skip_downgrades_drafts_and_prereleases(self):
        self.assertGreater(updates.version_tuple('v1.10.0'), updates.version_tuple('1.9.0'))
        self.assertIsNone(updates.choose_release(release('0.9.0'), '1.0.0'))
        self.assertIsNone(updates.choose_release(release('1.0.0'), '1.0.0'))
        data = release(); data['prerelease'] = True
        self.assertIsNone(updates.choose_release(data, '1.0.0'))
        data['prerelease'] = False; data['draft'] = True
        self.assertIsNone(updates.choose_release(data, '1.0.0'))
        for version in ('1.2', 'v1.2.3-beta', '01.2.3', '1.2.3.4'):
            with self.assertRaises(ValueError): updates.version_tuple(version)

    def test_require_exact_asset_checksum_and_repository(self):
        data = release()
        self.assertEqual(updates.choose_release(data, '1.0.0')['version'], '1.0.1')
        data['assets'][0]['browser_download_url'] = 'https://github.com/other/repo/releases/download/v1.0.1/x.zip'
        with self.assertRaises(ValueError): updates.choose_release(data, '1.0.0')
        data = release(digest=False)
        with self.assertRaises(ValueError): updates.choose_release(data, '1.0.0')
        data = release(); data['assets'][0]['name'] = 'Source code.zip'
        with self.assertRaises(ValueError): updates.choose_release(data, '1.0.0')

    def test_checksum_sidecar_fallback(self):
        data = release(digest=False)
        checksum = dict(data['assets'][0])
        checksum['name'] += '.sha256'; checksum['browser_download_url'] += '.sha256'
        data['assets'].append(checksum)
        chosen = updates.choose_release(data, '1.0.0')
        self.assertIsNone(chosen['sha256'])
        self.assertTrue(chosen['checksum_url'].endswith('.sha256'))

    def test_reject_hostile_zip_before_extracting(self):
        for name in ('../escape.py', 'configs/private.json', 'runtime/../escape.py',
                     'runtime/x:stream', 'runtime/NUL.txt', 'runtime/trailing. ', 'runtime\\escape.py',
                     'help_bot.py/child', 'HELP_BOT.PY'):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temp:
                path = Path(temp) / 'update.zip'; path.write_bytes(package({name: b'bad'}))
                destination = Path(temp) / 'payload'
                with self.assertRaises(ValueError): updates.extract_package(path, destination, '1.0.1')
                self.assertFalse(destination.exists())

    def test_reject_symlink_missing_runtime_and_version_mismatch(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'update.zip'; path.write_bytes(package())
            with zipfile.ZipFile(path, 'a') as z:
                info = zipfile.ZipInfo('GameBot-Windows-x64-v1.0.1/runtime/link')
                info.external_attr = (0o120777 << 16)
                z.writestr(info, '../outside')
            with self.assertRaises(ValueError): updates.extract_package(path, Path(temp) / 'payload', '1.0.1')
            path.write_bytes(package(version='1.0.2'))
            with self.assertRaises(ValueError): updates.extract_package(path, Path(temp) / 'payload', '1.0.1')
            with zipfile.ZipFile(path, 'w') as z: z.writestr('GameBot-Windows-x64-v1.0.1/GameBot.exe', b'x')
            with self.assertRaises(ValueError): updates.extract_package(path, Path(temp) / 'payload', '1.0.1')

    def test_download_ready_handoff_and_config_preservation(self):
        raw = package()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'runtime/python').mkdir(parents=True)
            (root / 'runtime/python/python.exe').touch()
            (root / 'GameBot.Updater.exe').write_bytes(b'trusted old updater')
            (root / 'configs').mkdir(); config = root / 'configs/user.json'; config.write_bytes(b'keep')
            manager = updates.UpdateManager(root)
            manager.release = updates.choose_release(release(payload=raw), manager.version)
            manager._set(phase='available')
            with patch.object(updates, 'open_url', return_value=io.BytesIO(raw)), \
                    patch.object(updates.shutil, 'disk_usage', return_value=shutil._ntuple_diskusage(10**10, 0, 10**10)):
                manager.download(); manager.thread.join(10)
            self.assertEqual(manager.snapshot()['phase'], 'ready', manager.snapshot()['message'])
            self.assertEqual(config.read_bytes(), b'keep')
            self.assertEqual((manager.stage / 'GameBot.Updater.exe').read_bytes(), b'trusted old updater')
            plan = Path(manager.prepare_install())
            manager.close()
            self.assertTrue(plan.is_file(), 'installation stage must survive bridge exit')
            self.assertEqual(json.loads(plan.read_text(encoding='utf-8'))['target'], str(root.resolve()))

    def test_bad_hash_and_truncated_download_leave_original_files(self):
        raw = package()
        for received in (raw[:-1], raw[:-1] + b'x'):
            with self.subTest(size=len(received)), tempfile.TemporaryDirectory() as temp:
                root = Path(temp); (root / 'runtime/python').mkdir(parents=True)
                (root / 'runtime/python/python.exe').touch(); (root / 'GameBot.Updater.exe').write_bytes(b'old')
                manager = updates.UpdateManager(root)
                manager.release = updates.choose_release(release(payload=raw), manager.version)
                manager._set(phase='available')
                with patch.object(updates, 'open_url', return_value=io.BytesIO(received)), \
                        patch.object(updates.shutil, 'disk_usage', return_value=shutil._ntuple_diskusage(10**10, 0, 10**10)):
                    manager.download(); manager.thread.join(10)
                self.assertEqual(manager.snapshot()['phase'], 'error')
                self.assertIn('校验失败', manager.snapshot()['message'])
                self.assertFalse(any(root.glob('.update-stage-*')))
                self.assertEqual((root / 'GameBot.Updater.exe').read_bytes(), b'old')

    def test_network_error_and_closed_manager_never_block_tasks(self):
        with tempfile.TemporaryDirectory() as temp:
            manager = updates.UpdateManager(temp)
            with patch.object(updates, 'open_url', side_effect=TimeoutError('offline')):
                manager.check(); manager.thread.join(5)
            self.assertEqual(manager.snapshot()['phase'], 'error')
            manager.close()
            self.assertEqual(manager.check()['phase'], 'error')

    def test_insufficient_disk_space_leaves_program_untouched(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); (root / 'runtime/python').mkdir(parents=True)
            (root / 'runtime/python/python.exe').touch(); (root / 'GameBot.Updater.exe').write_bytes(b'old')
            manager = updates.UpdateManager(root)
            manager.release = updates.choose_release(release(), manager.version)
            manager._set(phase='available')
            with patch.object(updates.shutil, 'disk_usage', return_value=shutil._ntuple_diskusage(100, 99, 1)), \
                    patch.object(updates, 'open_url') as network:
                manager.download(); manager.thread.join(5)
            self.assertIn('磁盘空间不足', manager.snapshot()['message'])
            network.assert_not_called()
            self.assertEqual((root / 'GameBot.Updater.exe').read_bytes(), b'old')
            self.assertFalse(any(root.glob('.update-stage-*')))

    def test_check_to_sidecar_download_through_desktop_bridge(self):
        raw = package()
        data = release(payload=raw, digest=False)
        asset = dict(data['assets'][0])
        asset['name'] += '.sha256'; asset['browser_download_url'] += '.sha256'
        data['assets'].append(asset)
        def response(url):
            if url.endswith('/latest'): return io.BytesIO(json.dumps(data).encode())
            if url.endswith('.sha256'): return io.BytesIO((hashlib.sha256(raw).hexdigest() + '  update.zip\n').encode())
            return io.BytesIO(raw)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); (root / 'runtime/python').mkdir(parents=True)
            (root / 'runtime/python/python.exe').touch(); (root / 'GameBot.Updater.exe').write_bytes(b'updater')
            api = gui_web.DesktopApi(root)
            try:
                with patch.object(updates, 'open_url', side_effect=response), \
                        patch.object(updates.shutil, 'disk_usage', return_value=shutil._ntuple_diskusage(10**10, 0, 10**10)):
                    api.check_updates(); api._updates.thread.join(5)
                    self.assertEqual(api.get_state()['update']['phase'], 'available')
                    api.download_update(); api._updates.thread.join(5)
                self.assertEqual(api.get_state()['update']['phase'], 'ready')
                api._launches['pending'] = object()
                with self.assertRaisesRegex(ValueError, '等待'): api.install_update()
                self.assertEqual(api.get_state()['update']['phase'], 'ready')
                api._launches.clear()
                output = io.StringIO()
                gui_web.serve_bridge(api, io.StringIO('{"id":1,"method":"install_update"}\n{"method":"close"}\n'), output)
                plan = Path(json.loads(output.getvalue())['result']['install_plan'])
                self.assertTrue(plan.is_file())
                self.assertTrue(api._updating)
            finally: api._launches.clear(); api._close()

    def test_install_guard_blocks_new_tasks_and_emulator_launch(self):
        with tempfile.TemporaryDirectory() as temp:
            api = gui_web.DesktopApi(temp)
            api._updating = True
            try:
                for method, args in ((api.start, ({},)), (api.launch_emulator, ('x',)), (api.restart_emulator, ('x',))):
                    with self.assertRaisesRegex(ValueError, '更新'): method(*args)
            finally: api._close()


@unittest.skipUnless(sys.platform == 'win32', 'native Windows updater')
class NativeUpdaterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='GameBot-updater-tests-')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(cls.temp.name)
        harness = cls.root / 'TransactionTest.cs'
        harness.write_text('''using System; using System.IO;
internal static class TransactionTest {
    static int Main(string[] args) {
        try { var update = new UpdateTransaction(args[0]);
            update.Apply(delegate { if(args[1] == "fail") throw new IOException("simulated new startup failure"); });
            Console.WriteLine("installed"); return 0;
        } catch(Exception e) { Console.WriteLine(e.Message); return 2; }
    }
}''', encoding='utf-8')
        cls.exe = cls.root / 'TransactionTest.exe'
        compiler = Path(os.environ['WINDIR']) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
        source = Path(__file__).parent / 'desktop/Updater.cs'
        subprocess.run([str(compiler), '/nologo', '/target:exe', '/main:TransactionTest',
                        '/reference:System.Drawing.dll', '/reference:System.Windows.Forms.dll',
                        '/reference:System.Web.Extensions.dll', '/reference:System.Core.dll',
                        '/out:' + str(cls.exe), str(source), str(harness)], check=True,
                       capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)

    def transaction(self, mode):
        with tempfile.TemporaryDirectory(dir=self.root, prefix='中文 空格-') as temp:
            target = Path(temp); stage = target / '.update-stage-test'; stage.mkdir()
            archive = stage / 'test.zip'; archive.write_bytes(package())
            inventory = updates.extract_package(archive, stage / 'payload', '1.0.1')
            (target / 'GameBot.exe').write_bytes(b'old-exe')
            (target / 'runtime').mkdir(); (target / 'runtime/old.dll').write_bytes(b'old-runtime')
            (target / 'configs').mkdir(); (target / 'configs/account.json').write_bytes(b'my-config')
            plan = stage / 'update-plan.json'
            plan.write_text(json.dumps({'target': str(target), 'payload': str(stage / 'payload'),
                'version': '1.0.1', 'repository': REPOSITORY, 'update_protocol': UPDATE_PROTOCOL,
                'files': inventory}), encoding='utf-8')
            result = subprocess.run([str(self.exe), str(plan), mode], capture_output=True, timeout=20,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertEqual((target / 'configs/account.json').read_bytes(), b'my-config')
            if mode == 'fail':
                self.assertEqual(result.returncode, 2, result.stdout)
                self.assertEqual((target / 'GameBot.exe').read_bytes(), b'old-exe')
                self.assertEqual((target / 'runtime/old.dll').read_bytes(), b'old-runtime')
                self.assertFalse((target / 'runtime/python/python.exe').exists())
            else:
                self.assertEqual(result.returncode, 0, result.stdout)
                self.assertEqual((target / 'GameBot.exe').read_bytes(), b'fixture')
                self.assertFalse((target / 'runtime/old.dll').exists())
                self.assertEqual((stage / 'backup/GameBot.exe').read_bytes(), b'old-exe')

    def test_native_install_replaces_runtime_and_keeps_user_config(self): self.transaction('success')
    def test_native_startup_failure_rolls_back_every_replaced_file(self): self.transaction('fail')


if __name__ == '__main__':
    unittest.main()

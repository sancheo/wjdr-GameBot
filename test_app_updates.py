"""Release selection, hostile packages, download failures and native rollback."""

import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import unittest
from urllib.error import HTTPError, URLError
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
    def setUp(self):
        # Update fixtures model 1.0.0 -> 1.0.1 independently of the release being built.
        version = patch.object(updates, 'VERSION', '1.0.0')
        version.start()
        self.addCleanup(version.stop)

    def test_api_rate_limit_uses_public_release_without_credentials(self):
        for status in (403, 429):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as temp:
                manager = updates.UpdateManager(temp)
                chosen = updates.choose_release(release(), manager.version)
                with (patch.object(updates, 'read_url', side_effect=HTTPError('api', status, 'limited', {}, None)),
                      patch.object(updates, 'public_latest_release', return_value=chosen) as fallback):
                    manager._check()
                fallback.assert_called_once_with('1.0.0')
                self.assertEqual(manager.snapshot()['phase'], 'available')
                self.assertEqual(manager.snapshot()['new_version'], '1.0.1')

    def test_public_release_checks_official_tag_checksum_size_and_notes(self):
        page = io.BytesIO(b'<div data-test-selector="body-content"><p>Fix &amp; update</p><ul><li>Keep configs</li></ul></div><p>Outside</p>')
        page.geturl = lambda: f'https://github.com/{REPOSITORY}/releases/tag/v1.0.1'
        checksum = io.BytesIO(('a' * 64 + '  GameBot-Windows-x64-v1.0.1.zip\n').encode())
        head = io.BytesIO()
        head.headers = {'Content-Length': '12345'}
        with patch.object(updates, 'open_url', side_effect=[page, checksum, head]) as network:
            chosen = updates.public_latest_release('1.0.0')
        self.assertEqual(chosen['version'], '1.0.1')
        self.assertEqual(chosen['sha256'], 'a' * 64)
        self.assertEqual(chosen['size'], 12345)
        self.assertEqual(chosen['notes'], 'Fix & update\nKeep configs')
        self.assertEqual(network.call_args_list[-1].kwargs, {'method': 'HEAD'})
        self.assertTrue(all(response.closed for response in (page, checksum, head)))

    def test_public_release_current_version_does_not_read_or_probe_assets(self):
        page = io.BytesIO()
        page.geturl = lambda: f'https://github.com/{REPOSITORY}/releases/tag/v1.0.0'
        page.read = lambda limit: self.fail('current release needs no body')
        with patch.object(updates, 'open_url', return_value=page) as network:
            self.assertIsNone(updates.public_latest_release('1.0.0'))
        network.assert_called_once()
        self.assertTrue(page.closed)

    def test_public_release_rejects_other_repository_and_nonfinal_tags(self):
        for url in ('https://github.com/other/repo/releases/tag/v1.0.1',
                    f'https://github.com/{REPOSITORY}/releases/tag/v1.0.1-beta'):
            with self.subTest(url=url):
                page = io.BytesIO()
                page.geturl = lambda: url
                with patch.object(updates, 'open_url', return_value=page) as network:
                    with self.assertRaises(ValueError): updates.public_latest_release('1.0.0')
                network.assert_called_once()

    def test_public_release_rejects_bad_checksum_and_invalid_size(self):
        for checksum, size in ((b'bad', '123'), (b'a' * 64 + b' other.zip', '123'),
                               (b'a' * 64, '0'), (b'a' * 64, str(updates.MAX_DOWNLOAD + 1))):
            with self.subTest(checksum=checksum, size=size):
                page = io.BytesIO(b'')
                page.geturl = lambda: f'https://github.com/{REPOSITORY}/releases/tag/v1.0.1'
                head = io.BytesIO()
                head.headers = {'Content-Length': size}
                with patch.object(updates, 'open_url', side_effect=[page, io.BytesIO(checksum), head]):
                    with self.assertRaises(ValueError): updates.public_latest_release('1.0.0')

    def test_failed_public_fallback_does_not_advertise_an_update(self):
        with tempfile.TemporaryDirectory() as temp:
            manager = updates.UpdateManager(temp)
            with (patch.object(updates, 'read_url', side_effect=HTTPError('api', 403, 'limited', {}, None)),
                  patch.object(updates, 'public_latest_release', side_effect=ValueError('invalid checksum'))):
                manager._check()
            self.assertEqual(manager.snapshot()['phase'], 'error')
            self.assertEqual(manager.snapshot()['new_version'], '')

    def test_check_recovers_from_tls_eof_and_interrupted_body(self):
        for during_read in (False, True):
            with self.subTest(during_read=during_read), tempfile.TemporaryDirectory() as temp:
                manager = updates.UpdateManager(temp)
                error = ssl.SSLEOFError(8, 'EOF occurred in violation of protocol')
                broken = io.BytesIO()
                if during_read:
                    broken.read = lambda limit: (_ for _ in ()).throw(error)
                responses = [broken if during_read else URLError(error), io.BytesIO(json.dumps(release()).encode())]
                with patch.object(updates, 'open_url', side_effect=responses) as network, patch.object(updates.time, 'sleep'):
                    manager._check()
                self.assertEqual(manager.snapshot()['phase'], 'available', manager.snapshot()['message'])
                self.assertEqual(network.call_count, 2)
                if during_read:
                    self.assertTrue(broken.closed)

    def test_persistent_tls_eof_has_bounded_retries_and_readable_error(self):
        with tempfile.TemporaryDirectory() as temp:
            manager = updates.UpdateManager(temp)
            error = URLError(ssl.SSLEOFError(8, 'EOF occurred in violation of protocol'))
            with patch.object(updates, 'open_url', side_effect=error) as network, patch.object(updates.time, 'sleep') as backoff:
                manager._check()
            self.assertEqual(network.call_count, 3)
            self.assertEqual(backoff.call_count, 2)
            self.assertEqual(manager.snapshot()['phase'], 'error')
            self.assertIn('HTTPS 连接被提前关闭', manager.snapshot()['message'])

    def test_cancel_download_cleans_stage_and_allows_retry_through_bridge(self):
        raw = package()
        entered, resume = threading.Event(), threading.Event()
        class SlowResponse(io.BytesIO):
            def read(self, limit):
                entered.set()
                if not resume.wait(5):
                    raise TimeoutError('test response did not resume')
                return super().read(limit)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'runtime/python').mkdir(parents=True)
            (root / 'runtime/python/python.exe').touch()
            (root / 'GameBot.Updater.exe').write_bytes(b'old')
            api = gui_web.DesktopApi(root)
            manager = api._updates
            manager.release = updates.choose_release(release(payload=raw), manager.version)
            manager._set(phase='available', new_version=manager.release['version'])
            try:
                with patch.object(updates, 'open_url', return_value=SlowResponse(raw)), \
                        patch.object(updates.shutil, 'disk_usage', return_value=shutil._ntuple_diskusage(10**10, 0, 10**10)):
                    api.download_update()
                    try:
                        self.assertTrue(entered.wait(5))
                        self.assertEqual(api.cancel_update_download()['update']['phase'], 'cancelling')
                        api.check_updates()
                        self.assertEqual(manager.snapshot()['phase'], 'cancelling')
                    finally:
                        resume.set()
                        manager.thread.join(5)
                self.assertEqual(manager.snapshot()['phase'], 'available')
                self.assertFalse(any(root.glob('.update-stage-*')))
                self.assertIsNone(manager.plan)
                self.assertEqual((root / 'GameBot.Updater.exe').read_bytes(), b'old')
                with patch.object(updates, 'open_url', return_value=io.BytesIO(raw)), \
                        patch.object(updates.shutil, 'disk_usage', return_value=shutil._ntuple_diskusage(10**10, 0, 10**10)):
                    api.download_update()
                    manager.thread.join(5)
                self.assertEqual(manager.snapshot()['phase'], 'ready', manager.snapshot()['message'])
            finally:
                api._close()

    def test_bundled_roots_work_without_system_certificates(self):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        self.assertEqual(context.cert_store_stats()['x509_ca'], 0)
        with patch.object(updates.ssl, 'create_default_context', return_value=context):
            configured = updates.update_ssl_context()
        self.assertGreater(configured.cert_store_stats()['x509_ca'], 0)
        self.assertTrue(configured.check_hostname)
        self.assertEqual(configured.verify_mode, ssl.CERT_REQUIRED)

    def test_bundled_roots_preserve_system_trust(self):
        context = ssl.create_default_context()
        system_roots = set(context.get_ca_certs(binary_form=True))
        with patch.object(updates.ssl, 'create_default_context', return_value=context):
            configured = updates.update_ssl_context()
        self.assertTrue(system_roots.issubset(set(configured.get_ca_certs(binary_form=True))))

    def test_certificate_failure_is_not_retried_without_verification(self):
        error = URLError(ssl.SSLCertVerificationError(1, 'unable to get local issuer certificate'))
        with patch.object(updates, 'build_opener') as factory:
            factory.return_value.open.side_effect = error
            with self.assertRaises(URLError):
                updates.open_url(f'https://api.github.com/repos/{REPOSITORY}/releases/latest')
            factory.return_value.open.assert_called_once()
            handlers = factory.call_args.args
            self.assertTrue(any(isinstance(h, updates.GithubRedirects) for h in handlers))
            https = next(h for h in handlers if isinstance(h, updates.HTTPSHandler))
            self.assertTrue(https._context.check_hostname)
            self.assertEqual(https._context.verify_mode, ssl.CERT_REQUIRED)
            factory.reset_mock()
            for url in ('http://api.github.com/', 'https://example.com/'):
                with self.assertRaises(ValueError): updates.open_url(url)
            factory.assert_not_called()

    def test_certificate_failure_shows_actionable_check_and_download_errors(self):
        for wrapped in (False, True):
            error = ssl.SSLCertVerificationError(1, 'unable to get local issuer certificate')
            if wrapped:
                error = URLError(error)
            with self.subTest(wrapped=wrapped), tempfile.TemporaryDirectory() as temp:
                manager = updates.UpdateManager(temp)
                with patch.object(updates, 'open_url', side_effect=error):
                    manager._check()
                    self.assertEqual(manager.snapshot()['phase'], 'error')
                    self.assertIn('HTTPS 证书验证失败', manager.snapshot()['message'])
                    self.assertIn('代理/安全软件', manager.snapshot()['message'])
                    with patch.object(updates.shutil, 'disk_usage', return_value=shutil._ntuple_diskusage(10**10, 0, 10**10)):
                        manager._download(updates.choose_release(release(), manager.version))
                    self.assertIn('HTTPS 证书验证失败', manager.snapshot()['message'])
                    self.assertFalse(any(Path(temp).glob('.update-stage-*')))

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
                api._resume_state = 'starting'
                with self.assertRaisesRegex(ValueError, '等待'): api.install_update()
                self.assertEqual(api.get_state()['update']['phase'], 'ready')
                api._resume_state = 'idle'
                output = io.StringIO()
                gui_web.serve_bridge(api, io.StringIO('{"id":1,"method":"install_update"}\n{"method":"close"}\n'), output)
                plan = Path(json.loads(output.getvalue())['result']['install_plan'])
                self.assertTrue(plan.is_file())
                self.assertTrue(api._updating)
            finally: api._resume_state = 'idle'; api._close()

    def test_install_guard_blocks_new_tasks(self):
        with tempfile.TemporaryDirectory() as temp:
            api = gui_web.DesktopApi(temp)
            api._updating = True
            try:
                with self.assertRaisesRegex(ValueError, '更新'):
                    api.start({})
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

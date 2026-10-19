"""File ranges, manifest validation, long Windows paths and transactional rollback."""

import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
import zipfile

import app_updates as updates
import file_updates as files
from test_app_updates import package, release
import test_app_updates as fixtures


class RangeResponse(io.BytesIO):
    status = 206


class FileUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='file-updates-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.old = self.root / 'old'
        self.old.mkdir()
        baseline = self.root / 'base.zip'
        baseline.write_bytes(package({'assets/obsolete.txt': b'old', 'assets/keep.txt': b'unchanged'}, version='1.0.0'))
        updates.extract_package(baseline, self.old / 'payload', '1.0.0')
        for path in (self.old / 'payload').iterdir(): shutil.move(str(path), self.old / path.name)
        (self.old / 'payload').rmdir()
        (self.old / 'assets/update-files.json').write_text(json.dumps(files.inventory_paths(self.old)))
        (self.old / 'configs').mkdir()
        (self.old / 'configs/user.json').write_bytes(b'configuration')
        (self.old / 'assets/unknown.txt').write_bytes(b'unknown user file')
        self.archive = self.root / 'GameBot-Windows-x64-v1.0.1.zip'
        self.archive.write_bytes(package({'help_bot.py': b'new-code', 'assets/new.txt': b'new',
                                          'assets/keep.txt': b'unchanged'}))
        self.manifest_path = files.build_manifest(self.archive)
        self.manifest = json.loads(self.manifest_path.read_text())
        self.chosen = updates.choose_release(release(payload=self.archive.read_bytes()), '1.0.0')
        self.chosen['manifest'] = {'url': self.chosen['url'].removesuffix('.zip') + '.files.json',
                                   'sha256': files.file_hash(self.manifest_path)}
        self.manager = updates.UpdateManager(self.old)
        self.requests = []

    def response(self, url, *, headers=None, **kwargs):
        self.requests.append((url, headers))
        if url.endswith('.files.json'):
            return io.BytesIO(self.manifest_path.read_bytes())
        if headers:
            start, end = map(int, headers['Range'].removeprefix('bytes=').split('-'))
            response = RangeResponse(self.archive.read_bytes()[start:end + 1])
            response.headers = {'Content-Range': f'bytes {start}-{end}/{self.archive.stat().st_size}'}
            return response
        return io.BytesIO(self.archive.read_bytes())

    def prepare(self):
        with patch.object(updates, 'open_url', side_effect=self.response):
            self.manager._download(self.chosen)
        self.assertEqual(self.manager.snapshot()['phase'], 'ready', self.manager.snapshot()['message'])
        return json.loads(self.manager.plan.read_text(encoding='utf-8'))

    def test_downloads_only_changed_file_ranges_and_tracks_only_known_deletions(self):
        plan = self.prepare()
        self.assertEqual({i['path'] for i in plan['files']}, {'release.json', 'help_bot.py', 'assets/new.txt'})
        self.assertEqual(plan['delete'], ['assets/obsolete.txt'])
        self.assertIn('runtime/python/python.exe', {i['path'] for i in plan['unchanged']})
        requested = [h['Range'] for _, h in self.requests if h]
        expected = {f'bytes={i["offset"]}-{i["offset"] + i["packed"] - 1}' for i in self.manifest['files']
                    if i['path'] in {j['path'] for j in plan['files']}}
        self.assertEqual(set(requested), expected)
        self.assertEqual(len(requested), 3)
        self.assertLess(self.manager.snapshot()['downloaded'], self.archive.stat().st_size)
        self.assertTrue(all((self.manager.stage / 'payload' / i['staged']).is_file() for i in plan['files']))
        self.assertEqual((self.old / 'configs/user.json').read_bytes(), b'configuration')

    def test_missing_or_modified_local_runtime_is_downloaded_for_repair(self):
        (self.old / 'runtime/python/python.exe').write_bytes(b'corrupted')
        (self.old / 'assets/keep.txt').unlink()
        plan = self.prepare()
        self.assertTrue({'runtime/python/python.exe', 'assets/keep.txt'}.issubset({i['path'] for i in plan['files']}))

    def test_range_ignored_falls_back_to_verified_full_package(self):
        def response(url, **kwargs):
            if url.endswith('.files.json'): return self.response(url)
            result = io.BytesIO(self.archive.read_bytes())
            result.status = 200
            return result
        with patch.object(updates, 'open_url', side_effect=response): self.manager._download(self.chosen)
        self.assertEqual(self.manager.snapshot()['phase'], 'ready', self.manager.snapshot()['message'])
        plan = json.loads(self.manager.plan.read_text(encoding='utf-8'))
        self.assertNotIn('mode', plan)
        self.assertTrue((self.manager.stage / 'payload/runtime/python/python.exe').exists())

    def test_invalid_manifest_hash_never_falls_back_or_modifies_originals(self):
        self.chosen['manifest']['sha256'] = 'a' * 64
        with patch.object(updates, 'open_url', side_effect=self.response): self.manager._download(self.chosen)
        self.assertEqual(self.manager.snapshot()['phase'], 'error')
        self.assertEqual(len(self.requests), 1)
        self.assertEqual((self.old / 'help_bot.py').read_bytes(), b'fixture')
        self.assertFalse(list(self.old.glob('.u-*')))

    def test_corrupt_or_truncated_ranges_are_rejected_without_full_download(self):
        for mode in ('truncated', 'corrupt', 'wrong-range'):
            def response(url, **kwargs):
                result = self.response(url, **kwargs)
                if kwargs.get('headers'):
                    body = result.getvalue()
                    changed = RangeResponse(body[:-1] if mode == 'truncated' else body[:-1] + bytes([body[-1] ^ 255]))
                    changed.headers = result.headers if mode != 'wrong-range' else {'Content-Range': 'bytes 0-0/1'}
                    return changed
                return result
            with self.subTest(mode=mode), patch.object(updates, 'open_url', side_effect=response):
                self.manager._download(self.chosen)
                self.assertEqual(self.manager.snapshot()['phase'], 'error')
                self.assertFalse(list(self.old.glob('.u-*')))

    def test_cancellation_removes_flat_staging_files(self):
        def response(url, **kwargs):
            if kwargs.get('headers'): self.manager.download_cancelled.set()
            return self.response(url, **kwargs)
        with patch.object(updates, 'open_url', side_effect=response): self.manager._download(self.chosen)
        self.assertEqual(self.manager.snapshot()['phase'], 'available')
        self.assertFalse(list(self.old.glob('.u-*')))

    def test_unsafe_previous_inventory_cannot_delete_user_data(self):
        (self.old / 'assets/update-files.json').write_text('["configs/user.json"]')
        with patch.object(updates, 'open_url', side_effect=self.response): self.manager._download(self.chosen)
        self.assertEqual(self.manager.snapshot()['phase'], 'error')
        self.assertEqual((self.old / 'configs/user.json').read_bytes(), b'configuration')

    def test_manifest_rejects_escape_duplicates_overlaps_and_missing_entry(self):
        for mode in ('escape', 'duplicate', 'overlap', 'oversize', 'missing'):
            document = json.loads(json.dumps(self.manifest))
            if mode == 'escape': document['files'][0]['path'] = 'runtime/../configs/user.json'
            if mode == 'duplicate': document['files'].append(document['files'][0])
            if mode == 'overlap': document['files'][1]['offset'] = document['files'][0]['offset']
            if mode == 'oversize': document['files'][0]['size'] = updates.MAX_EXTRACTED + 1
            if mode == 'missing': document['files'] = [i for i in document['files'] if i['path'] != 'GameBot.exe']
            with self.subTest(mode=mode), self.assertRaises(ValueError): files.validate_manifest(document, self.chosen)

    def test_public_release_without_manifest_uses_full_fallback(self):
        self.chosen.pop('manifest')
        self.chosen['manifest_probe'] = True
        with patch.object(updates, 'read_url', side_effect=HTTPError('url', 404, 'missing', {}, None)):
            self.assertIsNone(files.read_manifest(self.chosen))


@unittest.skipUnless(sys.platform == 'win32', 'native Windows updater')
class NativeFileUpdateTests(unittest.TestCase):
    # Reuse the real C# transaction harness, not a Python reimplementation.
    setUpClass = classmethod(fixtures.NativeUpdaterTests.setUpClass.__func__)
    setUp = FileUpdateTests.setUp
    response = FileUpdateTests.response
    prepare = FileUpdateTests.prepare

    def native(self, mode):
        result = subprocess.run([str(self.exe), str(self.manager.plan), mode], capture_output=True, timeout=30)
        return result.returncode, result.stdout.decode(errors='replace')

    def test_real_file_install_keeps_runtime_unknown_files_and_configuration(self):
        self.prepare()
        runtime = self.old / 'runtime/python/python.exe'
        stamp = runtime.stat().st_mtime_ns
        code, output = self.native('success')
        self.assertEqual(code, 0, output)
        self.assertEqual(runtime.stat().st_mtime_ns, stamp)
        self.assertEqual((self.old / 'help_bot.py').read_bytes(), b'new-code')
        self.assertFalse((self.old / 'assets/obsolete.txt').exists())
        self.assertEqual((self.old / 'assets/unknown.txt').read_bytes(), b'unknown user file')
        self.assertEqual((self.old / 'configs/user.json').read_bytes(), b'configuration')

    def test_real_startup_failure_restores_modified_and_deleted_files(self):
        self.prepare()
        code, output = self.native('fail')
        self.assertEqual(code, 2, output)
        self.assertEqual((self.old / 'help_bot.py').read_bytes(), b'fixture')
        self.assertEqual((self.old / 'assets/obsolete.txt').read_bytes(), b'old')
        self.assertFalse((self.old / 'assets/new.txt').exists())

    def test_real_install_rechecks_unchanged_files_before_touching_originals(self):
        self.prepare()
        (self.old / 'runtime/python/python.exe').write_bytes(b'modified after download')
        code, output = self.native('success')
        self.assertEqual(code, 2, output)
        self.assertEqual((self.old / 'help_bot.py').read_bytes(), b'fixture')
        self.assertFalse((self.manager.stage / 'backup').exists())

    def test_real_file_update_and_rollback_support_paths_over_260_characters(self):
        relative = 'assets/' + '/'.join(['very-long-directory-name'] * 12) + '/sample.bin'
        self.assertGreater(len(str(self.old / relative)), 300)
        for mode in ('success', 'fail'):
            with self.subTest(mode=mode):
                old = self.old / relative
                old.parent.mkdir(parents=True, exist_ok=True)
                old.write_bytes(b'old-long-file')
                self.archive.write_bytes(package({relative: b'new-long-file', 'assets/keep.txt': b'unchanged'}))
                self.manifest_path = files.build_manifest(self.archive)
                self.chosen = updates.choose_release(release(payload=self.archive.read_bytes()), '1.0.0')
                self.chosen['manifest'] = {'url': self.chosen['url'].removesuffix('.zip') + '.files.json',
                                           'sha256': files.file_hash(self.manifest_path)}
                self.prepare()
                code, output = self.native(mode)
                self.assertEqual(code, 0 if mode == 'success' else 2, output)
                self.assertEqual(old.read_bytes(), b'new-long-file' if mode == 'success' else b'old-long-file')

    def test_real_legacy_full_update_supports_deep_runtime_paths(self):
        relative = 'runtime/python/Lib/' + '/'.join(['long-package-directory'] * 12) + '/library.py'
        raw = package({relative: b'long-runtime'})
        self.chosen = updates.choose_release(release(payload=raw), '1.0.0')
        with patch.object(updates, 'open_url', return_value=io.BytesIO(raw)):
            self.manager._download(self.chosen)
        self.assertEqual(self.manager.snapshot()['phase'], 'ready', self.manager.snapshot()['message'])
        code, output = self.native('success')
        self.assertEqual(code, 0, output)
        self.assertEqual((self.old / relative).read_bytes(), b'long-runtime')


if __name__ == '__main__': unittest.main()

"""Verified per-file updates using byte ranges of the ordinary release ZIP."""

import hashlib
import json
from pathlib import Path
import re
import shutil
import struct
from urllib.error import HTTPError
import zipfile
import zlib

import app_updates as updates

MAX_MANIFEST = 16 * 1024**2


class RangeUnsupported(Exception):
    pass


def safe_path(value):
    if not isinstance(value, str):
        raise ValueError('文件更新路径不正确')
    parts = value.split('/')
    if (not parts or not updates.payload_name(parts[0]) or
            (len(parts) > 1 and parts[0] not in updates.PAYLOAD_FOLDERS) or
            any(not p or p in ('.', '..') or p.endswith((' ', '.')) or
                any(ord(c) < 32 or c in '\\:<>"|?*' for c in p) or
                re.fullmatch(r'(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', p) for p in parts)):
        raise ValueError('文件更新清单包含不安全的路径')
    return value


def file_hash(path):
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def check_local_path(root, relative):
    path = root / safe_path(relative)
    for parent in (path, *path.parents):
        if parent == root:
            break
        if parent.is_symlink() or parent.is_junction():
            raise ValueError('程序文件目录不能包含链接')
    return path


def inventory_paths(root):
    """Only application-owned files; never configuration, caches or screenshots."""
    return sorted(p.relative_to(root).as_posix() for p in root.rglob('*')
                  if p.is_file() and p.relative_to(root).parts[0] not in updates.DATA_FOLDERS
                  and '__pycache__' not in p.parts and p.suffix != '.pyc'
                  and p.relative_to(root).as_posix() != 'assets/update-files.json'
                  and updates.payload_name(p.relative_to(root).parts[0]))


def build_manifest(archive):
    """Offsets point to compressed file data, so unchanged bytes need no download."""
    archive = Path(archive)
    files = []
    with zipfile.ZipFile(archive) as source, archive.open('rb') as raw:
        for entry in source.infolist():
            if entry.is_dir():
                continue
            relative = safe_path(entry.filename.split('/', 1)[1])
            raw.seek(entry.header_offset)
            header = raw.read(30)
            if header[:4] != b'PK\x03\x04' or entry.compress_type not in (0, 8) or entry.flag_bits & 1:
                raise ValueError('发行包包含不支持的压缩方式')
            names, extra = struct.unpack_from('<HH', header, 26)
            with source.open(entry) as content:
                digest = hashlib.file_digest(content, 'sha256').hexdigest()
            files.append({'path': relative, 'size': entry.file_size, 'sha256': digest,
                          'offset': entry.header_offset + 30 + names + extra,
                          'packed': entry.compress_size, 'method': entry.compress_type})
        metadata = next(item for item in files if item['path'] == 'release.json')
        prefix = source.namelist()[0].split('/')[0] + '/'
        version = json.loads(source.read(prefix + metadata['path']))['version']
    document = {'format': 1, 'version': version, 'repository': updates.REPOSITORY,
                'platform': 'windows-x64', 'archive': archive.name, 'archive_size': archive.stat().st_size,
                'archive_sha256': file_hash(archive), 'files': files}
    target = archive.with_suffix('.files.json')
    target.write_text(json.dumps(document, ensure_ascii=False, separators=(',', ':')) + '\n', encoding='utf-8')
    target.with_suffix('.json.sha256').write_text(file_hash(target) + '  ' + target.name + '\n', encoding='ascii')
    return target


def validate_manifest(document, release):
    if (not isinstance(document, dict) or document.get('format') != 1 or
            document.get('version') != release['version'] or document.get('repository') != updates.REPOSITORY or
            document.get('platform') != 'windows-x64' or document.get('archive') != release['name'] or
            document.get('archive_size') != release['size'] or
            not isinstance(document.get('archive_sha256'), str) or
            not re.fullmatch('[0-9a-f]{64}', document['archive_sha256']) or
            (release.get('sha256') and document.get('archive_sha256') != release['sha256'].lower())):
        raise ValueError('文件更新清单与发行版本不一致')
    files = document.get('files')
    if not isinstance(files, list) or not 0 < len(files) <= 30000:
        raise ValueError('文件更新清单数量不正确')
    seen, total, end = set(), 0, 0
    for item in files:
        if not isinstance(item, dict):
            raise ValueError('文件更新清单条目不正确')
        path = safe_path(item.get('path'))
        key = path.casefold()
        if key in seen:
            raise ValueError('文件更新路径重复或冲突')
        seen.add(key)
        if any(type(item.get(k)) is not int or item[k] < 0 for k in ('size', 'offset', 'packed', 'method')):
            raise ValueError('文件更新大小或位置不正确')
        if (item['method'] not in (0, 8) or item['offset'] < end or
                item['offset'] + item['packed'] > release['size'] or
                not isinstance(item.get('sha256'), str) or not re.fullmatch('[0-9a-f]{64}', item['sha256'])):
            raise ValueError('文件更新压缩信息或校验值不正确')
        end = item['offset'] + item['packed']
        total += item['size']
    for path in seen:
        parts = path.split('/')
        if any('/'.join(parts[:i]) in seen for i in range(1, len(parts))):
            raise ValueError('文件更新路径层级冲突')
    if total > updates.MAX_EXTRACTED or not updates.REQUIRED.issubset({i['path'] for i in files}):
        raise ValueError('文件更新清单缺少程序或超出大小限制')
    return files


def read_manifest(release):
    descriptor = release.get('manifest')
    if not descriptor and not release.get('manifest_probe'):
        return None
    url = descriptor['url'] if descriptor else release['url'].removesuffix('.zip') + '.files.json'
    expected = descriptor.get('sha256') if descriptor else None
    if not expected:
        try:
            checksum = updates.read_url(url + '.sha256', 4097)
        except HTTPError as error:
            if error.code == 404 and not descriptor:
                return None  # A release made before per-file updates were introduced.
            raise
        fields = checksum.decode('ascii').split()
        if (len(checksum) > 4096 or len(fields) != 2 or not re.fullmatch('[0-9a-fA-F]{64}', fields[0])
                or fields[1].lstrip('*') != url.rsplit('/', 1)[1]):
            raise ValueError('文件清单 SHA-256 附件不正确')
        expected = fields[0].lower()
    raw = updates.read_url(url, MAX_MANIFEST + 1)
    if len(raw) > MAX_MANIFEST or hashlib.sha256(raw).hexdigest() != expected.lower():
        raise ValueError('文件更新清单 SHA-256 校验失败')
    return validate_manifest(json.loads(raw), release)


def download_file(manager, release, item, target):
    offset, packed = item['offset'], item['packed']
    digest, written = hashlib.sha256(), 0
    decoder = zlib.decompressobj(-15) if item['method'] == 8 else None
    with target.open('xb') as output:
        if packed:
            try:
                response = updates.open_url(release['url'], headers={'Range': f'bytes={offset}-{offset + packed - 1}',
                                                                     'Accept-Encoding': 'identity'})
            except HTTPError as error:
                if error.code == 416:
                    raise RangeUnsupported() from error
                raise
            with response:
                if response.status == 200:
                    raise RangeUnsupported()
                if (response.status != 206 or response.headers.get('Content-Range') !=
                        f'bytes {offset}-{offset + packed - 1}/{release["size"]}'):
                    raise ValueError('服务器返回的文件范围不正确')
                received = 0
                while chunk := response.read(min(256 * 1024, packed - received + 1)):
                    manager._check_download_cancelled()
                    received += len(chunk)
                    if received > packed:
                        raise ValueError('差异文件下载大小超限')
                    data = decoder.decompress(chunk, item['size'] - written + 1) if decoder else chunk
                    written += len(data)
                    if written > item['size'] or (decoder and decoder.unconsumed_tail):
                        raise ValueError('差异文件解压大小超限')
                    output.write(data)
                    digest.update(data)
                    manager._set(downloaded=manager.snapshot()['downloaded'] + len(chunk))
                if received != packed:
                    raise ValueError('差异文件下载不完整')
        if decoder and (not decoder.eof or decoder.unused_data):
            raise ValueError('差异文件压缩数据不完整')
    if written != item['size'] or digest.hexdigest() != item['sha256']:
        raise ValueError('差异文件 SHA-256 校验失败')


def prepare_file_update(manager, release, stage):
    items = read_manifest(release)
    if items is None:
        return None
    manager._set(message='正在比较本地文件，仅下载新增和修改的文件…')
    changed, unchanged = [], []
    for item in items:
        manager._check_download_cancelled()
        local = check_local_path(manager.root, item['path'])
        (unchanged if local.is_file() and local.stat().st_size == item['size'] and
         file_hash(local) == item['sha256'] else changed).append(item)
    names = {i['path'].casefold() for i in items}
    obsolete = []
    inventory = manager.root / 'assets/update-files.json'
    if inventory.is_file():
        if inventory.stat().st_size > MAX_MANIFEST:
            raise ValueError('本地程序文件清单过大')
        previous = json.loads(inventory.read_text(encoding='utf-8'))
        if not isinstance(previous, list) or len(previous) > 30000:
            raise ValueError('本地程序文件清单不正确')
        obsolete = sorted({safe_path(p) for p in previous if safe_path(p).casefold() not in names})
        for path in obsolete:
            check_local_path(manager.root, path)
    manager._set(total=sum(i['packed'] for i in changed), downloaded=0,
                 message=f'正在下载 {len(changed)} 个变更文件，跳过 {len(unchanged)} 个未变文件…')
    if shutil.disk_usage(manager.root).free < sum(i['size'] for i in changed) * 2 + 64 * 1024**2:
        raise ValueError('磁盘空间不足，无法准备变更文件和回滚备份')
    payload = stage / 'payload'
    payload.mkdir()
    prepared = []
    for index, item in enumerate(changed):
        manager._check_download_cancelled()
        download_file(manager, release, item, payload / str(index))
        prepared.append({k: item[k] for k in ('path', 'size', 'sha256')} | {'staged': str(index)})
    return {'target': str(manager.root), 'payload': str(payload), 'version': release['version'],
            'repository': updates.REPOSITORY, 'update_protocol': updates.UPDATE_PROTOCOL,
            'mode': 'files', 'files': prepared, 'unchanged':
            [{k: item[k] for k in ('path', 'size', 'sha256')} for item in unchanged], 'delete': obsolete}

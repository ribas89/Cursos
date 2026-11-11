"""Read archive *headers* for a course lesson preview.

This module never extracts an archive member or invokes archive_read_data().
ZIP uses the Python standard library; 7z/RAR use libarchive (a system library).
The course files remain read-only.
"""
from __future__ import annotations

import ctypes
from ctypes.util import find_library
from functools import lru_cache
from pathlib import Path
import zipfile

SUPPORTED_ARCHIVES = {'.zip', '.7z', '.rar'}
MAX_ARCHIVE_ENTRIES = 2000
MAX_PATH_CHARS = 2048
MAX_TREE_DEPTH = 20


def readable_bytes(size: int | None) -> str:
    if size is None or size < 0:
        return '—'
    value = float(size)
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if value < 1024 or unit == 'TB':
            return f'{value:.0f} {unit}' if unit == 'B' else f'{value:.1f} {unit}'
        value /= 1024
    return '—'


def _zip_entries(path: str):
    # infolist() reads the ZIP central directory, not the contents of members.
    with zipfile.ZipFile(path) as archive:
        for index, info in enumerate(archive.infolist()):
            if index >= MAX_ARCHIVE_ENTRIES:
                yield None
                return
            yield (info.filename, info.is_dir(), info.file_size)


def _libarchive_entries(path: str):
    # ctypes avoids a Python dependency; Docker installs Debian's libarchive13.
    lib_name = find_library('archive')
    if not lib_name:
        raise RuntimeError('libarchive is not installed')
    lib = ctypes.CDLL(lib_name)
    signatures = (
        ('archive_read_new', ctypes.c_void_p, []),
        ('archive_read_support_filter_all', ctypes.c_int, [ctypes.c_void_p]),
        ('archive_read_support_format_all', ctypes.c_int, [ctypes.c_void_p]),
        ('archive_read_open_filename', ctypes.c_int, [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t]),
        ('archive_read_next_header', ctypes.c_int, [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]),
        ('archive_entry_pathname', ctypes.c_char_p, [ctypes.c_void_p]),
        ('archive_entry_filetype', ctypes.c_uint, [ctypes.c_void_p]),
        ('archive_entry_size', ctypes.c_int64, [ctypes.c_void_p]),
        ('archive_read_free', ctypes.c_int, [ctypes.c_void_p]),
    )
    for name, restype, argtypes in signatures:
        fn = getattr(lib, name)
        fn.restype = restype
        fn.argtypes = argtypes
    reader = lib.archive_read_new()
    if not reader:
        raise RuntimeError('Cannot initialize archive reader')
    try:
        lib.archive_read_support_filter_all(reader)
        lib.archive_read_support_format_all(reader)
        if lib.archive_read_open_filename(reader, str(path).encode('utf-8'), 16384) != 0:
            raise ValueError('Archive header is unavailable')
        count = 0
        while count <= MAX_ARCHIVE_ENTRIES:
            entry = ctypes.c_void_p()
            status = lib.archive_read_next_header(reader, ctypes.byref(entry))
            if status == 1:  # ARCHIVE_EOF
                break
            if status != 0:  # ARCHIVE_OK
                raise ValueError('Cannot read archive listing (corrupt or encrypted header)')
            if count == MAX_ARCHIVE_ENTRIES:
                yield None
                break
            raw_name = lib.archive_entry_pathname(entry)
            name = raw_name.decode('utf-8', 'replace') if raw_name else '(unnamed)'
            is_dir = lib.archive_entry_filetype(entry) == 0o040000 or name.endswith('/')
            size = lib.archive_entry_size(entry)
            yield (name, is_dir, size if size >= 0 else None)
            count += 1
    finally:
        lib.archive_read_free(reader)


def _tree(entries):
    root = {'children': {}, 'is_dir': True}
    file_count = 0
    folder_count = 0
    uncompressed = 0
    unknown_sizes = False
    paths_seen = set()
    for name, is_dir, size in entries:
        # Paths are *display-only*. Never resolve, open, or write them.
        parts = [part for part in name.replace('\\', '/').strip('/').split('/') if part and part != '.']
        if not parts:
            continue
        parts = parts[:MAX_TREE_DEPTH]
        node = root
        for i, part in enumerate(parts):
            last = i == len(parts) - 1
            as_dir = not last or is_dir
            children = node['children']
            if part not in children:
                children[part] = {'name': part, 'is_dir': as_dir, 'size': None,
                                  'children': {}}
            child = children[part]
            if as_dir:
                child['is_dir'] = True
            node = child
            if child['is_dir']:
                key = tuple(parts[:i + 1])
                if key not in paths_seen:
                    paths_seen.add(key)
                    folder_count += 1
        if not is_dir:
            file_count += 1
            node['size'] = size
            if size is None:
                unknown_sizes = True
            else:
                uncompressed += size

    def finalize(node):
        return [
            {'name': child['name'], 'is_dir': child['is_dir'],
             'size_label': readable_bytes(child['size']) if not child['is_dir'] else '',
             'children': finalize(child) if child['is_dir'] else []}
            for child in sorted(node['children'].values(),
                                key=lambda part: (not part['is_dir'], part['name'].casefold()))
        ]
    return finalize(root), file_count, folder_count, None if unknown_sizes else uncompressed


@lru_cache(maxsize=48)
def _cached_preview(path: str, mtime_ns: int, file_size: int):
    del mtime_ns
    result = {
        'available': False, 'nodes': [], 'files': 0, 'folders': 0,
        'uncompressed_label': '—', 'compressed_label': readable_bytes(file_size),
        'truncated': False,
    }
    try:
        suffix = Path(path).suffix.lower()
        source = _zip_entries(path) if suffix == '.zip' else _libarchive_entries(path)
        rows = []
        for row in source:
            if row is None:
                result['truncated'] = True
                break
            if len(row[0]) > MAX_PATH_CHARS:
                row = (row[0][:MAX_PATH_CHARS] + '…', row[1], row[2])
            rows.append(row)
        tree, files, folders, uncompressed = _tree(rows)
        result.update(available=True, nodes=tree, files=files, folders=folders,
                      uncompressed_label=readable_bytes(uncompressed))
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile,
            zipfile.LargeZipFile, EOFError):
        # Safe fallback: filesystem file size, even if archive headers are unreadable.
        pass
    return result


def archive_preview(path: Path) -> dict:
    stat = path.stat()
    return _cached_preview(str(path), stat.st_mtime_ns, stat.st_size)

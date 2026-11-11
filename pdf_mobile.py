"""Persistent, browser-scrollable PDF preview images for iOS Safari.

Original PDFs are never changed. Successfully rendered JPEGs live in the
persistent /app/data/pdf-page-cache Docker volume, keyed by source path,
mtime and size, and are reused across reloads, visitors and restarts.
"""
from __future__ import annotations

import fcntl
import hashlib
import os
import secrets
import threading
import time
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path

import pypdfium2 as pdfium

RENDER_WIDTH = 1400
_pdfium_lock = threading.RLock()  # PDFium cannot safely be used concurrently in a process.


def _source_key(path: Path) -> str:
    stat = path.stat()
    # Keep V9's v2 key to reuse any already-successful JPEGs.
    return hashlib.sha256((str(path.resolve()) + '\0' + str(stat.st_mtime_ns)
                           + '\0' + str(stat.st_size) + '\0v2').encode('utf-8')).hexdigest()


def cache_version(path: Path) -> str:
    """Safe URL version for year-long browser caches, changing if the PDF changes."""
    return _source_key(path)


def _cache_folder() -> Path:
    root = Path(os.environ.get('DATABASE_PATH', '/app/data/cursos.sqlite3')).parent
    return root / 'pdf-page-cache'


def _target_dir(path: Path) -> Path:
    key = _source_key(path)
    return _cache_folder() / key[:2] / key


@contextmanager
def _document_lock(target_dir: Path):
    """Lock a PDF's cache across Gunicorn threads and additional workers.

    The file lock also prevents duplicate concurrent generation of a page.
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    with _pdfium_lock:  # PDFium itself is not thread safe
        with (target_dir / '.render.lock').open('a+b') as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@lru_cache(maxsize=64)
def _metadata(path: str, mtime_ns: int, size: int) -> tuple[tuple[int, int], ...]:
    with _document_lock(_target_dir(Path(path))):
        with pdfium.PdfDocument(path) as document:
            dimensions = []
            for i in range(len(document)):
                page = document[i]
                try:
                    width, height = page.get_size()
                    dimensions.append((max(1, round(width)), max(1, round(height))))
                finally:
                    page.close()
            return tuple(dimensions)


def page_dimensions(path: Path) -> tuple[tuple[int, int], ...]:
    stat = path.stat()
    return _metadata(str(path), stat.st_mtime_ns, stat.st_size)


def _ready_jpeg(target: Path) -> bool:
    """Reject partial/corrupt cached writes while avoiding an expensive full decode."""
    try:
        if target.stat().st_size < 256:
            return False
        with target.open('rb') as stream:
            beginning = stream.read(3)
            stream.seek(-2, os.SEEK_END)
            ending = stream.read(2)
        return beginning == b'\xff\xd8\xff' and ending == b'\xff\xd9'
    except OSError:
        return False


def render_page(path: Path, one_based_page: int) -> Path:
    """Return a persistent cached JPEG, rendering ONCE if missing or invalid."""
    if one_based_page < 1:
        raise ValueError('Invalid PDF page')
    target_dir = _target_dir(path)
    target = target_dir / f'{one_based_page:05d}.jpg'
    if _ready_jpeg(target):
        return target
    # The lock is shared by ALL pages of the PDF: parallel requests cannot
    # race through PDFium, even if more Gunicorn workers are added later.
    with _document_lock(target_dir):
        if _ready_jpeg(target):
            return target
        image = None
        # Brief retries for transient network filesystem / PDFium read failures.
        for attempt in range(3):
            try:
                with pdfium.PdfDocument(str(path)) as document:
                    if one_based_page > len(document):
                        raise ValueError('Invalid PDF page')
                    page = document[one_based_page - 1]
                    try:
                        width, height = page.get_size()
                        scale = min(RENDER_WIDTH / max(width, 1), 8000 / max(height, 1))
                        bitmap = page.render(scale=scale)
                        try:
                            image = bitmap.to_pil().convert('RGB')
                        finally:
                            bitmap.close()
                    finally:
                        page.close()
                break
            except pdfium.PdfiumError:
                if attempt == 2:
                    raise
                time.sleep(0.15 * (attempt + 1))
        # Write image atomically. No client ever gets a partial JPEG.
        temp = target_dir / f'.{one_based_page:05d}-{secrets.token_hex(8)}.tmp'
        try:
            try:
                image.save(temp, format='JPEG', quality=88, optimize=True)
            finally:
                image.close()
            os.replace(temp, target)
        finally:
            temp.unlink(missing_ok=True)
        return target

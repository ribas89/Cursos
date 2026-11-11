"""Read-only Office -> PDF previews, cached persistently on the data volume.

LibreOffice is invoked without a shell and with a disposable user profile. A
process-safe file lock makes concurrent first visits generate the PDF once.
The original document is never modified; only /app/data is writable.
"""
from __future__ import annotations

import fcntl
import hashlib
import os
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path

OFFICE_EXTENSIONS = frozenset({'.doc', '.docx', '.odt', '.rtf',
                               '.ppt', '.pptx', '.odp',
                               '.xls', '.xlsx', '.ods'})


def _key(source: Path) -> str:
    stat = source.stat()
    identity = f'{source.resolve()}\0{stat.st_size}\0{stat.st_mtime_ns}\0office-v1'
    return hashlib.sha256(identity.encode('utf-8')).hexdigest()


def _cache_root() -> Path:
    return Path(os.environ.get('DATABASE_PATH', '/app/data/cursos.sqlite3')).parent / 'office-preview-cache'


def cache_path(source: Path) -> Path:
    key = _key(source)
    return _cache_root() / key[:2] / key / 'preview.pdf'


def _valid_pdf(path: Path) -> bool:
    try:
        if path.stat().st_size < 100:
            return False
        with path.open('rb') as stream:
            return stream.read(5) == b'%PDF-'
    except OSError:
        return False


@contextmanager
def _locked(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def convert_to_pdf(source: Path) -> Path:
    """Return a stable cached PDF for a supported Office file.

    Conversion happens once for each file size/mtime/source path combination.
    If conversion fails the caller can still offer the original file.
    """
    if source.suffix.lower() not in OFFICE_EXTENSIONS or not source.is_file():
        raise ValueError('Not a supported Office document')
    target = cache_path(source)
    if _valid_pdf(target):
        return target

    # One global conversion at a time prevents office CPU/RAM spikes on a
    # small Docker host; the per-document lock prevents duplicate conversion.
    global_lock = _cache_root() / '.converter.lock'
    with _locked(global_lock):
        with _locked(target.with_suffix('.lock')):
            if _valid_pdf(target):
                return target
            converter = os.environ.get('LIBREOFFICE_BINARY', '/usr/bin/libreoffice')
            timeout = max(5, min(600, int(os.environ.get('OFFICE_CONVERT_TIMEOUT', '90'))))
            with tempfile.TemporaryDirectory(prefix='cursos-office-', dir=target.parent) as temp:
                temp_path = Path(temp)
                out = temp_path / 'output'
                out.mkdir()
                profile = temp_path / 'profile'
                cmd = [converter, f'-env:UserInstallation={profile.as_uri()}',
                       '--headless', '--convert-to', 'pdf', '--outdir', str(out), str(source)]
                try:
                    process = subprocess.run(cmd, capture_output=True, text=True,
                                             timeout=timeout, check=False,
                                             env={**os.environ, 'HOME':str(temp_path)})
                except subprocess.TimeoutExpired as exc:
                    raise RuntimeError('Office conversion timed out') from exc
                except OSError as exc:
                    raise RuntimeError('LibreOffice is not installed or cannot run') from exc
                rendered = out / f'{source.stem}.pdf'
                if process.returncode != 0 or not _valid_pdf(rendered):
                    # Avoid exposing full source file paths, internal profile
                    # details, or large conversion output to the browser.
                    raise RuntimeError(f'Office to PDF conversion failed (exit {process.returncode})')
                # os.replace is atomic on the same filesystem; never serve a
                # half-written PDF from a concurrent request.
                os.replace(rendered, target)
            return target

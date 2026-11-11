"""Safe, read-only access to administrator-configured meme images.

Only files in the configured directory can be served. Meme selection runs on
request and never modifies, extracts, or processes an image at runtime.
"""
from __future__ import annotations

import os
import secrets
from pathlib import Path

IMAGE_MIMETYPES = {
    '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png',
    '.webp': 'image/webp', '.gif': 'image/gif',
}
DEFAULT_MEMES_DIR = Path(__file__).resolve().parent / 'memes'


def memes_enabled() -> bool:
    return os.environ.get('MEMES_ENABLED', 'true').strip().lower() in {'1', 'true', 'yes', 'on'}


def memes_directory() -> Path:
    configured = os.environ.get('MEMES_DIR', '').strip()
    if not configured:
        return DEFAULT_MEMES_DIR
    path = Path(configured).expanduser()
    if not path.is_absolute():
        path = Path(__file__).resolve().parent / path
    return path


def meme_files() -> list[Path]:
    """List ordinary image files in the configured folder, excluding symlinks."""
    if not memes_enabled():
        return []
    folder = memes_directory()
    try:
        if not folder.is_dir():
            return []
        return [entry for entry in folder.iterdir()
                if entry.suffix.lower() in IMAGE_MIMETYPES and
                not entry.is_symlink() and entry.is_file()]
    except OSError:
        return []


def random_meme_file() -> Path | None:
    entries = meme_files()
    return secrets.choice(entries) if entries else None

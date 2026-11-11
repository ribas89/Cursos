"""Safely render Markdown to cached, self-contained HTML previews.

Source files remain read-only. The generated HTML is stored in /app/data
and is regenerated only when the source file or renderer version changes.
"""
from __future__ import annotations

import fcntl
import hashlib
import html
import os
import tempfile
from pathlib import Path

from markdown_it import MarkdownIt

MARKDOWN_EXTENSIONS = frozenset({'.md', '.markdown'})
MAX_MARKDOWN_BYTES = 16 * 1024 * 1024
_RENDERER_VERSION = 'markdown-v1'


def markdown_version(source: Path) -> str:
    stat = source.stat()
    identity = (f'{source.resolve()}\0{stat.st_size}\0'
                f'{stat.st_mtime_ns}\0{_RENDERER_VERSION}')
    return hashlib.sha256(identity.encode('utf-8')).hexdigest()


def markdown_cache_path(source: Path) -> Path:
    key = markdown_version(source)
    cache_root = Path(os.environ.get('DATABASE_PATH', '/app/data/cursos.sqlite3')).parent / 'markdown-preview-cache'
    return cache_root / key[:2] / key / 'preview.html'


def _render_html(markdown: str, title: str, base_url: str) -> str:
    # Raw HTML is disabled and dangerous link schemes are rejected by markdown-it.
    # The preview route adds an independent CSP sandbox, with scripts blocked.
    renderer = MarkdownIt('default', {'html': False, 'linkify': False})
    rendered = renderer.render(markdown)
    safe_title = html.escape(title, quote=True)
    safe_base = html.escape(base_url, quote=True)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<base href="{safe_base}">
<title>{safe_title}</title>
<style>
:root {{ color-scheme: light dark; font-family: system-ui, -apple-system, "Segoe UI", sans-serif; line-height: 1.65; }}
* {{ box-sizing: border-box; }}
body {{ max-width: 960px; margin: 0 auto; padding: 34px clamp(16px, 5vw, 56px) 70px; overflow-wrap: break-word; }}
h1, h2, h3, h4, h5, h6 {{ line-height: 1.3; margin: 1.6em 0 .65em; }}
h1:first-child {{ margin-top: 0; }}
h1, h2 {{ padding-bottom: .25em; border-bottom: 1px solid #9ca3af55; }}
a {{ color: #208bc7; text-underline-offset: 2px; }}
p, ul, ol, blockquote {{ margin: 1em 0; }}
li {{ margin: .2em 0; }}
li > p {{ margin: .25em 0; }}
blockquote {{ margin-left: 0; padding: .2em 1em; border-left: 4px solid #6b9cbd; color: #64748b; }}
pre {{ padding: 16px; background: #182637; color: #e7eef7; border-radius: 7px; overflow-x: auto; white-space: pre; }}
code {{ font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: .91em; }}
:not(pre) > code {{ padding: .15em .32em; background: #7f8fa122; border-radius: 4px; }}
pre code {{ color: inherit; background: none; }}
img, video {{ max-width: 100%; height: auto; }}
hr {{ border: none; border-top: 1px solid #9ca3af66; margin: 2em 0; }}
table {{ border-collapse: collapse; display: block; max-width: 100%; overflow-x: auto; }}
th, td {{ border: 1px solid #9ca3af66; padding: .45em .75em; text-align: left; }}
tr:nth-child(even) {{ background: #7f8fa111; }}
@media (prefers-color-scheme: dark) {{ a {{ color: #80c8fa; }} blockquote {{ color: #a9bbcf; }} }}
@media (max-width: 600px) {{ body {{ padding: 18px 15px 50px; }} }}
</style>
</head>
<body>
<article class="markdown-body">{rendered}</article>
</body>
</html>
"""


def convert_markdown(source: Path, base_url: str) -> Path:
    """Return a persistent HTML preview, converted once for each file revision."""
    if source.suffix.lower() not in MARKDOWN_EXTENSIONS or not source.is_file():
        raise ValueError('Not a Markdown file')
    stat = source.stat()
    if stat.st_size > MAX_MARKDOWN_BYTES:
        raise ValueError('Markdown is too large for HTML preview')
    target = markdown_cache_path(source)
    if target.is_file():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    lock = target.with_suffix('.lock')
    with lock.open('a+b') as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            if target.is_file():
                return target
            # Read a bounded amount of data and don't modify the mounted library.
            with source.open('rb') as stream:
                raw = stream.read(MAX_MARKDOWN_BYTES + 1)
            if len(raw) > MAX_MARKDOWN_BYTES:
                raise ValueError('Markdown is too large for HTML preview')
            rendered = _render_html(raw.decode('utf-8-sig', errors='replace'),
                                    source.name, base_url)
            if source.stat().st_mtime_ns != stat.st_mtime_ns or source.stat().st_size != stat.st_size:
                raise OSError('Markdown changed during conversion')
            fd, name = tempfile.mkstemp(prefix='.markdown-', suffix='.html', dir=target.parent)
            try:
                with os.fdopen(fd, 'w', encoding='utf-8') as file:
                    file.write(rendered)
                os.replace(name, target)
            finally:
                if os.path.exists(name):
                    os.unlink(name)
            return target
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

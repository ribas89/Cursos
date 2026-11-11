"""Filesystem course catalog. Every direct child folder is a course;
all deeper folders are recursive modules; supported files are lessons.
The library is deliberately never modified.
"""
from __future__ import annotations
import os
import re
from pathlib import Path

VIDEO = {'.mp4', '.webm', '.mkv', '.m4v', '.mov', '.avi', '.wmv', '.flv'}
AUDIO = {'.mp3', '.m4a', '.wav', '.aac', '.ogg', '.flac'}
IMAGE = {'.png', '.apng', '.jpg', '.jpeg', '.jfif', '.gif', '.webp',
         '.bmp', '.ico', '.svg', '.avif', '.heic', '.heif', '.tif', '.tiff'}
TEXT = {'.txt', '.csv', '.tsv', '.json', '.xml', '.yaml', '.yml',
        '.log', '.ini', '.cfg', '.conf', '.css', '.js', '.py', '.sh', '.sql',
        '.nfo', '.cue', '.srt', '.vtt'}
HTML = {'.html', '.htm', '.xhtml'}
OFFICE = {'.doc', '.docx', '.odt', '.rtf', '.ppt', '.pptx', '.odp', '.xls', '.xlsx', '.ods'}
SUBTITLES = {'.vtt', '.srt'}

# Every regular, non-hidden file can be listed and downloaded. These sets
# only decide whether Cursos provides an inline browser preview.


def natural_key(value):
    return [int(s) if s.isdigit() else s.casefold() for s in re.split(r'(\d+)', str(value))]


def hidden(path):
    return path.name.startswith('.') or path.name in {'__MACOSX', 'System Volume Information'}


def root_path():
    return Path(os.environ.get('COURSES_ROOT', '/app/courses')).resolve()


def get_course_path(course_id):
    if not course_id or course_id in ('.', '..') or '/' in course_id or '\\' in course_id or '\x00' in course_id or course_id.startswith('.'):
        raise ValueError('Invalid course')
    root = root_path()
    course = root / course_id
    if course.is_symlink() or not course.is_dir() or course.resolve().parent != root:
        raise FileNotFoundError('Course not found')
    return course


def get_lesson_path(course_id, relative):
    course = get_course_path(course_id)
    rel = Path(relative)
    if rel.is_absolute() or not rel.parts or any(part.startswith('.') or part in ('..', '__MACOSX') for part in rel.parts):
        raise ValueError('Invalid file path')
    full = (course / rel).resolve()
    if not full.is_relative_to(course.resolve()) or not full.is_file():
        raise FileNotFoundError('File not found')
    # Don't follow any symlink even if it points back inside the library.
    current = course
    for part in rel.parts:
        current = current / part
        if current.is_symlink():
            raise FileNotFoundError('File unavailable')
    return full


def list_courses():
    root = root_path()
    if not root.is_dir():
        return []
    return [{'id': p.name, 'name': p.name} for p in sorted(root.iterdir(), key=lambda p: natural_key(p.name))
            if p.is_dir() and not p.is_symlink() and not hidden(p)]


def file_kind(path):
    ext = path.suffix.lower()
    if ext in VIDEO: return 'video'
    if ext in AUDIO: return 'audio'
    if ext == '.pdf': return 'pdf'
    if ext in OFFICE: return 'office'
    if ext in IMAGE: return 'image'
    if ext in HTML: return 'html'
    if ext in {'.md', '.markdown'}: return 'markdown'
    if ext in TEXT: return 'text'
    return 'download'


def scan_course(course_id, completed=None, depth_limit=24):
    course = get_course_path(course_id)
    done = set(completed or [])
    visited = [0]

    def scan(where, depth=0):
        if depth > depth_limit:
            return {'name': where.name, 'folders': [], 'lessons': [], 'total': 0, 'done': 0, 'truncated': True}
        node = {'name': where.name, 'folders': [], 'lessons': [], 'total': 0, 'done': 0, 'truncated': False}
        try:
            entries = sorted(where.iterdir(), key=lambda p: natural_key(p.name))
        except OSError:
            return node
        for p in entries:
            if hidden(p) or p.is_symlink():
                continue
            if p.is_dir():
                child = scan(p, depth + 1)
                if child['total'] or child['folders'] or child['truncated']:
                    node['folders'].append(child)
                    node['total'] += child['total']
                    node['done'] += child['done']
            elif p.is_file():
                relative = p.relative_to(course).as_posix()
                visited[0] += 1
                item = {'name': p.stem, 'filename': p.name, 'path': relative, 'kind': file_kind(p), 'completed': relative in done}
                node['lessons'].append(item)
                node['total'] += 1
                node['done'] += int(item['completed'])
        return node

    tree = scan(course)
    return {'id': course_id, 'name': course_id, 'tree': tree,
            'total': tree['total'], 'done': tree['done']}


def flatten(node):
    """Returns lessons in the same order as the rendered tree: files then folders.
    Files and folders are each naturally sorted; no mutations are made.
    """
    result = list(node['lessons'])
    for folder in node['folders']:
        result.extend(flatten(folder))
    return result


def subtitle_for(course_id, relative):
    full = get_lesson_path(course_id, relative)
    for ext in ('.vtt', '.srt'):
        p = full.with_suffix(ext)
        if p.is_file() and not p.is_symlink():
            return p.relative_to(get_course_path(course_id)).as_posix()
    return None

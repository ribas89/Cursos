"""Multi-user accounts and independent lesson progress, SQLite only."""
import os
import sqlite3
import base64
import binascii
import re
from io import BytesIO
from PIL import Image, UnidentifiedImageError
from pathlib import Path
from werkzeug.security import check_password_hash, generate_password_hash


def db_path():
    return Path(os.environ.get('DATABASE_PATH', '/app/data/cursos.sqlite3'))


def connect():
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(path), timeout=15)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA busy_timeout=15000')
    db.execute('PRAGMA foreign_keys=ON')
    return db


def init_db():
    with connect() as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.executescript('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS user_oidc_identities (
                issuer TEXT NOT NULL,
                subject TEXT NOT NULL,
                user_id INTEGER NOT NULL UNIQUE,
                linked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (issuer, subject),
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS user_oidc_avatars (
                user_id INTEGER PRIMARY KEY,
                mime TEXT NOT NULL,
                image BLOB NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS lesson_progress (
                user_id INTEGER NOT NULL,
                course_id TEXT NOT NULL,
                lesson_path TEXT NOT NULL,
                completed INTEGER NOT NULL DEFAULT 0,
                progress_seconds INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, course_id, lesson_path),
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS progress_by_user_course ON lesson_progress(user_id,course_id);
            CREATE TABLE IF NOT EXISTS course_favorites (
                user_id INTEGER NOT NULL,
                course_id TEXT NOT NULL,
                PRIMARY KEY (user_id, course_id),
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS course_activity (
                user_id INTEGER NOT NULL,
                course_id TEXT NOT NULL,
                visited_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, course_id),
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
        ''')


def create_user(email, password):
    email = email.strip().lower()
    with connect() as db:
        cursor = db.execute('INSERT INTO users(email,password_hash) VALUES (?,?)',
                            (email, generate_password_hash(password, method='scrypt')))
        return cursor.lastrowid


def find_user(email):
    with connect() as db:
        row = db.execute('SELECT id,email,password_hash FROM users WHERE email=?', (email.strip().lower(),)).fetchone()
    return dict(row) if row else None


def user_by_id(user_id):
    with connect() as db:
        row = db.execute('SELECT id,email FROM users WHERE id=?', (user_id,)).fetchone()
    return dict(row) if row else None


_DUMMY_HASH = generate_password_hash('invalid-placeholder-password', method='scrypt')

def verify_user(email, password):
    user = find_user(email)
    if user and check_password_hash(user['password_hash'], password):
        return user
    # Equalize timing when email doesn't exist.
    if not user:
        check_password_hash(_DUMMY_HASH, password)
    return None


def progress_map(user_id, course_id):
    with connect() as db:
        rows = db.execute('SELECT lesson_path,completed,progress_seconds FROM lesson_progress WHERE user_id=? AND course_id=?', (user_id, course_id)).fetchall()
    return {row['lesson_path']: {'completed': bool(row['completed']), 'seconds': row['progress_seconds']} for row in rows}


def save_progress(user_id, course_id, relative, completed=None, seconds=None):
    """Atomically update one user's progress without losing completion on autosave."""
    # completion=None means "do not change completion state"; completed=False is an explicit undo.
    wanted = None if completed is None else int(bool(completed))
    elapsed = None if seconds is None else max(0, min(int(seconds), 864000))
    with connect() as db:
        db.execute("""INSERT INTO lesson_progress (user_id,course_id,lesson_path,completed,progress_seconds,updated_at)
             VALUES (?,?,?,?,?,CURRENT_TIMESTAMP)
             ON CONFLICT(user_id,course_id,lesson_path) DO UPDATE SET
                completed=COALESCE(?,lesson_progress.completed),
                progress_seconds=COALESCE(?,lesson_progress.progress_seconds),
                updated_at=CURRENT_TIMESTAMP""",
            (user_id, course_id, relative, wanted if wanted is not None else 0,
             elapsed if elapsed is not None else 0, wanted, elapsed))
        row = db.execute('SELECT completed,progress_seconds FROM lesson_progress WHERE user_id=? AND course_id=? AND lesson_path=?',
                         (user_id, course_id, relative)).fetchone()
    return {'completed': bool(row['completed']), 'seconds': row['progress_seconds']}


def change_password(email, password):
    with connect() as db:
        cursor = db.execute('UPDATE users SET password_hash=? WHERE email=?',
                            (generate_password_hash(password, method='scrypt'),email.lower().strip()))
        return cursor.rowcount > 0


def library_state(user_id):
    """Per-user favorites and started course ids, no filesystem content in DB."""
    with connect() as db:
        favorites = [row['course_id'] for row in db.execute(
            'SELECT course_id FROM course_favorites WHERE user_id=?', (user_id,))]
        visited = [row['course_id'] for row in db.execute(
            'SELECT course_id FROM course_activity WHERE user_id=? ORDER BY visited_at DESC', (user_id,))]
        progress = [row['course_id'] for row in db.execute(
            'SELECT DISTINCT course_id FROM lesson_progress WHERE user_id=?', (user_id,))]
    return {'favorites': favorites, 'started': list(dict.fromkeys(visited + progress))}


def touch_course(user_id, course_id):
    with connect() as db:
        db.execute("""INSERT INTO course_activity(user_id,course_id,visited_at)
                      VALUES (?,?,CURRENT_TIMESTAMP)
                      ON CONFLICT(user_id,course_id) DO UPDATE SET visited_at=CURRENT_TIMESTAMP""",
                   (user_id, course_id))


def set_favorite(user_id, course_id, favorite):
    with connect() as db:
        if favorite:
            db.execute('INSERT OR IGNORE INTO course_favorites(user_id,course_id) VALUES (?,?)',
                       (user_id, course_id))
        else:
            db.execute('DELETE FROM course_favorites WHERE user_id=? AND course_id=?',
                       (user_id, course_id))
    return favorite


# OIDC identities are linked by verified issuer + stable subject, NEVER by email alone.
def oidc_identity_for_user(user_id):
    with connect() as db:
        row = db.execute('SELECT issuer,subject FROM user_oidc_identities WHERE user_id=?',
                         (user_id,)).fetchone()
    return dict(row) if row else None


def link_oidc_identity(user_id, issuer, subject):
    """Link two identities only after authenticating both of them.

    Returns True if linked (or already linked), False if either is linked elsewhere.
    """
    with connect() as db:
        match = db.execute('SELECT user_id FROM user_oidc_identities WHERE issuer=? AND subject=?',
                           (issuer, subject)).fetchone()
        previous = db.execute('SELECT issuer,subject FROM user_oidc_identities WHERE user_id=?',
                              (user_id,)).fetchone()
        if match and match['user_id'] != user_id:
            return False
        if previous and (previous['issuer'], previous['subject']) != (issuer, subject):
            return False
        if not match:
            db.execute('INSERT INTO user_oidc_identities(issuer,subject,user_id) VALUES (?,?,?)',
                       (issuer, subject, user_id))
    return True


def login_or_create_oidc_user(issuer, subject, email):
    """Return (user, status), with no automatic matching to local password accounts.

    A matching existing email requires explicit linking from an authenticated local
    account, avoiding a silent account takeover or loss of lesson progress.
    """
    email = email.strip().lower()
    with connect() as db:
        existing = db.execute('''SELECT u.id,u.email FROM user_oidc_identities AS i
                                  JOIN users AS u ON u.id=i.user_id
                                  WHERE i.issuer=? AND i.subject=?''',
                              (issuer, subject)).fetchone()
        if existing:
            return dict(existing), 'existing'
        occupied = db.execute('SELECT id FROM users WHERE email=?', (email,)).fetchone()
        if occupied:
            return None, 'email_conflict'
        # A strong random password nobody knows, for compatibility with the
        # existing NOT NULL SQL password_hash column. No password is exposed.
        password_hash = generate_password_hash(
            __import__('secrets').token_urlsafe(48), method='scrypt')
        cursor = db.execute('INSERT INTO users(email,password_hash) VALUES (?,?)',
                            (email, password_hash))
        db.execute('INSERT INTO user_oidc_identities(issuer,subject,user_id) VALUES (?,?,?)',
                   (issuer, subject, cursor.lastrowid))
        return {'id': cursor.lastrowid, 'email': email}, 'created'


# Avatar comes ONLY from the custom Authentik attributes.avatar OIDC claim.
# There is no fallback to Steam, Gravatar, or generic OIDC picture.
def _decode_custom_avatar(value):
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r'data:image/(jpeg|png|webp);base64,([A-Za-z0-9+/]+={0,2})', value)
    if not match or len(match.group(2)) > 700_000:
        return None
    mime = 'image/' + match.group(1)
    try:
        raw = base64.b64decode(match.group(2), validate=True)
        if not raw or len(raw) > 512 * 1024:
            return None
        with Image.open(BytesIO(raw)) as image:
            if image.format.lower() not in ('jpeg', 'png', 'webp') or 'image/' + image.format.lower() != mime:
                return None
            if image.width * image.height > 4_000_000:
                return None
            image.verify()
        return mime, raw
    except (binascii.Error, ValueError, OSError, UnidentifiedImageError):
        return None


def update_oidc_avatar(user_id, value):
    # Refresh avatar for new or existing SSO users; empty value clears it.
    if value is None or value == '':
        with connect() as db:
            db.execute('DELETE FROM user_oidc_avatars WHERE user_id=?', (user_id,))
        return True
    parsed = _decode_custom_avatar(value)
    if not parsed:
        return False  # Malformed input must not erase a previously working avatar.
    mime, raw = parsed
    with connect() as db:
        db.execute('''INSERT INTO user_oidc_avatars(user_id,mime,image)
                      VALUES (?,?,?)
                      ON CONFLICT(user_id) DO UPDATE SET
                         mime=excluded.mime,
                         image=excluded.image,
                         updated_at=CURRENT_TIMESTAMP''', (user_id, mime, raw))
    return True


def oidc_avatar_for_user(user_id):
    with connect() as db:
        row = db.execute('SELECT mime,image FROM user_oidc_avatars WHERE user_id=?', (user_id,)).fetchone()
    return (row['mime'], row['image']) if row else None


def has_oidc_avatar(user_id):
    with connect() as db:
        row = db.execute('SELECT 1 FROM user_oidc_avatars WHERE user_id=?', (user_id,)).fetchone()
    return row is not None

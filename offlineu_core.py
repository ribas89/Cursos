#!/usr/bin/env python3
"""Cursos: multi-course, multi-user adaptation of OfflineU (MIT)."""
import os
import re
import secrets
import sqlite3
import threading
import time
from io import BytesIO
from functools import wraps
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlsplit

from oidc_internal import validate_oidc_host, oidc_backend_request

from flask import (Flask, abort, flash, g, jsonify, redirect, render_template,
                   request, send_file, session, url_for)
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.exceptions import HTTPException

from catalog import (get_course_path, get_lesson_path, list_courses,
                     scan_course, flatten, subtitle_for, file_kind)
from store import (init_db, user_by_id, verify_user, create_user,
                   update_oidc_avatar, oidc_avatar_for_user, has_oidc_avatar,
                   progress_map, save_progress, library_state, touch_course, set_favorite,
                   oidc_identity_for_user, link_oidc_identity, login_or_create_oidc_user)
from i18n import TRANSLATIONS, language_for, translate
from pdf_mobile import page_dimensions, render_page, cache_version
from office_preview import convert_to_pdf, OFFICE_EXTENSIONS
from markdown_preview import (convert_markdown, markdown_version, MARKDOWN_EXTENSIONS)
from archive_preview import archive_preview, SUPPORTED_ARCHIVES
from memes_service import IMAGE_MIMETYPES, meme_files, random_meme_file

app = Flask(__name__)
secret = os.environ.get('SECRET_KEY', '')
if not secret or len(secret) < 32 or secret.startswith('REPLACE_'):
    raise RuntimeError('Set a random SECRET_KEY of at least 32 characters in .env')
app.secret_key = secret
app.config.update(SESSION_COOKIE_HTTPONLY=True,
                  SESSION_COOKIE_SAMESITE='Lax',
                  SESSION_COOKIE_SECURE=os.environ.get('COOKIE_SECURE', 'true').lower() == 'true',
                  MAX_CONTENT_LENGTH=16 * 1024)
# A single trusted edge proxy (Traefik); do not expose this container directly.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

# Authentik OIDC is optional; existing SQL email/password logins keep working.
_OIDC_KEYS = ('OIDC_DISCOVERY_URL', 'OIDC_CLIENT_ID', 'OIDC_CLIENT_SECRET', 'OIDC_REDIRECT_URI')
OIDC_ENABLED = all(os.environ.get(key) for key in _OIDC_KEYS)
if not OIDC_ENABLED and any(os.environ.get(key) for key in _OIDC_KEYS):
    raise RuntimeError('To enable SSO, configure all four OIDC_* settings in .env')
OIDC_REQUIRED_GROUP = os.environ.get('OIDC_REQUIRED_GROUP', '').strip()
OIDC_HOST = os.environ.get('OIDC_HOST', '').strip()
if OIDC_HOST:
    OIDC_HOST = validate_oidc_host(OIDC_HOST)
    if not OIDC_ENABLED:
        raise RuntimeError('OIDC_HOST requires the four primary OIDC_* settings')
    if urlsplit(os.environ['OIDC_DISCOVERY_URL']).scheme not in ('http', 'https'):
        raise RuntimeError('OIDC_DISCOVERY_URL must use http or https')
_oidc = None
if OIDC_ENABLED:
    from authlib.integrations.flask_client import OAuth
    from authlib.integrations.requests_client import OAuth2Session
    from authlib.integrations.base_client.errors import OAuthError
    _oauth = OAuth(app)

    if OIDC_HOST:
        class _InternalOIDCSession(OAuth2Session):
            """Use internal Docker HTTP while keeping OIDC's public identity."""

            def request(self, method, url, **kwargs):
                network_url, forwarded = oidc_backend_request(
                    url, os.environ['OIDC_DISCOVERY_URL'], OIDC_HOST
                )
                if forwarded:
                    headers = dict(kwargs.get('headers') or {})
                    headers.update(forwarded)
                    kwargs['headers'] = headers
                    # Never forward the authorization code or bearer token to
                    # a different address if a backend endpoint redirects.
                    kwargs['allow_redirects'] = False
                return super().request(method, network_url, **kwargs)
    _oidc = _oauth.register(
        name='authentik',
        client_id=os.environ['OIDC_CLIENT_ID'],
        client_secret=os.environ['OIDC_CLIENT_SECRET'],
        server_metadata_url=os.environ['OIDC_DISCOVERY_URL'],
        # Generic User Avatar mapping is attached to the existing profile scope.
        client_kwargs={'scope': 'openid email profile', 'code_challenge_method': 'S256'},
    )
    if OIDC_HOST:
        _oidc.client_cls = _InternalOIDCSession

init_db()
_limiter = {}
_limiter_lock = threading.Lock()


def limited(key, limit=12, window=900):
    """Simple process-local throttling for small private deployments."""
    now = time.monotonic()
    with _limiter_lock:
        timestamps = [t for t in _limiter.get(key, []) if t > now - window]
        if len(_limiter) > 5000:
            for old_key, ts in list(_limiter.items()):
                if not ts or ts[-1] < now - window:
                    _limiter.pop(old_key, None)
        if len(timestamps) >= limit:
            _limiter[key] = timestamps
            return True
        timestamps.append(now)
        _limiter[key] = timestamps
    return False


@app.before_request
def load_user():
    g.user = user_by_id(session['uid']) if isinstance(session.get('uid'), int) else None
    if g.user is None and 'uid' in session:
        session.clear()
    if 'csrf' not in session:
        session['csrf'] = secrets.token_urlsafe(32)
    if request.method in ('POST', 'PUT', 'PATCH', 'DELETE'):
        token = request.headers.get('X-CSRF-Token') or request.form.get('csrf_token')
        if not token or not secrets.compare_digest(token, session['csrf']):
            abort(400, description='Invalid CSRF token')


@app.context_processor
def template_context():
    return {'csrf_token': lambda: session.get('csrf', ''), 'current_user': g.get('user'),
            'registration_enabled': os.environ.get('ALLOW_REGISTRATION', 'true').lower() == 'true',
            'sso_enabled': OIDC_ENABLED,
            'sso_linked': bool(g.get('user') and oidc_identity_for_user(g.user['id'])),
            'sso_login': session.get('auth_method') == 'oidc',
            'oidc_avatar_available': bool(g.get('user') and has_oidc_avatar(g.user['id'])),
            'lang': language_for(request.cookies.get('cursos-language')),
            'tr': lambda key: translate(language_for(request.cookies.get('cursos-language')), key),
            'ui_text': TRANSLATIONS[language_for(request.cookies.get('cursos-language'))]}


@app.after_request
def secure_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'same-origin'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    if request.path == '/account/avatar':
        response.headers['Cache-Control'] = 'private, no-store'
    elif request.path.startswith(('/pdf-preview/', '/office-page/', '/markdown-preview/')):
        # A versioned image URL is immutable and its generated JPEG is permanent.
        response.headers['Cache-Control'] = (
            'private, max-age=31536000, immutable'
            if response.status_code in (200, 304) and request.args.get('v')
            else ('private, no-cache' if response.status_code in (200, 304) else 'no-store')
        )
    else:
        response.headers['Cache-Control'] = ('private, no-store'
              if request.path.startswith(('/login', '/register', '/api/', '/logout', '/auth/oidc/'))
              else 'private, no-cache')
    response.headers['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=()'
    if request.is_secure:
        response.headers['Strict-Transport-Security'] = 'max-age=31536000'
    return response


@app.get('/account/avatar')
def account_avatar():
    if not g.user:
        abort(404)
    avatar = oidc_avatar_for_user(g.user['id'])
    if not avatar:
        abort(404)
    mime, image = avatar
    return send_file(BytesIO(image), mimetype=mime, conditional=False)


def login_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        if not g.user:
            if request.path.startswith(('/api/', '/media/')):
                return jsonify({'error': 'Authentication required'}), 401
            return redirect(url_for('login', next=request.path))
        return f(*a, **kw)
    return wrapper


def safe_local_target(raw):
    """Never accept an external redirect or an ambiguous backslash URL."""
    if not isinstance(raw, str) or not raw.startswith('/') or len(raw) > 2048:
        return url_for('library')
    if raw.startswith('//') or '\\' in raw or any(ord(c) < 32 for c in raw):
        return url_for('library')
    parts = urlsplit(raw)
    if parts.scheme or parts.netloc:
        return url_for('library')
    return raw


@app.before_request
def automatic_oidc_login():
    """One non-interactive SSO check for anonymous HTML page navigation.

    Cookies belong to tik.esquema.qzz.io: Cursos cannot directly inspect them.
    prompt=none asks the identity provider without showing a login page.
    """
    if not OIDC_ENABLED or g.user or request.method != 'GET':
        return None
    if request.endpoint not in ('library', 'course_page', 'lesson_page'):
        return None
    if request.headers.get('Sec-Fetch-Mode', 'navigate') != 'navigate':
        return None
    now = int(time.time())
    # A previous silent attempt may have failed before reaching Authentik
    # (for example, during a Docker DNS outage). Do not suppress attempts
    # for five minutes after transient failures, but prevent redirect loops.
    if session.get('oidc_auto_suppressed') or now - session.get('oidc_checked_at', 0) < 20:
        return None
    session['oidc_checked_at'] = now
    requested = request.full_path.rstrip('?')
    return redirect(url_for('oidc_start', mode='silent', next=requested))


@app.before_request
def automatic_avatar_refresh():
    """Refresh custom avatar for existing OIDC users without changing anonymous SSO.

    Runs only after the original automatic_oidc_login check. On an existing
    logged-in session, revisit Authentik at most once every five minutes.
    """
    if (not OIDC_ENABLED or not g.user or session.get('auth_method') != 'oidc'
            or request.method != 'GET'):
        return None
    if request.endpoint not in ('library', 'course_page', 'lesson_page'):
        return None
    if request.headers.get('Sec-Fetch-Mode', 'navigate') != 'navigate':
        return None
    now = int(time.time())
    if now - session.get('oidc_avatar_checked_at', 0) < 300:
        return None
    session['oidc_avatar_checked_at'] = now
    return redirect(url_for('oidc_start', mode='refresh',
                            next=request.full_path.rstrip('?')))


@app.get('/auth/oidc/start')
def oidc_start():
    if not OIDC_ENABLED:
        abort(404)
    mode = request.args.get('mode', 'login')
    if mode not in ('silent', 'login', 'link', 'refresh'):
        abort(400)
    if mode in ('link', 'refresh') and not g.user:
        return redirect(url_for('login'))
    if g.user and mode not in ('link', 'refresh'):
        return redirect(safe_local_target(request.args.get('next', '/')))
    target = safe_local_target(request.args.get('next', '/'))
    session['oidc_intent'] = {
        'mode': mode, 'next': target, 'uid': g.user['id'] if g.user else None,
        'started': int(time.time()),
    }
    try:
        parameters = {'prompt': 'none'} if mode in ('silent', 'refresh') else {}
        return _oidc.authorize_redirect(os.environ['OIDC_REDIRECT_URI'], **parameters)
    except Exception:
        app.logger.exception('Unable to start Authentik SSO')
        session.pop('oidc_intent', None)
        if mode not in ('silent', 'refresh'):
            flash(translate(language_for(request.cookies.get('cursos-language')), 'sso_error'), 'error')
        return redirect(target)


@app.get('/auth/oidc/callback')
def oidc_callback():
    if not OIDC_ENABLED:
        abort(404)
    intent = session.pop('oidc_intent', None)
    if not isinstance(intent, dict) or int(time.time()) - intent.get('started', 0) > 600:
        abort(400, description='SSO sign-in request expired or missing')
    mode = intent['mode']
    target = safe_local_target(intent.get('next', '/'))

    try:
        # Authlib verifies OAuth state, exchanges the authorization code,
        # and validates the ID token signature, issuer, audience and nonce.
        token = _oidc.authorize_access_token()
        claims = token.get('userinfo')
        if not claims or not isinstance(claims, Mapping):
            raise ValueError('No validated OIDC identity claims')
        # In Authentik 2026.8, scope mappings are returned by UserInfo;
        # including them in the ID token requires an optional provider toggle.
        # Fetch UserInfo and verify its sub against the validated ID token.
        metadata = _oidc.load_server_metadata()
        userinfo_url = metadata.get('userinfo_endpoint')
        if not userinfo_url:
            raise ValueError('OIDC discovery lacks UserInfo endpoint')
        userinfo_response = _oidc.get(userinfo_url, token=token)
        userinfo_response.raise_for_status()
        profile = userinfo_response.json()
        if not isinstance(profile, dict) or profile.get('sub') != claims.get('sub'):
            raise ValueError('OIDC UserInfo subject mismatch')
    except OAuthError as exc:
        # Also log silent failures: previously a prompt=none rejection was
        # swallowed, making successful manual login look inexplicable.
        app.logger.warning('Authentik %s SSO declined: %s', mode, exc.error)
        if mode not in ('silent', 'refresh'):
            flash(translate(language_for(request.cookies.get('cursos-language')), 'sso_error'), 'error')
        return redirect(target)
    except Exception:
        app.logger.exception('Authentik OIDC callback failed')
        if mode not in ('silent', 'refresh'):
            flash(translate(language_for(request.cookies.get('cursos-language')), 'sso_error'), 'error')
        return redirect(target)

    issuer = claims.get('iss', '')
    subject = claims.get('sub', '')
    email = profile.get('email', '')
    groups = profile.get('groups', [])
    valid_identity = (isinstance(issuer, str) and 0 < len(issuer) <= 512
                      and isinstance(subject, str) and 0 < len(subject) <= 512
                      and isinstance(email, str)
                      and re.fullmatch(r'[^\s@]{1,64}@[^\s@]{1,190}', email)
                      and len(email) <= 254)
    if not valid_identity or (OIDC_REQUIRED_GROUP and
                              (not isinstance(groups, list) or OIDC_REQUIRED_GROUP not in groups)):
        app.logger.warning('Authentik %s SSO rejected: missing identity claims or group mismatch', mode)
        if mode not in ('silent', 'refresh'):
            flash(translate(language_for(request.cookies.get('cursos-language')), 'sso_not_allowed'), 'error')
        return redirect(target)

    try:
        if mode == 'refresh':
            # A background refresh must never swap the logged-in Cursos user.
            if not g.user or g.user['id'] != intent.get('uid'):
                return redirect(target)
            identity = oidc_identity_for_user(g.user['id'])
            if not identity or identity['issuer'] != issuer or identity['subject'] != subject:
                app.logger.warning('Authentik avatar refresh identity mismatch')
                return redirect(target)
            if 'avatar' in profile:
                update_oidc_avatar(g.user['id'], profile['avatar'])
            session['oidc_avatar_checked_at'] = int(time.time())
            return redirect(target)

        if mode == 'link':
            if not g.user or g.user['id'] != intent['uid']:
                abort(403)
            if not link_oidc_identity(g.user['id'], issuer, subject):
                flash(translate(language_for(request.cookies.get('cursos-language')), 'sso_link_conflict'), 'error')
            else:
                if 'avatar' in profile:
                    update_oidc_avatar(g.user['id'], profile['avatar'])
                flash(translate(language_for(request.cookies.get('cursos-language')), 'sso_linked'), 'success')
            return redirect(target)

        user, status = login_or_create_oidc_user(issuer, subject, email)
        if status == 'email_conflict':
            app.logger.warning('Authentik %s SSO cannot auto-link existing SQL email', mode)
            if mode not in ('silent', 'refresh'):
                flash(translate(language_for(request.cookies.get('cursos-language')), 'sso_email_conflict'), 'error')
            return redirect(url_for('login') if mode == 'login' else target)
        # Synchronize the avatar for both new and ALREADY CREATED accounts.
        if 'avatar' in profile:
            update_oidc_avatar(user['id'], profile['avatar'])
    except sqlite3.IntegrityError:
        app.logger.warning('SSO account was concurrently changed')
        if mode not in ('silent', 'refresh'):
            flash(translate(language_for(request.cookies.get('cursos-language')), 'sso_error'), 'error')
        return redirect(target)

    # Do not keep OAuth tokens in the client-side Flask session cookie.
    session.clear()
    session['uid'] = user['id']
    session['csrf'] = secrets.token_urlsafe(32)
    session['auth_method'] = 'oidc'
    session['oidc_checked_at'] = int(time.time())
    session['oidc_avatar_checked_at'] = int(time.time())
    app.logger.info('Authentik %s SSO completed', mode)
    return redirect(target)


@app.get('/language/<code>')
def language(code):
    if code not in TRANSLATIONS:
        abort(404)
    from urllib.parse import urlsplit
    referer = request.referrer or ''
    parts = urlsplit(referer)
    back = (parts.path + (('?' + parts.query) if parts.query else '')) if parts.netloc == request.host and parts.path.startswith('/') else url_for('library')
    response = redirect(back)
    response.set_cookie('cursos-language', code, max_age=365 * 86400,
                        samesite='Lax', httponly=True,
                        secure=app.config['SESSION_COOKIE_SECURE'])
    return response


@app.route('/health')
def health():
    return jsonify(status='ok')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if g.user:
        return redirect(url_for('library'))
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()[:254]
        password = request.form.get('password', '')
        if limited(('login', request.remote_addr, email), 10, 900):
            flash(translate(language_for(request.cookies.get('cursos-language')), 'too_many_login'), 'error')
        else:
            user = verify_user(email, password)
            if user:
                session.clear()
                session['uid'] = user['id']
                session['csrf'] = secrets.token_urlsafe(32)
                target = request.form.get('next', '')
                return redirect(target if target.startswith('/') and not target.startswith('//') and not target.startswith('/\\') else url_for('library'))
            flash(translate(language_for(request.cookies.get('cursos-language')), 'invalid_credentials'), 'error')
    return render_template('auth.html', mode='login', next=request.args.get('next', ''))


@app.route('/register', methods=['GET', 'POST'])
def register():
    if os.environ.get('ALLOW_REGISTRATION', 'true').lower() != 'true':
        abort(404)
    if g.user:
        return redirect(url_for('library'))
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        if limited(('register', request.remote_addr), 5, 3600):
            flash(translate(language_for(request.cookies.get('cursos-language')), 'too_many_registrations'), 'error')
        elif not re.fullmatch(r'[^\s@]{1,64}@[^\s@]{1,190}', email) or len(email) > 254:
            flash(translate(language_for(request.cookies.get('cursos-language')), 'invalid_email'), 'error')
        elif not 10 <= len(password) <= 128:
            flash(translate(language_for(request.cookies.get('cursos-language')), 'invalid_password'), 'error')
        else:
            try:
                uid = create_user(email, password)
                session.clear()
                session['uid'] = uid
                session['csrf'] = secrets.token_urlsafe(32)
                return redirect(url_for('library'))
            except sqlite3.IntegrityError:
                flash(translate(language_for(request.cookies.get('cursos-language')), 'duplicate_email'), 'error')
    return render_template('auth.html', mode='register')


@app.post('/logout')
@login_required
def logout():
    session.clear()
    # Local logout does not destroy the Authentik session. Avoid silently
    # logging the user straight back in on the next page visit.
    session['oidc_auto_suppressed'] = True
    session['csrf'] = secrets.token_urlsafe(32)
    return redirect(url_for('library'))


@app.route('/')
def library():
    query = request.args.get('q', '').strip().casefold()
    courses = list_courses()
    if query:
        courses = [c for c in courses if query in c['name'].casefold()]
    return render_template('library.html', courses=courses, query=request.args.get('q', ''), total=len(list_courses()), show_memes=bool(meme_files()), state=library_state(g.user['id']) if g.user else {'favorites': [], 'started': []})


@app.get('/api/meme')
def random_meme():
    """Return one random meme image; no uploads or archive processing."""
    selected = random_meme_file()
    if selected is None:
        abort(404)
    try:
        response = send_file(selected, mimetype=IMAGE_MIMETYPES[selected.suffix.lower()],
                             conditional=False, etag=False, max_age=0)
    except OSError:
        abort(404)
    response.headers['Cache-Control'] = 'no-store'
    return response


@app.route('/course/<course_id>')
def course_page(course_id):
    try:
        progress = progress_map(g.user['id'], course_id) if g.user else {}
        course = scan_course(course_id, completed=[k for k, val in progress.items() if val['completed']])
    except (FileNotFoundError, ValueError):
        abort(404)
    if g.user:
        touch_course(g.user['id'], course_id)
    return render_template('course.html', course=course)


@app.route('/course/<course_id>/lesson/<path:relative>')
def lesson_page(course_id, relative):
    try:
        source = get_lesson_path(course_id, relative)
        if file_kind(source) not in {'video', 'audio', 'image', 'pdf', 'office', 'html', 'text', 'markdown', 'download'}:
            abort(404)
        progress = progress_map(g.user['id'], course_id) if g.user else {}
        course = scan_course(course_id, completed=[k for k, val in progress.items() if val['completed']])
    except (FileNotFoundError, ValueError):
        abort(404)
    lessons = flatten(course['tree'])
    paths = [item['path'] for item in lessons]
    if relative not in paths:
        abort(404)
    i = paths.index(relative)
    lesson = lessons[i]
    if g.user:
        touch_course(g.user['id'], course_id)
    pdf_pages = None
    pdf_version = None
    office_error = False
    archive_info = archive_preview(source) if source.suffix.lower() in SUPPORTED_ARCHIVES else None
    markdown_token = markdown_version(source) if lesson['kind'] == 'markdown' else None
    if lesson['kind'] in ('pdf', 'office'):
        try:
            document = source if lesson['kind'] == 'pdf' else convert_to_pdf(source)
            pdf_pages = page_dimensions(document)
            pdf_version = cache_version(document)
        except (ValueError, OSError, RuntimeError) as exc:
            app.logger.warning('No preview for document %s: %s', source, exc)
            office_error = lesson['kind'] == 'office'
        except Exception:
            app.logger.exception('Unexpected document preview error')
            office_error = lesson['kind'] == 'office'
    return render_template('lesson.html', course=course, lesson=lesson,
                           progress=progress.get(relative, {'completed': False, 'seconds': 0}),
                           subtitle=subtitle_for(course_id, relative) if lesson['kind'] == 'video' else None,
                           previous=paths[i - 1] if i else None,
                           following=paths[i + 1] if i + 1 < len(paths) else None,
                           location=Path(relative).parent.as_posix(),
                           pdf_pages=pdf_pages, pdf_version=pdf_version, office_error=office_error,
                           archive_preview=archive_info, markdown_token=markdown_token)


@app.post('/api/progress/<course_id>')
@login_required
def api_progress(course_id):
    data = request.get_json(silent=True) or {}
    relative = data.get('path', '')
    if not isinstance(relative, str) or len(relative) > 2048:
        return jsonify(error='Invalid path'), 400
    try:
        path = get_lesson_path(course_id, relative)
        seconds = data.get('seconds', None)
        if seconds is not None and (type(seconds) not in (int, float) or not 0 <= seconds <= 864000):
            return jsonify(error='Invalid progress'), 400
        completed = data.get('completed', None)
        if completed is not None and type(completed) is not bool:
            return jsonify(error='Invalid completion'), 400
        saved = save_progress(g.user['id'], course_id, relative, completed, seconds)
    except (FileNotFoundError, ValueError, TypeError):
        return jsonify(error='Lesson not found'), 404
    return jsonify(ok=True, **saved)


@app.post('/api/favorite/<course_id>')
@login_required
def api_favorite(course_id):
    try:
        get_course_path(course_id)
    except (ValueError, FileNotFoundError):
        abort(404)
    data = request.get_json(silent=True) or {}
    if type(data.get('favorite')) is not bool:
        return jsonify(error='Invalid favorite value'), 400
    return jsonify(ok=True, favorite=set_favorite(g.user['id'], course_id, data['favorite']))


@app.get('/pdf-preview/<course_id>/<int:page_number>/<path:relative>')
def pdf_preview_page(course_id, page_number, relative):
    """Serve a cached image of one PDF page for iOS inline scrolling."""
    if page_number < 1:
        abort(404)
    try:
        source = get_lesson_path(course_id, relative)
        if source.suffix.lower() != '.pdf':
            abort(404)
        if request.args.get('v') and request.args.get('v') != cache_version(source):
            abort(404)
        preview_path = render_page(source, page_number)
    except (FileNotFoundError, ValueError, OSError):
        abort(404)
    except Exception:
        app.logger.exception('Could not render PDF preview page')
        abort(500)
    response = send_file(preview_path, mimetype='image/jpeg', conditional=True)
    return response


@app.get('/office-preview/<course_id>/<path:relative>')
def office_pdf(course_id, relative):
    """Stream only the derived PDF, preserving original Office downloads."""
    try:
        source = get_lesson_path(course_id, relative)
        if source.suffix.lower() not in OFFICE_EXTENSIONS:
            abort(404)
        converted = convert_to_pdf(source)
    except (FileNotFoundError, ValueError):
        abort(404)
    except HTTPException:
        raise
    except Exception:
        app.logger.exception('Office conversion failed')
        abort(503)
    return send_file(converted, mimetype='application/pdf', conditional=True,
                     download_name=source.stem + '.pdf')


@app.get('/office-page/<course_id>/<int:page_number>/<path:relative>')
def office_pdf_page(course_id, page_number, relative):
    """Persistent JPEG pages for mobile Safari's Office PDF preview."""
    if page_number < 1:
        abort(404)
    try:
        source = get_lesson_path(course_id, relative)
        if source.suffix.lower() not in OFFICE_EXTENSIONS:
            abort(404)
        converted = convert_to_pdf(source)
        if request.args.get('v') and request.args.get('v') != cache_version(converted):
            abort(404)
        image_path = render_page(converted, page_number)
    except (FileNotFoundError, ValueError):
        abort(404)
    except HTTPException:
        raise
    except Exception:
        app.logger.exception('Office preview page failed')
        abort(503)
    return send_file(image_path, mimetype='image/jpeg', conditional=True)


@app.get('/markdown-preview/<course_id>/<path:relative>')
def markdown_html(course_id, relative):
    """Serve an isolated, cached Markdown-to-HTML preview."""
    try:
        source = get_lesson_path(course_id, relative)
        if source.suffix.lower() not in MARKDOWN_EXTENSIONS:
            abort(404)
        version = markdown_version(source)
        if request.args.get('v') and request.args.get('v') != version:
            abort(404)
        # Resolve local image and file references relative to the Markdown file.
        # The media route itself still enforces course path and symlink checks.
        base_url = url_for('media_file', course_id=course_id, relative=relative).rsplit('/', 1)[0] + '/'
        preview = convert_markdown(source, base_url)
    except ValueError:
        # Very large Markdown remains viewable as plain text.
        return redirect(url_for('media_file', course_id=course_id, relative=relative))
    except FileNotFoundError:
        abort(404)
    except HTTPException:
        raise
    except OSError:
        app.logger.exception('Could not generate Markdown preview')
        abort(503)
    response = send_file(preview, mimetype='text/html; charset=utf-8', conditional=True)
    # Always use an opaque origin and block scripts, forms, plugins and fetches.
    response.headers['Content-Security-Policy'] = (
        "sandbox; default-src 'none'; img-src http: https: data:; "
        "style-src 'unsafe-inline'; base-uri 'self'; object-src 'none'; "
        "form-action 'none'; frame-ancestors 'self'"
    )
    return response


@app.route('/media/<course_id>/<path:relative>')
def media_file(course_id, relative):
    try:
        path = get_lesson_path(course_id, relative)
    except (FileNotFoundError, ValueError):
        abort(404)
    is_download = request.args.get('download') == '1'
    extension = path.suffix.lower()
    kind = file_kind(path)
    # Files without a browser preview (including ISO, executables and unknown
    # extensions) must still download, even if the client omits ?download=1.
    if kind in ('download', 'office'):
        is_download = True

    # Treat source and configuration files as plain text, not executable HTML
    # or JavaScript. In particular, browsers must not sniff arbitrary uploads.
    mime = None
    if extension == '.vtt':
        # Preserve WebVTT's MIME type for the HTML5 video track element.
        mime = 'text/vtt'
    elif kind in ('text', 'markdown'):
        mime = 'text/plain; charset=utf-8'
    elif extension == '.svg':
        mime = 'image/svg+xml'
    elif extension in ('.jfif',):
        mime = 'image/jpeg'
    elif extension == '.apng':
        mime = 'image/apng'
    elif extension == '.avif':
        mime = 'image/avif'
    elif extension in ('.heic', '.heif'):
        mime = 'image/heic' if extension == '.heic' else 'image/heif'
    elif extension == '.xhtml':
        mime = 'application/xhtml+xml'

    response = send_file(path, as_attachment=is_download, download_name=path.name,
                         mimetype=mime, conditional=True, max_age=0)
    if kind == 'html' and not is_download:
        # Serve interactive course exercises in a sandboxed, opaque origin.
        response.headers['Content-Security-Policy'] = (
            "sandbox allow-scripts; default-src 'self' data: blob: "
            "'unsafe-inline' 'unsafe-eval'; frame-ancestors 'self'"
        )
    elif extension == '.svg' and not is_download:
        # SVG is an image in the viewer, but direct navigation must not run
        # scripts or read the application origin's cookies or local storage.
        response.headers['Content-Security-Policy'] = (
            "sandbox; default-src 'none'; style-src 'unsafe-inline'; "
            "img-src data:; frame-ancestors 'self'"
        )
    return response


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)

"""Nedara Claude Code Web — your local Claude Code sessions, in the browser.

An independent Nedara Project, not affiliated with or endorsed by Anthropic.

Usage:
    python3 app.py                  start the server (first start: web setup page)
    python3 app.py set-password     set or change the master password
    python3 app.py totp enable      turn on two-factor authentication
    python3 app.py totp disable     turn it off
    python3 app.py revoke-sessions  sign out every browser
"""

import argparse
import base64
import getpass
import hmac
import json
import logging
import os
import secrets
import sys
import threading
import time
from datetime import timedelta
from functools import wraps
from pathlib import Path

from flask import Flask, abort, jsonify, redirect, render_template, request, session, url_for
from flask_socketio import SocketIO, join_room, leave_room
from werkzeug.middleware.proxy_fix import ProxyFix

from config import BASE_DIR, EFFORT_LEVELS, load_config
from runner import RunnerError, RunnerManager, detach_from_parent_session
from security import (
    MIN_PASSWORD_LENGTH, AuditLog, AuthStore, LoginLimiter, apply_security_headers, csrf_token, csrf_valid,
    load_secret_key, write_private,
)
from transcripts import EventCache, SessionIndex, is_session_id, read_events
from workspace import FileViewer, WorkspaceError, git_changes

VERSION = json.loads((BASE_DIR / 'app.json').read_text())['version']
PAGE_SIZE = 600
TAIL_INTERVAL = 0.5
SIDEBAR_INTERVAL = 2.0

log = logging.getLogger('nccw')

config = load_config()
config.data_dir.mkdir(parents=True, exist_ok=True)
try:
    os.chmod(config.data_dir, 0o700)
except PermissionError:
    pass  # not the owner (e.g. a volume in a user-namespaced container)

auth = AuthStore(config.data_dir)
audit = AuditLog(config.data_dir)
limiter = LoginLimiter(config.max_login_attempts, config.lockout_minutes)
class WebSessions:
    """Sessions started or forked from this app, with the session each copy came from.

    Kept in data/web_sessions.json as {session id: parent id or null}.
    """

    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        data = json.loads(path.read_text()) if path.exists() else {}
        # Older versions stored a plain list of ids.
        self.sessions = dict.fromkeys(data) if isinstance(data, list) else data

    def __call__(self):
        return self.sessions

    def add(self, session_id, parent=None):
        with self.lock:
            self.sessions = {**self.sessions, session_id: parent}
            self.path.write_text(json.dumps(self.sessions, indent=1, sort_keys=True))


web_sessions = WebSessions(config.data_dir / 'web_sessions.json')
index = SessionIndex(config, web_sessions)
event_cache = EventCache()
files = FileViewer(config)

app = Flask(__name__)
if config.behind_proxy:
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.config.update(
    SECRET_KEY=load_secret_key(config.data_dir),
    SESSION_COOKIE_NAME='nccw_session',
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Strict',
    SESSION_COOKIE_SECURE=config.secure_cookies,
    PERMANENT_SESSION_LIFETIME=timedelta(hours=config.session_hours),
    MAX_CONTENT_LENGTH=2 * 1024 * 1024,
)
socketio = SocketIO(
    app,
    async_mode='threading',
    cors_allowed_origins=config.allowed_origins or None,
    max_http_buffer_size=2 * 1024 * 1024,
)


def emit(event, data, room):
    socketio.emit(event, data, to=room)


class UsageStore:
    """Last plan usage reported by the CLI (data/usage.json), shared with every browser."""

    def __init__(self, path):
        self.path = path
        self.usage = json.loads(path.read_text()) if path.exists() else None

    def update(self, usage):
        if not usage.get('plan') and self.usage:
            usage['plan'] = self.usage.get('plan')  # turns do not report it, refreshes do
        self.usage = usage
        write_private(self.path, json.dumps(usage))
        socketio.emit('usage', usage, to='app')


usage_store = UsageStore(config.data_dir / 'usage.json')
runners = RunnerManager(
    config, index, emit, lambda event, **kw: audit.write(event, **kw), on_usage=usage_store.update,
)


# ── Authentication ─────────────────────────────────────────────────────────

def client_ip():
    return request.remote_addr or ''


def is_authenticated():
    if not session.get('auth') or session.get('epoch') != auth.epoch:
        return False
    return time.time() - session.get('login_at', 0) < config.session_hours * 3600


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not is_authenticated():
            if request.path.startswith('/api/'):
                return jsonify(error='Your session has expired. Sign in again.'), 401
            return redirect(url_for('login'))
        if request.method == 'POST' and not csrf_valid(session, request.headers.get('X-CSRF-Token')):
            return jsonify(error='Invalid CSRF token. Reload the page.'), 403
        return view(*args, **kwargs)
    return wrapper


@app.after_request
def security_headers(response):
    return apply_security_headers(response)


# ── First-run setup ────────────────────────────────────────────────────────
# Without a master password the app starts in setup mode: every page leads to
# /setup, which only accepts the one-time code printed in the server log. A
# visitor who cannot read that log cannot claim the instance.

setup_state = None
CLAUDE_TOKEN_FILE = config.data_dir / 'claude_token'


def start_setup_mode():
    global setup_state
    setup_state = {'code': secrets.token_hex(4).upper(), 'totp': auth.new_totp_secret()}
    code = f"{setup_state['code'][:4]}-{setup_state['code'][4:]}"
    url = f'http://{"127.0.0.1" if config.host in ("0.0.0.0", "::") else config.host}:{config.port}/setup'
    banner = '─' * 60
    print(f'\n{banner}\n  First start: open {url}\n  One-time setup code: {code}\n{banner}\n', flush=True)
    audit.write('setup_started')


def _totp_qr(secret):
    import pyotp
    uri = pyotp.TOTP(secret).provisioning_uri(name='claude-code-web', issuer_name='Nedara')
    try:
        import qrcode
        from qrcode.image.svg import SvgPathImage
    except ImportError:
        return '', uri
    image = qrcode.make(uri, image_factory=SvgPathImage, border=1)
    svg = image.to_string()
    return 'data:image/svg+xml;base64,' + base64.b64encode(svg).decode(), uri


@app.before_request
def setup_gate():
    if setup_state is None or request.endpoint in ('setup', 'static'):
        return None
    if request.path.startswith('/api/') or request.path.startswith('/socket.io'):
        return jsonify(error='This instance is not set up yet.'), 503
    return redirect(url_for('setup'))


@app.route('/setup', methods=['GET', 'POST'])
def setup():
    global setup_state
    if setup_state is None:
        return redirect(url_for('login'))
    ip = client_ip()
    errors = []
    form = request.form
    if request.method == 'POST':
        wait = limiter.locked(ip)
        given = form.get('setup_code', '').replace('-', '').replace(' ', '').upper()
        password = form.get('password', '')
        code = form.get('code', '').replace(' ', '')
        claude_token = form.get('claude_token', '').strip()
        if wait:
            errors.append(f'Too many failed attempts. Try again in {wait // 60 + 1} min.')
        elif not csrf_valid(session, form.get('csrf')):
            errors.append('The form expired. Try again.')
        elif not hmac.compare_digest(given, setup_state['code']):
            limiter.fail(ip)
            audit.write('setup_failed', ip=ip)
            errors.append('Wrong setup code. It is printed in the server log.')
        else:
            if len(password) < MIN_PASSWORD_LENGTH:
                errors.append(f'The password needs at least {MIN_PASSWORD_LENGTH} characters.')
            elif password != form.get('password_confirm', ''):
                errors.append('The two passwords differ.')
            if code and not pyotp_verify(setup_state['totp'], code):
                errors.append('The authentication code is wrong. Check the time on your phone, or leave it empty.')
            if claude_token and not claude_token.startswith('sk-ant-'):
                errors.append('This does not look like a token from `claude setup-token`.')
        if not errors:
            auth.set_password(password)
            if code:
                auth.enable_totp(setup_state['totp'], code)
            if claude_token:
                write_private(CLAUDE_TOKEN_FILE, claude_token)
                if not config.oauth_token_file:
                    config.oauth_token_file = CLAUDE_TOKEN_FILE
            setup_state = None
            limiter.reset(ip)
            audit.write('setup_done', ip=ip, totp=bool(code), claude_token=bool(claude_token))
            session.clear()
            session.permanent = True
            session.update(auth=True, epoch=auth.epoch, login_at=time.time())
            return redirect(url_for('index_page'))
    qr, uri = _totp_qr(setup_state['totp'])
    return render_template(
        'setup.html', errors=errors, csrf=csrf_token(session), version=VERSION, qr=qr,
        totp_secret=setup_state['totp'], totp_uri=uri, cli_found=bool(config.claude_cli),
        token_configured=bool(config.oauth_token_file), totp_open=bool(form.get('code')),
    ), (400 if errors else 200)


def pyotp_verify(secret, code):
    import pyotp
    return pyotp.TOTP(secret).verify(code, valid_window=1)


@app.route('/login', methods=['GET', 'POST'])
def login():
    if is_authenticated():
        return redirect(url_for('index_page'))
    error = None
    ip = client_ip()
    if request.method == 'POST':
        wait = limiter.locked(ip)
        if wait:
            error = f'Too many failed attempts. Try again in {wait // 60 + 1} min.'
        elif not csrf_valid(session, request.form.get('csrf')):
            error = 'The form expired. Try again.'
        elif auth.verify(request.form.get('password', ''), request.form.get('code', '')):
            limiter.reset(ip)
            session.clear()
            session.permanent = True
            session.update(auth=True, epoch=auth.epoch, login_at=time.time())
            audit.write('login', ip=ip, user_agent=request.headers.get('User-Agent', '')[:200])
            return redirect(url_for('index_page'))
        else:
            limiter.fail(ip)
            audit.write('login_failed', ip=ip)
            error = 'Wrong password or code.'
    return render_template(
        'login.html', error=error, csrf=csrf_token(session), totp=auth.totp_enabled, version=VERSION,
    ), (429 if error and 'Too many' in error else 200)


@app.route('/logout', methods=['POST'])
def logout():
    if csrf_valid(session, request.form.get('csrf')):
        audit.write('logout', ip=client_ip())
        session.clear()
    return redirect(url_for('login'))


@app.route('/')
@login_required
def index_page():
    return render_template('index.html', csrf=csrf_token(session), version=VERSION)


# ── API ────────────────────────────────────────────────────────────────────

def _session_payload(item):
    state = runners.state_of(item['id'])
    return {**item, 'run_state': state}


def _get_session_or_404(session_id):
    if not is_session_id(session_id):
        abort(404)
    item = index.get(session_id)
    if not item:
        abort(404)
    return item


@app.route('/api/state')
@login_required
def api_state():
    return jsonify(
        version=VERSION,
        read_only=config.read_only or not config.claude_cli,
        cli_found=bool(config.claude_cli),
        permission_modes=list(config.allowed_permission_modes),
        default_permission_mode=config.default_permission_mode,
        models=config.models,
        default_model=config.default_model,
        efforts=list(EFFORT_LEVELS),
        roots=[str(r) for r in config.allowed_roots],
        home=str(Path.home()),
        usage=usage_store.usage,
        file_viewer=config.file_viewer,
        sessions=[_session_payload(s) for s in index.list()],
    )


@app.route('/api/sessions/<session_id>')
@login_required
def api_session(session_id):
    item = _get_session_or_404(session_id)
    path = index.find_path(session_id)
    events, offset = event_cache.get(path)
    before = request.args.get('before', type=int)
    end = len(events) if before is None else max(0, min(before, len(events)))
    start = max(0, end - PAGE_SIZE)
    # Never split a page between a tool call and its result.
    while start > 0 and events[start]['t'] == 'result':
        start -= 1
    page = events[start:end]
    if before is None:
        # Latest title/cost events may sit before the page start.
        page += [e for e in events[:start] if e['t'] in ('title', 'cost')][-2:]
    return jsonify(
        session=_session_payload(item),
        events=page,
        start=start,
        offset=offset,
        pending=runners.pending_requests(session_id),
    )


@app.route('/api/sessions/<session_id>/changes')
@login_required
def api_changes(session_id):
    item = _get_session_or_404(session_id)
    try:
        return jsonify(git_changes(item['cwd']))
    except WorkspaceError as exc:
        return jsonify(error=str(exc)), 400


@app.route('/api/sessions/<session_id>/files')
@login_required
def api_files(session_id):
    if not config.file_viewer:
        abort(404)
    item = _get_session_or_404(session_id)
    try:
        if request.args.get('file'):
            return jsonify(files.read(item['cwd'], request.args['file']))
        return jsonify(files.list(item['cwd'], request.args.get('path', '')))
    except (WorkspaceError, OSError) as exc:
        return jsonify(error=str(exc)), 400


@app.route('/api/folders')
@login_required
def api_folders():
    """Sub-folders of an allowed root, for the new-session folder picker."""
    raw = request.args.get('path') or str(config.allowed_roots[0])
    path = Path(raw).expanduser().resolve()
    if not config.is_allowed_path(path) or not path.is_dir():
        return jsonify(error='This folder is outside the allowed roots.'), 400
    try:
        children = sorted(
            (p for p in path.iterdir() if p.is_dir() and not p.name.startswith('.')),
            key=lambda p: p.name.lower(),
        )[:300]
    except OSError as exc:
        return jsonify(error=str(exc)), 400
    parent = path.parent if config.is_allowed_path(path.parent) and path.parent != path else None
    return jsonify(
        path=str(path),
        parent=str(parent) if parent else None,
        folders=[{'name': p.name, 'path': str(p)} for p in children],
    )


def _call(coro):
    try:
        return runners.call(coro), None
    except RunnerError as exc:
        return None, (jsonify(error=str(exc)), 409)


@app.route('/api/send', methods=['POST'])
@login_required
def api_send():
    data = request.get_json(silent=True) or {}
    prompt = (data.get('prompt') or '').strip()
    if not prompt:
        return jsonify(error='The message is empty.'), 400
    session_id = data.get('session_id') or None
    fork = bool(data.get('fork'))
    if session_id:
        item = _get_session_or_404(session_id)
        cwd = item['cwd']
    else:
        cwd = str(Path(data.get('cwd') or '').expanduser())
        if not data.get('cwd') or not Path(cwd).is_dir():
            return jsonify(error='Choose an existing folder.'), 400
    effort = data.get('effort') or ''
    if effort and effort not in EFFORT_LEVELS:
        return jsonify(error='Unknown effort level.'), 400
    model = data.get('model') or ''
    if model and model not in config.models:
        return jsonify(error='Unknown model.'), 400
    new_id, error = _call(runners.send(
        session_id=session_id, cwd=cwd, prompt=prompt, model=model, effort=effort,
        permission_mode=data.get('permission_mode') or '', fork=fork,
    ))
    if error:
        return error
    if new_id != session_id:
        web_sessions.add(new_id, parent=session_id if fork else None)
    audit.write('prompt', ip=client_ip(), session_id=new_id, fork=fork, resumed=bool(session_id), chars=len(prompt))
    return jsonify(session_id=new_id)


@app.route('/api/sessions/<session_id>/interrupt', methods=['POST'])
@login_required
def api_interrupt(session_id):
    _get_session_or_404(session_id)
    _, error = _call(runners.interrupt(session_id))
    audit.write('interrupt', ip=client_ip(), session_id=session_id)
    return error or jsonify(ok=True)


@app.route('/api/sessions/<session_id>/permission', methods=['POST'])
@login_required
def api_permission(session_id):
    data = request.get_json(silent=True) or {}
    behavior = 'allow' if data.get('behavior') == 'allow' else 'deny'
    decision = {
        'behavior': behavior,
        'remember': bool(data.get('remember')),
        'answers': data.get('answers') if isinstance(data.get('answers'), dict) else None,
        'message': str(data.get('message') or '')[:2000],
    }
    ok, error = _call(runners.resolve(session_id, str(data.get('request_id')), decision))
    if error:
        return error
    if not ok:
        return jsonify(error='This request is no longer pending.'), 410
    audit.write('permission', ip=client_ip(), session_id=session_id, behavior=behavior, remember=decision['remember'])
    return jsonify(ok=True)


@app.route('/api/sessions/<session_id>/mode', methods=['POST'])
@login_required
def api_mode(session_id):
    mode = (request.get_json(silent=True) or {}).get('mode', '')
    _, error = _call(runners.set_permission_mode(session_id, mode))
    return error or jsonify(ok=True)


@app.route('/api/sessions/<session_id>/close', methods=['POST'])
@login_required
def api_close(session_id):
    _call(runners.close(session_id))
    return jsonify(ok=True)


usage_refresh = threading.Lock()


@app.route('/api/usage/refresh', methods=['POST'])
@login_required
def api_usage_refresh():
    if not usage_refresh.acquire(blocking=False):
        return jsonify(error='A refresh is already running.'), 429
    try:
        usage, error = _call(runners.fetch_usage())
    finally:
        usage_refresh.release()
    if error:
        return error
    usage_store.update(usage)
    return jsonify(usage=usage)


@app.route('/api/revoke-sessions', methods=['POST'])
@login_required
def api_revoke():
    auth.revoke_sessions()
    audit.write('revoke_sessions', ip=client_ip())
    session.clear()
    return jsonify(ok=True)


# ── Live updates ───────────────────────────────────────────────────────────

class Tailer:
    """Pushes new transcript events to the browsers watching a session."""

    def __init__(self):
        self.watches = {}
        self.clients = {}
        self.lock = threading.Lock()

    def subscribe(self, sid, session_id, offset):
        path = index.find_path(session_id)
        if not path:
            return
        with self.lock:
            self.unsubscribe(sid, locked=True)
            watch = self.watches.get(session_id)
            if watch:
                self._poll(session_id, watch)
                if offset < watch['offset']:
                    events, _ = read_events(path, offset, watch['offset'])
                    socketio.emit('events', {
                        'session_id': session_id, 'events': events, 'from': offset, 'to': watch['offset'],
                    }, to=sid)
            else:
                watch = self.watches[session_id] = {'path': path, 'offset': offset, 'clients': set()}
            watch['clients'].add(sid)
            self.clients[sid] = session_id
        join_room(f'session:{session_id}', sid=sid)

    def unsubscribe(self, sid, locked=False):
        if not locked:
            with self.lock:
                return self.unsubscribe(sid, locked=True)
        session_id = self.clients.pop(sid, None)
        if not session_id:
            return
        leave_room(f'session:{session_id}', sid=sid)
        watch = self.watches.get(session_id)
        if watch:
            watch['clients'].discard(sid)
            if not watch['clients']:
                del self.watches[session_id]

    def _poll(self, session_id, watch):
        try:
            size = os.path.getsize(watch['path'])
        except OSError:
            return
        if size <= watch['offset']:
            return
        start = watch['offset']
        events, offset = read_events(watch['path'], start)
        if offset == start:
            return
        watch['offset'] = offset
        socketio.emit('events', {
            'session_id': session_id, 'events': events, 'from': start, 'to': offset,
        }, to=f'session:{session_id}')

    def run(self):
        while True:
            with self.lock:
                for session_id, watch in list(self.watches.items()):
                    self._poll(session_id, watch)
            socketio.sleep(TAIL_INTERVAL)


tailer = Tailer()


def sidebar_loop():
    """Pushes changed sessions so every open sidebar stays current."""
    last = {}
    while True:
        socketio.sleep(SIDEBAR_INTERVAL)
        try:
            current = {s['id']: _session_payload(s) for s in index.list()}
        except Exception:  # noqa: BLE001 - keep the loop alive
            log.exception('Session scan failed')
            continue
        upsert = [s for sid, s in current.items() if last.get(sid) != s]
        remove = [sid for sid in last if sid not in current]
        if (upsert or remove) and last:
            socketio.emit('sessions_patch', {'upsert': upsert, 'remove': remove}, to='app')
        last = current


@socketio.on('connect')
def on_connect(auth_data=None):
    if not is_authenticated():
        return False
    join_room('app')


@socketio.on('disconnect')
def on_disconnect(reason=None):
    tailer.unsubscribe(request.sid)


@socketio.on('subscribe')
def on_subscribe(data):
    if not is_authenticated():
        return False
    session_id = (data or {}).get('session_id')
    if not is_session_id(session_id):
        return
    tailer.subscribe(request.sid, session_id, int((data or {}).get('offset') or 0))


@socketio.on('unsubscribe')
def on_unsubscribe(data=None):
    tailer.unsubscribe(request.sid)


# ── Command line ───────────────────────────────────────────────────────────

def cmd_set_password():
    first = getpass.getpass('New master password: ')
    second = getpass.getpass('Repeat it: ')
    if first != second:
        sys.exit('The passwords differ.')
    try:
        auth.set_password(first)
    except ValueError as exc:
        sys.exit(str(exc))
    audit.write('password_changed')
    print('Master password saved. Every browser has been signed out.')


def cmd_totp(action):
    if action == 'disable':
        auth.disable_totp()
        audit.write('totp_disabled')
        print('Two-factor authentication is off.')
        return
    import pyotp
    secret = auth.new_totp_secret()
    uri = pyotp.TOTP(secret).provisioning_uri(name='claude-code-web', issuer_name='Nedara')
    try:
        import qrcode
        qr = qrcode.QRCode(border=1)
        qr.add_data(uri)
        qr.print_ascii(invert=True)
    except ImportError:
        pass
    print(f'Secret: {secret}\nURI:    {uri}\n')
    code = input('Enter the 6-digit code from your authenticator app: ').strip()
    if not auth.enable_totp(secret, code):
        sys.exit('Wrong code. Two-factor authentication was not turned on.')
    audit.write('totp_enabled')
    print('Two-factor authentication is on. Every browser has been signed out.')


def preflight():
    problems = []
    if not config.is_loopback and not config.allow_remote:
        problems.append(
            f'host = {config.host} exposes the app beyond this machine. Put it behind a TLS reverse proxy '
            'or a VPN, then set allow_remote = 1 in config.ini to confirm.'
        )
    if config.oauth_token_file:
        mode = config.oauth_token_file.stat().st_mode & 0o777 if config.oauth_token_file.exists() else None
        if mode is None:
            problems.append(f'oauth_token_file {config.oauth_token_file} does not exist.')
        elif mode & 0o077:
            problems.append(f'oauth_token_file is readable by others ({oct(mode)}). Run: chmod 600 {config.oauth_token_file}')
    if problems:
        sys.exit('\n'.join(problems))
    if not config.claude_cli:
        log.warning('The claude CLI was not found: sessions are read-only. Set claude_cli in config.ini.')
    if not config.is_loopback and not config.secure_cookies:
        log.warning('Listening on %s: if other machines can reach this port, serve it over HTTPS '
                    'and set secure_cookies = 1.', config.host)


def main():
    parser = argparse.ArgumentParser(description='Nedara Claude Code Web')
    sub = parser.add_subparsers(dest='command')
    sub.add_parser('set-password')
    totp = sub.add_parser('totp')
    totp.add_argument('action', choices=['enable', 'disable'])
    sub.add_parser('revoke-sessions')
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if config.debug else logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    if args.command == 'set-password':
        return cmd_set_password()
    if args.command == 'totp':
        return cmd_totp(args.action)
    if args.command == 'revoke-sessions':
        auth.revoke_sessions()
        return print('Every browser has been signed out.')

    if not config.oauth_token_file and CLAUDE_TOKEN_FILE.exists():
        config.oauth_token_file = CLAUDE_TOKEN_FILE
    preflight()
    if not auth.configured:
        start_setup_mode()
    detached = detach_from_parent_session()
    if detached:
        log.info('Started from inside a Claude Code session: %d inherited variables dropped.', detached)
    socketio.start_background_task(tailer.run)
    socketio.start_background_task(sidebar_loop)
    log.info('Nedara Claude Code Web %s on http://%s:%s', VERSION, config.host, config.port)
    try:
        socketio.run(app, host=config.host, port=config.port, debug=False, allow_unsafe_werkzeug=True)
    finally:
        runners.shutdown()


if __name__ == '__main__':
    main()

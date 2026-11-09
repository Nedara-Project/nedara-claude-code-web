"""Authentication, rate limiting, CSRF, audit trail and HTTP hardening.

The app is single-user: one password (scrypt hash) and an optional TOTP secret,
both stored in data/auth.json with 0600 permissions. Bumping the auth epoch
invalidates every issued session cookie at once.
"""

import hmac
import json
import os
import secrets
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pyotp
from werkzeug.security import check_password_hash, generate_password_hash

MIN_PASSWORD_LENGTH = 12


def write_private(path, content):
    """Write a file readable by the owner only, atomically."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except PermissionError:
        pass  # not the owner (e.g. a volume in a user-namespaced container)
    tmp = path.with_suffix(path.suffix + '.tmp')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as fh:
        fh.write(content)
    os.replace(tmp, path)


def load_secret_key(data_dir):
    path = Path(data_dir) / 'secret_key'
    if not path.exists():
        write_private(path, secrets.token_hex(32))
    return path.read_text().strip()


class AuthStore:
    def __init__(self, data_dir):
        self.path = Path(data_dir) / 'auth.json'
        self._lock = threading.Lock()

    def _read(self):
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text())

    def _save(self, data):
        write_private(self.path, json.dumps(data, indent=2))

    @property
    def configured(self):
        return bool(self._read().get('password_hash'))

    @property
    def epoch(self):
        return self._read().get('epoch', 0)

    @property
    def totp_enabled(self):
        return bool(self._read().get('totp_secret'))

    def set_password(self, password):
        if len(password) < MIN_PASSWORD_LENGTH:
            raise ValueError(f'The password needs at least {MIN_PASSWORD_LENGTH} characters.')
        with self._lock:
            data = self._read()
            data['password_hash'] = generate_password_hash(password, method='scrypt')
            data['epoch'] = data.get('epoch', 0) + 1
            self._save(data)

    def new_totp_secret(self):
        return pyotp.random_base32()

    def enable_totp(self, secret, code):
        if not pyotp.TOTP(secret).verify(code, valid_window=1):
            return False
        with self._lock:
            data = self._read()
            data['totp_secret'] = secret
            data['epoch'] = data.get('epoch', 0) + 1
            self._save(data)
        return True

    def disable_totp(self):
        with self._lock:
            data = self._read()
            data.pop('totp_secret', None)
            data['epoch'] = data.get('epoch', 0) + 1
            self._save(data)

    def revoke_sessions(self):
        with self._lock:
            data = self._read()
            data['epoch'] = data.get('epoch', 0) + 1
            self._save(data)

    def verify(self, password, code):
        data = self._read()
        password_hash = data.get('password_hash')
        if not password_hash:
            return False
        ok = check_password_hash(password_hash, password or '')
        secret = data.get('totp_secret')
        if secret:
            code = (code or '').replace(' ', '')
            ok = pyotp.TOTP(secret).verify(code, valid_window=1) and ok
        return ok


class LoginLimiter:
    """Per-client lockout after repeated failures, plus a global slowdown."""

    def __init__(self, max_attempts, lockout_minutes):
        self.max_attempts = max_attempts
        self.window = lockout_minutes * 60
        self._failures = {}
        self._lock = threading.Lock()

    def _recent(self, key, now):
        return [t for t in self._failures.get(key, []) if now - t < self.window]

    def locked(self, key):
        now = time.time()
        with self._lock:
            recent = self._recent(key, now)
            self._failures[key] = recent
            if len(recent) >= self.max_attempts:
                return int(self.window - (now - recent[0]))
        return 0

    def fail(self, key):
        now = time.time()
        with self._lock:
            self._failures[key] = self._recent(key, now) + [now]
        # Constant delay on every failure slows down online guessing.
        time.sleep(1)

    def reset(self, key):
        with self._lock:
            self._failures.pop(key, None)


class AuditLog:
    def __init__(self, data_dir):
        self.path = Path(data_dir) / 'audit.log'
        self._lock = threading.Lock()

    def write(self, event, ip='', **details):
        entry = {
            'ts': datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'event': event,
            'ip': ip,
            **details,
        }
        line = json.dumps(entry, ensure_ascii=False) + '\n'
        with self._lock:
            new = not self.path.exists()
            with open(self.path, 'a', encoding='utf-8') as fh:
                fh.write(line)
            if new:
                os.chmod(self.path, 0o600)


def csrf_token(session):
    token = session.get('csrf')
    if not token:
        token = secrets.token_urlsafe(32)
        session['csrf'] = token
    return token


def csrf_valid(session, token):
    expected = session.get('csrf')
    return bool(expected and token and hmac.compare_digest(expected, token))


CSP = '; '.join([
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self'",
    "img-src 'self' data:",
    "font-src 'self'",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'self'",
    "frame-ancestors 'none'",
])


def apply_security_headers(response):
    response.headers['Content-Security-Policy'] = CSP
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=(), payment=()'
    response.headers['Cross-Origin-Opener-Policy'] = 'same-origin'
    response.headers['Cross-Origin-Resource-Policy'] = 'same-origin'
    if response.mimetype in ('application/json', 'text/html'):
        response.headers['Cache-Control'] = 'no-store'
    return response

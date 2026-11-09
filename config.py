"""Configuration loading for Nedara Claude Code Web.

Every key has a safe default so a missing config.ini still yields a local-only,
password-protected instance. See config.ini.example for the documented keys.
"""

import configparser
import ipaddress
import os
import platform
import shutil
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = Path(os.environ.get('NCCW_CONFIG', BASE_DIR / 'config.ini'))

PERMISSION_MODES = ('default', 'acceptEdits', 'plan', 'auto', 'dontAsk', 'bypassPermissions')
EFFORT_LEVELS = ('low', 'medium', 'high', 'xhigh', 'max')


def _default_desktop_dir():
    home = Path.home()
    system = platform.system()
    if system == 'Darwin':
        return home / 'Library' / 'Application Support' / 'Claude'
    if system == 'Windows':
        return Path(os.environ.get('APPDATA', home)) / 'Claude'
    return Path(os.environ.get('XDG_CONFIG_HOME', home / '.config')) / 'Claude'


def _bundled_cli():
    """The CLI shipped inside the claude-agent-sdk wheel, used when none is on PATH."""
    try:
        import claude_agent_sdk
    except ImportError:
        return ''
    path = Path(claude_agent_sdk.__file__).parent / '_bundled' / 'claude'
    return str(path) if path.exists() else ''


def _split(value):
    return [v.strip() for v in (value or '').split(',') if v.strip()]


def _is_loopback(host):
    if host in ('localhost', ''):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@dataclass
class Config:
    host: str = '127.0.0.1'
    port: int = 5070
    allow_remote: bool = False
    behind_proxy: bool = False
    secure_cookies: bool = False
    session_hours: int = 12
    allowed_origins: list = field(default_factory=list)

    claude_dir: Path = field(default_factory=lambda: Path.home() / '.claude')
    desktop_dir: Path = field(default_factory=_default_desktop_dir)
    claude_cli: str = ''
    oauth_token_file: Path | None = None
    data_dir: Path = BASE_DIR / 'data'
    allowed_roots: list = field(default_factory=lambda: [Path.home()])
    show_automated: bool = False

    read_only: bool = False
    allowed_permission_modes: tuple = ('default', 'acceptEdits', 'plan', 'auto')
    default_permission_mode: str = 'default'
    default_model: str = ''
    models: list = field(default_factory=lambda: ['claude-opus-5-5', 'claude-sonnet-5-5', 'claude-haiku-5-5', 'claude-fable-5-1'])
    permission_timeout: int = 600
    idle_disconnect_minutes: int = 30

    file_viewer: bool = True
    file_max_kb: int = 512
    file_denylist: list = field(default_factory=lambda: [
        '.env', '.env.*', '*.key', '*.pem', '*.p12', '*.pfx', 'id_rsa*', 'id_ed25519*',
        '.netrc', '.pgpass', '*.kdbx', '.credentials.json', 'credentials*',
    ])

    max_login_attempts: int = 5
    lockout_minutes: int = 15
    debug: bool = False

    @property
    def is_loopback(self):
        return _is_loopback(self.host)

    @property
    def projects_dir(self):
        return self.claude_dir / 'projects'

    @property
    def registry_dir(self):
        return self.claude_dir / 'sessions'

    @property
    def desktop_sessions_dir(self):
        return self.desktop_dir / 'claude-code-sessions'

    def is_allowed_path(self, path):
        """True when `path` resolves inside one of the allowed roots."""
        try:
            real = Path(path).expanduser().resolve()
        except (OSError, RuntimeError):
            return False
        return any(real == root or root in real.parents for root in self.allowed_roots)


def load_config(path=CONFIG_PATH):
    cfg = Config()
    parser = configparser.ConfigParser(interpolation=None)
    if Path(path).exists():
        parser.read(path)

    s = parser['server'] if parser.has_section('server') else {}
    # Environment variables win, so a container can bind 0.0.0.0 internally.
    cfg.host = os.environ.get('NCCW_HOST') or s.get('host', cfg.host).strip()
    cfg.port = int(os.environ.get('NCCW_PORT') or s.get('port', cfg.port))
    cfg.allow_remote = (os.environ.get('NCCW_ALLOW_REMOTE') or s.get('allow_remote', '0')).strip() == '1'
    cfg.behind_proxy = s.get('behind_proxy', '0').strip() == '1'
    cfg.secure_cookies = s.get('secure_cookies', '1' if cfg.behind_proxy else '0').strip() == '1'
    cfg.session_hours = int(s.get('session_hours', cfg.session_hours))
    cfg.allowed_origins = _split(s.get('allowed_origins', ''))
    cfg.debug = s.get('debug', '0').strip() == '1'

    c = parser['claude'] if parser.has_section('claude') else {}
    if c.get('claude_dir', '').strip():
        cfg.claude_dir = Path(c['claude_dir']).expanduser()
    if c.get('desktop_dir', '').strip():
        cfg.desktop_dir = Path(c['desktop_dir']).expanduser()
    cfg.claude_cli = c.get('claude_cli', '').strip() or shutil.which('claude') or _bundled_cli()
    if c.get('oauth_token_file', '').strip():
        cfg.oauth_token_file = Path(c['oauth_token_file']).expanduser()
    if c.get('data_dir', '').strip():
        cfg.data_dir = Path(c['data_dir']).expanduser()
    roots = _split(c.get('allowed_roots', ''))
    if roots:
        cfg.allowed_roots = [Path(r).expanduser().resolve() for r in roots]
    else:
        cfg.allowed_roots = [Path.home().resolve()]

    cfg.show_automated = c.get('show_automated', '0').strip() == '1'

    a = parser['agent'] if parser.has_section('agent') else {}
    cfg.read_only = a.get('read_only', '0').strip() == '1'
    modes = _split(a.get('allowed_permission_modes', ''))
    if modes:
        unknown = [m for m in modes if m not in PERMISSION_MODES]
        if unknown:
            raise ValueError(f'Unknown permission modes in config: {", ".join(unknown)}')
        cfg.allowed_permission_modes = tuple(modes)
    cfg.default_permission_mode = a.get('default_permission_mode', cfg.default_permission_mode).strip()
    if cfg.default_permission_mode not in cfg.allowed_permission_modes:
        cfg.default_permission_mode = cfg.allowed_permission_modes[0]
    cfg.default_model = a.get('default_model', '').strip()
    if _split(a.get('models', '')):
        cfg.models = _split(a['models'])
    cfg.permission_timeout = int(a.get('permission_timeout', cfg.permission_timeout))
    cfg.idle_disconnect_minutes = int(a.get('idle_disconnect_minutes', cfg.idle_disconnect_minutes))

    f = parser['files'] if parser.has_section('files') else {}
    cfg.file_viewer = f.get('file_viewer', '1').strip() == '1'
    cfg.file_max_kb = int(f.get('file_max_kb', cfg.file_max_kb))
    extra = _split(f.get('denylist', ''))
    if extra:
        cfg.file_denylist = cfg.file_denylist + extra

    sec = parser['security'] if parser.has_section('security') else {}
    cfg.max_login_attempts = int(sec.get('max_login_attempts', cfg.max_login_attempts))
    cfg.lockout_minutes = int(sec.get('lockout_minutes', cfg.lockout_minutes))
    return cfg

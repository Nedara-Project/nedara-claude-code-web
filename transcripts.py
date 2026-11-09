"""Reading Claude Code sessions from disk.

Claude Code (CLI, Desktop and the Agent SDK alike) appends every session to
~/.claude/projects/<project>/<session-id>.jsonl. This module lists those
sessions, enriches them with Claude Desktop's own metadata (titles, archive
flag) and the live process registry, and turns transcript records into the
compact events the web UI renders.
"""

import difflib
import json
import os
import re
import threading
from pathlib import Path

from render import markdown_to_html

HEAD_BYTES = 64 * 1024
TAIL_BYTES = 256 * 1024
RESULT_MAX_CHARS = 12000
INPUT_MAX_CHARS = 4000
DIFF_MAX_LINES = 400

# Record types that never produce anything visible; skipped before JSON parsing.
_SKIP_PREFIXES = tuple(f'{{"type":"{t}"' for t in (
    'api-request', 'api-request-blob', 'api-request-shape', 'file-history-snapshot',
    'file-history-delta', 'atis-latch', 'isolation-latch', 'queue-operation', 'mode',
))
_REMINDER_RE = re.compile(r'<system-reminder>.*?</system-reminder>', re.S)
_COMMAND_RE = re.compile(r'<command-name>(.*?)</command-name>.*?(?:<command-args>(.*?)</command-args>)?', re.S)
_STDOUT_RE = re.compile(r'<local-command-(stdout|stderr)>(.*?)</local-command-\1>', re.S)
_PASTE_RE = re.compile(r'<pasted_content[^>]*>\n?(.*?)\n?</pasted_content[^>]*>', re.S)
_UUID_RE = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')


def is_session_id(value):
    return bool(value and _UUID_RE.match(value))


# ── Record → events ────────────────────────────────────────────────────────

def _short_tool_name(name):
    if name.startswith('mcp__'):
        parts = name.split('__', 2)
        if len(parts) == 3:
            return parts[2], parts[1]
    return name, ''


def _truncate(text, limit):
    if len(text) <= limit:
        return text, False
    return text[:limit], True


def _edit_diff(old, new):
    lines = list(difflib.unified_diff(
        (old or '').splitlines(), (new or '').splitlines(), lineterm='', n=2,
    ))[2:]
    return lines[:DIFF_MAX_LINES]


def _tool_details(name, inp):
    """Return (summary, diff_lines) for the tool card header and body."""
    inp = inp if isinstance(inp, dict) else {}
    diff = None
    if name == 'Bash':
        summary = inp.get('description') or inp.get('command', '')
    elif name in ('Read', 'NotebookRead'):
        summary = inp.get('file_path') or inp.get('notebook_path', '')
    elif name == 'Edit':
        summary = inp.get('file_path', '')
        diff = _edit_diff(inp.get('old_string'), inp.get('new_string'))
    elif name == 'MultiEdit':
        summary = inp.get('file_path', '')
        diff = []
        for edit in inp.get('edits', []):
            diff += _edit_diff(edit.get('old_string'), edit.get('new_string')) + ['']
        diff = diff[:DIFF_MAX_LINES]
    elif name == 'Write':
        summary = inp.get('file_path', '')
        diff = ['+' + line for line in (inp.get('content') or '').splitlines()[:DIFF_MAX_LINES]]
    elif name in ('Grep', 'Glob'):
        summary = inp.get('pattern', '')
    elif name == 'WebFetch':
        summary = inp.get('url', '')
    elif name == 'WebSearch':
        summary = inp.get('query', '')
    elif name in ('Agent', 'Task'):
        summary = inp.get('description', '')
    elif name == 'Skill':
        summary = inp.get('skill', '')
    elif name == 'ToolSearch':
        summary = inp.get('query', '')
    else:
        summary = ''
        for key in ('description', 'title', 'query', 'path', 'url', 'name'):
            if isinstance(inp.get(key), str):
                summary = inp[key]
                break
    summary = summary.strip().splitlines()[0][:200] if summary and summary.strip() else ''
    return summary, diff


# Claude Desktop's built-in next-step buttons, turned into plain replies here.
_APP_ACTIONS = {
    'commit': ('Commit', 'Commit these changes.'),
    'create_pr': ('Create PR', 'Create a pull request for these changes.'),
    'merge': ('Merge', 'Merge the pull request.'),
    'fix_ci': ('Fix CI', 'Fix the failing CI checks.'),
    'address_comments': ('Address comments', 'Address the review comments.'),
    'resolve_conflicts': ('Resolve conflicts', 'Resolve the merge conflicts.'),
    'verify': ('Verify', 'Verify that the change works.'),
}


def _wrap_up_actions(raw):
    """Buttons of a turn summary as [{label, message}]; Desktop-only actions are dropped."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return []
    actions = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        app = item.get('action')
        if app:
            if app not in _APP_ACTIONS:
                continue
            label, message = _APP_ACTIONS[app]
            message = item.get('message') or message
        else:
            label = str(item.get('label') or '').strip()
            message = str(item.get('message') or label).strip()
        if label and message:
            actions.append({'label': label[:40], 'message': message[:1000]})
    return actions[:3]


def _clean_user_text(text):
    text = _REMINDER_RE.sub('', text or '').strip()
    match = _COMMAND_RE.search(text)
    if match and text.startswith('<command-'):
        args = (match.group(2) or '').strip()
        return f'{match.group(1).strip()} {args}'.strip(), 'command'
    out = _STDOUT_RE.search(text)
    if out and text.startswith('<local-command-'):
        return out.group(2).strip(), 'stdout'
    return text, None


def _segments(text):
    """Split user text into plain parts and pasted blocks (shown collapsed)."""
    segments, last = [], 0
    for match in _PASTE_RE.finditer(text):
        before = text[last:match.start()].strip()
        if before:
            segments.append({'kind': 'text', 'text': before})
        segments.append({'kind': 'pasted', 'text': match.group(1), 'lines': match.group(1).count('\n') + 1})
        last = match.end()
    rest = text[last:].strip()
    if rest:
        segments.append({'kind': 'text', 'text': rest})
    return segments


def _result_text(content):
    if isinstance(content, str):
        return content, 0
    texts, images = [], 0
    for block in content or []:
        if block.get('type') == 'text':
            texts.append(block.get('text', ''))
        elif block.get('type') == 'image':
            images += 1
    return '\n'.join(texts), images


def record_to_events(rec):
    """Convert one transcript record into zero or more UI events."""
    rtype = rec.get('type')
    uid = rec.get('uuid', '')
    ts = rec.get('timestamp', '')
    events = []

    if rtype == 'custom-title':
        return [{'t': 'title', 'title': rec.get('customTitle', '')}]
    if rtype == 'cost-state':
        return [{'t': 'cost', 'usd': rec.get('totalCostUSD') or 0}]
    if rtype == 'system':
        subtype = rec.get('subtype')
        if subtype == 'compact_boundary':
            return [{'t': 'notice', 'id': uid, 'ts': ts, 'level': 'info', 'text': 'Conversation compacted'}]
        if rec.get('level') in ('error', 'warning') and rec.get('content'):
            return [{'t': 'notice', 'id': uid, 'ts': ts, 'level': rec['level'], 'text': str(rec['content'])[:2000]}]
        return []
    if rtype not in ('user', 'assistant') or rec.get('isSidechain'):
        return []

    message = rec.get('message') or {}
    content = message.get('content')

    if rtype == 'user':
        if rec.get('isMeta'):
            return []
        if rec.get('isCompactSummary'):
            return [{'t': 'notice', 'id': uid, 'ts': ts, 'level': 'info', 'text': 'Summary of the earlier conversation'}]
        blocks = [{'type': 'text', 'text': content}] if isinstance(content, str) else (content or [])
        texts, images = [], 0
        for i, block in enumerate(blocks):
            btype = block.get('type')
            if btype == 'tool_result':
                text, imgs = _result_text(block.get('content'))
                text, truncated = _truncate(text, RESULT_MAX_CHARS)
                events.append({
                    't': 'result', 'tool_id': block.get('tool_use_id'), 'text': text,
                    'is_error': bool(block.get('is_error')), 'truncated': truncated, 'images': imgs,
                })
            elif btype == 'text':
                texts.append(block.get('text', ''))
            elif btype == 'image':
                images += 1
        if texts or images:
            text, kind = _clean_user_text('\n'.join(texts))
            if text.startswith('[Request interrupted by user'):
                events.append({'t': 'notice', 'id': uid, 'ts': ts, 'level': 'warning', 'text': 'Interrupted by user'})
            elif kind == 'stdout':
                events.append({'t': 'notice', 'id': uid, 'ts': ts, 'level': 'info', 'text': text[:2000]})
            elif text or images:
                events.append({
                    't': 'user', 'id': uid, 'ts': ts, 'segments': _segments(text),
                    'images': images, 'command': kind == 'command',
                })
        return events

    for i, block in enumerate(content or []):
        btype = block.get('type')
        bid = f'{uid}:{i}'
        if btype == 'text' and block.get('text', '').strip():
            events.append({'t': 'text', 'id': bid, 'ts': ts, 'html': markdown_to_html(block['text'])})
        elif btype == 'thinking' and block.get('thinking', '').strip():
            events.append({'t': 'thinking', 'id': bid, 'ts': ts, 'text': block['thinking']})
        elif btype == 'tool_use':
            name = block.get('name', '')
            inp = block.get('input') or {}
            label, server = _short_tool_name(name)
            summary, diff = _tool_details(label, inp)
            event = {
                't': 'tool', 'id': block.get('id'), 'ts': ts, 'name': name, 'label': label,
                'server': server, 'summary': summary, 'diff': diff,
            }
            if label == 'update_plan' and isinstance(inp.get('items'), list):
                event['plan'] = [{'content': it.get('content', ''), 'status': it.get('status', 'pending')} for it in inp['items']]
            elif label == 'wrap_up':
                event['recap'] = inp.get('recap', '')
                event['ask'] = inp.get('ask', '')
                event['actions'] = _wrap_up_actions(inp.get('actions'))
            elif label == 'Bash':
                event['command'] = inp.get('command', '')
            if 'plan' not in event and 'recap' not in event and label != 'Bash':
                raw, _ = _truncate(json.dumps(inp, indent=2, ensure_ascii=False), INPUT_MAX_CHARS)
                event['input'] = raw
            events.append(event)
    if message.get('model') == '<synthetic>' and not events:
        return []
    return events


# ── Reading files ──────────────────────────────────────────────────────────

def iter_records(path, start=0, end=None):
    """Yield (record, offset_after) for complete lines between start and end."""
    with open(path, 'rb') as fh:
        fh.seek(start)
        offset = start
        while True:
            if end is not None and offset >= end:
                break
            line = fh.readline()
            if not line or not line.endswith(b'\n'):
                break
            offset += len(line)
            if line.startswith(tuple(p.encode() for p in _SKIP_PREFIXES)):
                continue
            try:
                yield json.loads(line), offset
            except (ValueError, UnicodeDecodeError):
                continue


def read_events(path, start=0, end=None):
    """Parse a transcript from `start`; return (events, offset of last full line)."""
    events, offset = [], start
    for rec, offset in iter_records(path, start, end):
        events.extend(record_to_events(rec))
    if offset == start and end is None:
        offset = _complete_offset(path, start)
    # Titles and costs are rewritten on every turn; keep the latest of each.
    latest = {}
    for event in events:
        if event['t'] in ('title', 'cost'):
            latest[event['t']] = event
    events = [e for e in events if e['t'] not in ('title', 'cost')] + list(latest.values())
    return events, offset


def _complete_offset(path, start):
    """Offset after the last complete line at or after `start`."""
    offset = start
    with open(path, 'rb') as fh:
        fh.seek(start)
        for line in fh:
            if not line.endswith(b'\n'):
                break
            offset += len(line)
    return offset


class EventCache:
    """Parsed events per transcript, re-parsed incrementally as the file grows."""

    def __init__(self, max_entries=8):
        self._entries = {}
        self._lock = threading.Lock()
        self.max_entries = max_entries

    def get(self, path):
        path = str(path)
        with self._lock:
            entry = self._entries.pop(path, None)
        size = os.path.getsize(path)
        if entry is None or entry['offset'] > size:
            entry = {'events': [], 'offset': 0}
        if entry['offset'] < size:
            new_events, offset = read_events(path, entry['offset'])
            entry = {'events': entry['events'] + new_events, 'offset': offset}
        with self._lock:
            self._entries[path] = entry
            while len(self._entries) > self.max_entries:
                self._entries.pop(next(iter(self._entries)))
        return entry['events'], entry['offset']


# ── Session index ──────────────────────────────────────────────────────────

def _first_prompt_and_cwd(path):
    cwd, prompt, branch, created, entrypoint = '', '', '', '', ''
    with open(path, 'rb') as fh:
        chunk = fh.read(HEAD_BYTES)
    for raw in chunk.split(b'\n'):
        if not raw.startswith(b'{') or raw.startswith(tuple(p.encode() for p in _SKIP_PREFIXES)):
            continue
        try:
            rec = json.loads(raw)
        except ValueError:
            continue
        cwd = cwd or rec.get('cwd', '')
        entrypoint = entrypoint or rec.get('entrypoint', '')
        branch = branch or rec.get('gitBranch', '')
        created = created or rec.get('timestamp', '')
        if not prompt and rec.get('type') == 'user' and not rec.get('isMeta'):
            content = (rec.get('message') or {}).get('content')
            if isinstance(content, list):
                content = '\n'.join(b.get('text', '') for b in content if b.get('type') == 'text')
            if isinstance(content, str):
                prompt, _ = _clean_user_text(_PASTE_RE.sub(' [pasted text] ', content))
        if cwd and prompt and entrypoint:
            break
    return cwd, prompt, branch, created, entrypoint


def _tail_metadata(path, size):
    title, last_prompt = '', ''
    with open(path, 'rb') as fh:
        fh.seek(max(0, size - TAIL_BYTES))
        chunk = fh.read()
    for raw in reversed(chunk.split(b'\n')):
        if title and last_prompt:
            break
        if not title and raw.startswith(b'{"type":"custom-title"'):
            try:
                title = json.loads(raw).get('customTitle', '')
            except ValueError:
                pass
        elif not last_prompt and raw.startswith(b'{"type":"last-prompt"'):
            try:
                last_prompt = json.loads(raw).get('lastPrompt', '')
            except ValueError:
                pass
    return title, last_prompt


class SessionIndex:
    def __init__(self, config, web_sessions=frozenset):
        self.config = config
        # Callable returning the ids of sessions started from this web app.
        self.web_sessions = web_sessions
        self._files = {}
        self._desktop = {}
        self._lock = threading.Lock()

    # Claude Desktop keeps one JSON file per session with its title and flags.
    def _desktop_metadata(self):
        root = self.config.desktop_sessions_dir
        result = {}
        if not root.is_dir():
            return result
        for path in root.glob('*/*/local_*.json'):
            try:
                stat = path.stat()
            except OSError:
                continue
            cached = self._desktop.get(path)
            if cached and cached[0] == stat.st_mtime_ns:
                data = cached[1]
            else:
                try:
                    raw = json.loads(path.read_text())
                except (OSError, ValueError):
                    continue
                data = {
                    'cli_id': raw.get('cliSessionId'),
                    'title': raw.get('title') or '',
                    'archived': bool(raw.get('isArchived')),
                    'model': raw.get('model') or '',
                    'effort': raw.get('effort') or '',
                    'permission_mode': raw.get('permissionMode') or '',
                }
                self._desktop[path] = (stat.st_mtime_ns, data)
            if data['cli_id']:
                result[data['cli_id']] = data
        return result

    def live_processes(self):
        """Session id → live Claude Code process holding it (Desktop, CLI or SDK)."""
        live = {}
        root = self.config.registry_dir
        if not root.is_dir():
            return live
        for path in root.glob('*.json'):
            try:
                data = json.loads(path.read_text())
                pid = int(data['pid'])
                os.kill(pid, 0)
            except PermissionError:
                pass  # alive, owned by another user
            except (OSError, ValueError, KeyError, TypeError):
                continue
            sid = data.get('sessionId')
            if sid:
                live[sid] = {
                    'pid': pid,
                    'status': data.get('status', ''),
                    'entrypoint': data.get('entrypoint', ''),
                }
        return live

    def _summary(self, path, stat):
        key = (stat.st_mtime_ns, stat.st_size)
        cached = self._files.get(path)
        if cached and cached[0] == key:
            return cached[1]
        cwd, first_prompt, branch, created, entrypoint = _first_prompt_and_cwd(path)
        title, last_prompt = _tail_metadata(path, stat.st_size)
        summary = {
            'id': path.stem,
            'path': str(path),
            'cwd': cwd,
            'branch': branch,
            'created': created,
            'entrypoint': entrypoint,
            'transcript_title': title,
            'first_prompt': first_prompt[:200],
            'last_prompt': last_prompt[:200],
            'updated': stat.st_mtime,
            'size': stat.st_size,
        }
        self._files[path] = (key, summary)
        return summary

    def list(self):
        projects = self.config.projects_dir
        if not projects.is_dir():
            return []
        with self._lock:
            desktop = self._desktop_metadata()
            live = self.live_processes()
            web = self.web_sessions()
            sessions, seen = [], set()
            for path in projects.glob('*/*.jsonl'):
                if not is_session_id(path.stem):
                    continue
                try:
                    stat = path.stat()
                except OSError:
                    continue
                if stat.st_size == 0:
                    continue
                seen.add(path)
                summary = self._summary(path, stat)
                if not summary['cwd'] or not self.config.is_allowed_path(summary['cwd']):
                    continue
                meta = desktop.get(summary['id'], {})
                # Agent SDK sessions are scripts and bots (changelog generators, CI...),
                # hidden like in Claude Desktop unless they were started from here.
                automated = summary['entrypoint'].startswith('sdk') and summary['id'] not in web
                if automated and not self.config.show_automated:
                    continue
                title = meta.get('title') or summary['transcript_title'] or summary['first_prompt'] or 'Untitled session'
                if not (summary['first_prompt'] or summary['transcript_title'] or meta):
                    continue
                proc = live.get(summary['id'])
                sessions.append({
                    **{k: v for k, v in summary.items() if k != 'path'},
                    'title': title,
                    'archived': meta.get('archived', False),
                    'desktop': bool(meta),
                    'web': summary['id'] in web,
                    'model': meta.get('model', ''),
                    'effort': meta.get('effort', ''),
                    'permission_mode': meta.get('permission_mode', ''),
                    'process': proc,
                })
            for stale in set(self._files) - seen:
                self._files.pop(stale, None)
        sessions.sort(key=lambda s: s['updated'], reverse=True)
        return sessions

    def find_path(self, session_id):
        if not is_session_id(session_id):
            return None
        for path in self.config.projects_dir.glob(f'*/{session_id}.jsonl'):
            return path
        return None

    def get(self, session_id):
        for session in self.list():
            if session['id'] == session_id:
                return session
        return None

"""Driving Claude Code sessions through the Claude Agent SDK.

Each session controlled from the web gets a Runner holding a connected
ClaudeSDKClient. All runners share one asyncio loop in a background thread;
Flask-SocketIO handlers talk to it with run_coroutine_threadsafe.

The transcript file stays the single source of truth for what the UI shows: the
CLI writes every message there and the tailer forwards it. The runner only adds
what the file cannot carry: streamed text deltas, run state and permission
requests.
"""

import asyncio
import logging
import os
import secrets
import threading
import time
import uuid
from dataclasses import replace

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    PermissionResultAllow,
    PermissionResultDeny,
    RateLimitEvent,
    ResultMessage,
    StreamEvent,
)

log = logging.getLogger(__name__)

# Variables that tie a process to the Claude Code session that launched it.
# Started from inside Claude Code (Desktop, CLI or a dev preview), the app would
# otherwise hand them to every CLI it spawns, which then tries to reuse the
# parent's authentication and messaging channel.
PARENT_SESSION_VARS = (
    'CLAUDECODE', 'CLAUDE_PID', 'CLAUDE_EFFORT', 'CLAUDE_AGENT_SDK_VERSION', 'CLAUDE_AGENT_SDK_MCP_NO_PREFIX',
    'CLAUDE_CODE_ENTRYPOINT', 'CLAUDE_CODE_SESSION_ID', 'CLAUDE_CODE_HOST_SESSION_ID', 'CLAUDE_CODE_CHILD_SESSION',
    'CLAUDE_CODE_SESSION_ATTENDED', 'CLAUDE_CODE_MESSAGING_SOCKET', 'CLAUDE_CODE_MESSAGING_TOKEN',
    'CLAUDE_CODE_SDK_HAS_HOST_AUTH_REFRESH', 'CLAUDE_CODE_DESKTOP_APP_VERSION', 'CLAUDE_CODE_EXECPATH',
    'CLAUDE_CODE_OAUTH_SCOPES', 'CLAUDE_CODE_STARTUP_FAILURE_RESULTS', 'CLAUDE_CODE_TERMINAL_MCP_TOOLS',
    'CLAUDE_CODE_REPORT_FINDINGS', 'CLAUDE_PREVIEW_CLASSIFIER_FLOOR', 'CLAUDE_CODE_SIMPLE',
    'CLAUDE_CODE_EMIT_TOOL_USE_SUMMARIES', 'CLAUDE_CODE_EMIT_STARTUP_TIMING', 'CLAUDE_CODE_EAGER_FLUSH',
    'CLAUDE_CODE_DISABLE_CRON', 'CLAUDE_CODE_DISABLE_TERMINAL_TITLE', 'CLAUDE_CODE_ENABLE_SDK_FILE_CHECKPOINTING',
    'CLAUDE_CODE_ENABLE_ASK_USER_QUESTION_TOOL', 'CLAUDE_CODE_ORGANIZATION_UUID', 'CLAUDE_CODE_ACCOUNT_UUID',
    'CLAUDE_CODE_USER_EMAIL',
)


def detach_from_parent_session():
    """Drop inherited Claude Code session variables; return how many were removed."""
    if not os.environ.get('CLAUDECODE') and not os.environ.get('CLAUDE_CODE_SESSION_ID'):
        return 0
    removed = [name for name in PARENT_SESSION_VARS if os.environ.pop(name, None) is not None]
    return len(removed)


def usage_from_rate_limit(info):
    """Plan usage as reported by the CLI on each turn: one entry per limit window."""
    raw = info.raw or {}
    windows = {
        name: {'utilization': w.get('utilization'), 'resets_at': w.get('resetsAt')}
        for name, w in (raw.get('unifiedWindows') or {}).items() if isinstance(w, dict)
    }
    if not windows and info.rate_limit_type:
        windows[info.rate_limit_type] = {'utilization': info.utilization, 'resets_at': info.resets_at}
    return {
        'status': info.status,
        'limited_window': info.rate_limit_type,
        'overage_status': info.overage_status,
        'using_overage': bool(raw.get('isUsingOverage')),
        'windows': windows,
        'updated_at': time.time(),
    }


class RunnerError(Exception):
    """An error meant to be shown to the web user as is."""


class Runner:
    def __init__(self, manager, session_id, cwd):
        self.manager = manager
        self.session_id = session_id
        self.cwd = cwd
        self.client = None
        self.state = 'starting'
        self.pending = {}
        self.last_activity = time.time()
        self.task = None
        self.cost = None

    @property
    def room(self):
        return f'session:{self.session_id}'

    def emit(self, event, data):
        self.manager.emit(event, {'session_id': self.session_id, **data}, self.room)

    def set_state(self, state, **extra):
        self.state = state
        self.last_activity = time.time()
        self.manager.emit('run_state', {'session_id': self.session_id, 'state': state, **extra}, 'app')

    async def can_use_tool(self, tool_name, tool_input, context):
        request_id = secrets.token_urlsafe(12)
        future = asyncio.get_running_loop().create_future()
        request = {
            'request_id': request_id,
            'tool': tool_name,
            'input': tool_input,
            'title': context.title or f'Claude wants to use {tool_name}',
            'description': context.description or context.decision_reason or '',
            'display_name': context.display_name or tool_name,
            'blocked_path': context.blocked_path or '',
            'can_remember': bool(context.suggestions),
        }
        self.pending[request_id] = (future, request, context)
        self.emit('permission_request', request)
        self.manager.audit('permission_requested', session_id=self.session_id, tool=tool_name)
        try:
            decision = await asyncio.wait_for(future, timeout=self.manager.config.permission_timeout)
        except asyncio.TimeoutError:
            self.emit('permission_resolved', {'request_id': request_id, 'behavior': 'deny', 'reason': 'timeout'})
            return PermissionResultDeny(message='No answer from the web user in time.')
        finally:
            self.pending.pop(request_id, None)

        self.emit('permission_resolved', {'request_id': request_id, 'behavior': decision['behavior']})
        if decision['behavior'] != 'allow':
            return PermissionResultDeny(
                message=decision.get('message') or 'The user denied this action.',
                interrupt=bool(decision.get('interrupt')),
            )
        updated_input = None
        if tool_name == 'AskUserQuestion' and isinstance(decision.get('answers'), dict):
            updated_input = {**tool_input, 'answers': decision['answers']}
        updated_permissions = None
        if decision.get('remember') and context.suggestions:
            # "Always allow" lasts for this session only; nothing is written to settings files.
            updated_permissions = [replace(s, destination='session') for s in context.suggestions]
        return PermissionResultAllow(updated_input=updated_input, updated_permissions=updated_permissions)

    async def connect(self, options):
        self.client = ClaudeSDKClient(options)
        await self.client.connect()

    async def send(self, prompt):
        self.set_state('busy')
        self.task = asyncio.ensure_future(self._consume(prompt))

    async def _consume(self, prompt):
        try:
            await self.client.query(prompt)
            async for message in self.client.receive_response():
                if isinstance(message, StreamEvent):
                    self._forward_stream(message.event)
                elif isinstance(message, RateLimitEvent):
                    self.manager.on_usage(usage_from_rate_limit(message.rate_limit_info))
                elif isinstance(message, ResultMessage):
                    self.cost = message.total_cost_usd
                    error = None
                    if message.is_error:
                        error = '; '.join(message.errors or []) or message.result or 'The turn ended with an error.'
                    self.set_state('idle', cost=self.cost, error=error, interrupted=(message.terminal_reason or '').startswith('aborted'))
        except Exception as exc:  # noqa: BLE001 - reported to the user, then the runner is dropped
            log.exception('Session %s failed', self.session_id)
            self.set_state('error', error=str(exc) or exc.__class__.__name__)
            await self.manager._drop(self)
        else:
            if self.state == 'busy':
                self.set_state('idle', cost=self.cost)

    def _forward_stream(self, event):
        etype = event.get('type')
        if etype == 'content_block_delta':
            delta = event.get('delta') or {}
            if delta.get('type') == 'text_delta':
                self.emit('stream', {'kind': 'text', 'text': delta.get('text', '')})
            elif delta.get('type') == 'thinking_delta':
                self.emit('stream', {'kind': 'thinking', 'text': delta.get('thinking', '')})
        elif etype == 'content_block_start':
            block = event.get('content_block') or {}
            if block.get('type') == 'tool_use':
                self.emit('stream', {'kind': 'tool', 'text': block.get('name', '')})
        elif etype == 'message_stop':
            self.emit('stream', {'kind': 'end', 'text': ''})

    def resolve(self, request_id, decision):
        item = self.pending.get(request_id)
        if not item:
            return False
        future = item[0]
        if not future.done():
            future.set_result(decision)
        return True

    async def interrupt(self):
        for future, _, _ in list(self.pending.values()):
            if not future.done():
                future.set_result({'behavior': 'deny', 'message': 'Interrupted by the user.', 'interrupt': True})
        if self.client and self.state == 'busy':
            await self.client.interrupt()

    async def close(self):
        await self.interrupt()
        if self.task and not self.task.done():
            self.task.cancel()
        if self.client:
            try:
                await self.client.disconnect()
            except Exception:  # noqa: BLE001 - the process may already be gone
                pass
        self.client = None


class RunnerManager:
    def __init__(self, config, index, emit, audit, on_usage=lambda usage: None):
        self.config = config
        self.index = index
        self.emit = emit
        self.audit = audit
        self.on_usage = on_usage
        self.runners = {}
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, name='runner-loop', daemon=True)
        self._thread.start()
        asyncio.run_coroutine_threadsafe(self._reaper(), self.loop)

    def _run_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def call(self, coro, timeout=60):
        """Run a coroutine on the runner loop from a Flask/Socket.IO thread."""
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    def state_of(self, session_id):
        runner = self.runners.get(session_id)
        return runner.state if runner else None

    def pending_requests(self, session_id):
        runner = self.runners.get(session_id)
        return [request for _, request, _ in runner.pending.values()] if runner else []

    def held_elsewhere(self, session_id):
        """A live Claude process other than ours holds this session."""
        if session_id in self.runners:
            return None
        return self.index.live_processes().get(session_id)

    def _cli_env(self):
        """Extra environment for the CLI: the long-lived token from `claude setup-token`, if configured."""
        path = self.config.oauth_token_file
        if not path:
            return {}
        try:
            token = path.read_text().strip()
        except OSError as exc:
            raise RunnerError(f'Cannot read oauth_token_file: {exc.strerror}') from exc
        return {'CLAUDE_CODE_OAUTH_TOKEN': token} if token else {}

    def _options(self, runner, *, resume=None, fork=False, model='', effort='', permission_mode=''):
        return ClaudeAgentOptions(
            env=self._cli_env(),
            cwd=runner.cwd,
            resume=resume,
            fork_session=fork,
            session_id=runner.session_id if (fork or not resume) else None,
            model=model or self.config.default_model or None,
            effort=effort or None,
            permission_mode=permission_mode or self.config.default_permission_mode,
            cli_path=self.config.claude_cli or None,
            can_use_tool=runner.can_use_tool,
            include_partial_messages=True,
            stderr=lambda line: log.debug('[claude %s] %s', runner.session_id[:8], line),
        )

    async def send(self, *, session_id=None, cwd, prompt, model='', effort='', permission_mode='', fork=False):
        """Send a prompt; start, resume or fork the session as needed. Returns the session id."""
        if self.config.read_only:
            raise RunnerError('This instance is read-only.')
        if permission_mode and permission_mode not in self.config.allowed_permission_modes:
            raise RunnerError(f'Permission mode "{permission_mode}" is not allowed on this instance.')
        if not self.config.is_allowed_path(cwd):
            raise RunnerError('This folder is outside the allowed roots.')

        runner = self.runners.get(session_id) if session_id and not fork else None
        if runner:
            if runner.state in ('busy', 'starting'):
                raise RunnerError('Claude is still working on this session. Wait or stop it first.')
            if permission_mode:
                await runner.client.set_permission_mode(permission_mode)
            if model:
                await runner.client.set_model(model)
            await runner.send(prompt)
            return runner.session_id

        if session_id and not fork:
            holder = self.held_elsewhere(session_id)
            if holder:
                raise RunnerError(
                    'This session is open in another Claude process '
                    f'({holder.get("entrypoint") or "unknown"}, pid {holder["pid"]}). '
                    'Continue it in a copy, or close it there first.'
                )

        new_id = session_id if (session_id and not fork) else str(uuid.uuid4())
        runner = Runner(self, new_id, cwd)
        self.runners[new_id] = runner
        runner.set_state('starting')
        try:
            options = self._options(
                runner, resume=session_id, fork=fork, model=model, effort=effort, permission_mode=permission_mode,
            )
            await runner.connect(options)
        except Exception as exc:
            await self._drop(runner)
            runner.set_state('error', error=str(exc) or exc.__class__.__name__)
            raise RunnerError(f'Could not start Claude Code: {exc}') from exc
        await runner.send(prompt)
        return new_id

    async def resolve(self, session_id, request_id, decision):
        runner = self.runners.get(session_id)
        return bool(runner and runner.resolve(request_id, decision))

    async def interrupt(self, session_id):
        runner = self.runners.get(session_id)
        if runner:
            await runner.interrupt()

    async def set_permission_mode(self, session_id, mode):
        if mode not in self.config.allowed_permission_modes:
            raise RunnerError(f'Permission mode "{mode}" is not allowed on this instance.')
        runner = self.runners.get(session_id)
        if runner and runner.client:
            await runner.client.set_permission_mode(mode)

    async def close(self, session_id):
        runner = self.runners.get(session_id)
        if runner:
            await self._drop(runner)
            runner.set_state('closed')

    async def _drop(self, runner):
        self.runners.pop(runner.session_id, None)
        await runner.close()

    async def _reaper(self):
        """Disconnect idle runners so their CLI processes do not linger."""
        limit = self.config.idle_disconnect_minutes * 60
        while True:
            await asyncio.sleep(30)
            now = time.time()
            for runner in list(self.runners.values()):
                if runner.state in ('idle', 'error') and not runner.pending and now - runner.last_activity > limit:
                    await self._drop(runner)
                    runner.set_state('closed')

    def shutdown(self):
        async def _all():
            for runner in list(self.runners.values()):
                await self._drop(runner)
        try:
            self.call(_all(), timeout=15)
        except Exception:  # noqa: BLE001 - best effort on exit
            pass

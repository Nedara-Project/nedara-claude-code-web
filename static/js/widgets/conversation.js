'use strict';

import { Nedara, api, basename, bus, copyText, escapeHtml, icon, isBusy, relativeTime, render, store, tildePath, toNode, toast } from '../core.js';

const TOOL_ICONS = {
    Bash: 'terminal', Read: 'eye', NotebookRead: 'eye', Edit: 'pencil', MultiEdit: 'pencil', Write: 'pencil',
    NotebookEdit: 'pencil', Grep: 'search', Glob: 'search', ToolSearch: 'search', WebFetch: 'globe',
    WebSearch: 'globe', Agent: 'sparkles', Task: 'sparkles', Skill: 'sparkles', AskUserQuestion: 'chat',
    TodoWrite: 'tasks', update_plan: 'tasks',
};
const STICK_DISTANCE = 160;
const GREETINGS = ['What shall we build today?', 'Where were we?', 'Ready when you are.'];

function toolIcon(event) {
    return icon(TOOL_ICONS[event.label] || (event.server ? 'plug' : 'tool'), 'size-[15px]');
}

function planItems(items) {
    return items.map((item) => {
        const map = {
            completed: ['done', 'text-ink-3 line-through decoration-ink-3/40', 'text-ok'],
            in_progress: ['current', 'text-ink font-medium', 'text-accent'],
            pending: ['pending', 'text-ink-2', 'text-ink-3'],
        };
        const [name, cls, iconCls] = map[item.status] || map.pending;
        return render('plan_item', { content: item.content, cls, icon: `<span class="${iconCls}">${icon(name, 'size-[15px]')}</span>` });
    }).join('');
}

function planProgress(items) {
    return `${items.filter((i) => i.status === 'completed').length}/${items.length}`;
}

function diffHtml(lines) {
    if (!lines || !lines.length) return '';
    const rows = lines.map((line) => {
        let cls = '';
        if (line.startsWith('@@')) cls = 'hunk';
        else if (line.startsWith('+')) cls = 'add';
        else if (line.startsWith('-')) cls = 'del';
        return `<div class="${cls}">${escapeHtml(line) || ' '}</div>`;
    }).join('');
    return `<div class="diff py-1.5">${rows}</div>`;
}

function section(title, content) {
    return render('tool_section', { title, content });
}

export function createConversation() {
    return Nedara.createWidget({
        selector: '#conversation',
        events: {
            'click [data-pane]': '_onPaneButton',
            'submit [data-question-form]': '_onQuestionSubmit',
        },

        start: function () {
            this.session = null;
            this.offset = 0;
            this.start_ = 0;
            this.loadToken = 0;
            this.plan = null;
            this.$selector.html(render('conversation_shell', { iconMenu: icon('menu'), iconDown: icon('down') }));
            this.scroller = this.$selector.find('#conv-scroll').get(0);
            this.body = this.$selector.find('#conv-body').get(0);
            this.scroller.addEventListener('scroll', () => this._onScroll(), { passive: true });

            const socket = store.socket;
            socket.on('events', (data) => this._onLiveEvents(data));
            socket.on('stream', (data) => this._onStream(data));
            socket.on('run_state', (data) => this._onRunState(data));
            socket.on('permission_request', (data) => data.session_id === store.currentId && this._addPermission(data));
            socket.on('permission_resolved', (data) => this._removePermission(data.request_id));
            socket.on('connect', () => this.session && this._subscribe());

            bus.on('route', (route) => this._route(route));
            bus.on('pane:changed', () => this._renderActions());
            bus.on('sessions:changed', () => this._refreshMeta());
            bus.on('composer:failed', () => {
                this.body.querySelectorAll('.recap-actions button').forEach((b) => { b.disabled = false; });
            });
        },

        // ── Routing ────────────────────────────────────────────────────────

        _route: function (route) {
            if (route.name === 'session') {
                this.open(route.id, { retry: route.fresh });
            } else {
                this.showWelcome();
            }
        },

        showWelcome: function () {
            this._leave();
            store.currentId = null;
            bus.emit('session:opened', null);
            const greeting = GREETINGS[Math.floor(Math.random() * GREETINGS.length)];
            this.$selector.find('#conv-heading').html(render('conversation_heading', {
                title: 'New session', cwd: '', folder: 'Choose a folder and describe the task', branch: '', status: '',
            }));
            this.$selector.find('#conv-actions').html('');
            this.body.innerHTML = render('welcome', {
                greeting: store.config.read_only ? 'Your Claude Code sessions' : greeting,
                subtitle: store.config.read_only
                    ? 'This instance is read-only: pick a session on the left to follow it live.'
                    : 'Start a new Claude Code session in one of your folders, or pick up a conversation on the left.',
            });
            this.body.classList.add('h-full');
            this._renderDock();
            bus.emit('composer:context', { mode: 'new' });
        },

        _leave: function () {
            if (this.session) store.socket.emit('unsubscribe', {});
            this.session = null;
            this.plan = null;
            this.toolEls = new Map();
            this.$selector.find('#dock-notices').html('');
        },

        open: async function (id, { retry = false } = {}) {
            const token = ++this.loadToken;
            if (this.session?.id !== id) {
                this._leave();
                store.currentId = id;
                bus.emit('session:opened', id);
                this.body.classList.remove('h-full');
                this.body.innerHTML = `<div class="flex justify-center py-24 text-ink-3">${icon('spinner', 'size-5')}</div>`;
            }
            let data = null;
            for (let attempt = 0; attempt < (retry ? 40 : 1); attempt++) {
                try {
                    data = await api(`/api/sessions/${id}`);
                    break;
                } catch (err) {
                    if (!retry || token !== this.loadToken) {
                        if (token === this.loadToken) {
                            toast(err.message, 'error');
                            window.location.hash = '#/';
                        }
                        return;
                    }
                    await new Promise((resolve) => setTimeout(resolve, 500));
                }
            }
            if (token !== this.loadToken || !data) return;

            this.session = data.session;
            store.sessions.set(data.session.id, { ...store.sessions.get(data.session.id), ...data.session });
            this.offset = data.offset;
            this.start_ = data.start;
            this.toolEls = new Map();
            this.plan = null;
            this.body.innerHTML = '';
            if (data.start > 0) {
                this.body.append(toNode(render('load_earlier', { icon: icon('down', 'size-4 rotate-180') })));
            }
            this._appendEvents(data.events, false);
            this._renderHeading();
            this._renderActions();
            this._renderDock();
            this.$selector.find('#dock-notices [data-request]').each((i, el) => el.remove());
            data.pending.forEach((request) => this._addPermission(request));
            this._syncWorking();
            this._subscribe();
            this._scrollToBottom();
            bus.emit('composer:context', { mode: 'session', session: this.session });
            bus.emit('session:loaded', this.session);
        },

        _subscribe: function () {
            store.socket.emit('subscribe', { session_id: this.session.id, offset: this.offset });
        },

        _onLoadEarlier: async function (ev) {
            const button = ev.currentTarget;
            button.disabled = true;
            try {
                const data = await api(`/api/sessions/${this.session.id}?before=${this.start_}`);
                const anchor = this.scroller.scrollHeight - this.scroller.scrollTop;
                button.closest('div').remove();
                const fragment = document.createDocumentFragment();
                const holder = document.createElement('div');
                this._appendEvents(data.events, false, holder);
                fragment.append(...holder.childNodes);
                this.body.prepend(fragment);
                this.start_ = data.start;
                if (data.start > 0) {
                    this.body.prepend(toNode(render('load_earlier', { icon: icon('down', 'size-4 rotate-180') })));
                }
                this.scroller.scrollTop = this.scroller.scrollHeight - anchor;
            } catch (err) {
                button.disabled = false;
                toast(err.message, 'error');
            }
        },

        // ── Header and dock ────────────────────────────────────────────────

        _sessionState: function () {
            const live = store.sessions.get(this.session?.id) || this.session;
            return live || {};
        },

        _renderHeading: function () {
            if (!this.session) return;
            const s = this._sessionState();
            let status = '';
            if (isBusy(s)) {
                status = '<span class="chip !bg-accent-soft !text-accent-ink"><span class="dot dot-busy !size-1.5"></span>Working</span>';
            } else if (s.run_state === 'idle') {
                status = '<span class="chip"><span class="dot dot-idle !size-1.5"></span>Connected</span>';
            } else if (s.process) {
                status = `<span class="chip">${icon('desktop', 'size-3')}${s.process.entrypoint === 'claude-desktop' ? 'Claude Desktop' : 'Other process'}</span>`;
            }
            this.$selector.find('#conv-heading').html(render('conversation_heading', {
                title: s.title,
                cwd: tildePath(s.cwd),
                folder: basename(s.cwd),
                branch: s.branch && s.branch !== 'HEAD' ? `<span class="text-ink-3/60">·</span><span class="inline-flex items-center gap-1 truncate">${icon('branch', 'size-3')}${escapeHtml(s.branch)}</span>` : '',
                status: (s.parent_id ? `<a href="#/s/${s.parent_id}" class="chip ml-1 shrink-0 hover:text-ink hover:bg-fill-strong" title="Open the original session">${icon('fork', 'size-3')}Copy</a>` : '')
                    + (status ? `<span class="ml-1 shrink-0">${status}</span>` : ''),
            }));
            document.title = `${s.title} · Claude Code`;
        },

        _renderActions: function () {
            if (!this.session) return;
            this.$selector.find('#conv-actions').html(render('conversation_actions', {
                iconDiff: icon('diff'), iconFolder: icon('folder'), iconInfo: icon('info'),
                changes: String(store.pane === 'changes'), files: String(store.pane === 'files'),
                info: String(store.pane === 'info'), filesHidden: store.config.file_viewer ? '' : 'hidden',
            }));
        },

        _renderDock: function () {
            const notices = this.$selector.find('#dock-notices').get(0);
            notices.querySelectorAll('[data-banner], #dock-plan').forEach((el) => el.remove());
            if (!this.session) return;
            const s = this._sessionState();
            const strong = (text) => `<strong class="text-ink font-medium">${escapeHtml(text)}</strong>`;
            const link = (id, text) => `<a href="#/s/${id}" class="text-accent-ink font-medium hover:underline">${escapeHtml(text)}</a>`;
            const banners = [];
            if (store.config.read_only) {
                banners.push(['eye', `This instance is ${strong('read-only')}. New messages in this session appear here live.`]);
            } else if (s.process && !s.run_state) {
                const where = s.process.entrypoint === 'claude-desktop' ? 'Claude Desktop' : 'another Claude process';
                let html = `Open in ${strong(where)}, mirrored live. Replying here continues it in a ${strong('copy')}.`;
                if (s.copies?.length) {
                    const latest = s.copies[0];
                    const more = s.copies.length > 1 ? ` (${s.copies.length} copies)` : '';
                    html += ` ${link(latest.id, `Latest copy, ${relativeTime(latest.updated)} →`)}${more}`;
                }
                banners.push(['fork', html]);
            } else if (s.copies?.length) {
                banners.push(['fork', `Continued from the web in ${link(s.copies[0].id, s.copies.length > 1 ? `${s.copies.length} copies →` : 'a copy →')}`]);
            }
            if (s.parent_id) {
                const command = `claude --resume ${s.id}`;
                banners.push(['fork', `Web copy of ${link(s.parent_id, 'the original session')}, not listed in Claude Desktop. `
                    + `Reopen it in a terminal: <code class="font-mono text-[11.5px] bg-fill rounded-md px-1.5 py-0.5">${escapeHtml(command)}</code> `
                    + `<button class="text-accent-ink font-medium hover:underline" data-js-function="_onCopyText" data-text="${escapeHtml(command)}">Copy</button>`]);
            }
            for (const [name, html] of banners.reverse()) {
                const node = toNode(render('dock_banner', { icon: icon(name), html }));
                node.dataset.banner = '1';
                notices.prepend(node);
            }
            if (this.plan && this.plan.some((i) => i.status !== 'completed')) {
                const current = this.plan.find((i) => i.status === 'in_progress') || this.plan.find((i) => i.status === 'pending');
                const node = toNode(render('dock_plan', {
                    icon: `<span class="text-accent">${icon('tasks', 'size-[15px]')}</span>`,
                    current: current?.content || 'Plan',
                    progress: planProgress(this.plan),
                    chevron: icon('down', 'size-4'),
                    items: planItems(this.plan),
                }));
                notices.append(node);
            }
        },

        _refreshMeta: function () {
            if (!this.session) return;
            const s = store.sessions.get(this.session.id);
            if (!s) return;
            const changed = s.title !== this.session.title || Boolean(s.process) !== Boolean(this.session.process)
                || s.run_state !== this.session.run_state || s.process?.status !== this.session.process?.status
                || (s.copies || []).length !== (this.session.copies || []).length || s.parent_id !== this.session.parent_id;
            this.session = { ...this.session, ...s };
            if (changed) {
                this._renderHeading();
                this._renderDock();
                this._syncWorking();
                bus.emit('composer:context', { mode: 'session', session: this.session });
            }
        },

        // ── Events ─────────────────────────────────────────────────────────

        _appendEvents: function (events, live, container = this.body) {
            for (const event of events) {
                const node = this._renderEvent(event, container);
                if (node) container.append(node);
            }
            if (live) this._syncWorking();
        },

        _renderEvent: function (event, container = this.body) {
            switch (event.t) {
            case 'user': {
                // A newer message answers any earlier summary: its buttons go away.
                container.querySelectorAll('.recap-actions').forEach((el) => el.remove());
                let body = event.segments.map((seg) => {
                    if (seg.kind === 'pasted') {
                        return render('ev_user_paste', { text: seg.text, lines: seg.lines, icon: icon('paste', 'size-3.5') });
                    }
                    return render(event.command ? 'ev_user_command' : 'ev_user_text', { text: seg.text });
                }).join('');
                if (event.images) {
                    body += `<div class="text-[12.5px] text-ink-2 flex items-center gap-1.5">${icon('file', 'size-3.5')}${event.images} image${event.images > 1 ? 's' : ''}</div>`;
                }
                return toNode(render('ev_user', { id: event.id, body }));
            }
            case 'text':
                this._clearStream();
                return toNode(render('ev_text', { id: event.id, html: event.html }));
            case 'thinking':
                return toNode(render('ev_thinking', { id: event.id, text: event.text, icon: icon('bulb', 'size-3.5') }));
            case 'tool':
                this._clearStream();
                return this._renderTool(event);
            case 'result':
                this._applyResult(event);
                return null;
            case 'notice': {
                const styles = { warning: 'text-warn', error: '!text-bad', info: '' };
                return toNode(render('ev_notice', {
                    id: event.id, text: event.text, cls: styles[event.level] || '',
                    icon: icon(event.level === 'info' ? 'info' : 'alert', 'size-3.5'),
                }));
            }
            case 'title':
                if (this.session) {
                    this.session.title = event.title;
                    this._renderHeading();
                }
                return null;
            case 'cost':
                if (this.session) this.session.cost = event.usd;
                return null;
            default:
                return null;
            }
        },

        _renderTool: function (event) {
            if (event.plan) {
                this.plan = event.plan;
                this._renderDock();
                return toNode(render('ev_plan', {
                    id: event.id, title: 'Plan updated', progress: planProgress(event.plan),
                    icon: `<span class="text-accent">${icon('tasks', 'size-[15px]')}</span>`, items: planItems(event.plan),
                }));
            }
            if (event.recap !== undefined) {
                // A wrap-up closes the turn: the pinned plan is done, as in Claude Desktop.
                if (this.plan) {
                    this.plan = null;
                    this._renderDock();
                }
                const ask = event.ask ? `<p class="mt-2 font-medium text-ink">${escapeHtml(event.ask)}</p>` : '';
                let actions = '';
                if (event.actions?.length && !store.config.read_only) {
                    // Like Claude Desktop: the first button is the suggested answer.
                    actions = render('recap_actions', {
                        buttons: event.actions.map((a, i) => render('recap_action', {
                            label: a.label, message: a.message, cls: i === 0 ? 'btn-primary' : 'btn-soft',
                        })).join(''),
                    });
                }
                return toNode(render('ev_recap', { id: event.id, recap: event.recap, ask, actions, icon: icon('check', 'size-3.5') }));
            }
            let body = '';
            if (event.command) body += section('Command', render('code_block', { text: event.command, cls: '' }));
            if (event.diff && event.diff.length) body += section('Changes', diffHtml(event.diff));
            else if (event.input) body += section('Input', render('code_block', { text: event.input, cls: '' }));
            const node = toNode(render('ev_tool', {
                id: event.id, label: event.label, summary: event.summary, icon: toolIcon(event),
                status: icon('spinner', 'size-3.5 text-ink-3'), body,
            }));
            this.toolEls.set(event.id, node);
            return node;
        },

        _applyResult: function (event) {
            const node = this.toolEls.get(event.tool_id);
            if (!node) return;
            node.dataset.state = event.is_error ? 'error' : 'done';
            node.querySelector('.tool-status').innerHTML = event.is_error
                ? `<span class="text-bad">${icon('error', 'size-3.5')}</span>`
                : '';
            if (event.is_error) node.querySelector('.tool-icon').classList.replace('text-ink-3', 'text-bad');
            let text = event.text || (event.images ? `[${event.images} image(s)]` : '');
            if (event.truncated) text += '\n…';
            if (text) {
                node.querySelector('.tool-body').insertAdjacentHTML('beforeend', section(
                    event.is_error ? 'Error' : 'Output',
                    render('code_block', { text, cls: event.is_error ? '!text-bad' : 'text-ink-2' }),
                ));
            }
        },

        _onCopyText: async function (ev) {
            const ok = await copyText(ev.currentTarget.dataset.text);
            toast(ok ? 'Copied to the clipboard.' : 'Copy failed: select the text instead.', ok ? 'ok' : 'error');
        },

        _onRecapAction: function (ev) {
            const button = ev.currentTarget;
            button.closest('.recap-actions').querySelectorAll('button').forEach((b) => { b.disabled = true; });
            bus.emit('composer:send', button.dataset.message);
        },

        _onToggleTool: function (ev) {
            const button = ev.currentTarget;
            const body = button.parentElement.querySelector('.tool-body');
            const open = button.getAttribute('aria-expanded') !== 'true';
            button.setAttribute('aria-expanded', String(open));
            body.classList.toggle('hidden', !open);
        },

        _onLiveEvents: function (data) {
            if (!this.session || data.session_id !== this.session.id) return;
            if (data.from !== this.offset) {
                if (data.to <= this.offset) return;
                this.open(this.session.id);
                return;
            }
            this.offset = data.to;
            const stick = this._nearBottom();
            this._appendEvents(data.events, true);
            if (stick) this._scrollToBottom(true);
            else this._showJump(true);
        },

        // ── Streaming and run state ────────────────────────────────────────

        _onStream: function (data) {
            if (!this.session || data.session_id !== this.session.id) return;
            const stick = this._nearBottom();
            if (data.kind === 'text') {
                let stream = this.body.querySelector('#stream');
                if (!stream) {
                    this.body.querySelector('#working')?.remove();
                    stream = toNode(render('ev_stream'));
                    this.body.append(stream);
                }
                stream.querySelector('#stream-thinking').classList.add('hidden');
                stream.querySelector('#stream-text').textContent += data.text;
            } else if (data.kind === 'thinking') {
                this._setWorking('Thinking…');
            } else if (data.kind === 'tool') {
                this._setWorking(`Using ${data.text}…`);
            }
            if (stick) this._scrollToBottom();
        },

        _clearStream: function () {
            this.body?.querySelector('#stream')?.remove();
        },

        _setWorking: function (text) {
            if (this.body.querySelector('#stream')) return;
            let node = this.body.querySelector('#working');
            if (!node) {
                node = toNode(render('working', { text }));
                this.body.append(node);
            } else {
                node.querySelector('.shimmer').textContent = text;
                this.body.append(node);
            }
        },

        _syncWorking: function () {
            if (!this.session) return;
            const busy = isBusy(this._sessionState());
            if (busy) {
                this._setWorking('Working…');
            } else {
                this.body.querySelector('#working')?.remove();
                this._clearStream();
            }
        },

        _onRunState: function (data) {
            const session = store.sessions.get(data.session_id);
            if (session) {
                session.run_state = data.state === 'closed' ? null : data.state;
                bus.emit('sessions:changed');
            }
            if (data.error && (data.session_id === store.currentId)) toast(data.error, 'error');
            if (!this.session || data.session_id !== this.session.id) return;
            this.session.run_state = data.state === 'closed' ? null : data.state;
            if (data.cost != null) this.session.cost = data.cost;
            this._renderHeading();
            this._renderDock();
            this._syncWorking();
            bus.emit('composer:context', { mode: 'session', session: this.session });
        },

        // ── Permissions and questions ──────────────────────────────────────

        _addPermission: function (request) {
            const notices = this.$selector.find('#dock-notices').get(0);
            if (notices.querySelector(`[data-request="${CSS.escape(request.request_id)}"]`)) return;
            let html;
            if (request.tool === 'AskUserQuestion' && Array.isArray(request.input?.questions)) {
                const questions = request.input.questions.map((q, qi) => render('question_block', {
                    question: q.question,
                    multi: q.multiSelect ? '1' : '',
                    options: (q.options || []).map((o) => render('question_option', {
                        type: q.multiSelect ? 'checkbox' : 'radio', name: `q${qi}`,
                        label: o.label, description: o.description || '',
                    })).join(''),
                })).join('');
                html = render('question_card', { request_id: request.request_id, questions });
            } else {
                let body = '';
                const input = request.input || {};
                if (request.tool === 'Bash' && input.command) {
                    body = render('code_block', { text: input.command, cls: '' });
                } else if (request.tool === 'ExitPlanMode' && input.plan) {
                    body = render('code_block', { text: input.plan, cls: '!whitespace-pre-wrap font-sans text-[13.5px]' });
                } else if ((request.tool === 'Edit' || request.tool === 'Write') && input.file_path) {
                    const lines = request.tool === 'Write'
                        ? (input.content || '').split('\n').slice(0, 200).map((l) => `+${l}`)
                        : [...(input.old_string || '').split('\n').map((l) => `-${l}`), ...(input.new_string || '').split('\n').map((l) => `+${l}`)];
                    body = `<div class="font-mono text-[12px] text-ink-2 truncate">${escapeHtml(input.file_path)}</div>${diffHtml(lines)}`;
                } else {
                    body = render('code_block', { text: JSON.stringify(input, null, 2).slice(0, 4000), cls: '' });
                }
                html = render('permission_card', {
                    request_id: request.request_id,
                    title: request.title,
                    description: request.description || request.blocked_path || request.display_name,
                    icon: icon(request.tool === 'ExitPlanMode' ? 'tasks' : 'lock', 'size-4'),
                    body,
                    rememberHidden: request.can_remember ? '' : 'hidden',
                });
            }
            const stick = this._nearBottom();
            notices.append(toNode(html));
            if (stick) this._scrollToBottom();
            bus.emit('permission:pending', true);
        },

        _removePermission: function (requestId) {
            this.$selector.find(`#dock-notices [data-request="${CSS.escape(requestId)}"]`).each((i, el) => el.remove());
        },

        _sendDecision: async function (card, payload) {
            card.querySelectorAll('button').forEach((b) => { b.disabled = true; });
            try {
                await api(`/api/sessions/${this.session.id}/permission`, {
                    method: 'POST', body: { request_id: card.dataset.request, ...payload },
                });
                card.remove();
            } catch (err) {
                toast(err.message, 'error');
                card.querySelectorAll('button').forEach((b) => { b.disabled = false; });
                if (/no longer pending/.test(err.message)) card.remove();
            }
        },

        _onPermission: function (ev) {
            const button = ev.currentTarget;
            const card = button.closest('[data-request]');
            this._sendDecision(card, {
                behavior: button.dataset.behavior,
                remember: button.dataset.remember === '1',
            });
        },

        _onQuestionSubmit: function (ev) {
            ev.preventDefault();
            const form = ev.currentTarget;
            const answers = {};
            form.querySelectorAll('[data-question]').forEach((fieldset) => {
                const picked = [...fieldset.querySelectorAll('input:checked')].map((i) => i.value);
                const other = fieldset.querySelector('[data-other]').value.trim();
                if (other) picked.push(other);
                answers[fieldset.dataset.question] = picked.join(', ');
            });
            this._sendDecision(form, { behavior: 'allow', answers });
        },

        // ── Scrolling ──────────────────────────────────────────────────────

        _nearBottom: function () {
            const s = this.scroller;
            return s.scrollHeight - s.scrollTop - s.clientHeight < STICK_DISTANCE;
        },

        _scrollToBottom: function (smooth = false) {
            requestAnimationFrame(() => {
                this.scroller.scrollTo({ top: this.scroller.scrollHeight, behavior: smooth ? 'smooth' : 'auto' });
            });
            this._showJump(false);
        },

        _showJump: function (show) {
            this.$selector.find('#dock-jump').toggleClass('hidden', !show);
        },

        _onScroll: function () {
            if (this._nearBottom()) this._showJump(false);
        },

        _onJump: function () {
            this._scrollToBottom(true);
        },

        _onMenu: function () {
            bus.emit('sidebar:open');
        },

        _onPaneButton: function (ev) {
            bus.emit('pane:toggle', ev.currentTarget.dataset.pane);
        },
    });
}

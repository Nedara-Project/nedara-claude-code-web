'use strict';

import { Nedara, api, basename, bus, escapeHtml, formatBytes, icon, render, store, tildePath } from '../core.js';

const STATUS_STYLES = { M: 'text-warn', A: 'text-ok', D: 'text-bad', R: 'text-info', '??': 'text-ok', '?': 'text-ok' };

function diffLines(diff) {
    return diff.split('\n').map((line) => {
        let cls = '';
        if (line.startsWith('@@')) cls = 'hunk';
        else if (line.startsWith('+++') || line.startsWith('---') || line.startsWith('diff ') || line.startsWith('index ')) cls = 'text-ink-3';
        else if (line.startsWith('+')) cls = 'add';
        else if (line.startsWith('-')) cls = 'del';
        return `<div class="${cls}">${escapeHtml(line) || ' '}</div>`;
    }).join('');
}

function message(name, text) {
    return render('pane_message', { icon: icon(name, 'size-6'), text });
}

export function createPane() {
    return Nedara.createWidget({
        selector: '#pane',
        events: {
            'click [data-tab]': '_onTab',
        },

        start: function () {
            this.tab = null;
            this.filePath = '';
            bus.on('pane:toggle', (tab) => (this.tab === tab ? this.close() : this.show(tab)));
            bus.on('session:loaded', () => this.tab && this.show(this.tab, { keepPath: false }));
            bus.on('session:opened', (id) => !id && this.close());
        },

        show: function (tab, { keepPath = true } = {}) {
            if (!store.currentId) return;
            if (!keepPath) this.filePath = '';
            const opening = !this.tab;
            this.tab = tab;
            store.pane = tab;
            this.$selector.html(render('pane_shell', {
                changes: String(tab === 'changes'), files: String(tab === 'files'), info: String(tab === 'info'),
                filesHidden: store.config.file_viewer ? '' : 'hidden',
                iconRefresh: icon('refresh'), iconClose: icon('close'),
            }));
            this.$selector.removeClass('hidden').addClass('flex');
            if (opening) this.$selector.addClass('pane-enter');
            this.bodyEl = this.$selector.find('#pane-body').get(0);
            bus.emit('pane:changed');
            this._load();
        },

        close: function () {
            this.tab = null;
            store.pane = null;
            this.$selector.addClass('hidden').removeClass('flex', 'pane-enter');
            bus.emit('pane:changed');
        },

        _onTab: function (ev) {
            this.show(ev.currentTarget.dataset.tab);
        },

        _onRefresh: function () {
            this._load();
        },

        _load: async function () {
            const id = store.currentId;
            this.bodyEl.innerHTML = `<div class="flex justify-center py-16 text-ink-3">${icon('spinner', 'size-5')}</div>`;
            try {
                if (this.tab === 'changes') await this._loadChanges(id);
                else if (this.tab === 'files') await this._loadFiles(id, this.filePath, this.fileIsDir !== false);
                else this._renderInfo();
            } catch (err) {
                this.bodyEl.innerHTML = message('alert', err.message);
            }
        },

        _loadChanges: async function (id) {
            const data = await api(`/api/sessions/${id}/changes`);
            if (!data.repo) {
                this.bodyEl.innerHTML = message('diff', 'This folder is not a git repository.');
                return;
            }
            if (!data.files.length) {
                this.bodyEl.innerHTML = message('done', `No uncommitted changes on ${data.branch || 'this branch'}.`);
                return;
            }
            this.bodyEl.innerHTML = render('pane_changes', {
                iconBranch: `<span class="text-ink-3">${icon('branch', 'size-3.5')}</span>`,
                branch: data.branch || 'detached HEAD',
                root: tildePath(data.root),
                files: data.files.map((f) => render('change_row', { ...f, cls: STATUS_STYLES[f.status] || 'text-ink-2' })).join(''),
                diff: data.diff
                    ? `<div class="diff py-1.5 !max-h-none">${diffLines(data.diff)}</div>${data.truncated ? '<p class="text-[12px] text-ink-3">Diff truncated.</p>' : ''}`
                    : '',
            });
        },

        _loadFiles: async function (id, path, isDir) {
            if (isDir) {
                const data = await api(`/api/sessions/${id}/files?path=${encodeURIComponent(path)}`);
                const parts = data.path ? data.path.split('/') : [];
                let crumbs = render('crumb', { name: basename(store.sessions.get(id)?.cwd) || 'root', path: '' });
                parts.forEach((part, i) => {
                    crumbs += render('crumb', { name: part, path: parts.slice(0, i + 1).join('/') });
                });
                this.bodyEl.innerHTML = render('pane_files', {
                    crumbs,
                    entries: data.entries.length
                        ? data.entries.map((e) => render('file_row', {
                            name: e.name, path: e.path, dir: e.dir ? '1' : '',
                            icon: icon(e.dir ? 'folder' : (e.denied ? 'lock' : 'file'), 'size-4'),
                            cls: e.denied ? 'text-ink-3' : '',
                        })).join('')
                        : '<li class="px-3 py-6 text-center text-[13px] text-ink-3">Empty folder.</li>',
                });
                return;
            }
            const data = await api(`/api/sessions/${id}/files?file=${encodeURIComponent(path)}`);
            let content;
            if (data.binary) content = message('file', 'Binary file, not shown.');
            else {
                content = render('code_block', { text: data.content, cls: '!max-h-none text-[12px]' });
                if (data.truncated) content += '<p class="text-[12px] text-ink-3">Only the beginning of the file is shown.</p>';
            }
            this.bodyEl.innerHTML = render('pane_file', {
                path, parent: path.split('/').slice(0, -1).join('/'), size: formatBytes(data.size),
                iconBack: icon('back'), content,
            });
        },

        _onFileClick: function (ev) {
            const el = ev.currentTarget;
            this.filePath = el.dataset.path;
            this.fileIsDir = el.dataset.dir === '1';
            this._load();
        },

        _renderInfo: function () {
            const s = { ...store.sessions.get(store.currentId) };
            const rows = [
                ['Session', s.id, 'font-mono text-[12px]'],
                ['Folder', tildePath(s.cwd), 'font-mono text-[12px]'],
                ['Branch', s.branch || '—'],
                ['Started', s.created ? new Date(s.created).toLocaleString() : '—'],
                ['Last activity', new Date(s.updated * 1000).toLocaleString()],
                ['Model', s.model || 'Default'],
                ['Permissions', s.permission_mode || 'Default'],
                ['Size', formatBytes(s.size || 0)],
                ['Origin', s.parent_id ? 'Web copy' : (s.desktop ? 'Claude Desktop' : (s.web ? 'This web app' : 'Claude Code CLI'))],
                ['Held by', s.process ? `${s.process.entrypoint} · pid ${s.process.pid} · ${s.process.status || 'idle'}` : (s.run_state ? 'This server' : 'Nobody')],
                ['Resume in a terminal', `claude --resume ${s.id}`, 'font-mono text-[12px]'],
            ];
            if (s.parent_id) {
                const parent = store.sessions.get(s.parent_id);
                rows.splice(1, 0, ['Copy of', `${parent ? parent.title : s.parent_id} (${s.parent_id})`]);
            }
            if (s.copies?.length) {
                rows.splice(1, 0, ['Web copies', s.copies.map((c) => c.id).join('\n'), 'font-mono text-[12px] whitespace-pre-line']);
            }
            this.bodyEl.innerHTML = render('pane_info', {
                rows: rows.map(([label, value, cls]) => render('info_row', { label, value, cls: cls || '' })).join(''),
            }) + '<p class="mt-4 px-1 text-[11.5px] text-ink-3">Nedara Claude Code Web is an independent Nedara Project, '
                + 'not affiliated with or endorsed by Anthropic.</p>';
        },
    });
}

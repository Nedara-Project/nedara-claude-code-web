'use strict';

import { Nedara, basename, bus, icon, isBusy, nedaraMark, postForm, api, relativeTime, render, store, toast } from '../core.js';

const THEMES = ['auto', 'light', 'dark'];
const THEME_LABELS = { auto: 'Automatic', light: 'Light', dark: 'Dark' };
const THEME_ICONS = { auto: 'auto', light: 'sun', dark: 'moon' };

function currentTheme() {
    try {
        return localStorage.getItem('nccw-theme') || 'auto';
    } catch (e) {
        return 'auto';
    }
}

function applyTheme(theme) {
    const dark = theme === 'dark' || (theme === 'auto' && window.matchMedia('(prefers-color-scheme: dark)').matches);
    document.documentElement.classList.toggle('dark', dark);
}

function dayBucket(updated) {
    const date = new Date(updated * 1000);
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const days = Math.floor((today - date) / 86400000) + 1;
    if (date >= today) return 'Today';
    if (days <= 1) return 'Yesterday';
    if (days <= 7) return 'Previous 7 days';
    if (days <= 30) return 'Previous 30 days';
    return 'Older';
}

function statusHtml(session) {
    if (isBusy(session)) {
        return '<span class="dot dot-busy" title="Working"></span>';
    }
    if (session.run_state === 'idle') {
        return '<span class="dot dot-idle" title="Connected here"></span>';
    }
    if (session.process) {
        return `<span class="text-ink-3" title="Open in ${session.process.entrypoint === 'claude-desktop' ? 'Claude Desktop' : 'another Claude process'}">${icon('desktop', 'size-3.5')}</span>`;
    }
    return '';
}

export function createSidebar() {
    return Nedara.createWidget({
        selector: '#sidebar',
        events: {
            'input #session-search': '_onSearch',
            'click [data-filter]': '_onFilter',
            'click [data-session]': '_onSessionClick',
        },

        start: function () {
            this.filter = 'recent';
            this.query = '';
            this.theme = currentTheme();
            this._renderShell();
            this.renderList();

            bus.on('sessions:changed', () => this.renderList());
            bus.on('session:opened', () => this._markCurrent());
            window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => applyTheme(this.theme));
            document.getElementById('scrim').addEventListener('click', () => this.close());
            document.addEventListener('keydown', (ev) => {
                const typing = ['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement?.tagName);
                if (!typing && ev.key === 'n' && !ev.metaKey && !ev.ctrlKey) {
                    ev.preventDefault();
                    this._onNew();
                } else if (!typing && ev.key === '/') {
                    ev.preventDefault();
                    this.$selector.find('#session-search').get(0).focus();
                }
            });
            bus.on('sidebar:open', () => this.open());
        },

        _renderShell: function () {
            this.$selector.html(render('sidebar_shell', {
                version: store.config.version,
                newHidden: store.config.read_only ? 'hidden' : '',
                iconNew: icon('new'),
                iconSearch: icon('search', 'size-4'),
                iconClose: icon('close'),
                iconTheme: icon(THEME_ICONS[this.theme]),
                themeLabel: THEME_LABELS[this.theme],
                iconShield: icon('shield'),
                iconLogout: icon('logout'),
                nedaraMark: nedaraMark('size-3'),
                iconGithub: icon('github', 'size-3.5'),
            }));
        },

        _visibleSessions: function () {
            const query = this.query.toLowerCase();
            return [...store.sessions.values()].filter((s) => {
                if (this.filter === 'archived' ? !s.archived : s.archived) return false;
                if (!query) return true;
                return `${s.title} ${s.first_prompt} ${s.cwd}`.toLowerCase().includes(query);
            }).sort((a, b) => b.updated - a.updated);
        },

        renderList: function () {
            const sessions = this._visibleSessions();
            const list = this.$selector.find('#session-list');
            if (!sessions.length) {
                list.html(render('session_empty', { text: this.query ? 'No matching session.' : 'No session here yet.' }));
                return;
            }
            const groups = new Map();
            for (const session of sessions) {
                const key = this.filter === 'projects' ? session.cwd : dayBucket(session.updated);
                if (!groups.has(key)) groups.set(key, []);
                groups.get(key).push(session);
            }
            let html = '';
            for (const [key, items] of groups) {
                const label = this.filter === 'projects' ? basename(key) : key;
                html += render('session_section', { label, title: key });
                for (const session of items) {
                    const meta = this.filter === 'projects'
                        ? relativeTime(session.updated)
                        : `${basename(session.cwd)} · ${relativeTime(session.updated)}`;
                    html += render('session_row', {
                        id: session.id,
                        title: session.title,
                        meta,
                        current: String(session.id === store.currentId),
                        status: statusHtml(session),
                    });
                }
            }
            list.html(html);
        },

        _markCurrent: function () {
            this.$selector.find('[data-session]').each((i, el) => {
                el.setAttribute('aria-current', String(el.dataset.session === store.currentId));
            });
            this.close();
        },

        _onSearch: function (ev) {
            this.query = ev.currentTarget.value.trim();
            this.renderList();
        },

        _onFilter: function (ev) {
            this.filter = ev.currentTarget.dataset.filter;
            this.$selector.find('[data-filter]').each((i, el) => {
                el.setAttribute('aria-selected', String(el.dataset.filter === this.filter));
            });
            this.renderList();
        },

        _onSessionClick: function (ev) {
            if (ev.metaKey || ev.ctrlKey) return;
            ev.preventDefault();
            window.location.hash = `#/s/${ev.currentTarget.dataset.session}`;
        },

        _onNew: function () {
            if (store.config.read_only) return;
            window.location.hash = '#/new';
            this.close();
        },

        _onTheme: function () {
            this.theme = THEMES[(THEMES.indexOf(this.theme) + 1) % THEMES.length];
            try {
                localStorage.setItem('nccw-theme', this.theme);
            } catch (e) { /* storage may be blocked */ }
            applyTheme(this.theme);
            this.$selector.find('[data-js-function="_onTheme"]').html(icon(THEME_ICONS[this.theme]));
            this.$selector.find('#theme-label').text(THEME_LABELS[this.theme]);
        },

        _onLogout: function () {
            postForm('/logout');
        },

        _onRevokeAll: async function () {
            if (!window.confirm('Sign out every browser, including this one?')) return;
            try {
                await api('/api/revoke-sessions', { method: 'POST' });
                window.location.href = '/login';
            } catch (err) {
                toast(err.message, 'error');
            }
        },

        open: function () {
            this.$selector.removeClass('-translate-x-full');
            Nedara.el('#scrim').removeClass('hidden');
        },

        close: function () {
            if (window.matchMedia('(min-width: 768px)').matches) return;
            this.$selector.addClass('-translate-x-full');
            Nedara.el('#scrim').addClass('hidden');
        },
    });
}

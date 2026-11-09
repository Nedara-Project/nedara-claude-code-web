'use strict';

import { Nedara, api, basename, bus, icon, isBusy, render, store, tildePath, toast } from '../core.js';
import { openFolderPicker } from './dialogs.js';

const MODE_LABELS = {
    default: 'Ask before acting',
    acceptEdits: 'Accept edits',
    plan: 'Plan mode',
    auto: 'Auto',
    dontAsk: "Don't ask",
    bypassPermissions: 'Bypass permissions',
};
const MODE_DESCRIPTIONS = {
    default: 'Asks before editing files or running commands',
    acceptEdits: 'Edits files freely, asks for the rest',
    plan: 'Explores and proposes a plan, changes nothing',
    auto: 'A classifier approves or denies each action',
    dontAsk: 'Denies anything not pre-approved',
    bypassPermissions: 'Runs everything without asking',
};
const EFFORT_DESCRIPTIONS = {
    low: 'Fastest answers, minimal thinking',
    medium: 'Balanced',
    high: 'Thinks things through',
    xhigh: 'Deeper reasoning for hard tasks',
    max: 'Most thorough, slowest',
};
const PICKER_TITLES = { model: 'Model', effort: 'Effort', mode: 'Permissions' };

function modelLabel(id) {
    const match = /^claude-([a-z]+)-(\d+)-(\d+)/.exec(id);
    if (!match) return id;
    return `${match[1][0].toUpperCase()}${match[1].slice(1)} ${match[2]}.${match[3]}`;
}


function remember(key, value) {
    try {
        if (value === undefined) return localStorage.getItem(`nccw-${key}`) || '';
        localStorage.setItem(`nccw-${key}`, value);
    } catch (e) { /* storage may be blocked */ }
    return '';
}

export function createComposer() {
    return Nedara.createWidget({
        selector: '#composer',
        events: {
            'submit #composer-form': '_onSubmit',
            'keydown #composer-input': '_onKeydown',
            'input #composer-input': '_onInput',
            'click [data-picker]': '_onPickerToggle',
            'click .picker-option': '_onPickerChoose',
            'keydown .picker-menu': '_onPickerKeydown',
        },

        start: function () {
            this.mode = 'new';
            this.session = null;
            this.cwd = remember('cwd') || store.config.roots[0];
            this.sending = false;
            this._buildChoices();
            this._render();
            bus.on('composer:context', (ctx) => this._setContext(ctx));
            bus.on('composer:send', (text) => this.sendText(text));
            document.addEventListener('click', (ev) => {
                if (!ev.target.closest?.('[data-picker], .picker-menu')) this._closePicker();
            });
        },

        _buildChoices: function () {
            const cfg = store.config;
            const effortLabel = (e) => `${e[0].toUpperCase()}${e.slice(1)}`;
            this.choices = {
                model: [{ value: '', label: 'Default model', short: 'Default model' },
                    ...cfg.models.map((m) => ({ value: m, label: modelLabel(m), short: modelLabel(m) }))],
                effort: [{ value: '', label: 'Default effort', short: 'Default effort' },
                    ...cfg.efforts.map((e) => ({
                        value: e, label: effortLabel(e), short: `${effortLabel(e)} effort`, description: EFFORT_DESCRIPTIONS[e],
                    }))],
                mode: cfg.permission_modes.map((m) => ({
                    value: m, label: MODE_LABELS[m] || m, short: MODE_LABELS[m] || m, description: MODE_DESCRIPTIONS[m],
                })),
            };
            const savedMode = remember('mode');
            this.prefs = {
                model: this._valid('model', remember('model')),
                effort: this._valid('effort', remember('effort')),
                mode: cfg.permission_modes.includes(savedMode) ? savedMode : cfg.default_permission_mode,
            };
        },

        _valid: function (name, value) {
            return this.choices[name].some((c) => c.value === value) ? value : '';
        },

        _syncPickers: function () {
            this.$selector.find('[data-picker]').each((i, el) => {
                const choice = this.choices[el.dataset.picker].find((c) => c.value === this.prefs[el.dataset.picker]);
                el.querySelector('span').textContent = choice ? choice.short : '';
            });
        },

        _onPickerToggle: function (ev) {
            const button = ev.currentTarget;
            const name = button.dataset.picker;
            const wasOpen = button.getAttribute('aria-expanded') === 'true';
            this._closePicker();
            if (wasOpen) return;
            const options = this.choices[name].map((c) => render('picker_option', {
                value: c.value, label: c.label, description: c.description || '',
                descriptionHidden: c.description ? '' : 'hidden',
                selected: String(c.value === this.prefs[name]), check: icon('check', 'size-4'),
            })).join('');
            const form = this.$selector.find('#composer-form').get(0);
            form.insertAdjacentHTML('beforeend', render('picker_menu', { title: PICKER_TITLES[name], options }));
            const menu = form.lastElementChild;
            menu.dataset.for = name;
            // Align the menu with its button, kept inside the composer.
            const left = Math.max(0, Math.min(button.offsetLeft, form.clientWidth - menu.offsetWidth));
            menu.style.left = `${left}px`;
            button.setAttribute('aria-expanded', 'true');
            (menu.querySelector('[aria-selected="true"]') || menu.querySelector('.picker-option')).focus();
        },

        _closePicker: function () {
            this.$selector.find('.picker-menu').each((i, el) => el.remove());
            this.$selector.find('[data-picker]').attr('aria-expanded', 'false');
        },

        _onPickerKeydown: function (ev) {
            const items = [...ev.currentTarget.querySelectorAll('.picker-option')];
            const index = items.indexOf(document.activeElement);
            if (ev.key === 'Escape') {
                ev.preventDefault();
                const name = ev.currentTarget.dataset.for;
                this._closePicker();
                this.$selector.find(`[data-picker="${name}"]`).get(0)?.focus();
            } else if (ev.key === 'ArrowDown' || ev.key === 'ArrowUp') {
                ev.preventDefault();
                const next = (index + (ev.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length;
                items[next].focus();
            }
        },

        _onPickerChoose: async function (ev) {
            const name = ev.currentTarget.closest('.picker-menu').dataset.for;
            const value = ev.currentTarget.dataset.value;
            this._closePicker();
            this.prefs[name] = value;
            remember(name, value);
            this._syncPickers();
            if (name === 'mode') await this._applyMode(value);
        },

        _render: function () {
            const cfg = store.config;
            this.$selector.html(render('composer_shell', {
                placeholder: cfg.read_only ? 'This instance is read-only' : 'Ask Claude to do something…',
                folderHidden: '',
                folder: basename(this.cwd),
                iconFolder: icon('folder', 'size-3.5'),
                chevron: icon('down', 'size-3 shrink-0 opacity-60'),
                iconSend: icon('send', 'size-4'),
                hint: 'Enter to send · Shift + Enter for a new line',
            }));
            this.input = this.$selector.find('#composer-input').get(0);
            this.sendButton = this.$selector.find('#composer-send').get(0);
            this._syncPickers();
            if (cfg.read_only) {
                this.input.disabled = true;
                this.sendButton.disabled = true;
            }
        },

        _setContext: function (ctx) {
            const previous = this.session?.id;
            this.mode = ctx.mode;
            this.session = ctx.session || null;
            const folderButton = this.$selector.find('#composer-folder');
            folderButton.toggleClass('hidden', this.mode !== 'new');
            if (this.mode === 'new') {
                folderButton.find('span').text(basename(this.cwd));
                folderButton.attr('title', tildePath(this.cwd));
            }
            if (this.session && this.session.id !== previous && this.session.permission_mode
                && store.config.permission_modes.includes(this.session.permission_mode)) {
                this.prefs.mode = this.session.permission_mode;
                this._syncPickers();
            }
            this._syncButton();
            if (this.session?.id !== previous && window.matchMedia('(pointer: fine)').matches) {
                this.input.focus();
            }
        },

        _busy: function () {
            return this.mode === 'session' && isBusy(store.sessions.get(this.session?.id) || this.session);
        },

        _held: function () {
            const s = store.sessions.get(this.session?.id) || this.session;
            return this.mode === 'session' && Boolean(s?.process) && !s?.run_state;
        },

        _syncButton: function () {
            if (store.config.read_only) return;
            const busy = this._busy();
            this.sendButton.innerHTML = busy ? icon('stop', 'size-4') : icon('send', 'size-4');
            this.sendButton.setAttribute('aria-label', busy ? 'Stop' : 'Send');
            this.sendButton.disabled = !busy && (this.sending || !this.input.value.trim());
            this.input.placeholder = this._held()
                ? 'Reply to continue in a copy…'
                : (this.mode === 'new' ? 'Describe the task…' : 'Reply to Claude…');
        },

        _onInput: function () {
            this.input.style.height = 'auto';
            this.input.style.height = `${Math.min(this.input.scrollHeight, window.innerHeight * 0.4)}px`;
            this._syncButton();
        },

        _onKeydown: function (ev) {
            if (ev.key === 'Enter' && !ev.shiftKey && !ev.isComposing && window.matchMedia('(pointer: fine)').matches) {
                ev.preventDefault();
                this.$selector.find('#composer-form').get(0).requestSubmit();
            } else if (ev.key === 'Escape' && this._busy()) {
                this._interrupt();
            }
        },

        _applyMode: async function (mode) {
            const s = store.sessions.get(this.session?.id);
            if (this.mode === 'session' && s?.run_state) {
                try {
                    await api(`/api/sessions/${this.session.id}/mode`, { method: 'POST', body: { mode } });
                    toast(`Permission mode: ${MODE_LABELS[mode] || mode}`, 'ok');
                } catch (err) {
                    toast(err.message, 'error');
                }
            }
        },

        _onPickFolder: function () {
            openFolderPicker(this.cwd, (path) => {
                this.cwd = path;
                remember('cwd', path);
                this._setContext({ mode: 'new' });
            });
        },

        _interrupt: async function () {
            try {
                await api(`/api/sessions/${this.session.id}/interrupt`, { method: 'POST' });
            } catch (err) {
                toast(err.message, 'error');
            }
        },

        _onSubmit: function (ev) {
            ev.preventDefault();
            if (this._busy()) {
                this._interrupt();
                return;
            }
            const prompt = this.input.value.trim();
            if (prompt) this.sendText(prompt, { fromInput: true });
        },

        /** Sends a prompt to the current (or a new) session; also used by turn-summary buttons. */
        sendText: async function (prompt, { fromInput = false } = {}) {
            if (this.sending || store.config.read_only) return;
            if (this._busy()) {
                toast('Claude is still working on this session.', 'error');
                bus.emit('composer:failed');
                return;
            }
            const body = {
                prompt,
                model: this.prefs.model,
                effort: this.prefs.effort,
                permission_mode: this.prefs.mode,
            };
            const fork = this._held();
            if (this.mode === 'new') body.cwd = this.cwd;
            else Object.assign(body, { session_id: this.session.id, fork });

            this.sending = true;
            this._syncButton();
            try {
                const result = await api('/api/send', { method: 'POST', body });
                if (fromInput) {
                    this.input.value = '';
                    this._onInput();
                }
                if (this.mode === 'new' || result.session_id !== this.session?.id) {
                    if (fork) toast('Continuing in a copy of this session.', 'ok');
                    window.location.hash = `#/s/${result.session_id}?fresh=1`;
                }
            } catch (err) {
                toast(err.message, 'error');
                bus.emit('composer:failed');
            } finally {
                this.sending = false;
                this._syncButton();
            }
        },
    });
}

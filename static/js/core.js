'use strict';

import Nedara from './lib/nedarajs/nedara.js';

// ── Templates ──────────────────────────────────────────────────────────────

const TEMPLATES = '/static/html/templates.html';
const rawKeys = {};

export async function loadTemplates(version) {
    const url = `${TEMPLATES}?v=${encodeURIComponent(version)}`;
    await Nedara.importTemplates(url);
    // Remember which keys each template injects as raw HTML ({{{key}}}).
    const text = await (await fetch(url)).text();
    const doc = new DOMParser().parseFromString(text, 'text/html');
    doc.querySelectorAll('template').forEach((tpl) => {
        rawKeys[tpl.id] = new Set([...tpl.innerHTML.matchAll(/\{\{\{\s*([\w.]+)\s*\}\}\}/g)].map((m) => m[1]));
    });
}

export function escapeHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

/**
 * Renders a NedaraJS template. Values are swapped in after NedaraJS renders
 * placeholder tokens, so its output rewriting never touches user content.
 */
export function render(id, data = {}) {
    const values = [];
    const tokens = {};
    const raw = rawKeys[id] || new Set();
    for (const [key, value] of Object.entries(data)) {
        values.push(raw.has(key) ? String(value ?? '') : escapeHtml(value));
        tokens[key] = `@@${values.length - 1}@@`;
    }
    return Nedara.renderTemplate(id, tokens).replace(/@@(\d+)@@/g, (_, i) => values[Number(i)]);
}

export function toNode(html) {
    const tpl = document.createElement('template');
    tpl.innerHTML = html.trim();
    return tpl.content.firstElementChild;
}

export { Nedara };

// ── Event bus and shared state ─────────────────────────────────────────────

const target = new EventTarget();

export const bus = {
    on(name, fn) {
        target.addEventListener(name, (ev) => fn(ev.detail));
    },
    emit(name, detail) {
        target.dispatchEvent(new CustomEvent(name, { detail }));
    },
};

export const store = {
    config: null,
    sessions: new Map(),
    currentId: null,
    socket: null,
    pane: null,
};

// ── HTTP ───────────────────────────────────────────────────────────────────

const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';

export async function api(path, { method = 'GET', body } = {}) {
    const response = await fetch(path, {
        method,
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
        body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (response.status === 401 || response.status === 503) {
        window.location.href = response.status === 401 ? '/login' : '/setup';
        throw new Error('Signed out');
    }
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
        throw new Error(data.error || `${response.status} ${response.statusText}`);
    }
    return data;
}

export function postForm(path) {
    const form = document.createElement('form');
    form.method = 'post';
    form.action = path;
    const input = document.createElement('input');
    input.type = 'hidden';
    input.name = 'csrf';
    input.value = csrf;
    form.append(input);
    document.body.append(form);
    form.submit();
}

// ── Icons (Lucide, ISC licence) ────────────────────────────────────────────

const PATHS = {
    new: '<path d="M12 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.4 2.6a1 1 0 0 1 3 3l-9 9a2 2 0 0 1-.85.5l-2.87.84a.5.5 0 0 1-.62-.62l.84-2.87a2 2 0 0 1 .5-.85z"/>',
    search: '<circle cx="11" cy="11" r="7.5"/><path d="m20.5 20.5-4.2-4.2"/>',
    close: '<path d="M18 6 6 18M6 6l12 12"/>',
    menu: '<path d="M4 7h16M4 12h16M4 17h16"/>',
    sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41"/>',
    moon: '<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/>',
    auto: '<circle cx="12" cy="12" r="9"/><path d="M12 3a9 9 0 0 1 0 18z" fill="currentColor"/>',
    logout: '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path d="m16 17 5-5-5-5M21 12H9"/>',
    shield: '<path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"/>',
    terminal: '<path d="m4 17 6-6-6-6M12 19h8"/>',
    file: '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4M16 13H8M16 17H8"/>',
    pencil: '<path d="M21.17 6.81a1 1 0 0 0-3.99-3.99L3.84 16.17a2 2 0 0 0-.5.83l-1.32 4.35a.5.5 0 0 0 .62.62l4.35-1.32a2 2 0 0 0 .83-.5z"/>',
    globe: '<circle cx="12" cy="12" r="10"/><path d="M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20M2 12h20"/>',
    sparkles: '<path d="M9.94 15.5A2 2 0 0 0 8.5 14.06l-6.14-1.58a.5.5 0 0 1 0-.96L8.5 9.94A2 2 0 0 0 9.94 8.5l1.58-6.14a.5.5 0 0 1 .96 0l1.58 6.14a2 2 0 0 0 1.44 1.44l6.14 1.58a.5.5 0 0 1 0 .96l-6.14 1.58a2 2 0 0 0-1.44 1.44l-1.58 6.14a.5.5 0 0 1-.96 0z"/>',
    tasks: '<path d="m3 17 2 2 4-4M3 7l2 2 4-4M13 6h8M13 12h8M13 18h8"/>',
    folder: '<path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/>',
    branch: '<circle cx="6" cy="6" r="2.5"/><circle cx="6" cy="18" r="2.5"/><circle cx="18" cy="8" r="2.5"/><path d="M6 8.5v7M18 10.5a6 6 0 0 1-6 6H8.5"/>',
    diff: '<circle cx="18" cy="18" r="3"/><circle cx="6" cy="6" r="3"/><path d="M13 6h3a2 2 0 0 1 2 2v7M11 18H8a2 2 0 0 1-2-2V9"/>',
    info: '<circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/>',
    send: '<path d="M12 19V5M5.5 11.5 12 5l6.5 6.5"/>',
    stop: '<rect x="7" y="7" width="10" height="10" rx="2" fill="currentColor" stroke="none"/>',
    check: '<path d="M20 6 9 17l-5-5"/>',
    done: '<circle cx="12" cy="12" r="9"/><path d="m8.5 12 2.5 2.5 4.5-5"/>',
    pending: '<circle cx="12" cy="12" r="9"/>',
    current: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="3.5" fill="currentColor"/>',
    alert: '<circle cx="12" cy="12" r="10"/><path d="M12 8v4M12 16h.01"/>',
    error: '<circle cx="12" cy="12" r="10"/><path d="m15 9-6 6M9 9l6 6"/>',
    down: '<path d="m6 9 6 6 6-6"/>',
    back: '<path d="m15 18-6-6 6-6"/>',
    refresh: '<path d="M21 12a9 9 0 0 1-15.5 6.2L3 16M3 12a9 9 0 0 1 15.5-6.2L21 8M21 3v5h-5M3 21v-5h5"/>',
    bulb: '<path d="M15 14c.2-1 .7-1.7 1.5-2.5 1-.9 1.5-2.2 1.5-3.5A6 6 0 0 0 6 8c0 1 .2 2.2 1.5 3.5.7.7 1.3 1.5 1.5 2.5M9 18h6M10 22h4"/>',
    paste: '<rect x="8" y="2" width="8" height="4" rx="1"/><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/>',
    desktop: '<rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8M12 17v4"/>',
    lock: '<rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
    tool: '<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94z"/>',
    eye: '<path d="M2.06 12.35a1 1 0 0 1 0-.7 10.75 10.75 0 0 1 19.88 0 1 1 0 0 1 0 .7 10.75 10.75 0 0 1-19.88 0"/><circle cx="12" cy="12" r="3"/>',
    chat: '<path d="M7.9 20A9 9 0 1 0 4 16.1L2 22Z"/>',
    fork: '<circle cx="12" cy="18" r="3"/><circle cx="6" cy="6" r="3"/><circle cx="18" cy="6" r="3"/><path d="M18 9v2a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V9M12 12v3"/>',
    plug: '<path d="M12 22v-5M9 8V2M15 8V2M18 8v5a4 4 0 0 1-4 4h-4a4 4 0 0 1-4-4V8Z"/>',
    spinner: '<path d="M21 12a9 9 0 1 1-6.22-8.56"/>',
    github: '<path d="M15 22v-4a4.8 4.8 0 0 0-1-3.5c3 0 6-2 6-5.5.08-1.25-.27-2.48-1-3.5.28-1.15.28-2.35 0-3.5 0 0-1 0-3 1.5-2.64-.5-5.36-.5-8 0C6 2 5 2 5 2c-.3 1.15-.3 2.35 0 3.5A5.4 5.4 0 0 0 4 9c0 3.5 3 5.5 6 5.5-.39.49-.68 1.05-.85 1.65-.17.6-.22 1.23-.15 1.85v4"/><path d="M9 18c-4.51 2-5-2-7-2"/>',
};

const NEDARA_MARK = '<path d="M0 0A48.4 100 0 0 1 48.4 100A48.4 100 0 0 1 0 0Z"/><rect x="51" y="0" width="49" height="48.1"/><rect x="51" y="51.9" width="49" height="48.1"/>';

/** The Nedara Project monogram, in the current text colour. */
export function nedaraMark(cls = 'size-3') {
    return `<svg class="${cls}" viewBox="0 0 100 100" fill="currentColor" aria-hidden="true">${NEDARA_MARK}</svg>`;
}

export function icon(name, cls = 'size-4') {
    const extra = name === 'spinner' ? ' animate-spin' : '';
    return `<svg class="${cls}${extra}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${PATHS[name] || PATHS.tool}</svg>`;
}

// ── Helpers ────────────────────────────────────────────────────────────────

export function basename(path) {
    const parts = String(path || '').split(/[\\/]/).filter(Boolean);
    return parts.length ? parts[parts.length - 1] : path || '';
}

export function relativeTime(value) {
    const date = typeof value === 'number' ? new Date(value * 1000) : new Date(value);
    const seconds = Math.round((Date.now() - date.getTime()) / 1000);
    if (Number.isNaN(seconds)) return '';
    if (seconds < 45) return 'now';
    if (seconds < 3600) return `${Math.round(seconds / 60)} min`;
    if (seconds < 86400) return `${Math.round(seconds / 3600)} h`;
    if (seconds < 7 * 86400) return `${Math.round(seconds / 86400)} d`;
    return date.toLocaleDateString(undefined, { day: 'numeric', month: 'short' });
}

export function formatBytes(size) {
    if (size < 1024) return `${size} B`;
    if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
    return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

export function toast(text, kind = 'info') {
    const root = document.getElementById('toasts');
    const styles = { info: ['info', 'text-info'], error: ['error', 'text-bad'], ok: ['done', 'text-ok'] };
    const [name, cls] = styles[kind] || styles.info;
    const node = toNode(render('toast', { text, icon: icon(name), cls }));
    root.append(node);
    setTimeout(() => {
        node.style.transition = 'opacity .3s';
        node.style.opacity = '0';
        setTimeout(() => node.remove(), 300);
    }, kind === 'error' ? 6000 : 3000);
}

export function isBusy(session) {
    return session?.run_state === 'busy' || session?.run_state === 'starting' || session?.process?.status === 'busy';
}

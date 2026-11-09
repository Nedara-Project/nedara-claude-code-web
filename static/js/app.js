'use strict';

import { io } from './lib/socket.io/socket.io.esm.min.js';
import { api, bus, loadTemplates, store, toast } from './core.js';
import { createSidebar } from './widgets/sidebar.js';
import { createConversation } from './widgets/conversation.js';
import { createComposer } from './widgets/composer.js';
import { createPane } from './widgets/pane.js';

function parseRoute() {
    const hash = window.location.hash.replace(/^#/, '');
    const match = /^\/s\/([0-9a-f-]{36})(\?fresh=1)?$/.exec(hash);
    if (match) return { name: 'session', id: match[1], fresh: Boolean(match[2]) };
    return { name: 'new' };
}

function applySessions(list) {
    store.sessions = new Map(list.map((s) => [s.id, s]));
    bus.emit('sessions:changed');
}

document.addEventListener('DOMContentLoaded', async () => {
    const version = document.querySelector('meta[name="app-version"]').content;
    const [state] = await Promise.all([api('/api/state'), loadTemplates(version)]);
    store.config = state;
    applySessions(state.sessions);

    store.socket = io({ transports: ['websocket'], withCredentials: true });
    store.socket.on('sessions_patch', (patch) => {
        for (const session of patch.upsert) {
            store.sessions.set(session.id, { ...store.sessions.get(session.id), ...session });
        }
        patch.remove.forEach((id) => store.sessions.delete(id));
        bus.emit('sessions:changed');
    });
    let connectedOnce = false;
    store.socket.on('connect', async () => {
        // After a reconnection the server may have restarted: resync the list.
        if (connectedOnce) applySessions((await api('/api/state')).sessions);
        connectedOnce = true;
    });
    store.socket.on('connect_error', () => {
        toast('Connection lost. Retrying…', 'error');
        // A refused connection may mean the session expired: api() redirects then.
        api('/api/state').catch(() => {});
    });
    store.socket.on('disconnect', (reason) => {
        if (reason === 'io server disconnect') window.location.href = '/login';
    });

    createSidebar();
    createConversation();
    createComposer();
    createPane();

    window.addEventListener('hashchange', () => bus.emit('route', parseRoute()));
    bus.emit('route', parseRoute());

    if (!state.cli_found) toast('The claude CLI was not found on the server: read-only mode.', 'error');
});

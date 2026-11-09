'use strict';

import { Nedara, api, icon, render, tildePath, toast } from '../core.js';

/** Modal folder browser limited to the allowed roots; calls onChoose(path). */
export function openFolderPicker(startPath, onChoose) {
    const root = document.getElementById('dialog-root');
    const widget = Nedara.createWidget({
        selector: '#dialog-root',
        events: {},

        start: function () {
            this._keydown = (ev) => ev.key === 'Escape' && this._onDismiss();
            document.addEventListener('keydown', this._keydown);
            this._load(startPath);
        },

        _load: async function (path) {
            try {
                const data = await api(`/api/folders?path=${encodeURIComponent(path || '')}`);
                this.$selector.html(render('folder_dialog', {
                    path: data.path,
                    displayPath: tildePath(data.path),
                    parent: data.parent || '',
                    upHidden: data.parent ? '' : 'invisible',
                    iconClose: icon('close'),
                    iconBack: icon('back'),
                    folders: data.folders.length
                        ? data.folders.map((f) => render('folder_row', { ...f, icon: icon('folder') })).join('')
                        : '<li class="px-3 py-6 text-center text-[13px] text-ink-3">No sub-folder.</li>',
                }));
            } catch (err) {
                toast(err.message, 'error');
                if (!this.$selector.find('[data-dialog]').length) this._onDismiss();
            }
        },

        _onFolderOpen: function (ev) {
            this._load(ev.currentTarget.dataset.path);
        },

        _onFolderChoose: function (ev) {
            onChoose(ev.currentTarget.dataset.path);
            this._onDismiss();
        },

        _onDismiss: function () {
            document.removeEventListener('keydown', this._keydown);
            this.destroy();
            root.innerHTML = '';
        },
    });
    return widget;
}

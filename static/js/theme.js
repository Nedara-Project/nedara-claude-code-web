'use strict';

// Loaded synchronously in <head> so the page never flashes the wrong theme.
(function () {
    let theme = 'auto';
    try {
        theme = localStorage.getItem('nccw-theme') || 'auto';
    } catch (e) { /* storage may be blocked */ }
    const dark = theme === 'dark' || (theme === 'auto' && window.matchMedia('(prefers-color-scheme: dark)').matches);
    document.documentElement.classList.toggle('dark', dark);
})();

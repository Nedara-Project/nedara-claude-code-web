// Copies the third-party browser files the app serves itself (no CDN, strict CSP).
import { copyFileSync, mkdirSync } from 'node:fs';

const files = [
    ['node_modules/socket.io-client/dist/socket.io.esm.min.js', 'static/js/lib/socket.io/socket.io.esm.min.js'],
    ['node_modules/socket.io-client/LICENSE', 'static/js/lib/socket.io/LICENSE'],
    ['node_modules/@fontsource-variable/inter/files/inter-latin-wght-normal.woff2', 'static/fonts/inter-latin.woff2'],
    ['node_modules/@fontsource-variable/inter/files/inter-latin-ext-wght-normal.woff2', 'static/fonts/inter-latin-ext.woff2'],
    ['node_modules/@fontsource-variable/inter/LICENSE', 'static/fonts/LICENSE-Inter.txt'],
];

for (const [from, to] of files) {
    mkdirSync(to.substring(0, to.lastIndexOf('/')), { recursive: true });
    copyFileSync(from, to);
    console.log(`${from} -> ${to}`);
}

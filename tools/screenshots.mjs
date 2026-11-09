// Captures the README screenshots from a running demo instance (see tools/make_demo.py),
// driving headless Chrome over the DevTools protocol. No dependency beyond Node 22.
//
//   DEMO_PASSWORD=... node tools/screenshots.mjs http://127.0.0.1:5072 docs/screenshots
import { spawn } from 'node:child_process';
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const [base = 'http://127.0.0.1:5072', outDir = 'docs/screenshots'] = process.argv.slice(2);
const password = process.env.DEMO_PASSWORD;
const chromePath = process.env.CHROME || 'google-chrome';
const port = 9333;
if (!password) throw new Error('Set DEMO_PASSWORD.');

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const profile = mkdtempSync(join(tmpdir(), 'nccw-shots-'));
const chrome = spawn(chromePath, [
    '--headless=new', `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`,
    '--no-first-run', '--no-default-browser-check', '--hide-scrollbars', '--font-render-hinting=none', 'about:blank',
], { stdio: 'ignore' });

async function connect() {
    for (let i = 0; i < 50; i++) {
        try {
            const page = await (await fetch(`http://127.0.0.1:${port}/json/new?${base}/login`, { method: 'PUT' })).json();
            if (page.webSocketDebuggerUrl) return page.webSocketDebuggerUrl;
        } catch { /* Chrome is still starting */ }
        await sleep(200);
    }
    throw new Error('Chrome did not start.');
}

const ws = new WebSocket(await connect());
await new Promise((resolve) => ws.addEventListener('open', resolve, { once: true }));
let nextId = 0;
const pending = new Map();
ws.addEventListener('message', (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.id && pending.has(msg.id)) {
        const { resolve, reject } = pending.get(msg.id);
        pending.delete(msg.id);
        msg.error ? reject(new Error(msg.error.message)) : resolve(msg.result);
    } else if (msg.method === 'Page.loadEventFired') {
        loaded?.();
    }
});
let loaded = null;
const waitLoad = () => new Promise((resolve) => { loaded = resolve; });
const send = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++nextId;
    pending.set(id, { resolve, reject: (err) => reject(new Error(`${method}: ${err.message}`)) });
    ws.send(JSON.stringify({ id, method, params }));
});
const navigate = async (url) => {
    const done = waitLoad();
    await send('Page.navigate', { url });
    await Promise.race([done, sleep(8000)]);
};
const evaluate = async (expression) => {
    const { result, exceptionDetails } = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
    if (exceptionDetails) throw new Error(exceptionDetails.exception?.description || exceptionDetails.text);
    return result.value;
};

async function viewport({ width, height, scale = 2, mobile = false }) {
    await send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: scale, mobile });
}

async function open(url, theme) {
    await send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-color-scheme', value: theme }] });
    await navigate(`${base}/login`);
    await evaluate(`localStorage.setItem('nccw-theme', ${JSON.stringify(theme)})`);
    // A query string forces a full load: a hash-only change would keep the old theme.
    await navigate(url.replace('/#', `/?t=${Date.now()}#`));
    await sleep(2000);
}

async function shot(name) {
    const { data } = await send('Page.captureScreenshot', { format: 'png' });
    writeFileSync(join(outDir, `${name}.png`), Buffer.from(data, 'base64'));
    console.log(`${name}.png`);
}

try {
    mkdirSync(outDir, { recursive: true });
    await send('Page.enable');
    await send('Runtime.enable');
    await viewport({ width: 1440, height: 900 });

    // Sign-in page, then sign in.
    await open(`${base}/login`, 'light');
    await shot('sign-in');
    const signedIn = waitLoad();
    await evaluate(`(() => { const f = document.querySelector('form'); f.password.value = ${JSON.stringify(password)}; f.submit(); })()`);
    await Promise.race([signedIn, sleep(8000)]);
    await sleep(500);

    const sessions = await evaluate(`fetch('/api/state').then((r) => r.json()).then((s) => s.sessions)`);
    const byTitle = (title) => sessions.find((s) => s.title === title).id;
    const checkout = byTitle('Add Stripe checkout to the cart');
    const flaky = byTitle('Fix flaky timezone test');

    await open(`${base}/#/s/${checkout}`, 'dark');
    await shot('conversation-dark');

    await open(`${base}/#/s/${checkout}`, 'light');
    await evaluate(`document.querySelector('[data-pane="changes"]').click()`);
    await sleep(1500);
    await shot('changes-light');

    await open(`${base}/#/s/${flaky}`, 'dark');
    // A permission request as the server would push it while Claude runs.
    await evaluate(`import('/static/js/core.js').then((m) => {
        m.store.socket.listeners('permission_request').forEach((fn) => fn({
            session_id: ${JSON.stringify(flaky)}, request_id: 'demo', tool: 'Bash',
            title: 'Claude wants to run a command',
            description: 'Run the forecast tests with a fake clock',
            can_remember: true,
            input: { command: 'FAKETIME="2026-03-29 00:59:59" pytest -q tests/test_forecast.py -k rollover' },
        }));
    })`);
    await sleep(1200);
    await shot('permission-dark');

    await viewport({ width: 390, height: 844, scale: 3, mobile: true });
    await open(`${base}/#/s/${checkout}`, 'light');
    await shot('mobile-light');
} finally {
    ws.close();
    chrome.kill();
    await sleep(300);
    rmSync(profile, { recursive: true, force: true });
}

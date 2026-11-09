# Nedara Claude Code Web

<p align="center">
 <img src="static/favicon.svg" width="96" height="96" alt="">
</p>

Your local Claude Code sessions, in the browser.

Nedara Claude Code Web is a small self-hosted server that runs next to Claude Code on your
own machine. It shows every session you have, including those of Claude Desktop, and
mirrors them live. It also lets you continue them, or start new ones, from any browser:
the laptop at home, a tablet, your phone.

Nothing leaves your machine except what Claude Code already sends: the app reads the
transcripts Claude Code writes to `~/.claude` and drives the `claude` CLI you already
have through the official [Claude Agent SDK](https://github.com/anthropics/claude-agent-sdk-python).

Built with [NedaraJS](https://github.com/Nedara-Project/nedarajs), Tailwind CSS, Flask and
Socket.IO.

## Features

- **All your sessions**: Claude Desktop, CLI and web sessions in one sidebar, with
  Desktop's titles and archive flag. Grouped by day or by project, and searchable.
- **Live mirroring**: a session running in Claude Desktop or a terminal updates in the
  browser as it happens.
- **Continue or start sessions** with the model, effort and permission mode of your choice.
  Answers stream in as Claude types.
- **Permission prompts in the browser**: allow once, allow for the session, or deny.
  Claude's questions (`AskUserQuestion`) and plan approvals (`ExitPlanMode`) are answered
  the same way.
- **Desktop-like transcript**: Markdown, collapsible tool calls with their output, edit
  diffs, the pinned task list (`update_plan`), turn summaries, pasted text and thinking.
- **Side pane**: the git changes of the session folder, a read-only file browser and
  session details.
- **No clash with Claude Desktop**: a session still open in another Claude process is
  never written to. Replying to it continues it in a copy (a fork), with its full history.
- **Light and dark themes**, a phone layout, and keyboard shortcuts (`N` new session,
  `/` search, `Esc` stop).
- **Guided first start**: a setup page protected by a one-time code from the server log.
- **Security first**: master password plus optional TOTP, strict Content-Security-Policy,
  CSRF protection, loopback-only by default, an audit log. See [SECURITY.md](SECURITY.md).

## How it works

```
 browser ──HTTPS/WebSocket──▶ Flask + Socket.IO ──reads──▶ ~/.claude/projects/*/<session>.jsonl
                                     │                     ~/.claude/sessions/*.json (live processes)
                                     │                     Claude Desktop metadata (titles)
                                     └──Claude Agent SDK──▶ claude CLI ──▶ Anthropic API
```

The transcript file is the single source of truth. Whoever writes to it (Claude Desktop,
a terminal, or this app through the Agent SDK), the server tails it and pushes the new
events to the browsers watching that session. The SDK connection only adds what the file
cannot carry: streamed text, run state and permission requests.

## Requirements

- Linux or macOS (Windows untested), Python 3.11+.
- Claude Code installed and signed in for the user running the app. If only Claude
  Desktop is signed in, create a long-lived token with `claude setup-token` and point
  `oauth_token_file` at it (see below).
- `git` for the Changes pane.

## Installation

```bash
git clone --recurse-submodules https://github.com/Nedara-Project/nedara-claude-code-web.git
cd nedara-claude-code-web
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp config.ini.example config.ini
.venv/bin/python app.py
```

On the first start the server prints a one-time **setup code**. Open
http://127.0.0.1:5070: the setup page asks for that code, your master password, and
optionally sets up two-factor authentication (QR code) and a Claude token. The page is
gone once this is done.

Prefer the terminal? `.venv/bin/python app.py set-password` does the same before the
first start.

The front-end assets (`static/css/app.css`, the Socket.IO client, the Inter font) are
committed, so Node.js is only needed to change the styles (see Development).

### Two-factor authentication

```bash
.venv/bin/python app.py totp enable
```

Scan the QR code with any authenticator app (Aegis, 1Password, Google Authenticator…)
and type a code to confirm. Every browser is signed out when this changes.
`totp disable` turns it off, and `revoke-sessions` signs every browser out.

### Signing in the claude CLI

The app runs the `claude` CLI found on `PATH`, or the one bundled with the Agent SDK.
That CLI needs a valid login of its own:

- run `claude` once in a terminal and sign in, or
- run `claude setup-token`, store the token in a file readable by you only
  (`chmod 600`), and set `oauth_token_file` in `config.ini`.

If the CLI is not signed in, Claude answers "Failed to authenticate" in the transcript.

### Run it as a user service (systemd)

```ini
# ~/.config/systemd/user/nedara-claude-code-web.service
[Unit]
Description=Nedara Claude Code Web

[Service]
WorkingDirectory=%h/nedara-claude-code-web
ExecStart=%h/nedara-claude-code-web/.venv/bin/python app.py
Restart=on-failure

[Install]
WantedBy=default.target
```

```bash
systemctl --user enable --now nedara-claude-code-web
```

Use `loginctl enable-linger` if it should keep running after you log out.

## Reaching it from another device

The app listens on `127.0.0.1` only and refuses to start on another address unless
`allow_remote = 1` is set. Pick one of these:

- **A VPN** (WireGuard, Tailscale…): set `host` to the VPN address and `allow_remote = 1`.
- **A TLS reverse proxy** on the same machine. With Caddy:

  ```
  claude.example.com {
      reverse_proxy 127.0.0.1:5070
  }
  ```

  With nginx, forward the WebSocket upgrade as well:

  ```nginx
  location / {
      proxy_pass http://127.0.0.1:5070;
      proxy_http_version 1.1;
      proxy_set_header Upgrade $http_upgrade;
      proxy_set_header Connection "upgrade";
      proxy_set_header Host $host;
      proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
      proxy_set_header X-Forwarded-Proto $scheme;
  }
  ```

  Then set `behind_proxy = 1` (secure cookies and the real client address for the
  login lockout).

Never publish the plain HTTP port on the internet: whoever signs in can make Claude run
commands on your machine.

## Docker / Podman

```bash
cp config.ini.example config.ini
cat > .env <<EOF
UID=$(id -u)
GID=$(id -g)
HOME=$HOME
PROJECTS=$HOME/dev
EOF
docker compose build
docker compose up -d
docker compose logs nccw   # shows the one-time setup code
```

Then open http://127.0.0.1:5070 to finish the setup.

Podman works the same with `podman compose` (uncomment `userns_mode: keep-id` for rootless
Podman). With rootless Docker, set `UID=0` and `GID=0`: root inside the container is your
own user on the host.

The container mounts `~/.claude`, Claude Desktop's folder (read-only) and your projects
folder at the same absolute paths as on the host, because sessions record their folder.
It shares the host's process namespace (`pid: host`) so it can see which sessions Claude
Desktop holds.

Keep in mind that **Claude's tools then run inside the container**: git, the shell, Python,
SSH keys and VPN routes are the container's, not your machine's. For the exact same
behaviour as Claude Desktop, run the app directly on the host.

## Configuration

Every key is documented in [config.ini.example](config.ini.example). The main ones:

| Section | Key | Default | Purpose |
|---|---|---|---|
| server | `host`, `port` | `127.0.0.1`, `5070` | Where the app listens |
| server | `allow_remote` | `0` | Required for a non-loopback `host` |
| server | `behind_proxy` | `0` | Trust one reverse proxy hop, secure cookies |
| claude | `allowed_roots` | home folder | Folders whose sessions are shown and usable |
| claude | `oauth_token_file` | | Token from `claude setup-token` |
| claude | `show_automated` | `0` | Also list Agent SDK sessions run by scripts and bots |
| agent | `read_only` | `0` | Mirror sessions without ever sending anything |
| agent | `allowed_permission_modes` | `default, acceptEdits, plan, auto` | Modes offered in the composer |
| files | `file_viewer`, `denylist` | `1` | Read-only file browser and the files it never shows |

## Good to know

- Sessions continued or started here run through the Agent SDK. They do not show up in
  Claude Desktop's sidebar, which lists only the sessions Desktop created itself. They do
  show up in `claude --resume`.
- Tools that only exist in Claude Desktop (its task list, panes, sessions…) are not
  available to sessions run from here. Their past calls still render.
- Claude Desktop keeps a process open for each of its recent sessions, so most Desktop
  sessions are mirrored and continued in a copy rather than in place.

## Development

```bash
npm install
npm run watch     # rebuild static/css/app.css on change
npm run vendor    # refresh the Socket.IO client and the Inter font from node_modules
```

The UI is a set of NedaraJS widgets (`static/js/widgets/`) rendering the templates in
`static/html/templates.html`. Templates use escaped `{{vars}}`, trusted `{{{html}}}` built
in JS (or sanitised on the server) and loops, never `{{#if}}`: its `Function()` evaluation
is blocked by the Content-Security-Policy.

## License

[MIT](LICENSE) — a [Nedara Project](https://nedara.org) ([GitHub](https://github.com/Nedara-Project)).

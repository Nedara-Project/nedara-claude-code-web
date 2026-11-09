# Security

Nedara Claude Code Web gives a browser the power to run Claude Code on your machine, and
Claude Code can run shell commands, edit files and reach your network. Treat access to the
app like SSH access to your account.

## Threat model

- **In scope**: someone on the network who can reach the port, a malicious web page open
  in the same browser (CSRF, cross-origin WebSocket), hostile content inside transcripts
  (web pages, tool output, file contents rendered in the UI), password guessing, a stolen
  session cookie.
- **Out of scope**: someone who already has a shell as your user, or who controls the
  machine. They can read `~/.claude` directly.

## Controls

**Access**
- A master password is mandatory (scrypt hash, at least 12 characters). Until one is set,
  the app serves nothing but the setup page, and that page requires a one-time code
  printed in the server log: someone who can merely reach the port cannot claim the
  instance. The code is rate-limited like the sign-in and dies once setup is done.
  `python3 app.py set-password` does the same from the terminal.
- Optional TOTP second factor.
- Per-client lockout after repeated failures (5 in 15 minutes by default) and a fixed delay
  on every failure.
- Session cookies are `HttpOnly`, `SameSite=Strict`, `Secure` behind a proxy, and expire
  after 12 hours. Changing the password, toggling TOTP or `revoke-sessions` invalidates
  every issued cookie (auth epoch).

**Network exposure**
- Binds to `127.0.0.1` by default and refuses any other address without `allow_remote = 1`.
- The WebSocket only accepts same-origin connections, plus the origins listed in
  `allowed_origins`, and requires an authenticated session.

**Browser**
- Strict Content-Security-Policy: scripts, styles, fonts and connections from the app's own
  origin only. No inline script, no `eval`, no CDN.
- `X-Frame-Options: DENY` / `frame-ancestors 'none'`, `nosniff`, `no-referrer`,
  `Cross-Origin-Opener-Policy`, `no-store` on pages and API answers.
- Every state-changing request needs a CSRF token.
- Transcript text is escaped. Markdown is rendered on the server with raw HTML disabled,
  then cleaned by an allow-list sanitizer (nh3). Only `http`, `https` and `mailto` links
  are kept.

**Claude Code**
- Folders are limited to `allowed_roots`, with symlinks resolved.
- Permission modes are limited to `allowed_permission_modes`; `bypassPermissions` is off by
  default.
- "Allow for this session" lasts for the running session only. Nothing is written to your
  Claude Code settings files.
- Unanswered permission requests are denied after `permission_timeout`.
- A session held by another Claude process (Desktop, a terminal) is never written to. It
  is continued in a fork instead.
- `read_only = 1` turns every write path off.

**Files**
- The file browser is read-only, confined to the session folder, size-capped, and never
  shows files matching the deny list (`.env`, keys, certificates, SSH keys, `.netrc`,
  `.pgpass`, credentials…).
- `git` runs without a shell, with a timeout and an output cap.

**Traces**
- `data/audit.log` records the setup, sign-ins (successful and failed), prompts sent (length only, not
  their content), permission decisions, interrupts and security changes.
- `data/` is created with `0700` permissions, its secret files (password hash, TOTP secret,
  cookie key, Claude token) with `0600`.

## Recommendations

- Reach the app through a VPN or an HTTPS reverse proxy, never over plain HTTP from another
  machine.
- Turn on TOTP as soon as the app is reachable from anywhere other than localhost.
- Keep `allowed_roots` as narrow as your work allows.
- Run it as your own user, never as root.

## Reporting a vulnerability

Please open a private security advisory on the GitHub repository rather than a public
issue.

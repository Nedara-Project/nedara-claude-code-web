# Third-party notices

Nedara Claude Code Web is released under the [MIT License](LICENSE). It includes or relies on
the third-party software listed below, each under its own licence.

## Trademarks and affiliation

Nedara Claude Code Web is an independent project of the Nedara Project. It is **not
affiliated with, endorsed by or sponsored by Anthropic, PBC**. Claude, Claude Code and
Claude Desktop are trademarks of Anthropic, PBC; they are used here only to describe the
software this project works with.

## Included in this repository

| Component | Where | Licence |
|---|---|---|
| [NedaraJS](https://github.com/Nedara-Project/nedarajs) | `static/js/lib/nedarajs/` (git submodule) | MIT, see its `LICENSE` |
| [Socket.IO client](https://github.com/socketio/socket.io) | `static/js/lib/socket.io/` | MIT, see `static/js/lib/socket.io/LICENSE` |
| [Inter](https://github.com/rsms/inter) font | `static/fonts/` | SIL Open Font License 1.1, see `static/fonts/LICENSE-Inter.txt` |
| [Lucide](https://lucide.dev) icons (SVG paths) | `static/js/core.js` | ISC, with some icons derived from Feather under MIT; full text below |
| [Tailwind CSS](https://tailwindcss.com) output | `static/css/app.css` | MIT (build tool only; generated CSS) |

### Lucide

```
ISC License

Copyright (c) 2026 Lucide Icons and Contributors

Permission to use, copy, modify, and/or distribute this software for any
purpose with or without fee is hereby granted, provided that the above
copyright notice and this permission notice appear in all copies.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES
WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF
MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR
ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN
ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF
OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.
```

Some of the Lucide icons used here (check, info, lock, moon, search, terminal, x…) are
derived from the Feather project:

```
The MIT License (MIT) (for the icons derived from Feather)

Copyright (c) 2013-present Cole Bemis

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Installed at setup time (not included in this repository)

These Python packages are installed by `pip` from `requirements.txt`:

| Package | Licence |
|---|---|
| claude-agent-sdk | MIT (© Anthropic, PBC) |
| Flask, Werkzeug | BSD-3-Clause |
| Flask-SocketIO, python-socketio, python-engineio, simple-websocket | MIT |
| markdown-it-py | MIT |
| nh3 | MIT |
| PyOTP | MIT |
| qrcode | BSD |

## Claude Code CLI

The app drives the `claude` command-line tool: the one installed on your machine, or the
one bundled inside the `claude-agent-sdk` package. Claude Code itself is Anthropic's
software, provided under Anthropic's own terms, not under the licences above. This
repository does not contain it.

A container image built from the `Dockerfile` includes the CLI through `claude-agent-sdk`.
Building it for your own use is fine; before **publishing** such an image (Docker Hub,
GHCR…), check Anthropic's terms for redistributing Claude Code, or install the CLI when the
container starts instead.

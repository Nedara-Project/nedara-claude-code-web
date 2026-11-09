#!/usr/bin/env python3
"""Builds a self-contained, fictional demo workspace for screenshots.

    python3 tools/make_demo.py /path/to/demo

creates /path/to/demo/home (a fake HOME with ~/.claude transcripts, Claude Desktop
metadata and three small projects, one a git repository with uncommitted changes)
and /path/to/demo/config.ini. Run the app against it with:

    HOME=/path/to/demo/home NCCW_CONFIG=/path/to/demo/config.ini python3 app.py

Nothing here comes from a real session: every project, prompt and answer is made up.
"""

import json
import os
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

NOW = datetime.now(timezone.utc)
MODEL = 'claude-opus-5-5'


def iso(minutes_ago):
    return (NOW - timedelta(minutes=minutes_ago)).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


class Transcript:
    def __init__(self, cwd, title, minutes_ago, branch='main'):
        self.id = str(uuid.uuid4())
        self.cwd = str(cwd)
        self.title = title
        self.branch = branch
        self.clock = minutes_ago
        self.records = []
        self.parent = None

    def _base(self, kind):
        self.clock = max(self.clock - 0.4, 0)
        rec = {
            'parentUuid': self.parent, 'isSidechain': False, 'type': kind, 'uuid': str(uuid.uuid4()),
            'timestamp': iso(self.clock), 'cwd': self.cwd, 'sessionId': self.id,
            'entrypoint': 'claude-desktop', 'gitBranch': self.branch, 'version': '2.1.308',
        }
        self.parent = rec['uuid']
        return rec

    def user(self, text):
        rec = self._base('user')
        rec['message'] = {'role': 'user', 'content': text}
        self.records.append(rec)

    def text(self, markdown):
        rec = self._base('assistant')
        rec['message'] = {'model': MODEL, 'role': 'assistant', 'content': [{'type': 'text', 'text': markdown}]}
        self.records.append(rec)

    def tool(self, name, tool_input, result=None, error=False):
        tool_id = f'toolu_{uuid.uuid4().hex[:24]}'
        rec = self._base('assistant')
        rec['message'] = {'model': MODEL, 'role': 'assistant', 'content': [
            {'type': 'tool_use', 'id': tool_id, 'name': name, 'input': tool_input},
        ]}
        self.records.append(rec)
        if result is not None:
            res = self._base('user')
            res['message'] = {'role': 'user', 'content': [
                {'type': 'tool_result', 'tool_use_id': tool_id, 'content': result, 'is_error': error},
            ]}
            self.records.append(res)

    def write(self, claude_dir):
        folder = claude_dir / 'projects' / self.cwd.replace('/', '-').replace('.', '-')
        folder.mkdir(parents=True, exist_ok=True)
        lines = [json.dumps({'type': 'custom-title', 'customTitle': self.title, 'sessionId': self.id})]
        lines += [json.dumps(r) for r in self.records]
        path = folder / f'{self.id}.jsonl'
        path.write_text('\n'.join(lines) + '\n')
        # The sidebar sorts by file time: date the file at its last message.
        last = (NOW - timedelta(minutes=self.clock)).timestamp()
        os.utime(path, (last, last))
        return path


def git(repo, *args):
    subprocess.run(
        ['git', '-C', str(repo), '-c', 'user.name=Demo', '-c', 'user.email=demo@example.com', *args],
        check=True, capture_output=True,
    )


def make_projects(home):
    projects = home / 'projects'
    shop = projects / 'acme-shop'
    (shop / 'src' / 'shop').mkdir(parents=True)
    (shop / 'templates').mkdir()
    (shop / 'tests').mkdir()
    (shop / 'README.md').write_text('# Acme Shop\n\nA small demo storefront.\n')
    (shop / 'src' / 'shop' / 'cart.py').write_text(
        'def cart_total(items):\n'
        '    """Sum of the cart lines, in cents."""\n'
        '    return sum(item.price * item.quantity for item in items)\n'
    )
    (shop / 'src' / 'shop' / 'checkout.py').write_text(
        'from flask import Blueprint\n\n'
        "bp = Blueprint('checkout', __name__)\n"
    )
    (shop / 'templates' / 'cart.html').write_text(
        '<section class="cart">\n'
        '  {% for item in items %}{{ item.name }}{% endfor %}\n'
        '  <p class="total">{{ total }}</p>\n'
        '</section>\n'
    )
    (shop / 'tests' / 'test_cart.py').write_text('def test_total():\n    assert True\n')
    git(shop, 'init', '-q', '-b', 'main')
    git(shop, 'add', '-A')
    git(shop, 'commit', '-q', '-m', 'Initial storefront')
    # Uncommitted work, for the Changes pane.
    (shop / 'src' / 'shop' / 'checkout.py').write_text(
        'import os\n\n'
        'import stripe\n'
        'from flask import Blueprint, redirect, url_for\n\n'
        'from .cart import cart_total, current_cart\n\n'
        "bp = Blueprint('checkout', __name__)\n"
        "stripe.api_key = os.environ['STRIPE_SECRET_KEY']\n\n\n"
        "@bp.post('/checkout')\n"
        'def create_checkout():\n'
        '    cart = current_cart()\n'
        '    session = stripe.checkout.Session.create(\n'
        "        mode='payment',\n"
        "        line_items=[item.as_stripe_line() for item in cart.items],\n"
        "        success_url=url_for('orders.thanks', _external=True),\n"
        "        cancel_url=url_for('cart.show', _external=True),\n"
        '    )\n'
        '    return redirect(session.url, code=303)\n'
    )
    (shop / 'templates' / 'cart.html').write_text(
        '<section class="cart">\n'
        '  {% for item in items %}{{ item.name }}{% endfor %}\n'
        '  <p class="total">{{ total }}</p>\n'
        '  <form method="post" action="{{ url_for(\'checkout.create_checkout\') }}">\n'
        '    <button class="btn-primary">Checkout</button>\n'
        '  </form>\n'
        '</section>\n'
    )
    (shop / 'tests' / 'test_checkout.py').write_text(
        'def test_checkout_redirects(client, stripe_mock):\n'
        "    response = client.post('/checkout')\n"
        '    assert response.status_code == 303\n'
    )
    for name in ('weather-api', 'blog'):
        (projects / name).mkdir(parents=True)
        (projects / name / 'README.md').write_text(f'# {name}\n')
    return shop, projects / 'weather-api', projects / 'blog'


def make_sessions(claude_dir, shop, weather, blog):
    sessions = []

    t = Transcript(shop, 'Add Stripe checkout to the cart', 9)
    t.user('Add a Stripe checkout to the cart page. Keep it in test mode for now, and make sure '
           'the existing cart tests still pass.')
    t.text("I'll look at how the cart is built before adding anything.")
    t.tool('mcp__ccd_turn__update_plan', {'items': [
        {'content': 'Read the cart module', 'status': 'completed'},
        {'content': 'Add the checkout endpoint', 'status': 'completed'},
        {'content': 'Wire the button in the cart page', 'status': 'completed'},
        {'content': 'Run the cart and checkout tests', 'status': 'completed'},
    ], 'scope': 'first_task'}, 'Plan shown (4 steps).')
    t.tool('Grep', {'pattern': 'def cart_total', 'path': 'src'}, 'src/shop/cart.py:1:def cart_total(items):')
    t.tool('Read', {'file_path': f'{shop}/src/shop/cart.py'},
           '1\tdef cart_total(items):\n2\t    """Sum of the cart lines, in cents."""\n'
           '3\t    return sum(item.price * item.quantity for item in items)')
    t.tool('Edit', {
        'file_path': f'{shop}/src/shop/checkout.py',
        'old_string': "bp = Blueprint('checkout', __name__)\n",
        'new_string': "bp = Blueprint('checkout', __name__)\nstripe.api_key = os.environ['STRIPE_SECRET_KEY']\n\n\n"
                      "@bp.post('/checkout')\ndef create_checkout():\n    cart = current_cart()\n"
                      "    session = stripe.checkout.Session.create(\n        mode='payment',\n"
                      "        line_items=[item.as_stripe_line() for item in cart.items],\n    )\n"
                      "    return redirect(session.url, code=303)\n",
    }, 'The file src/shop/checkout.py has been updated.')
    t.tool('Edit', {
        'file_path': f'{shop}/templates/cart.html',
        'old_string': '  <p class="total">{{ total }}</p>\n',
        'new_string': '  <p class="total">{{ total }}</p>\n  <form method="post" action="{{ url_for(\'checkout.create_checkout\') }}">\n'
                      '    <button class="btn-primary">Checkout</button>\n  </form>\n',
    }, 'The file templates/cart.html has been updated.')
    t.tool('Bash', {'command': 'pytest -q tests/test_cart.py tests/test_checkout.py',
                    'description': 'Run the cart and checkout tests'},
           '..............                                                   [100%]\n14 passed in 1.82s')
    t.text(
        'The checkout is in place, in Stripe **test mode**:\n\n'
        '- `POST /checkout` creates a Checkout Session from the current cart and redirects to it.\n'
        '- The cart page gets a **Checkout** button under the total.\n'
        '- The secret key is read from `STRIPE_SECRET_KEY`; nothing is hard-coded.\n\n'
        '| File | Change |\n|---|---|\n'
        '| `src/shop/checkout.py` | New endpoint |\n'
        '| `templates/cart.html` | Checkout button |\n'
        '| `tests/test_checkout.py` | Redirect test |\n\n'
        'All 14 cart and checkout tests pass.'
    )
    t.tool('mcp__ccd_turn__wrap_up', {
        'recap': 'The cart page now has a Stripe checkout in test mode, and the 14 cart and checkout '
                 'tests pass. Nothing is committed yet.',
        'ask': 'Shall I commit the checkout?',
        'actions': [{'label': 'Yes, commit', 'message': 'Yes, commit the checkout.'},
                    {'label': 'Not yet', 'message': 'Not yet, I want to try it first.'}],
    }, '')
    sessions.append((t, {'desktop': True, 'held': 'idle'}))

    t = Transcript(weather, 'Fix flaky timezone test', 1)
    t.user('test_forecast_rollover fails about once a week on CI. Can you find out why?')
    t.text('It only fails around midnight UTC, which points at the clock. Reproducing it with a frozen time first.')
    t.tool('Bash', {'command': 'FAKETIME="2026-03-29 00:59:59" pytest -q tests/test_forecast.py -k rollover',
                    'description': 'Reproduce the failure across the DST change'})
    sessions.append((t, {'desktop': True, 'held': 'busy'}))

    t = Transcript(shop, 'Speed up product search', 140)
    t.user('Product search takes 2 seconds on the full catalogue. Profile it and suggest a fix.')
    t.text('The time goes into one `ILIKE` scan per keystroke. A trigram index on `products.name` brings it under 40 ms.')
    sessions.append((t, {'desktop': True}))

    t = Transcript(blog, 'Draft release notes for v2.4', 60 * 22)
    t.user('Draft the release notes for v2.4 from the merged pull requests.')
    t.text('Here is a first draft, grouped into **New**, **Improved** and **Fixed**.')
    sessions.append((t, {'desktop': True}))

    t = Transcript(weather, 'Migrate CI to GitHub Actions', 60 * 24 * 3)
    t.user('Move the CI pipeline from Jenkins to GitHub Actions, same steps.')
    t.text('The workflow is in `.github/workflows/ci.yml`: lint, tests on 3.11 and 3.12, and the Docker build.')
    sessions.append((t, {'desktop': True}))

    t = Transcript(blog, 'Write the onboarding guide', 60 * 24 * 9)
    t.user('Write an onboarding guide for new contributors.')
    t.text('The guide covers the local setup, the branching model and how releases are cut.')
    sessions.append((t, {'desktop': True, 'archived': True}))

    for transcript, _ in sessions:
        transcript.write(claude_dir)
    return sessions


def make_desktop_metadata(home, sessions):
    folder = home / '.config' / 'Claude' / 'claude-code-sessions' / 'demo-account' / 'demo-org'
    folder.mkdir(parents=True)
    registry = home / '.claude' / 'sessions'
    registry.mkdir(parents=True)
    for transcript, flags in sessions:
        if not flags.get('desktop'):
            continue
        (folder / f'local_{transcript.id}.json').write_text(json.dumps({
            'sessionId': f'local_{transcript.id}', 'cliSessionId': transcript.id, 'cwd': transcript.cwd,
            'title': transcript.title, 'isArchived': bool(flags.get('archived')), 'model': MODEL,
            'permissionMode': 'default',
        }))
        if flags.get('held'):
            # pid 1 and 2 always exist: the app sees these sessions as held by Claude Desktop.
            pid = 1 if flags['held'] == 'busy' else 2
            (registry / f'{pid}.json').write_text(json.dumps({
                'pid': pid, 'sessionId': transcript.id, 'cwd': transcript.cwd,
                'entrypoint': 'claude-desktop', 'status': flags['held'],
            }))


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    root = Path(sys.argv[1]).resolve()
    if root.exists():
        shutil.rmtree(root)
    home = root / 'home'
    claude_dir = home / '.claude'
    shop, weather, blog = make_projects(home)
    sessions = make_sessions(claude_dir, shop, weather, blog)
    make_desktop_metadata(home, sessions)
    # Plan usage as the CLI would have reported it on the last web turn.
    (root / 'data').mkdir()
    (root / 'data' / 'usage.json').write_text(json.dumps({
        'status': 'allowed', 'limited_window': 'five_hour', 'overage_status': 'allowed', 'using_overage': False,
        'windows': {
            'five_hour': {'utilization': 0.43, 'resets_at': int((NOW + timedelta(hours=2, minutes=40)).timestamp())},
            'seven_day': {'utilization': 0.18, 'resets_at': int((NOW + timedelta(days=4)).timestamp())},
        },
        'updated_at': (NOW - timedelta(minutes=4)).timestamp(),
    }))
    (root / 'config.ini').write_text(
        f'[server]\nport = 5072\n\n[claude]\nclaude_cli = /nonexistent\ndata_dir = {root}/data\n'
    )
    print(f'Demo workspace ready in {root}')


if __name__ == '__main__':
    main()

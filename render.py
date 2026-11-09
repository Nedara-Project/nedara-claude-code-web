"""Markdown rendering for transcript text.

Raw HTML in the source is escaped by markdown-it (html=False), and the output is
passed through nh3 as a second line of defence before it reaches the browser.
"""

import nh3
from markdown_it import MarkdownIt

_md = MarkdownIt('commonmark', {'html': False, 'linkify': False, 'breaks': False}).enable('table').enable('strikethrough')

_TAGS = {
    'p', 'br', 'hr', 'strong', 'em', 'del', 's', 'code', 'pre', 'blockquote',
    'ul', 'ol', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'a',
    'table', 'thead', 'tbody', 'tr', 'th', 'td',
}
_ATTRS = {'a': {'href', 'title'}, 'code': {'class'}, 'ol': {'start'}}


def markdown_to_html(text):
    html = _md.render(text or '')
    return nh3.clean(
        html,
        tags=_TAGS,
        attributes=_ATTRS,
        url_schemes={'http', 'https', 'mailto'},
        link_rel='noopener noreferrer nofollow',
    )

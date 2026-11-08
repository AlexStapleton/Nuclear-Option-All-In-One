"""Apply the panel's layout to the pinned Shockfront command template."""

import re
import sys
from pathlib import Path


def replace_once(template, old, new):
    if template.count(old) != 1:
        raise ValueError(f"Unexpected Shockfront console template near {old!r}")
    return template.replace(old, new, 1)


path = Path(sys.argv[1])
template = path.read_text(encoding="utf-8")
template, stylesheet_count = re.subn(
    r'    <link href="https://cdn\.jsdelivr\.net/npm/bootstrap[^\n]+\n    <style>.*?</style>',
    '    <link rel="stylesheet" href="{{ url_for(\'static\', filename=\'dashboard.css\') }}">',
    template, count=1, flags=re.S,
)
if stylesheet_count != 1:
    raise ValueError("Could not find the Shockfront console stylesheet")

template = replace_once(template, '<title>Nuclear Option Server Manager</title>',
                        '<title>Advanced commands · Nuclear Option</title>')
template = replace_once(template, '''<body>
    <div class="container py-5" style="height: 100%; display: flex; flex-direction: column;">
        <h1 class="mb-4">Nuclear Option Server Manager</h1>''', '''<body>
    {% include 'navigation.html' %}
    <main class="shell page">
        <header class="hero">
            <div>
                <p class="eyebrow">Server administration</p>
                <h1>Advanced commands</h1>
                <p class="meta">Run game commands, moderate players, and inspect server responses.</p>
            </div>
        </header>''')
template = replace_once(template, '<div class="row flex-grow-1">', '<div class="console-layout">')
template = replace_once(template, '<div class="col-md-8" style="max-height: calc(100vh - 150px); overflow-y: auto;">',
                        '<div class="console-controls">')
template = replace_once(template, '<div class="col-md-4" style="max-height: calc(100vh - 150px); display: flex; flex-direction: column;">',
                        '<aside class="console-output card" aria-label="Connection and response">')
template = replace_once(template, '<h5>Server Response</h5>', '<h2>Server response</h2>')
template = replace_once(template, '<pre id="response-area">', '<pre id="response-area" aria-live="polite">')
template = replace_once(template, '            </div>\n        </div>\n    </div>\n\n    <script>',
                        '            </aside>\n        </div>\n    </main>\n\n    <script>')

# The original page computed command URLs from its own location at the root.
# The console now lives at /commands, while the API routes still live at /command/.
template = replace_once(template, """        const path = window.location.pathname;
        // If the path ends with /, remove it.
        const base_path = path.endsWith('/') ? path.slice(0, -1) : path;
""", "")
template = replace_once(template, 'fetch(base_path + endpoint,', 'fetch(endpoint,')
path.write_text(template, encoding="utf-8")

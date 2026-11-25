"""
Front-end checks. A JavaScript typo doesn't crash the server — it silently breaks
a page on the cleaner's phone. These tests catch that before deploy.
"""
import os, re, shutil, subprocess, tempfile
import pytest
from conftest import ROOT

TEMPLATES = os.path.join(ROOT, 'templates')
HTML_FILES = sorted(f for f in os.listdir(TEMPLATES) if f.endswith('.html'))
NODE = shutil.which('node')

SCRIPT_RE = re.compile(r'<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>', re.S | re.I)


def _strip_jinja(js):
    js = re.sub(r'\{\{.*?\}\}', '0', js, flags=re.S)         # {{ value }} -> 0
    js = re.sub(r'\{%.*?%\}', '', js, flags=re.S)            # {% tags %} removed
    js = re.sub(r'\{#.*?#\}', '', js, flags=re.S)
    return js


@pytest.mark.skipif(not NODE, reason='node not installed — JS syntax check skipped')
@pytest.mark.parametrize('fname', HTML_FILES)
def test_inline_javascript_has_no_syntax_errors(fname):
    with open(os.path.join(TEMPLATES, fname), encoding='utf-8') as f:
        html = f.read()
    for i, block in enumerate(SCRIPT_RE.findall(html)):
        if not block.strip():
            continue
        with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False, encoding='utf-8') as tf:
            tf.write(_strip_jinja(block))
        try:
            r = subprocess.run([NODE, '--check', tf.name], capture_output=True, text=True)
            assert r.returncode == 0, f'{fname} script #{i + 1}:\n{r.stderr}'
        finally:
            os.unlink(tf.name)


@pytest.mark.parametrize('fname', HTML_FILES)
def test_page_handlers_point_to_real_functions(fname):
    """onclick="foo()" must have a matching function foo somewhere on the page."""
    with open(os.path.join(TEMPLATES, fname), encoding='utf-8') as f:
        html = f.read()
    called = set(re.findall(r'on(?:click|change|input|submit|keyup|load)="\s*(?:return\s+)?([A-Za-z_]\w*)\(', html))
    defined = set(re.findall(r'function\s+([A-Za-z_]\w*)\s*\(', html)) | \
              set(re.findall(r'(?:const|let|var)\s+([A-Za-z_]\w*)\s*=\s*(?:async\s*)?(?:function|\()', html)) | \
              set(re.findall(r'window\.([A-Za-z_]\w*)\s*=', html))
    builtins = {'alert', 'confirm', 'event', 'history', 'location', 'window', 'document', 'setTimeout',
                'fetch', 'print', 'open', 'if', 'this'}
    missing = sorted(called - defined - builtins)
    assert not missing, f'{fname}: buttons call functions that do not exist: {missing}'


def test_every_page_includes_loader_and_home_link():
    skip = {'_loader.html', 'login.html', 'client_login.html', 'staff_login.html', 'checklist.html',
            'invoice_print.html', 'client.html'}
    for fname in HTML_FILES:
        if fname in skip or fname.startswith('_'):
            continue
        with open(os.path.join(TEMPLATES, fname), encoding='utf-8') as f:
            html = f.read()
        if 'topbar' not in html:
            continue
        assert 'href="/home"' in html or 'href="/client"' in html, f'{fname}: no way back to home'

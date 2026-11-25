"""
Smoke tests: every page and every API endpoint must load without crashing,
both on an empty system (day one) and on a system full of data.

Routes are discovered automatically, so any NEW route you add is tested too.
"""
import pytest
from conftest import appmod

# Routes that need a real ID in the URL are covered in the feature tests instead.
# Routes that intentionally don't return 200 when hit bare are listed here with why.
SKIP = {
    '/logout', '/checklist/logout', '/client/logout',   # redirect by design
    '/apple-touch-icon.png', '/apple-touch-icon-precomposed.png',
}
CLIENT_ONLY_PREFIXES = ('/client', '/api/client/')


def _get_routes():
    out = []
    for rule in appmod.app.url_map.iter_rules():
        if 'GET' not in rule.methods or rule.arguments or rule.rule.startswith('/static'):
            continue
        if rule.rule in SKIP:
            continue
        out.append(rule.rule)
    return sorted(out)


ADMIN_ROUTES  = [r for r in _get_routes() if not r.startswith(CLIENT_ONLY_PREFIXES)]
CLIENT_ROUTES = [r for r in _get_routes() if r.startswith(CLIENT_ONLY_PREFIXES) and r != '/client/login']


@pytest.mark.parametrize('route', ADMIN_ROUTES)
def test_admin_route_loads_on_empty_system(admin, route):
    r = admin.get(route, follow_redirects=True)
    assert r.status_code == 200, f'{route} -> {r.status_code}\n{r.data[:500]}'


@pytest.mark.parametrize('route', ADMIN_ROUTES)
def test_admin_route_loads_with_real_data(admin, seeded, route):
    r = admin.get(route, follow_redirects=True)
    assert r.status_code == 200, f'{route} -> {r.status_code}\n{r.data[:500]}'


@pytest.mark.parametrize('route', CLIENT_ROUTES)
def test_client_portal_route_loads(app, seeded, route):
    c = app.test_client()
    c.post('/client/login', data={'username': 'jane', 'password': 'clientpw'})
    r = c.get(route, follow_redirects=True)
    assert r.status_code == 200, f'{route} -> {r.status_code}\n{r.data[:500]}'


PUBLIC_OK = {'/', '/staff', '/checklist/login', '/client/login', '/api/employees', '/api/checklist/session'}


@pytest.mark.parametrize('route', [r for r in ADMIN_ROUTES if r not in PUBLIC_OK and r != '/checklist'])
def test_admin_route_blocked_when_logged_out(anon, route):
    r = anon.get(route)
    assert r.status_code in (302, 401, 403), f'{route} is reachable without logging in ({r.status_code})'


def test_api_responses_are_json(admin, seeded):
    for route in ADMIN_ROUTES:
        if not route.startswith('/api/') or route.startswith('/api/export'):
            continue
        r = admin.get(route)
        assert r.is_json, f'{route} did not return JSON'

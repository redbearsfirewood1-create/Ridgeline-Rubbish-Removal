"""Logins, logouts and who-can-see-what."""
from conftest import read_data


# ── Admin ────────────────────────────────────────────────────────────────────
def test_admin_login_success(anon):
    r = anon.post('/', data={'username': 'ADMIN', 'password': 'test-pass'})
    assert r.status_code == 302 and r.headers['Location'].endswith('/home')


def test_admin_login_wrong_password(anon):
    r = anon.post('/', data={'username': 'admin', 'password': 'nope'})
    assert r.status_code == 200 and b'Invalid username or password' in r.data
    assert anon.get('/home').status_code == 302


def test_logged_in_admin_visiting_root_goes_home(admin):
    r = admin.get('/')
    assert r.status_code == 302 and r.headers['Location'].endswith('/home')


def test_logout_really_logs_out(admin):
    admin.get('/logout')
    assert admin.get('/home').status_code == 302
    assert admin.get('/api/invoices').status_code == 302


# ── Staff (PIN) ──────────────────────────────────────────────────────────────
def test_staff_wrong_pin_rejected(app, admin):
    emp = admin.post('/api/employees', json={'name': 'A', 'pin': '1111'}).get_json()
    c = app.test_client()
    r = c.post('/staff', data={'emp_id': emp['id'], 'pin': '9999'})
    assert r.status_code == 200 and b'Wrong PIN' in r.data
    assert c.post('/api/checklist', json={'site_id': 'x'}).status_code == 401


def test_deactivated_staff_cannot_log_in(app, admin):
    emp = admin.post('/api/employees', json={'name': 'A', 'pin': '1111'}).get_json()
    admin.put(f"/api/employees/{emp['id']}", json={'active': False})
    r = app.test_client().post('/staff', data={'emp_id': emp['id'], 'pin': '1111'})
    assert r.status_code == 200 and b'Wrong PIN' in r.data


def test_staff_cannot_reach_admin_pages_or_money(employee):
    c, _ = employee
    for url in ('/home', '/invoices', '/api/invoices', '/api/revenue-summary',
                '/api/expenses', '/api/clients', '/employees', '/api/export/tax'):
        assert c.get(url).status_code == 302, f'staff could open {url}'


def test_public_employee_list_hides_pins(anon, admin):
    admin.post('/api/employees', json={'name': 'A', 'pin': '4321'})
    data = anon.get('/api/employees').get_json()
    assert data and all('pin' not in e for e in data)


# ── Client portal ────────────────────────────────────────────────────────────
def test_client_password_is_stored_hashed(admin):
    admin.post('/api/clients', json={'name': 'C', 'username': 'c1', 'password': 'secret'})
    stored = read_data('clients.json')[0]['password']
    assert stored != 'secret' and stored.startswith(('pbkdf2:', 'scrypt:'))


def test_client_login_and_wrong_password(app, admin):
    admin.post('/api/clients', json={'name': 'C', 'username': 'c1', 'password': 'secret'})
    c = app.test_client()
    assert b'Invalid' in c.post('/client/login', data={'username': 'c1', 'password': 'bad'}).data
    r = c.post('/client/login', data={'username': 'C1', 'password': 'secret'})
    assert r.status_code == 302
    assert c.get('/client').status_code == 200


def test_legacy_plaintext_client_password_upgrades_on_login(app, admin):
    """Clients created before hashing was added must still be able to log in — and get upgraded."""
    import json, os
    from conftest import _TMP
    with open(os.path.join(_TMP, 'clients.json'), 'w') as f:
        json.dump([{'id': 'old1', 'name': 'Old', 'username': 'old', 'password': 'plain', 'active': True, 'sites': []}], f)
    r = app.test_client().post('/client/login', data={'username': 'old', 'password': 'plain'})
    assert r.status_code == 302
    assert read_data('clients.json')[0]['password'].startswith(('pbkdf2:', 'scrypt:'))


def test_editing_client_password_keeps_it_hashed(app, admin):
    cl = admin.post('/api/clients', json={'name': 'C', 'username': 'c1', 'password': 'old'}).get_json()
    admin.put(f"/api/clients/{cl['id']}", json={'password': 'newpass'})
    stored = read_data('clients.json')[0]['password']
    assert stored != 'newpass', 'Edited client password was saved in plain text'
    assert app.test_client().post('/client/login', data={'username': 'c1', 'password': 'newpass'}).status_code == 302


def test_editing_client_without_password_keeps_old_one(app, admin):
    cl = admin.post('/api/clients', json={'name': 'C', 'username': 'c1', 'password': 'keepme'}).get_json()
    admin.put(f"/api/clients/{cl['id']}", json={'name': 'Renamed', 'password': ''})
    assert app.test_client().post('/client/login', data={'username': 'c1', 'password': 'keepme'}).status_code == 302


def test_client_api_never_exposes_password_hashes(admin):
    admin.post('/api/clients', json={'name': 'C', 'username': 'c1', 'password': 'x'})
    for c in admin.get('/api/clients').get_json():
        assert 'password' not in c, 'Password hash sent to the browser'


def test_deactivated_client_cannot_log_in(app, admin):
    cl = admin.post('/api/clients', json={'name': 'C', 'username': 'c1', 'password': 'x'}).get_json()
    admin.put(f"/api/clients/{cl['id']}", json={'active': False})
    assert b'Invalid' in app.test_client().post('/client/login', data={'username': 'c1', 'password': 'x'}).data


def test_client_cannot_reach_admin(app, seeded):
    c = app.test_client()
    c.post('/client/login', data={'username': 'jane', 'password': 'clientpw'})
    for url in ('/home', '/api/invoices', '/api/revenue-summary', '/api/clients', '/api/expenses'):
        assert c.get(url).status_code == 302, f'client could open {url}'


def test_client_only_sees_their_own_sites_checklists(app, admin, seeded):
    # A checklist at a site Jane doesn't own
    seeded['emp_client'].post('/api/checklist', json={'site_id': seeded['sites']['office']['id'],
                                                       'site_name': 'Parramatta Office', 'daily_items': {}})
    c = app.test_client()
    c.post('/client/login', data={'username': 'jane', 'password': 'clientpw'})
    data = c.get('/api/client/checklists').get_json()
    items = data if isinstance(data, list) else data.get('checklists', [])
    assert items, 'client should see their own checklist'
    assert all(cl['site_id'] == seeded['sites']['restaurant']['id'] for cl in items)


def test_client_cannot_download_other_clients_pdf(app, admin, seeded):
    other = seeded['emp_client'].post('/api/checklist', json={'site_id': seeded['sites']['office']['id'],
                                                               'site_name': 'X', 'daily_items': {}}).get_json()
    c = app.test_client()
    c.post('/client/login', data={'username': 'jane', 'password': 'clientpw'})
    assert c.get(f"/api/client/checklist/{other['id']}/pdf").status_code in (403, 404)

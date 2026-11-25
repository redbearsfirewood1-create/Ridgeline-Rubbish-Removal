"""
Shared test setup.

Every test runs against a brand-new, empty data folder in the system temp dir.
Your real data/ folder (and Render's persistent disk) is NEVER read or written.
"""
import os, sys, json, shutil, tempfile, io
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix='ridgeline_test_')
os.environ['RIDGELINE_DATA_DIR'] = _TMP
os.environ['APP_USER']   = 'admin'
os.environ['APP_PASS']   = 'test-pass'
os.environ['SECRET_KEY'] = 'test-secret'
os.environ['SMTP_PASS']  = ''          # make sure tests can never send real email

import app as appmod  # noqa: E402  (must import after env vars are set)

assert appmod.DATA_DIR == _TMP, 'Tests are not isolated from real data — aborting'


def _wipe_data_dir():
    for name in os.listdir(_TMP):
        p = os.path.join(_TMP, name)
        shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)
    appmod.ensure_dirs()
    appmod._templates_cache = None
    appmod._templates_mtime = 0


@pytest.fixture(autouse=True)
def fresh_data():
    _wipe_data_dir()
    yield
    _wipe_data_dir()


@pytest.fixture
def app():
    appmod.app.config.update(TESTING=True)
    return appmod.app


@pytest.fixture
def anon(app):
    """A browser that is not logged in."""
    return app.test_client()


@pytest.fixture
def admin(app):
    c = app.test_client()
    r = c.post('/', data={'username': 'admin', 'password': 'test-pass'})
    assert r.status_code == 302 and r.headers['Location'].endswith('/home')
    return c


@pytest.fixture
def employee(app, admin):
    """A staff member logged in with their PIN. Returns (client, employee_record)."""
    emp = admin.post('/api/employees', json={'name': 'Test Cleaner', 'pin': '1234', 'mobile': '0400000000'}).get_json()
    c = app.test_client()
    r = c.post('/staff', data={'emp_id': emp['id'], 'pin': '1234'})
    assert r.status_code == 302
    return c, emp


@pytest.fixture
def make_site(admin):
    def _make(**kw):
        body = {'name': 'Test Site', 'site_type': 'restaurant', 'has_kitchen': False}
        body.update(kw)
        r = admin.post('/api/sites', json=body)
        assert r.status_code == 201, r.data
        return r.get_json()
    return _make


@pytest.fixture
def tiny_jpeg():
    """A real (tiny) JPEG so PDF/photo code paths run for real."""
    from PIL import Image
    buf = io.BytesIO()
    Image.new('RGB', (40, 30), (61, 139, 61)).save(buf, 'JPEG')
    buf.seek(0)
    return buf


@pytest.fixture
def seeded(admin, make_site, employee):
    """A realistic little business: every site type, a client, expenses, invoices, checklists."""
    emp_client, emp = employee
    sites = {
        'restaurant':   make_site(name='Penrith Maccas', site_type='restaurant', has_kitchen=True),
        'office':       make_site(name='Parramatta Office', site_type='office', num_rooms='8', has_kitchen=True),
        'construction': make_site(name='Kellyville Build', site_type='construction', waste_type='General', skip_bin_size='6m3'),
        'residential':  make_site(name='12 Smith St', site_type='residential', num_rooms='3', num_bathrooms='2',
                                  property_type='House', has_kitchen=True),
    }
    pricing = {
        'restaurant':   {'dining_weekly': 1000, 'kitchen_weekly': 1250, 'dining_cleaner_rate': 83,
                         'kitchen_cleaner_rate': 110, 'days_per_week': 7},
        'office':       {'office_weekly': 500, 'office_cleaner_rate': 60, 'days_per_week': 5},
        'construction': {'construction_weekly': 800, 'construction_cleaner_rate': 90, 'days_per_week': 5},
        'residential':  {'residential_weekly': 350, 'residential_cleaner_rate': 200, 'days_per_week': 1},
    }
    for t, s in sites.items():
        assert admin.put(f"/api/sites/{s['id']}", json={'pricing': pricing[t],
                         'address': '1 Test St, Sydney NSW'}).status_code == 200
    client = admin.post('/api/clients', json={
        'name': 'Jane Client', 'business_name': 'Maccas Penrith', 'username': 'jane',
        'password': 'clientpw', 'email': 'jane@example.com', 'sites': [sites['restaurant']['id']],
    }).get_json()
    admin.post('/api/expenses', json={'date': '2026-11-20', 'category': 'Fuel', 'description': 'Diesel', 'amount': 120.5})
    admin.post('/api/invoices', json={'invoice_number': 'INV-0001', 'status': 'sent', 'date': '2026-11-20',
                                      'bill_to': {'company': 'Maccas Penrith'},
                                      'line_items': [{'description': 'Weekly clean', 'qty': 1, 'rate': 1000, 'total': 1000}],
                                      'gst_rate': 10})
    cl = emp_client.post('/api/checklist', json={
        'site_id': sites['restaurant']['id'], 'site_name': 'Penrith Maccas',
        'date': appmod.sydney_now().strftime('%Y-%m-%d'), 'day': 'Mon',
        'daily_items': {'Bins': {'Empty bins': True, 'Replace liners': False}},
        'weekly_items': {'Clean vents': True}, 'notes': 'All good', 'manager_name': 'Bob',
    }).get_json()
    return {'sites': sites, 'client': client, 'employee': emp, 'emp_client': emp_client, 'checklist': cl}


def read_data(name):
    with open(os.path.join(_TMP, name)) as f:
        return json.load(f)

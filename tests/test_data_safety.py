"""Data must never be lost or corrupted."""
import os, json
from conftest import appmod, _TMP, read_data


def test_save_is_atomic_and_leaves_no_temp_files(admin):
    for i in range(20):
        admin.post('/api/expenses', json={'category': 'X', 'amount': i})
    assert len(read_data('expenses.json')) == 20
    assert not [f for f in os.listdir(_TMP) if f.endswith('.tmp')]


def test_corrupt_file_does_not_take_site_down(admin):
    with open(os.path.join(_TMP, 'invoices.json'), 'w') as f:
        f.write('{ this is not json')
    with open(os.path.join(_TMP, 'sites_config.json'), 'w') as f:
        f.write('garbage')
    for url in ('/home', '/api/revenue-summary', '/api/sites', '/api/invoices'):
        assert admin.get(url).status_code == 200, url


def test_existing_production_style_data_still_loads(admin):
    """Old records (created before newer fields existed) must keep working."""
    with open(os.path.join(_TMP, 'sites_config.json'), 'w') as f:
        json.dump({'old_site': {'name': 'Old Site'},                              # no site_type, no pricing
                   'cambridge': {'pricing': {'dining_weekly': '1000'}}}, f)        # price stored as text
    sites = {s['id']: s for s in admin.get('/api/sites').get_json()}
    assert sites['old_site']['site_type'] == 'restaurant'
    assert admin.get('/api/revenue-summary').status_code == 200
    assert admin.get('/api/checklist/template/old_site').status_code == 200


def test_tests_are_isolated_from_real_data():
    real = os.path.join(appmod.os.path.dirname(appmod.__file__), 'data')
    assert os.path.abspath(appmod.DATA_DIR) != os.path.abspath(real)

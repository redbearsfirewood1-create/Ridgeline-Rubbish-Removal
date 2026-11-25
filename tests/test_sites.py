"""Sites: every site type, its extra fields, checklist template and proof photos."""
import pytest
from conftest import read_data


def _site(admin, sid):
    return next(s for s in admin.get('/api/sites').get_json() if s['id'] == sid)


def test_builtin_sites_always_present(admin):
    ids = {s['id'] for s in admin.get('/api/sites').get_json()}
    assert {'cambridge', 'blaxland'} <= ids


def test_builtin_sites_cannot_be_deleted(admin):
    assert admin.delete('/api/sites/cambridge').status_code == 400


@pytest.mark.parametrize('stype', ['restaurant', 'office', 'construction', 'residential'])
def test_create_each_site_type(admin, make_site, stype):
    s = make_site(name=f'My {stype}', site_type=stype)
    assert s['site_type'] == stype
    assert _site(admin, s['id'])['site_type'] == stype


def test_unknown_site_type_falls_back_to_restaurant(make_site):
    assert make_site(site_type='spaceship')['site_type'] == 'restaurant'


def test_site_name_required(admin):
    assert admin.post('/api/sites', json={'name': '  '}).status_code == 400


def test_duplicate_site_names_get_unique_ids(make_site):
    a, b = make_site(name='Same Name'), make_site(name='Same Name')
    assert a['id'] != b['id']


def test_office_fields_saved(admin, make_site):
    s = _site(admin, make_site(site_type='office', num_rooms='12', has_kitchen=True)['id'])
    assert s['num_rooms'] == '12' and s['has_kitchen'] is True


def test_construction_fields_saved(admin, make_site):
    s = _site(admin, make_site(site_type='construction', waste_type='Hazardous', skip_bin_size='10m3')['id'])
    assert s['waste_type'] == 'Hazardous' and s['skip_bin_size'] == '10m3'


def test_residential_fields_saved(admin, make_site):
    s = _site(admin, make_site(site_type='residential', num_rooms='4', num_bathrooms='2',
                               property_type='Apartment', has_kitchen=True)['id'])
    assert (s['num_rooms'], s['num_bathrooms'], s['property_type']) == ('4', '2', 'Apartment')


def test_edit_site_fields(admin, make_site):
    s = make_site(site_type='residential', num_rooms='2', num_bathrooms='1')
    admin.put(f"/api/sites/{s['id']}", json={'name': 'Renamed', 'num_rooms': '5', 'num_bathrooms': '3',
                                             'address': '5 New Rd', 'start_time': '18:30'})
    got = _site(admin, s['id'])
    assert (got['name'], got['num_rooms'], got['num_bathrooms'], got['address'], got['start_time']) == \
           ('Renamed', '5', '3', '5 New Rd', '18:30')


def test_change_site_type_after_creation(admin, make_site):
    s = make_site(site_type='office')
    admin.put(f"/api/sites/{s['id']}", json={'site_type': 'residential', 'num_rooms': '2'})
    t = admin.get(f"/api/checklist/template/{s['id']}").get_json()
    assert t['daily_sections'][0]['name'] == 'General / Throughout'


def test_edit_does_not_wipe_other_sites(admin, make_site):
    a, b = make_site(name='A'), make_site(name='B')
    admin.put(f"/api/sites/{a['id']}", json={'name': 'A2'})
    assert _site(admin, b['id'])['name'] == 'B'


def test_delete_site(admin, make_site):
    s = make_site()
    assert admin.delete(f"/api/sites/{s['id']}").status_code == 200
    assert s['id'] not in {x['id'] for x in admin.get('/api/sites').get_json()}


def test_edit_is_visible_immediately(admin, make_site):
    """Guards the template cache: an edit must never show stale data."""
    s = make_site(name='Before')
    for i in range(5):
        admin.put(f"/api/sites/{s['id']}", json={'name': f'Name {i}'})
        assert _site(admin, s['id'])['name'] == f'Name {i}'


def test_settings_survive_other_saves(admin, make_site):
    """Saving schedule / employee assignment must not erase pricing or fields."""
    s = make_site(site_type='residential', num_rooms='3')
    admin.put(f"/api/sites/{s['id']}", json={'pricing': {'residential_weekly': 300}})
    admin.put(f"/api/sites/{s['id']}", json={'assigned_days': ['Mon'], 'assigned_employees': []})
    got = _site(admin, s['id'])
    assert got['pricing']['residential_weekly'] == 300 and got['num_rooms'] == '3'


# ── Checklist templates ─────────────────────────────────────────────────────
def test_residential_gets_end_of_lease_checklist(admin, make_site):
    s = make_site(site_type='residential', num_rooms='2')
    t = admin.get(f"/api/checklist/template/{s['id']}").get_json()
    names = [sec['name'] for sec in t['daily_sections']]
    for must in ('Kitchen', 'Bathrooms', 'Laundry', 'Bedrooms (per room)', 'Living / Dining Areas'):
        assert must in names
    assert any('oven' in task.lower() for sec in t['daily_sections'] for task in sec['tasks'])


def test_restaurant_checklist_unchanged(admin, make_site):
    s = make_site(site_type='restaurant', has_kitchen=True)
    t = admin.get(f"/api/checklist/template/{s['id']}").get_json()
    assert [x['name'] for x in t['daily_sections']] == \
           [x['name'] for x in admin.get('/api/checklist/template/cambridge').get_json()['daily_sections']]


def test_unknown_site_template_404(admin):
    assert admin.get('/api/checklist/template/nope').status_code == 404


# ── Proof photos ────────────────────────────────────────────────────────────
def _photo_labels(admin, sid):
    return [p['label'] for p in admin.get(f'/api/checklist/template/{sid}').get_json()['proof_photos']]


def test_residential_photos_3_bed_2_bath(admin, make_site):
    s = make_site(site_type='residential', num_rooms='3', num_bathrooms='2', has_kitchen=True)
    assert _photo_labels(admin, s['id']) == ['Bedroom 1', 'Bedroom 2', 'Bedroom 3', 'Kitchen',
                                             'Oven (inside)', 'Bathroom 1', 'Bathroom 2', 'Living Room']


def test_residential_photos_no_kitchen(admin, make_site):
    labels = _photo_labels(admin, make_site(site_type='residential', num_rooms='1', num_bathrooms='1',
                                            has_kitchen=False)['id'])
    assert 'Kitchen' not in labels and 'Oven (inside)' not in labels


@pytest.mark.parametrize('beds,baths', [('', ''), ('abc', '-2'), (None, None)])
def test_residential_photos_bad_counts_default_to_one(admin, make_site, beds, baths):
    labels = _photo_labels(admin, make_site(site_type='residential', num_rooms=beds, num_bathrooms=baths)['id'])
    assert labels.count('Bedroom 1') == 1 and 'Bedroom 2' not in labels
    assert labels.count('Bathroom 1') == 1 and 'Bathroom 2' not in labels


def test_residential_photos_capped(admin, make_site):
    labels = _photo_labels(admin, make_site(site_type='residential', num_rooms='999', num_bathrooms='1')['id'])
    assert len([l for l in labels if l.startswith('Bedroom')]) <= 20


def test_photo_count_follows_edits(admin, make_site):
    s = make_site(site_type='residential', num_rooms='2', num_bathrooms='1')
    admin.put(f"/api/sites/{s['id']}", json={'num_rooms': '4', 'num_bathrooms': '3'})
    labels = _photo_labels(admin, s['id'])
    assert 'Bedroom 4' in labels and 'Bathroom 3' in labels


def test_restaurant_photos_unchanged(admin, make_site):
    assert _photo_labels(admin, make_site(site_type='restaurant', has_kitchen=False)['id']) == \
           ['Dining Area Inside', 'Bathroom', 'Dining Area Outside']
    assert len(_photo_labels(admin, make_site(site_type='restaurant', has_kitchen=True)['id'])) == 5


def test_photo_keys_are_unique_and_filename_safe(admin, make_site):
    import re
    s = make_site(site_type='residential', num_rooms='5', num_bathrooms='3')
    keys = [p['key'] for p in admin.get(f"/api/checklist/template/{s['id']}").get_json()['proof_photos']]
    assert len(keys) == len(set(keys))
    assert all(re.fullmatch(r'[a-z0-9_]+', k) for k in keys)

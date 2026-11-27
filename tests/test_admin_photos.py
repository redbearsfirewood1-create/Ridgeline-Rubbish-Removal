"""Admin can open a completed checklist and add / replace / remove proof photos the team missed."""
import io, os
import pytest
from PIL import Image
from conftest import read_data, _TMP


def _jpg(color=(10, 120, 10)):
    b = io.BytesIO(); Image.new('RGB', (30, 20), color).save(b, 'JPEG'); b.seek(0); return b


def _png():
    b = io.BytesIO(); Image.new('RGB', (30, 20)).save(b, 'PNG'); b.seek(0); return b


def _upload(client, cid, key, f=None, name='p.jpg'):
    return client.post(f'/api/checklist/{cid}/admin-photo/{key}',
                       data={'photo': (f or _jpg(), name)}, content_type='multipart/form-data')


@pytest.fixture
def res_checklist(seeded):
    """A submitted residential (3 bed / 2 bath) checklist with NO photos."""
    sid = seeded['sites']['residential']['id']
    r = seeded['emp_client'].post('/api/checklist', json={'site_id': sid, 'site_name': '12 Smith St', 'daily_items': {}})
    return r.get_json()['id']


def _photos(admin, cid):
    r = admin.get(f'/api/checklist/{cid}/photos')
    assert r.status_code == 200
    return r.get_json()


def test_lists_every_expected_slot_and_counts_missing(admin, res_checklist):
    d = _photos(admin, res_checklist)
    assert [s['label'] for s in d['slots']] == ['Bedroom 1', 'Bedroom 2', 'Bedroom 3', 'Kitchen',
                                                 'Oven (inside)', 'Bathroom 1', 'Bathroom 2', 'Living Room']
    assert d['missing'] == 8 and all(s['url'] == '' for s in d['slots'])


def test_admin_fills_missing_photo(admin, res_checklist):
    r = _upload(admin, res_checklist, 'oven')
    assert r.status_code == 200
    d = _photos(admin, res_checklist)
    oven = next(s for s in d['slots'] if s['key'] == 'oven')
    assert oven['url'] and oven['added_by'] == 'Office'
    assert d['missing'] == 7
    assert admin.get(oven['url'].split('?')[0]).status_code == 200


def test_cleaner_photos_are_credited_to_the_cleaner(admin, seeded, res_checklist):
    seeded['emp_client'].post(f'/api/checklist/{res_checklist}/proof/kitchen',
                              data={'photo': (_jpg(), 'k.jpg')}, content_type='multipart/form-data')
    k = next(s for s in _photos(admin, res_checklist)['slots'] if s['key'] == 'kitchen')
    assert k['added_by'] == 'Test Cleaner'


def test_replace_photo_removes_old_file(admin, res_checklist):
    _upload(admin, res_checklist, 'kitchen', _jpg(), 'a.jpg')
    _upload(admin, res_checklist, 'kitchen', _png(), 'b.png')
    fn = read_data('checklists.json')[0]['proof_photos']['kitchen']
    assert fn.endswith('.png')
    proofs = os.listdir(os.path.join(_TMP, 'photos', 'proofs'))
    assert [f for f in proofs if '_kitchen.' in f] == [fn], 'old photo file left behind'


def test_add_extra_photos_get_unique_slots(admin, res_checklist):
    assert _upload(admin, res_checklist, 'new_extra').get_json()['key'] == 'extra_1'
    assert _upload(admin, res_checklist, 'new_extra').get_json()['key'] == 'extra_2'
    labels = [s['label'] for s in _photos(admin, res_checklist)['slots']]
    assert 'Extra 1' in labels and 'Extra 2' in labels
    assert _photos(admin, res_checklist)['missing'] == 8   # extras don't count toward missing


def test_remove_photo(admin, res_checklist):
    _upload(admin, res_checklist, 'living_room')
    assert admin.delete(f'/api/checklist/{res_checklist}/photo/living_room').status_code == 200
    assert next(s for s in _photos(admin, res_checklist)['slots'] if s['key'] == 'living_room')['url'] == ''
    assert admin.delete(f'/api/checklist/{res_checklist}/photo/living_room').status_code == 404


def test_admin_photos_show_in_pdf_and_client_portal(app, admin, seeded):
    cid = seeded['checklist']['id']                       # Jane's restaurant checklist
    _upload(admin, cid, 'dining_in')
    assert admin.get(f'/api/checklist/{cid}/pdf').data[:4] == b'%PDF'
    c = app.test_client()
    c.post('/client/login', data={'username': 'jane', 'password': 'clientpw'})
    assert c.get(f'/api/client/checklist/{cid}/pdf').data[:4] == b'%PDF'


@pytest.mark.parametrize('bad', ['evil.sh', 'notes.txt', 'noextension'])
def test_rejects_non_images(admin, res_checklist, bad):
    r = _upload(admin, res_checklist, 'kitchen', io.BytesIO(b'xx'), bad)
    assert r.status_code == 400
    assert not read_data('checklists.json')[0].get('proof_photos')


def test_bad_slot_names_rejected(admin, res_checklist):
    for key in ('Kitchen', 'a-b', 'x' * 41):
        assert _upload(admin, res_checklist, key).status_code == 400


def test_unknown_checklist_404_and_no_orphan_file(admin):
    assert _upload(admin, 'nope', 'kitchen').status_code == 404
    assert admin.get('/api/checklist/nope/photos').status_code == 404
    proofs = os.path.join(_TMP, 'photos', 'proofs')
    assert not os.path.isdir(proofs) or not os.listdir(proofs)


def test_staff_and_clients_cannot_use_admin_photo_tools(app, seeded, res_checklist):
    staff = seeded['emp_client']
    assert _upload(staff, res_checklist, 'kitchen').status_code == 302
    assert staff.delete(f'/api/checklist/{res_checklist}/photo/kitchen').status_code == 302
    assert staff.get(f'/api/checklist/{res_checklist}/photos').status_code == 302
    c = app.test_client()
    c.post('/client/login', data={'username': 'jane', 'password': 'clientpw'})
    assert _upload(c, res_checklist, 'kitchen').status_code == 302


def test_checklists_page_has_photo_tools_for_admin_only(app, admin, seeded):
    html = admin.get('/checklists').get_data(as_text=True)
    assert 'loadPhotoAdmin' in html and 'adminUploadPhoto' in html
    c = app.test_client()
    c.post('/client/login', data={'username': 'jane', 'password': 'clientpw'})
    r = c.get('/client/checklists', follow_redirects=True)
    assert r.status_code == 200 and 'const IS_CLIENT = true' in r.get_data(as_text=True)

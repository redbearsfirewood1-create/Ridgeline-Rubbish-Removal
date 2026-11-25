"""The cleaner's job flow: arrive, tick tasks, upload proof photos, submit, PDF report."""
import io
from conftest import read_data, appmod


def test_submit_checklist_counts_tasks(seeded):
    cl = seeded['checklist']
    assert (cl['done'], cl['total']) == (2, 3)


def test_checklist_records_who_and_when(admin, seeded):
    rec = admin.get('/api/checklist').get_json()[0]
    assert rec['employee_name'] == 'Test Cleaner'
    assert rec['submitted_at'].endswith(('+11:00', '+10:00')), 'Not stamped in Sydney time'


def test_logged_out_cannot_submit(anon):
    assert anon.post('/api/checklist', json={'site_id': 'cambridge'}).status_code == 401


def test_empty_submission_does_not_crash(employee):
    c, _ = employee
    assert c.post('/api/checklist', json={}).status_code in (201, 400)


def test_proof_photo_upload_and_view(admin, seeded, tiny_jpeg):
    c, cid = seeded['emp_client'], seeded['checklist']['id']
    r = c.post(f'/api/checklist/{cid}/proof/bedroom_1',
               data={'photo': (tiny_jpeg, 'bed.jpg')}, content_type='multipart/form-data')
    assert r.status_code == 200
    fn = read_data('checklists.json')[0]['proof_photos']['bedroom_1']
    assert admin.get(f'/proof-photos/{fn}').status_code == 200


def test_proof_photo_rejects_non_images(seeded):
    c, cid = seeded['emp_client'], seeded['checklist']['id']
    r = c.post(f'/api/checklist/{cid}/proof/kitchen',
               data={'photo': (io.BytesIO(b'#!/bin/sh'), 'evil.sh')}, content_type='multipart/form-data')
    assert r.status_code == 400


def test_proof_photo_key_cannot_escape_folder(seeded, tiny_jpeg):
    c, cid = seeded['emp_client'], seeded['checklist']['id']
    r = c.post(f'/api/checklist/{cid}/proof/..%2F..%2Fhack',
               data={'photo': (tiny_jpeg, 'x.jpg')}, content_type='multipart/form-data')
    assert r.status_code in (400, 404)


def test_logged_out_cannot_upload_proof(anon, seeded, tiny_jpeg):
    r = anon.post(f"/api/checklist/{seeded['checklist']['id']}/proof/kitchen",
                  data={'photo': (tiny_jpeg, 'x.jpg')}, content_type='multipart/form-data')
    assert r.status_code == 401


def test_admin_pdf_report(admin, seeded, tiny_jpeg):
    c, cid = seeded['emp_client'], seeded['checklist']['id']
    c.post(f'/api/checklist/{cid}/proof/dining_in', data={'photo': (tiny_jpeg, 'a.jpg')},
           content_type='multipart/form-data')
    r = admin.get(f'/api/checklist/{cid}/pdf')
    assert r.status_code == 200 and r.data[:4] == b'%PDF'


def test_residential_pdf_with_room_photos(admin, seeded, tiny_jpeg):
    from PIL import Image
    sid = seeded['sites']['residential']['id']
    c = seeded['emp_client']
    cid = c.post('/api/checklist', json={'site_id': sid, 'site_name': '12 Smith St',
                                         'daily_items': {'Kitchen': {'Clean oven': True}}}).get_json()['id']
    for key in ('bedroom_1', 'bedroom_2', 'bedroom_3', 'kitchen', 'oven', 'bathroom_1', 'bathroom_2', 'living_room'):
        buf = io.BytesIO(); Image.new('RGB', (40, 30)).save(buf, 'JPEG'); buf.seek(0)
        c.post(f'/api/checklist/{cid}/proof/{key}', data={'photo': (buf, 'p.jpg')}, content_type='multipart/form-data')
    r = admin.get(f'/api/checklist/{cid}/pdf')
    assert r.status_code == 200 and r.data[:4] == b'%PDF'


def test_client_pdf_report(app, seeded):
    c = app.test_client()
    c.post('/client/login', data={'username': 'jane', 'password': 'clientpw'})
    r = c.get(f"/api/client/checklist/{seeded['checklist']['id']}/pdf")
    assert r.status_code == 200 and r.data[:4] == b'%PDF'


def test_pdf_for_missing_checklist_is_404(admin):
    assert admin.get('/api/checklist/nope/pdf').status_code == 404


def test_delete_checklist(admin, seeded):
    admin.delete(f"/api/checklist/{seeded['checklist']['id']}")
    assert admin.get('/api/checklist').get_json() == []


def test_arrival_logged(seeded):
    c = seeded['emp_client']
    r = c.post('/api/arrivals', data={'site_id': seeded['sites']['residential']['id'], 'lat': '-33.8', 'lng': '151.0'})
    assert r.status_code in (200, 201), r.data

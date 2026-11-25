"""Revenue, labour, invoices and expenses — the numbers have to be right."""
import pytest
from conftest import read_data, appmod


def _rev(admin):
    return admin.get('/api/revenue-summary').get_json()


def _site_row(rev, sid):
    return next(s for s in rev['sites'] if s['id'] == sid)


# ── Contracted revenue per site type ────────────────────────────────────────
def test_restaurant_with_kitchen_revenue(admin, seeded):
    row = _site_row(_rev(admin), seeded['sites']['restaurant']['id'])
    assert row['weekly_revenue'] == 2250          # dining 1000 + kitchen 1250
    assert row['weekly_labor'] == (83 + 110) * 7  # nightly rates x 7 nights
    assert row['weekly_profit'] == 2250 - 1351


def test_restaurant_without_kitchen_ignores_kitchen_prices(admin, make_site):
    s = make_site(site_type='restaurant', has_kitchen=False)
    admin.put(f"/api/sites/{s['id']}", json={'pricing': {'dining_weekly': 1000, 'kitchen_weekly': 9999,
              'dining_cleaner_rate': 80, 'kitchen_cleaner_rate': 9999, 'days_per_week': 5}})
    row = _site_row(_rev(admin), s['id'])
    assert row['weekly_revenue'] == 1000 and row['weekly_labor'] == 400


def test_office_revenue(admin, seeded):
    row = _site_row(_rev(admin), seeded['sites']['office']['id'])
    assert (row['weekly_revenue'], row['weekly_labor']) == (500, 300)


def test_construction_revenue(admin, seeded):
    row = _site_row(_rev(admin), seeded['sites']['construction']['id'])
    assert (row['weekly_revenue'], row['weekly_labor']) == (800, 450)


def test_residential_revenue(admin, seeded):
    row = _site_row(_rev(admin), seeded['sites']['residential']['id'])
    assert (row['weekly_revenue'], row['weekly_labor'], row['weekly_profit']) == (350, 200, 150)


def test_totals_add_up(admin, seeded):
    rev = _rev(admin)
    assert rev['weekly_contracted'] == round(sum(s['weekly_revenue'] for s in rev['sites']), 2)
    assert rev['weekly_labor_contracted'] == round(sum(s['weekly_labor'] for s in rev['sites']), 2)
    assert rev['weekly_profit_contracted'] == round(rev['weekly_contracted'] - rev['weekly_labor_contracted'], 2)
    assert rev['month_contracted'] == round(rev['weekly_contracted'] * 52 / 12, 2)


def test_site_with_no_pricing_counts_as_zero(admin, make_site):
    s = make_site(site_type='office')
    assert _site_row(_rev(admin), s['id'])['weekly_revenue'] == 0


@pytest.mark.parametrize('bad', ['', None, 'abc', 0, -3])
def test_bad_days_per_week_does_not_crash(admin, make_site, bad):
    s = make_site(site_type='office')
    admin.put(f"/api/sites/{s['id']}", json={'pricing': {'office_weekly': 500, 'days_per_week': bad}})
    assert admin.get('/api/revenue-summary').status_code == 200


def test_bad_price_text_does_not_crash(admin, make_site):
    s = make_site(site_type='office')
    admin.put(f"/api/sites/{s['id']}", json={'pricing': {'office_weekly': 'five hundred'}})
    assert admin.get('/api/revenue-summary').status_code == 200


# ── Actual revenue ──────────────────────────────────────────────────────────
def test_completed_clean_counts_as_actual_revenue(admin, seeded):
    rev = _rev(admin)
    row = _site_row(rev, seeded['sites']['restaurant']['id'])
    assert rev['week_actual_revenue'] >= row['per_visit_revenue'] > 0


def test_only_paid_invoices_count(admin, seeded):
    before = _rev(admin)['week_actual_revenue']
    today = appmod.sydney_now().strftime('%Y-%m-%d')
    admin.post('/api/invoices', json={'id': 'unpaid1', 'status': 'sent', 'date': today,
                                      'line_items': [{'total': 500}], 'gst_rate': 10})
    assert _rev(admin)['week_actual_revenue'] == before
    admin.post('/api/invoices', json={'id': 'paid1', 'status': 'paid', 'date': today,
                                      'line_items': [{'total': 500}], 'gst_rate': 10, 'discount': 100})
    assert _rev(admin)['week_actual_revenue'] == round(before + 440, 2)   # (500-100) + 10% GST


def test_malformed_invoice_does_not_break_dashboard(admin):
    admin.post('/api/invoices', json={'status': 'paid', 'date': 'not-a-date', 'line_items': [{'total': 'x'}]})
    assert admin.get('/api/revenue-summary').status_code == 200


# ── Invoice auto-fill ───────────────────────────────────────────────────────
@pytest.mark.parametrize('stype,expected', [('restaurant', 2250), ('office', 500),
                                             ('construction', 800), ('residential', 350)])
def test_invoice_autofill_amount_matches_revenue(admin, seeded, stype, expected):
    sid = seeded['sites'][stype]['id']
    admin.put(f"/api/clients/{seeded['client']['id']}", json={'sites': [sid]})
    data = admin.get('/api/clients/for-invoice').get_json()
    site = next(s for c in data if c['id'] == seeded['client']['id'] for s in c['sites'] if s['id'] == sid)
    assert site['weekly_revenue'] == expected
    assert site['weekly_revenue'] == _site_row(_rev(admin), sid)['weekly_revenue']


def test_invoice_autofill_includes_email(admin, seeded):
    c = next(c for c in admin.get('/api/clients/for-invoice').get_json() if c['id'] == seeded['client']['id'])
    assert c['email'] == 'jane@example.com'


# ── Invoices CRUD ───────────────────────────────────────────────────────────
def test_invoice_create_edit_delete(admin):
    inv_id = admin.post('/api/invoices', json={'invoice_number': 'INV-9', 'status': 'draft'}).get_json()['id']
    admin.post('/api/invoices', json={'id': inv_id, 'invoice_number': 'INV-9', 'status': 'paid'})
    invs = admin.get('/api/invoices').get_json()
    assert len(invs) == 1 and invs[0]['status'] == 'paid'
    assert admin.get(f'/invoices/{inv_id}/edit').status_code == 200
    admin.delete(f'/api/invoices/{inv_id}')
    assert admin.get('/api/invoices').get_json() == []


def test_editing_invoice_keeps_created_date(admin):
    r = admin.post('/api/invoices', json={'status': 'draft'}).get_json()
    created = admin.get('/api/invoices').get_json()[0]['created_at']
    admin.post('/api/invoices', json={'id': r['id'], 'status': 'sent',
                                      'created_at': created})
    assert admin.get('/api/invoices').get_json()[0]['created_at'] == created


def test_one_off_invoice_without_client(admin):
    r = admin.post('/api/invoices', json={'invoice_number': 'INV-1', 'bill_to': {'company': 'Random Co'},
                                          'line_items': [{'description': 'Once-off', 'total': 200}]})
    assert r.status_code == 200
    assert admin.get('/invoices/new').status_code == 200


def test_email_invoice_refuses_when_not_configured(admin):
    r = admin.post('/api/invoices/abc/email', json={'to_email': 'x@example.com', 'html': '<p>hi</p>'})
    assert r.status_code == 500 and 'not configured' in r.get_json()['error']


# ── Expenses ────────────────────────────────────────────────────────────────
def test_expense_create_and_delete(admin):
    e = admin.post('/api/expenses', json={'category': 'Fuel', 'description': 'Diesel', 'amount': '45.50'}).get_json()
    assert e['amount'] == 45.5
    admin.delete(f"/api/expenses/{e['id']}")
    assert admin.get('/api/expenses').get_json() == []


@pytest.mark.parametrize('bad', ['', 'abc', None])
def test_expense_bad_amount_rejected_cleanly(admin, bad):
    r = admin.post('/api/expenses', json={'category': 'Fuel', 'amount': bad})
    assert r.status_code == 400
    assert admin.get('/api/expenses').get_json() == []


def test_tax_export_downloads(admin, seeded):
    r = admin.get('/api/export/tax')
    assert r.status_code == 200 and len(r.data) > 100

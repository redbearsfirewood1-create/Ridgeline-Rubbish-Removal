"""Adhoc (once-off) sites, the staff Back button and the Navigate button."""
import os, re, shutil, subprocess
from datetime import timedelta
import pytest
from conftest import appmod, ROOT

TODAY = lambda: appmod.sydney_now().date()


def _iso(d): return d.isoformat()


def _staff_today(emp_client):
    return {s['id']: s for s in emp_client.get('/api/sites/today').get_json()}


# ── Adhoc: creating ─────────────────────────────────────────────────────────
def test_adhoc_site_created_with_date(admin, make_site):
    s = make_site(name='Bond Clean 5 Rose St', site_type='residential', is_adhoc=True, adhoc_date=_iso(TODAY()))
    got = next(x for x in admin.get('/api/sites').get_json() if x['id'] == s['id'])
    assert got['is_adhoc'] is True and got['adhoc_date'] == _iso(TODAY())


@pytest.mark.parametrize('bad', ['', 'tomorrow', '2026-13-45', None])
def test_adhoc_requires_a_real_date(admin, bad):
    r = admin.post('/api/sites', json={'name': 'X', 'is_adhoc': True, 'adhoc_date': bad})
    assert r.status_code == 400


def test_recurring_is_the_default(admin, make_site):
    s = make_site(name='Normal')
    got = next(x for x in admin.get('/api/sites').get_json() if x['id'] == s['id'])
    assert got['is_adhoc'] is False


def test_convert_recurring_to_adhoc_and_back(admin, make_site):
    s = make_site()
    assert admin.put(f"/api/sites/{s['id']}", json={'is_adhoc': True, 'adhoc_date': ''}).status_code == 400
    assert admin.put(f"/api/sites/{s['id']}", json={'is_adhoc': True, 'adhoc_date': _iso(TODAY())}).status_code == 200
    assert admin.put(f"/api/sites/{s['id']}", json={'is_adhoc': False, 'adhoc_date': ''}).status_code == 200
    got = next(x for x in admin.get('/api/sites').get_json() if x['id'] == s['id'])
    assert got['is_adhoc'] is False


# ── Adhoc: who sees it, when ────────────────────────────────────────────────
def test_adhoc_shows_for_staff_only_on_job_date(admin, make_site, employee):
    c, _ = employee
    today    = make_site(name='Today Job',    is_adhoc=True, adhoc_date=_iso(TODAY()))
    tomorrow = make_site(name='Tomorrow Job', is_adhoc=True, adhoc_date=_iso(TODAY() + timedelta(days=1)))
    past     = make_site(name='Old Job',      is_adhoc=True, adhoc_date=_iso(TODAY() - timedelta(days=7)))
    sites = _staff_today(c)
    assert today['id'] in sites and sites[today['id']]['is_adhoc'] is True
    assert tomorrow['id'] not in sites
    assert past['id'] not in sites


def test_adhoc_ignores_active_days(admin, make_site, employee):
    """Even if the job date's weekday is switched off, a once-off job still shows on its date."""
    c, _ = employee
    s = make_site(name='Once', is_adhoc=True, adhoc_date=_iso(TODAY()))
    admin.put(f"/api/sites/{s['id']}", json={'assigned_days': [], 'assigned_employees': []})
    assert s['id'] in _staff_today(c)


def test_adhoc_appears_in_week_view_on_its_day_only(make_site, employee):
    c, _ = employee
    d = TODAY() + timedelta(days=1) if TODAY().weekday() < 6 else TODAY()
    s = make_site(name='Week Job', is_adhoc=True, adhoc_date=_iso(d))
    week = c.get('/api/sites/week').get_json()
    days_with_it = [day for day, sites in week.items() if any(x['id'] == s['id'] for x in sites)]
    assert days_with_it == [appmod._DAY_NAMES[d.weekday()]]


def test_disabled_adhoc_site_hidden(admin, make_site, employee):
    c, _ = employee
    s = make_site(name='Off', is_adhoc=True, adhoc_date=_iso(TODAY()))
    admin.put(f"/api/sites/{s['id']}", json={'enabled': False})
    assert s['id'] not in _staff_today(c)


def test_adhoc_only_for_assigned_cleaner(admin, make_site, employee, app):
    c, emp = employee
    other = admin.post('/api/employees', json={'name': 'Other', 'pin': '2222'}).get_json()
    s = make_site(name='Assigned', is_adhoc=True, adhoc_date=_iso(TODAY()))
    admin.put(f"/api/sites/{s['id']}", json={'assigned_employees': [other['id']]})
    assert s['id'] not in _staff_today(c)


def test_recurring_sites_still_follow_active_days(admin, make_site, employee):
    c, _ = employee
    s = make_site(name='Weekly')
    today_abbr = appmod._today_day_abbr()
    admin.put(f"/api/sites/{s['id']}", json={'assigned_days': [d for d in appmod._DAY_NAMES if d != today_abbr]})
    assert s['id'] not in _staff_today(c)
    admin.put(f"/api/sites/{s['id']}", json={'assigned_days': [today_abbr]})
    assert s['id'] in _staff_today(c)


def test_live_monitor_shows_todays_adhoc(admin, make_site):
    s = make_site(name='Live Adhoc', is_adhoc=True, adhoc_date=_iso(TODAY()))
    make_site(name='Future Adhoc', is_adhoc=True, adhoc_date=_iso(TODAY() + timedelta(days=3)))
    r = admin.get('/api/live-status')
    assert r.status_code == 200
    blob = r.get_data(as_text=True)
    assert 'Live Adhoc' in blob and 'Future Adhoc' not in blob


# ── Adhoc: money ────────────────────────────────────────────────────────────
def test_adhoc_not_counted_as_weekly_contracted(admin, make_site):
    s = make_site(site_type='residential', is_adhoc=True, adhoc_date=_iso(TODAY()))
    admin.put(f"/api/sites/{s['id']}", json={'pricing': {'residential_weekly': 450, 'residential_cleaner_rate': 250,
                                                          'days_per_week': 3}})
    rev = admin.get('/api/revenue-summary').get_json()
    assert rev['weekly_contracted'] == 0
    row = next(x for x in rev['sites'] if x['id'] == s['id'])
    assert row['is_adhoc'] and row['per_visit_revenue'] == 450 and row['per_visit_labor'] == 250


def test_completed_adhoc_job_counts_full_price(admin, make_site, employee):
    c, _ = employee
    s = make_site(site_type='office', is_adhoc=True, adhoc_date=_iso(TODAY()))
    admin.put(f"/api/sites/{s['id']}", json={'pricing': {'office_weekly': 600, 'office_cleaner_rate': 300}})
    before = admin.get('/api/revenue-summary').get_json()
    c.post('/api/checklist', json={'site_id': s['id'], 'site_name': 'x', 'date': _iso(TODAY()), 'daily_items': {}})
    after = admin.get('/api/revenue-summary').get_json()
    assert round(after['week_actual_revenue'] - before['week_actual_revenue'], 2) == 600
    assert round(after['week_actual_labor'] - before['week_actual_labor'], 2) == 300


# ── Staff checklist page: Back button + Navigate ────────────────────────────
@pytest.fixture
def checklist_html(employee):
    c, _ = employee
    r = c.get('/checklist')
    assert r.status_code == 200
    return r.get_data(as_text=True)


def test_back_button_on_arrival_and_checklist_screens(checklist_html):
    arrival   = checklist_html.split('id="phaseArrival"')[1].split('id="phaseChecklist"')[0]
    checklist = checklist_html.split('id="phaseChecklist"')[1].split('id="phaseDone"')[0]
    assert 'onclick="backToSites()"' in arrival
    assert 'onclick="backToSites()"' in checklist


def test_back_button_warns_before_clearing_ticks(checklist_html):
    fn = re.search(r'function backToSites\(\)\s*\{(.*?)\n\}', checklist_html, re.S).group(1)
    assert 'confirm(' in fn and 'goToSites()' in fn


def test_navigate_button_rendered_for_each_site(checklist_html):
    assert 'btn-nav-site' in checklist_html and 'mapsDirectionsUrl(s.address)' in checklist_html
    assert 'event.stopPropagation()' in checklist_html, 'Navigate tap would also open the checklist'


@pytest.mark.skipif(not shutil.which('node'), reason='node not installed')
def test_navigate_link_is_a_valid_google_maps_url(checklist_html):
    fn = re.search(r'(function mapsDirectionsUrl\(address\)\s*\{.*?\n\})', checklist_html, re.S).group(1)
    js = fn + "\nconsole.log(mapsDirectionsUrl('12 Smith St, Penrith NSW 2750 & Co #3'));"
    out = subprocess.run(['node', '-e', js], capture_output=True, text=True).stdout.strip()
    assert out.startswith('https://www.google.com/maps/dir/?api=1')
    assert 'destination=12%20Smith%20St%2C%20Penrith%20NSW%202750%20%26%20Co%20%233' in out


def test_staff_today_api_provides_address_for_navigation(admin, make_site, employee):
    c, _ = employee
    s = make_site(name='Nav Site')
    admin.put(f"/api/sites/{s['id']}", json={'address': '1 George St, Sydney NSW'})
    assert _staff_today(c)[s['id']]['address'] == '1 George St, Sydney NSW'


def test_admin_site_form_has_adhoc_controls(admin):
    html = admin.get('/employees').get_data(as_text=True)
    for must in ('id="newSiteFrequency"', 'value="adhoc"', 'id="newAdhocDate"',
                 'siteFrequency-${site.id}', 'siteAdhocDate-${site.id}'):
        assert must in html, must

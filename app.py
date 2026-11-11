import os, json, uuid, shutil, io, smtplib, re
from datetime import datetime
from functools import wraps
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from flask import (Flask, render_template, request, redirect, url_for,
                   session, jsonify, send_from_directory, Response)
from werkzeug.utils import secure_filename

try:
    from reportlab.pdfgen import canvas as rl_canvas
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.colors import HexColor
    from reportlab.lib.utils import ImageReader
    import base64 as _b64
    REPORTLAB = True
except ImportError:
    REPORTLAB = False

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'ridgeline-secret-2024')
app.permanent_session_lifetime = 86400 * 30

# ── Config ────────────────────────────────────────────────────────────────────
USERNAME     = os.environ.get('APP_USER', 'admin')
PASSWORD     = os.environ.get('APP_PASS', 'ridgeline2024')
GOOGLE_MAPS  = os.environ.get('GOOGLE_MAPS_KEY', '')
BASE_ADDRESS = '620 Merrylands Road, Greystanes NSW 2145'
SMTP_USER    = os.environ.get('SMTP_USER', '')
SMTP_PASS    = os.environ.get('SMTP_PASS', '')   # Gmail App Password
BUSINESS_PHONE = os.environ.get('BUSINESS_PHONE', '')

DATA_DIR     = os.path.join(os.path.dirname(__file__), 'data')
PHOTOS_DIR   = os.path.join(DATA_DIR, 'photos')
QUOTES_FILE  = os.path.join(DATA_DIR, 'quotes.json')
EXPENSES_FILE= os.path.join(DATA_DIR, 'expenses.json')
TRUCKS_FILE      = os.path.join(DATA_DIR, 'trucks.json')
EMPLOYEES_FILE   = os.path.join(DATA_DIR, 'employees.json')
CHECKLISTS_FILE  = os.path.join(DATA_DIR, 'checklists.json')
ALLOWED_EXT  = {'png','jpg','jpeg','gif','webp','heic'}
EMP_PHOTOS_DIR = os.path.join(DATA_DIR, 'photos', 'emp_photos')

# ── Item pricing ──────────────────────────────────────────────────────────────
ITEM_PRICES = {
    'Mattress': 200,
    'Tyre':     100,
}

# ── KM brackets (same as RBF) ─────────────────────────────────────────────────
def collection_fee_for_km(km):
    if km <=  20: return 25
    if km <=  40: return 30
    if km <=  60: return 35
    if km <=  80: return 40
    if km <= 100: return 45
    return 50

def ensure_dirs():
    for d in (DATA_DIR, PHOTOS_DIR, CLIENT_LOGOS_DIR, EMP_PHOTOS_DIR):
        os.makedirs(d, exist_ok=True)

def load_json(path):
    try:
        with open(path) as f: return json.load(f)
    except: return []

def save_json(path, data):
    with open(path, 'w') as f: json.dump(data, f, indent=2)

def next_quote_number(quotes):
    nums = [int(q.get('quote_number','Q0').replace('Q','')) for q in quotes if q.get('quote_number','').startswith('Q')]
    return 'Q' + str(max(nums, default=0) + 1).zfill(4)

def allowed_file(fn):
    return '.' in fn and fn.rsplit('.',1)[1].lower() in ALLOWED_EXT

# ── Auth ──────────────────────────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

def client_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('client_id'):
            return redirect(url_for('client_login'))
        return f(*args, **kwargs)
    return decorated

# ── Routes ────────────────────────────────────────────────────────────────────
@app.route('/', methods=['GET','POST'])
def login():
    error = None
    if request.method == 'POST':
        if request.form.get('username','').strip().lower() == USERNAME.lower() \
           and request.form.get('password','') == PASSWORD:
            session.permanent = True
            session['logged_in'] = True
            return redirect(url_for('home'))
        error = 'Invalid username or password'
    return render_template('login.html', error=error)

@app.route('/apple-touch-icon.png')
@app.route('/apple-touch-icon-precomposed.png')
def apple_touch_icon():
    return send_from_directory('static', 'logo.png', mimetype='image/png')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/home')
@login_required
def home():
    return render_template('home.html')

@app.route('/quote')
@login_required
def quote():
    return render_template('quote.html',
                           google_maps_key=GOOGLE_MAPS,
                           base_address=BASE_ADDRESS,
                           item_prices=ITEM_PRICES)

@app.route('/quotes')
@login_required
def quotes():
    return render_template('quotes.html')

@app.route('/dashboard')
@login_required
def dashboard():
    return render_template('dashboard.html')

@app.route('/expenses')
@login_required
def expenses():
    return render_template('expenses.html')

@app.route('/job/<qid>')
@login_required
def job_page(qid):
    quotes = load_json(QUOTES_FILE)
    q = next((x for x in quotes if x['id'] == qid), None)
    if not q:
        return redirect(url_for('route_page'))
    return render_template('job.html', quote=q, google_maps_key=GOOGLE_MAPS)

@app.route('/schedule')
@login_required
def schedule():
    return render_template('schedule.html', google_maps_key=GOOGLE_MAPS)

@app.route('/route')
@login_required
def route_page():
    return render_template('route.html', google_maps_key=GOOGLE_MAPS, base_address=BASE_ADDRESS)

# ── Quote APIs ────────────────────────────────────────────────────────────────
@app.route('/api/quotes', methods=['GET'])
@login_required
def api_get_quotes():
    q = load_json(QUOTES_FILE)
    q.sort(key=lambda x: x.get('created_at',''), reverse=True)
    return jsonify(q)

@app.route('/api/quotes', methods=['POST'])
@login_required
def api_create_quote():
    ensure_dirs()
    data = request.get_json()
    quotes = load_json(QUOTES_FILE)
    quote = {
        'id':             str(uuid.uuid4()),
        'quote_number':   next_quote_number(quotes),
        'created_at':     datetime.now().isoformat(),
        'customer_name':  data.get('customer_name',''),
        'mobile':         data.get('mobile',''),
        'address':        data.get('address',''),
        'distance_km':    data.get('distance_km', 0),
        'collection_fee': data.get('collection_fee', 0),
        'items':          data.get('items', []),
        'custom_items':   data.get('custom_items', []),
        'total':          data.get('total', 0),
        'notes':          data.get('notes',''),
        'photo':          data.get('photo',''),
        'status':         'pending',
    }
    quotes.insert(0, quote)
    save_json(QUOTES_FILE, quotes)
    return jsonify(quote), 201

@app.route('/api/quotes/<qid>', methods=['GET'])
@login_required
def api_get_quote(qid):
    quotes = load_json(QUOTES_FILE)
    q = next((x for x in quotes if x['id']==qid), None)
    return jsonify(q) if q else ('Not found', 404)

@app.route('/api/quotes/<qid>', methods=['PUT'])
@login_required
def api_update_quote(qid):
    quotes = load_json(QUOTES_FILE)
    idx = next((i for i,x in enumerate(quotes) if x['id']==qid), None)
    if idx is None: return ('Not found', 404)
    quotes[idx].update(request.get_json())
    save_json(QUOTES_FILE, quotes)
    return jsonify(quotes[idx])

@app.route('/api/quotes/<qid>/photo', methods=['POST'])
@login_required
def api_upload_photo(qid):
    ensure_dirs()
    if 'photo' not in request.files: return jsonify({'error':'No file'}), 400
    f = request.files['photo']
    if not allowed_file(f.filename): return jsonify({'error':'Invalid type'}), 400
    ext = f.filename.rsplit('.',1)[1].lower()
    fn  = f'{qid}.{ext}'
    f.save(os.path.join(PHOTOS_DIR, fn))
    quotes = load_json(QUOTES_FILE)
    idx = next((i for i,x in enumerate(quotes) if x['id']==qid), None)
    if idx is not None:
        quotes[idx]['photo'] = fn
        save_json(QUOTES_FILE, quotes)
    return jsonify({'photo': fn})

@app.route('/api/quotes/<qid>/photos/<photo_type>', methods=['POST'])
@login_required
def api_upload_job_photo(qid, photo_type):
    """photo_type: 'before' or 'after'"""
    ensure_dirs()
    if 'photo' not in request.files:
        return jsonify({'error': 'No file'}), 400
    f = request.files['photo']
    if not allowed_file(f.filename):
        return jsonify({'error': 'Invalid type'}), 400
    ext = f.filename.rsplit('.', 1)[1].lower()

    quotes = load_json(QUOTES_FILE)
    idx = next((i for i, x in enumerate(quotes) if x['id'] == qid), None)
    if idx is None:
        return ('Not found', 404)

    key = f'photos_{photo_type}'
    existing = quotes[idx].get(key, [])
    n = len(existing)
    fn = f'{qid}_{photo_type}_{n}.{ext}'
    f.save(os.path.join(PHOTOS_DIR, fn))
    existing.append(fn)
    quotes[idx][key] = existing
    save_json(QUOTES_FILE, quotes)
    return jsonify({'photo': fn, 'photos': existing})

@app.route('/quote-photos/<filename>')
@login_required
def quote_photo(filename):
    return send_from_directory(PHOTOS_DIR, filename)

# ── Invoice PDF ───────────────────────────────────────────────────────────────
def _build_invoice_pdf(q):
    buf = io.BytesIO()
    if not REPORTLAB:
        buf.write(b'ReportLab not installed')
        buf.seek(0)
        return buf

    W, H = A4
    M = 40
    GREEN = HexColor('#3d8b3d')
    DARK  = HexColor('#111111')
    GREY  = HexColor('#666666')

    c = rl_canvas.Canvas(buf, pagesize=A4)

    def txt(text, x, y, font='Helvetica', size=10, color=DARK, align='left'):
        c.setFont(font, size)
        c.setFillColor(color)
        if align == 'right':
            c.drawRightString(x, y, str(text))
        elif align == 'center':
            c.drawCentredString(x, y, str(text))
        else:
            c.drawString(x, y, str(text))

    y = H - M
    # Header bar
    c.setFillColor(GREEN)
    c.rect(0, H - 80, W, 80, fill=1, stroke=0)
    txt('RIDGELINE RUBBISH REMOVAL', M, H - 30, 'Helvetica-Bold', 18, HexColor('#ffffff'))
    txt('TAX INVOICE', W - M, H - 30, 'Helvetica-Bold', 18, HexColor('#ffffff'), 'right')
    txt('620 Merrylands Road, Greystanes NSW 2145', M, H - 52, 'Helvetica', 11, HexColor('#ccffcc'))
    txt(f"Quote: {q.get('quote_number','')}", W - M, H - 52, 'Helvetica', 11, HexColor('#ccffcc'), 'right')
    issued = q.get('created_at','')[:10]
    txt(f"Date: {issued}", W - M, H - 65, 'Helvetica', 10, HexColor('#ccffcc'), 'right')

    y = H - 100

    # Bill to
    txt('Bill To:', M, y, 'Helvetica-Bold', 10, GREY)
    y -= 14
    txt(q.get('customer_name', ''), M, y, 'Helvetica-Bold', 12, DARK)
    y -= 13
    txt(q.get('address', ''), M, y, 'Helvetica', 10, GREY)
    y -= 11
    txt(q.get('mobile', ''), M, y, 'Helvetica', 10, GREY)

    y -= 24
    # Line
    c.setStrokeColor(GREEN); c.setLineWidth(1)
    c.line(M, y, W - M, y)
    y -= 16

    # Table header
    c.setFillColor(HexColor('#f0f7f0'))
    c.rect(M, y - 4, W - 2*M, 18, fill=1, stroke=0)
    txt('Item', M + 4, y + 2, 'Helvetica-Bold', 10, DARK)
    txt('Qty', W - 160, y + 2, 'Helvetica-Bold', 10, DARK)
    txt('Unit Price', W - 110, y + 2, 'Helvetica-Bold', 10, DARK)
    txt('Amount', W - M - 4, y + 2, 'Helvetica-Bold', 10, DARK, 'right')
    y -= 20

    subtotal = 0
    # Standard items
    for item in q.get('items', []):
        name  = item.get('name', '')
        qty   = int(item.get('qty', 0))
        price = float(item.get('unit_price', 0))
        total = qty * price
        subtotal += total
        txt(name, M + 4, y, 'Helvetica', 10, DARK)
        txt(str(qty), W - 160, y, 'Helvetica', 10, DARK)
        txt(f'${price:.2f}', W - 110, y, 'Helvetica', 10, DARK)
        txt(f'${total:.2f}', W - M - 4, y, 'Helvetica', 10, DARK, 'right')
        y -= 16
        c.setStrokeColor(HexColor('#eeeeee')); c.setLineWidth(0.5)
        c.line(M, y + 4, W - M, y + 4)

    # Custom items
    for item in q.get('custom_items', []):
        desc   = item.get('description', '')
        amount = float(item.get('amount', 0))
        subtotal += amount
        txt(desc, M + 4, y, 'Helvetica', 10, DARK)
        txt('1', W - 160, y, 'Helvetica', 10, DARK)
        txt(f'${amount:.2f}', W - 110, y, 'Helvetica', 10, DARK)
        txt(f'${amount:.2f}', W - M - 4, y, 'Helvetica', 10, DARK, 'right')
        y -= 16
        c.setStrokeColor(HexColor('#eeeeee')); c.setLineWidth(0.5)
        c.line(M, y + 4, W - M, y + 4)

    # Collection fee
    cf = float(q.get('collection_fee', 0))
    if cf:
        subtotal += cf
        km = q.get('distance_km', 0)
        txt(f'Collection Fee ({km} km)', M + 4, y, 'Helvetica', 10, DARK)
        txt('1', W - 160, y, 'Helvetica', 10, DARK)
        txt(f'${cf:.2f}', W - 110, y, 'Helvetica', 10, DARK)
        txt(f'${cf:.2f}', W - M - 4, y, 'Helvetica', 10, DARK, 'right')
        y -= 16

    y -= 10
    c.setStrokeColor(GREEN); c.setLineWidth(1.5)
    c.line(W - 200, y, W - M, y)
    y -= 16

    txt('TOTAL', W - 200, y, 'Helvetica-Bold', 12, DARK)
    txt(f'${subtotal:.2f}', W - M - 4, y, 'Helvetica-Bold', 14, GREEN, 'right')

    # Payment method
    pm = q.get('payment_method', '')
    if pm:
        y -= 30
        txt(f'Payment Method: {pm}', M, y, 'Helvetica', 10, GREY)

    # Notes
    notes = q.get('notes', '')
    if notes:
        y -= 20
        txt('Notes:', M, y, 'Helvetica-Bold', 10, GREY)
        y -= 13
        txt(notes, M, y, 'Helvetica', 10, GREY)

    # Footer
    c.setFillColor(HexColor('#f5f5f5'))
    c.rect(0, 0, W, 50, fill=1, stroke=0)
    txt('Thank you for choosing Ridgeline Services', W/2, 30,
        'Helvetica-Bold', 10, GREEN, 'center')
    txt(f'{BUSINESS_PHONE}  |  {BASE_ADDRESS}', W/2, 16,
        'Helvetica', 9, GREY, 'center')

    c.save()
    buf.seek(0)
    return buf

@app.route('/api/quotes/<qid>/invoice')
@login_required
def api_invoice_pdf(qid):
    quotes = load_json(QUOTES_FILE)
    q = next((x for x in quotes if x['id'] == qid), None)
    if not q:
        return ('Not found', 404)
    buf = _build_invoice_pdf(q)
    qnum = q.get('quote_number', qid)
    return Response(buf, mimetype='application/pdf',
                    headers={'Content-Disposition': f'inline; filename=Invoice_{qnum}.pdf'})

@app.route('/api/quotes/<qid>/send-invoice', methods=['POST'])
@login_required
def api_send_invoice(qid):
    if not SMTP_PASS:
        return jsonify({'error': 'Email not configured. Add SMTP_USER and SMTP_PASS to Render env vars.'}), 500
    quotes = load_json(QUOTES_FILE)
    q = next((x for x in quotes if x['id'] == qid), None)
    if not q:
        return ('Not found', 404)

    data      = request.get_json() or {}
    recipient = data.get('email', '').strip()
    if not recipient:
        return jsonify({'error': 'No email address provided'}), 400

    try:
        pdf_buf  = _build_invoice_pdf(q)
        qnum     = q.get('quote_number', qid)
        msg      = MIMEMultipart()
        msg['From']    = f'Ridgeline Services <{SMTP_USER}>'
        msg['To']      = recipient
        msg['Subject'] = f'Invoice {qnum} — Ridgeline Services'

        total = q.get('total', 0)
        body  = (f"Hi {q.get('customer_name','')},\n\n"
                 f"Please find your invoice attached.\n\n"
                 f"Quote: {qnum}\n"
                 f"Address: {q.get('address','')}\n"
                 f"Total: ${total:.2f}\n\n"
                 f"Thank you for choosing Ridgeline Services!\n"
                 f"{BUSINESS_PHONE}  |  {BASE_ADDRESS}")
        msg.attach(MIMEText(body, 'plain'))

        pdf_part = MIMEApplication(pdf_buf.read(), _subtype='pdf')
        pdf_part.add_header('Content-Disposition', 'attachment', filename=f'Invoice_{qnum}.pdf')
        msg.attach(pdf_part)

        with smtplib.SMTP('smtp.gmail.com', 587, timeout=20) as srv:
            srv.ehlo(); srv.starttls()
            srv.login(SMTP_USER, SMTP_PASS)
            srv.send_message(msg)

        return jsonify({'sent': True, 'to': recipient})

    except smtplib.SMTPAuthenticationError:
        return jsonify({'error': 'Gmail login failed. Check SMTP_PASS is a valid App Password.'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ── KM fee API ────────────────────────────────────────────────────────────────
@app.route('/api/collection-fee')
@login_required
def api_collection_fee():
    try:
        km = float(request.args.get('km', 0))
        return jsonify({'fee': collection_fee_for_km(km), 'km': km})
    except:
        return jsonify({'fee': 0, 'km': 0})

# ── Expense APIs ──────────────────────────────────────────────────────────────
@app.route('/api/expenses', methods=['GET'])
@login_required
def api_get_expenses():
    return jsonify(load_json(EXPENSES_FILE))

@app.route('/api/expenses', methods=['POST'])
@login_required
def api_create_expense():
    ensure_dirs()
    data = request.get_json()
    expenses = load_json(EXPENSES_FILE)
    exp = {
        'id':          str(uuid.uuid4()),
        'created_at':  datetime.now().isoformat(),
        'date':        data.get('date', datetime.now().strftime('%Y-%m-%d')),
        'category':    data.get('category',''),
        'description': data.get('description',''),
        'amount':      float(data.get('amount', 0)),
    }
    expenses.insert(0, exp)
    save_json(EXPENSES_FILE, expenses)
    return jsonify(exp), 201

@app.route('/api/expenses/<eid>', methods=['DELETE'])
@login_required
def api_delete_expense(eid):
    expenses = load_json(EXPENSES_FILE)
    expenses = [e for e in expenses if e['id'] != eid]
    save_json(EXPENSES_FILE, expenses)
    return jsonify({'ok': True})

# ── Schedule API ─────────────────────────────────────────────────────────────
@app.route('/api/schedule')
@login_required
def api_schedule():
    date = request.args.get('date')
    truck_id = request.args.get('truck')
    quotes = load_json(QUOTES_FILE)
    result = [q for q in quotes if q.get('scheduled_date') == date]
    if truck_id:
        result = [q for q in result if q.get('truck_id') == truck_id]
    result.sort(key=lambda x: x.get('route_order', 999))
    return jsonify(result)

@app.route('/api/schedule/month')
@login_required
def api_schedule_month():
    """Return count of scheduled jobs per day for a given month (YYYY-MM)."""
    month = request.args.get('month', datetime.now().strftime('%Y-%m'))
    quotes = load_json(QUOTES_FILE)
    counts = {}
    for q in quotes:
        d = q.get('scheduled_date','')
        if d and d.startswith(month):
            counts[d] = counts.get(d, 0) + 1
    return jsonify(counts)

# ── Truck APIs ────────────────────────────────────────────────────────────────
DEFAULT_TRUCKS = [
    {'id': 'truck-1', 'name': 'Truck 1', 'color': '#3d8b3d'},
    {'id': 'truck-2', 'name': 'Truck 2', 'color': '#1565c0'},
]

@app.route('/api/trucks', methods=['GET'])
@login_required
def api_get_trucks():
    trucks = load_json(TRUCKS_FILE)
    if not trucks:
        trucks = DEFAULT_TRUCKS
        save_json(TRUCKS_FILE, trucks)
    return jsonify(trucks)

@app.route('/api/trucks', methods=['POST'])
@login_required
def api_create_truck():
    ensure_dirs()
    data   = request.get_json()
    trucks = load_json(TRUCKS_FILE) or DEFAULT_TRUCKS
    truck  = {
        'id':    str(uuid.uuid4()),
        'name':  data.get('name', 'New Truck'),
        'color': data.get('color', '#888888'),
    }
    trucks.append(truck)
    save_json(TRUCKS_FILE, trucks)
    return jsonify(truck), 201

@app.route('/api/trucks/<tid>', methods=['PUT'])
@login_required
def api_update_truck(tid):
    trucks = load_json(TRUCKS_FILE) or DEFAULT_TRUCKS
    idx = next((i for i,t in enumerate(trucks) if t['id']==tid), None)
    if idx is None: return ('Not found', 404)
    trucks[idx].update(request.get_json())
    save_json(TRUCKS_FILE, trucks)
    return jsonify(trucks[idx])

@app.route('/api/trucks/<tid>', methods=['DELETE'])
@login_required
def api_delete_truck(tid):
    trucks = load_json(TRUCKS_FILE) or DEFAULT_TRUCKS
    trucks = [t for t in trucks if t['id'] != tid]
    save_json(TRUCKS_FILE, trucks)
    return jsonify({'ok': True})

# ── Dashboard stats API ───────────────────────────────────────────────────────
@app.route('/api/stats')
@login_required
def api_stats():
    quotes   = load_json(QUOTES_FILE)
    expenses = load_json(EXPENSES_FILE)
    now = datetime.now()
    this_month = now.strftime('%Y-%m')
    mq = [q for q in quotes if q.get('created_at','').startswith(this_month)]
    me = [e for e in expenses if e.get('date','').startswith(this_month)]
    return jsonify({
        'total_quotes':    len(quotes),
        'month_quotes':    len(mq),
        'month_revenue':   sum(q.get('total',0) for q in mq),
        'month_expenses':  sum(e.get('amount',0) for e in me),
        'pending_quotes':  sum(1 for q in quotes if q.get('status')=='pending'),
        'accepted_quotes': sum(1 for q in quotes if q.get('status')=='accepted'),
    })

# ── Checklist Templates ───────────────────────────────────────────────────────
_DAILY_COMMON = [
    {'name': 'Tables, Chairs and Sofas', 'tasks': [
        'Tables, chairs and sofas wiped',
        'All corners wiped',
        'All stone benches including front counter and condiment bar wiped',
    ]},
    {'name': 'Bins', 'tasks': [
        'Bin housing wiped all sides',
        'Bin doors wiped all sides',
        'Bin washed when needed',
    ]},
    {'name': 'Toilets, Customer and Crew', 'tasks': [
        'Doors wiped both sides',
        'Wall tiles wiped',
        'Wooden walls wiped',
        'Sinks and under sinks wiped',
        'Toilet scrubbed and bowl wiped',
        'Brush and squeegee toilet mirrors',
        'Soap holder filled and wiped',
        'Toilet paper holder filled and wiped',
    ]},
]
_CARPARK = {'name': 'Carpark', 'tasks': [
    'All rubbish removed',
    'All bins wiped all sides including mouth spout',
    'All menu boards and directional signs wiped down',
    'Spider webs cleaned',
    'Drive-through scrubbed',
    'Carpark spills washed',
]}
_STAINLESS = {'name': 'Stainless Steel', 'tasks': [
    'Buffer used on all store stainless',
    'All toilet stainless buffed including door jambs',
    'All stainless legs buffed',
]}
_WALLS = {'name': 'Walls', 'tasks': [
    'All walls wiped, especially the bottom part',
    'Drive-through wall wiped',
]}
_WINDOWS = {'name': 'Windows', 'tasks': [
    'All windows cleaned both sides',
    'All window sills wiped with a cloth',
    'Toy display glass cleaned',
]}

SITES_FILE        = os.path.join(DATA_DIR, 'sites.json')
ARRIVALS_FILE     = os.path.join(DATA_DIR, 'arrivals.json')
CLIENTS_FILE      = os.path.join(DATA_DIR, 'clients.json')
SITES_CONFIG_FILE = os.path.join(DATA_DIR, 'sites_config.json')
CLIENT_LOGOS_DIR  = os.path.join(DATA_DIR, 'photos', 'client_logos')

PROOF_PHOTOS_STANDARD = [
    {'key': 'dining_in',  'label': 'Dining Area Inside'},
    {'key': 'bathroom',   'label': 'Bathroom'},
    {'key': 'dining_out', 'label': 'Dining Area Outside'},
]
PROOF_PHOTOS_KITCHEN = PROOF_PHOTOS_STANDARD + [
    {'key': 'floors',     'label': 'Floors'},
    {'key': 'equipment',  'label': 'Behind Equipment'},
]

_BASE_TEMPLATES = {
    'cambridge': {
        'name': "Cambridge Gardens McDonald's",
        'has_kitchen': True,
        'daily_sections': _DAILY_COMMON + [
            {'name': 'Dining and Patio', 'tasks': [
                'All corners swept',
                'Chairs moved and under swept',
                'All baseboards mopped',
                'Dining floor mopped',
                'Under all sofas and chairs mopped',
                'All door frames wiped',
                'Playlands cleaned',
                'Patio mopped',
            ]},
            _WINDOWS, _WALLS, _STAINLESS, _CARPARK,
            {'name': 'Corral', 'tasks': [
                'All rubbish removed',
                'Corral washed',
            ]},
            {'name': 'Kitchen Floor', 'tasks': [
                'All floors swept including under equipment',
                'Behind trolleys and fridges swept and mopped',
                'All main areas scrubbed including back corridor',
                'All drains washed',
            ]},
        ],
        'weekly_items': [
            {'task': 'Dining and patio floor scrubbed, including toilets',              'days': ['sun']},
            {'task': 'Windows cleaned to very top and behind playland equipment',       'days': ['mon','thu']},
            {'task': 'Corral scrubbed with hot water and degreaser',                   'days': ['sun','tue','thu','sat']},
            {'task': 'Crew room and crew toilets scrubbed',                             'days': ['wed']},
            {'task': 'All toilets fully bleached including crew toilets',               'days': ['wed']},
            {'task': 'Grills pulled',                                                   'days': ['sun','wed']},
            {'task': 'Vats pulled',                                                     'days': ['mon','thu']},
            {'task': 'All beverage cell machines pulled',                               'days': ['tue','fri']},
            {'task': 'All kitchen baseboards detailed with power pad or other means',   'days': ['sat']},
        ],
    },
    'blaxland': {
        'name': "Blaxland McDonald's",
        'has_kitchen': False,
        'daily_sections': _DAILY_COMMON + [
            {'name': 'Dining and Patio', 'tasks': [
                'All corners swept',
                'Chairs moved and under swept',
                'All baseboards mopped',
                'Dining floor mopped',
                'Under all sofas and chairs mopped',
                'All door frames wiped',
                'Patio mopped',
            ]},
            _WINDOWS, _WALLS, _STAINLESS, _CARPARK,
        ],
        'weekly_items': [
            {'task': 'Dining and patio floor scrubbed, including toilets',        'days': ['sun']},
            {'task': 'Windows cleaned to very top and behind playland equipment', 'days': ['mon','thu']},
            {'task': 'Crew room and crew toilets scrubbed',                       'days': ['wed']},
            {'task': 'All toilets fully bleached including crew toilets',         'days': ['wed']},
        ],
    },
}

# Day abbreviation used in templates ('sun','mon',…)
_DAY_NAMES = ['mon','tue','wed','thu','fri','sat','sun']  # weekday() 0=Mon…6=Sun

def _today_day_abbr():
    return _DAY_NAMES[datetime.now().weekday()]

def get_templates():
    """Return all site templates: hardcoded base sites + any user-added sites from sites_config.json."""
    try:
        with open(SITES_CONFIG_FILE) as f:
            scfg = json.load(f)
    except:
        scfg = {}

    result = {}
    # Start with base templates, applying any stored name/kitchen overrides
    for sid, t in _BASE_TEMPLATES.items():
        result[sid] = dict(t)
        if sid in scfg:
            result[sid]['name']        = scfg[sid].get('name',       t['name'])
            result[sid]['has_kitchen'] = scfg[sid].get('has_kitchen', t['has_kitchen'])

    # Add user-created sites (inherit tasks from matching base template)
    for sid, cfg in scfg.items():
        if sid not in _BASE_TEMPLATES:
            src = _BASE_TEMPLATES['cambridge'] if cfg.get('has_kitchen') else _BASE_TEMPLATES['blaxland']
            result[sid] = {
                'name':           cfg['name'],
                'has_kitchen':    cfg.get('has_kitchen', False),
                'daily_sections': src['daily_sections'],
                'weekly_items':   src['weekly_items'],
            }

    return result

# ── Employee auth ─────────────────────────────────────────────────────────────
def employee_or_admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('employee_id') and not session.get('logged_in'):
            return redirect(url_for('checklist_login'))
        return f(*args, **kwargs)
    return decorated

# ── Employees routes ──────────────────────────────────────────────────────────
@app.route('/employees')
@login_required
def employees_page():
    return render_template('employees.html')

@app.route('/monitor')
@login_required
def monitor_page():
    return render_template('monitor.html', google_maps_key=GOOGLE_MAPS)

# ── Checklist routes ──────────────────────────────────────────────────────────
@app.route('/checklist/login', methods=['GET','POST'])
def checklist_login():
    if request.method == 'POST':
        data = request.get_json(force=True, silent=True) or {}
        emp_id  = data.get('id','')
        pin     = str(data.get('pin','')).strip()
        emps    = load_json(EMPLOYEES_FILE)
        emp     = next((e for e in emps if e['id']==emp_id and str(e.get('pin',''))==pin and e.get('active',True)), None)
        if emp:
            session['employee_id']   = emp['id']
            session['employee_name'] = emp['name']
            return jsonify({'ok': True, 'name': emp['name']})
        return jsonify({'error': 'Wrong PIN'}), 401
    return render_template('checklist.html',
        templates=get_templates(),
        today=datetime.now().strftime('%Y-%m-%d'),
        day=_today_day_abbr(),
        session_emp_id=session.get('employee_id',''),
        session_emp_name=session.get('employee_name','')
    )

# ── Staff login (separate simple page, form-POST — no JSON/fetch) ─────────────
@app.route('/staff', methods=['GET','POST'])
def staff_login():
    error = ''
    if request.method == 'POST':
        emp_id = request.form.get('emp_id','').strip()
        pin    = request.form.get('pin','').strip()
        emps   = load_json(EMPLOYEES_FILE)
        emp    = next((e for e in emps if e['id']==emp_id and str(e.get('pin',''))==pin and e.get('active',True)), None)
        if emp:
            session['employee_id']   = emp['id']
            session['employee_name'] = emp['name']
            return redirect(url_for('checklist_home'))
        error = 'Wrong PIN — please try again'
    active_emps = [e for e in load_json(EMPLOYEES_FILE) if e.get('active', True)]
    return render_template('staff_login.html', employees=active_emps, error=error)

@app.route('/api/checklist/session')
def api_checklist_session():
    if session.get('employee_id'):
        return jsonify({'employee_id': session['employee_id'], 'employee_name': session.get('employee_name','')})
    return jsonify({})

@app.route('/checklist')
def checklist_home():
    return render_template('checklist.html',
        templates=get_templates(),
        today=datetime.now().strftime('%Y-%m-%d'),
        day=_today_day_abbr(),
        session_emp_id=session.get('employee_id',''),
        session_emp_name=session.get('employee_name','')
    )

@app.route('/checklist/logout')
def checklist_logout():
    session.pop('employee_id', None)
    session.pop('employee_name', None)
    return redirect(url_for('checklist_home'))

@app.route('/checklists')
@login_required
def checklists_page():
    return render_template('checklists.html')

# ── Employee APIs ─────────────────────────────────────────────────────────────
@app.route('/api/employees', methods=['GET'])
def api_get_employees():
    emps = load_json(EMPLOYEES_FILE)
    # Public endpoint: strip PINs unless admin
    if session.get('logged_in'):
        return jsonify(emps)
    return jsonify([{'id': e['id'], 'name': e['name'], 'active': e.get('active', True)} for e in emps if e.get('active', True)])

@app.route('/api/employees', methods=['POST'])
@login_required
def api_create_employee():
    ensure_dirs()
    data = request.get_json()
    emps = load_json(EMPLOYEES_FILE)
    emp = {
        'id':     str(uuid.uuid4()),
        'name':   data.get('name','').strip(),
        'pin':    data.get('pin','').strip(),
        'mobile': data.get('mobile','').strip(),
        'active': True,
    }
    emps.append(emp)
    save_json(EMPLOYEES_FILE, emps)
    return jsonify(emp), 201

@app.route('/api/employees/<eid>', methods=['PUT'])
@login_required
def api_update_employee(eid):
    emps = load_json(EMPLOYEES_FILE)
    idx  = next((i for i,e in enumerate(emps) if e['id']==eid), None)
    if idx is None: return ('Not found', 404)
    data = request.get_json()
    # Only update known fields to avoid overwriting photo accidentally
    for key in ('name','pin','mobile','active'):
        if key in data:
            emps[idx][key] = data[key]
    save_json(EMPLOYEES_FILE, emps)
    return jsonify(emps[idx])

@app.route('/api/employees/<eid>', methods=['DELETE'])
@login_required
def api_delete_employee(eid):
    emps = load_json(EMPLOYEES_FILE)
    emps = [e for e in emps if e['id'] != eid]
    save_json(EMPLOYEES_FILE, emps)
    return jsonify({'ok': True})

@app.route('/api/employees/<eid>/photo', methods=['POST'])
@login_required
def api_upload_emp_photo(eid):
    ensure_dirs()
    f = request.files.get('photo')
    if not f or not allowed_file(f.filename):
        return ('Bad file', 400)
    ext = f.filename.rsplit('.',1)[1].lower()
    fn  = f'emp_{eid}.{ext}'
    f.save(os.path.join(EMP_PHOTOS_DIR, fn))
    emps = load_json(EMPLOYEES_FILE)
    idx  = next((i for i,e in enumerate(emps) if e['id']==eid), None)
    if idx is not None:
        emps[idx]['photo'] = fn
        save_json(EMPLOYEES_FILE, emps)
    return jsonify({'photo': fn})

@app.route('/emp-photos/<filename>')
def serve_emp_photo(filename):
    return send_from_directory(EMP_PHOTOS_DIR, filename)

# ── Checklist template API ────────────────────────────────────────────────────
@app.route('/api/checklist/template/<site_id>')
def api_checklist_template(site_id):
    t = get_templates().get(site_id)
    if not t: return ('Not found', 404)
    day = request.args.get('day', _today_day_abbr())
    weekly_today = [w['task'] for w in t['weekly_items'] if day in w['days']]
    has_kitchen = t.get('has_kitchen', False)
    return jsonify({
        'site_id':        site_id,
        'site_name':      t['name'],
        'has_kitchen':    has_kitchen,
        'daily_sections': t['daily_sections'],
        'weekly_today':   weekly_today,
        'day':            day,
        'proof_photos':   PROOF_PHOTOS_KITCHEN if has_kitchen else PROOF_PHOTOS_STANDARD,
    })

# ── Site config helpers ───────────────────────────────────────────────────────
def load_all_site_configs():
    try:
        with open(SITES_FILE) as f: return json.load(f)
    except: return {}

def save_all_site_configs(configs):
    with open(SITES_FILE, 'w') as f: json.dump(configs, f, indent=2)

def site_config(site_id):
    return load_all_site_configs().get(site_id, {
        'assigned_days': _DAY_NAMES,
        'assigned_employees': [],
        'enabled': True,
    })

# ── Site APIs ─────────────────────────────────────────────────────────────────
@app.route('/api/sites', methods=['GET'])
@login_required
def api_get_sites():
    all_cfg = load_all_site_configs()
    try:
        with open(SITES_CONFIG_FILE) as f: scfg = json.load(f)
    except: scfg = {}
    result = []
    for sid, t in get_templates().items():
        cfg = all_cfg.get(sid, {'assigned_days': _DAY_NAMES, 'assigned_employees': [], 'enabled': True})
        sc  = scfg.get(sid, {})
        result.append({
            'id':          sid,
            'name':        t['name'],
            'has_kitchen': t.get('has_kitchen', False),
            'is_builtin':  sid in _BASE_TEMPLATES,
            'address':     sc.get('address', ''),
            'start_time':  sc.get('start_time', ''),
            **cfg,
        })
    return jsonify(result)

@app.route('/api/sites', methods=['POST'])
@login_required
def api_create_site():
    data     = request.get_json() or {}
    name     = data.get('name','').strip()
    if not name: return jsonify({'error': 'Name required'}), 400
    sid      = re.sub(r'[^a-z0-9]+', '_', name.lower()).strip('_') or 'site'
    templates = get_templates()
    base_sid, counter = sid, 2
    while sid in templates:
        sid = f"{base_sid}_{counter}"; counter += 1
    has_kitchen = bool(data.get('has_kitchen', False))
    try:
        with open(SITES_CONFIG_FILE) as f: scfg = json.load(f)
    except: scfg = {}
    scfg[sid] = {'name': name, 'has_kitchen': has_kitchen}
    with open(SITES_CONFIG_FILE, 'w') as f: json.dump(scfg, f, indent=2)
    return jsonify({'id': sid, 'name': name, 'has_kitchen': has_kitchen, 'is_builtin': False}), 201

@app.route('/api/sites/<site_id>', methods=['PUT'])
@login_required
def api_update_site(site_id):
    if site_id not in get_templates(): return ('Not found', 404)
    data = dict(request.get_json() or {})
    # Save name/has_kitchen/start_time/address to sites_config.json if present
    site_meta = {}
    for key in ('name', 'has_kitchen', 'start_time', 'address'):
        if key in data: site_meta[key] = data.pop(key)
    if site_meta:
        try:
            with open(SITES_CONFIG_FILE) as f: scfg = json.load(f)
        except: scfg = {}
        if site_id not in scfg: scfg[site_id] = {}
        scfg[site_id].update(site_meta)
        with open(SITES_CONFIG_FILE, 'w') as f: json.dump(scfg, f, indent=2)
    # Save schedule config (assigned_days, assigned_employees, enabled)
    if data:
        cfg = load_all_site_configs()
        if site_id not in cfg: cfg[site_id] = {}
        cfg[site_id].update(data)
        save_all_site_configs(cfg)
    return jsonify({'ok': True})

@app.route('/api/sites/<site_id>', methods=['DELETE'])
@login_required
def api_delete_site(site_id):
    if site_id in _BASE_TEMPLATES:
        return jsonify({'error': 'Cannot delete built-in sites'}), 400
    try:
        with open(SITES_CONFIG_FILE) as f: scfg = json.load(f)
    except: scfg = {}
    scfg.pop(site_id, None)
    with open(SITES_CONFIG_FILE, 'w') as f: json.dump(scfg, f, indent=2)
    all_cfg = load_all_site_configs()
    all_cfg.pop(site_id, None)
    save_all_site_configs(all_cfg)
    return jsonify({'ok': True})

@app.route('/api/sites/today')
def api_sites_today():
    day    = _today_day_abbr()
    emp_id = session.get('employee_id')
    all_cfg = load_all_site_configs()
    result = []
    for sid, t in get_templates().items():
        cfg = all_cfg.get(sid, {'assigned_days': _DAY_NAMES, 'assigned_employees': [], 'enabled': True})
        if not cfg.get('enabled', True): continue
        if day not in cfg.get('assigned_days', _DAY_NAMES): continue
        assigned = cfg.get('assigned_employees', [])
        if assigned and emp_id and emp_id not in assigned: continue
        result.append({'id': sid, 'name': t['name'], 'has_kitchen': t.get('has_kitchen', False)})
    return jsonify(result)

@app.route('/api/sites/week')
def api_sites_week():
    emp_id  = session.get('employee_id')
    all_cfg = load_all_site_configs()
    week = {}
    for day in _DAY_NAMES:
        sites = []
        for sid, t in get_templates().items():
            cfg = all_cfg.get(sid, {'assigned_days': _DAY_NAMES, 'assigned_employees': [], 'enabled': True})
            if not cfg.get('enabled', True): continue
            if day not in cfg.get('assigned_days', _DAY_NAMES): continue
            assigned = cfg.get('assigned_employees', [])
            if assigned and emp_id and emp_id not in assigned: continue
            sites.append({'id': sid, 'name': t['name']})
        week[day] = sites
    return jsonify(week)

# ── Session check for employee ─────────────────────────────────────────────────
@app.route('/api/checklist/session')
def api_checklist_session():
    if session.get('employee_id'):
        return jsonify({'employee_id': session['employee_id'], 'employee_name': session['employee_name']})
    if session.get('logged_in'):
        return jsonify({'employee_id': 'admin', 'employee_name': 'Admin'})
    return jsonify({}), 200

# ── Arrival photo ─────────────────────────────────────────────────────────────
@app.route('/api/arrivals', methods=['POST'])
def api_post_arrival():
    if not session.get('employee_id') and not session.get('logged_in'):
        return jsonify({'error': 'Not authenticated'}), 401
    ensure_dirs()
    record = {
        'id':            str(uuid.uuid4()),
        'employee_id':   session.get('employee_id',''),
        'employee_name': session.get('employee_name','Admin'),
        'site_id':       request.form.get('site_id',''),
        'site_name':     get_templates().get(request.form.get('site_id',''), {}).get('name',''),
        'arrived_at':    datetime.now().isoformat(),
        'lat':           request.form.get('lat',''),
        'lng':           request.form.get('lng',''),
        'accuracy':      request.form.get('accuracy',''),
        'photo':         '',
    }
    if 'photo' in request.files:
        f = request.files['photo']
        if f and allowed_file(f.filename):
            arr_dir = os.path.join(PHOTOS_DIR, 'arrivals')
            os.makedirs(arr_dir, exist_ok=True)
            ext = f.filename.rsplit('.',1)[1].lower() if '.' in f.filename else 'jpg'
            fn  = f"{record['id']}.{ext}"
            f.save(os.path.join(arr_dir, fn))
            record['photo'] = fn
    arrivals = load_json(ARRIVALS_FILE)
    arrivals.insert(0, record)
    save_json(ARRIVALS_FILE, arrivals)
    return jsonify({'id': record['id'], 'ok': True}), 201

@app.route('/arrival-photos/<filename>')
@login_required
def arrival_photo_serve(filename):
    return send_from_directory(os.path.join(PHOTOS_DIR, 'arrivals'), filename)

@app.route('/api/employee/<eid>/today')
@login_required
def api_employee_today(eid):
    """Returns today's activity for one employee: sites, arrival photo, proof photos."""
    today      = datetime.now().strftime('%Y-%m-%d')
    today_abbr = datetime.now().strftime('%a').lower()
    templates  = get_templates()
    all_cfg    = load_all_site_configs()

    # Arrivals today for this employee
    arrivals = [a for a in load_json(ARRIVALS_FILE)
                if a.get('employee_id') == eid and a.get('arrived_at','').startswith(today)]

    # Checklists today for this employee
    checklists = [c for c in load_json(CHECKLISTS_FILE)
                  if c.get('employee_id') == eid and c.get('date','') == today]

    # Build site activity list
    site_ids = set(a['site_id'] for a in arrivals) | set(c['site_id'] for c in checklists)
    sites_out = []
    for sid in site_ids:
        tmpl     = templates.get(sid, {})
        arrival  = next((a for a in arrivals if a['site_id'] == sid), None)
        cl       = next((c for c in checklists if c['site_id'] == sid), None)
        proof    = []
        if cl and cl.get('proof_photos'):
            for key, fn in cl['proof_photos'].items():
                proof.append({'key': key, 'url': f'/proof-photos/{fn}'})
        sites_out.append({
            'site_id':      sid,
            'site_name':    tmpl.get('name', sid),
            'arrival_time': arrival['arrived_at'] if arrival else '',
            'arrival_photo': f"/arrival-photos/{arrival['photo']}" if arrival and arrival.get('photo') else '',
            'arrival_lat':  arrival.get('lat','') if arrival else '',
            'arrival_lng':  arrival.get('lng','') if arrival else '',
            'checklist_id': cl['id'] if cl else '',
            'done_tasks':   cl.get('done_tasks', 0) if cl else 0,
            'total_tasks':  cl.get('total_tasks', 0) if cl else 0,
            'proof_photos': proof,
            'submitted':    bool(cl and cl.get('submitted_at')),
        })

    employee = next((e for e in load_json(EMPLOYEES_FILE) if e['id'] == eid), {})
    photo_fn = employee.get('photo','')
    return jsonify({
        'id':     eid,
        'name':   employee.get('name', eid),
        'mobile': employee.get('mobile',''),
        'photo':  f'/emp-photos/{photo_fn}' if photo_fn else '',
        'sites':  sites_out,
    })

# ── Proof photo upload ────────────────────────────────────────────────────────
@app.route('/api/checklist/<cid>/proof/<photo_key>', methods=['POST'])
def api_upload_proof(cid, photo_key):
    if not session.get('employee_id') and not session.get('logged_in'):
        return jsonify({'error': 'Not authenticated'}), 401
    ensure_dirs()
    if 'photo' not in request.files: return jsonify({'error':'No file'}), 400
    f = request.files['photo']
    if not allowed_file(f.filename): return jsonify({'error':'Invalid type'}), 400
    ext = f.filename.rsplit('.',1)[1].lower() if '.' in f.filename else 'jpg'
    proof_dir = os.path.join(PHOTOS_DIR, 'proofs')
    os.makedirs(proof_dir, exist_ok=True)
    fn = f'{cid}_{photo_key}.{ext}'
    f.save(os.path.join(proof_dir, fn))
    checklists = load_json(CHECKLISTS_FILE)
    idx = next((i for i,x in enumerate(checklists) if x['id']==cid), None)
    if idx is not None:
        if 'proof_photos' not in checklists[idx]: checklists[idx]['proof_photos'] = {}
        checklists[idx]['proof_photos'][photo_key] = fn
        save_json(CHECKLISTS_FILE, checklists)
    return jsonify({'photo': fn})

@app.route('/proof-photos/<filename>')
@login_required
def proof_photo_serve(filename):
    return send_from_directory(os.path.join(PHOTOS_DIR, 'proofs'), filename)

# ── Checklist submit API ──────────────────────────────────────────────────────
@app.route('/api/checklist', methods=['POST'])
def api_submit_checklist():
    if not session.get('employee_id') and not session.get('logged_in'):
        return jsonify({'error': 'Not authenticated'}), 401
    ensure_dirs()
    data = request.get_json()
    record = {
        'id':            str(uuid.uuid4()),
        'site_id':       data.get('site_id',''),
        'site_name':     data.get('site_name',''),
        'date':          data.get('date', datetime.now().strftime('%Y-%m-%d')),
        'day':           data.get('day',''),
        'employee_id':   session.get('employee_id','admin'),
        'employee_name': session.get('employee_name', 'Admin'),
        'submitted_at':  datetime.now().isoformat(),
        'daily_items':   data.get('daily_items', {}),
        'weekly_items':  data.get('weekly_items', {}),
        'notes':            data.get('notes',''),
        'arrival_id':       data.get('arrival_id',''),
        'manager_name':     data.get('manager_name',''),
        'manager_signature': data.get('manager_signature',''),  # base64 PNG data URL
        'proof_photos':     {},
    }
    # Count totals
    total = done = 0
    for tasks in record['daily_items'].values():
        for v in tasks.values():
            total += 1
            if v: done += 1
    for v in record['weekly_items'].values():
        total += 1
        if v: done += 1
    record['total_tasks'] = total
    record['done_tasks']  = done

    checklists = load_json(CHECKLISTS_FILE)
    checklists.insert(0, record)
    save_json(CHECKLISTS_FILE, checklists)
    return jsonify({'id': record['id'], 'done': done, 'total': total}), 201

@app.route('/api/checklist', methods=['GET'])
@login_required
def api_get_checklists():
    cls  = load_json(CHECKLISTS_FILE)
    site = request.args.get('site')
    date = request.args.get('date')
    emp  = request.args.get('employee')
    if site: cls = [c for c in cls if c.get('site_id') == site]
    if date: cls = [c for c in cls if c.get('date') == date]
    if emp:  cls = [c for c in cls if c.get('employee_name','').lower().find(emp.lower()) >= 0]
    return jsonify(cls)

# ── Checklist PDF ─────────────────────────────────────────────────────────────
def _build_checklist_pdf(cl, hide_employee=False, include_photos=False):
    buf = io.BytesIO()
    if not REPORTLAB:
        buf.write(b'ReportLab not installed')
        buf.seek(0)
        return buf

    W, H = A4
    M    = 40
    GREEN = HexColor('#3d8b3d')
    BLUE  = HexColor('#1565c0')
    DARK  = HexColor('#111111')
    GREY  = HexColor('#888888')
    LGREY = HexColor('#cccccc')
    DKGRN = HexColor('#2e7d32')
    RED   = HexColor('#b71c1c')

    c = rl_canvas.Canvas(buf, pagesize=A4)
    page_y = [H]  # mutable for nested function

    # McDonald's logo path (for client PDFs)
    _mcdo_logo = os.path.join(os.path.dirname(__file__), 'static', 'mcdonalds_logo.jpg')

    def draw_header_bar():
        c.setFillColor(GREEN)
        c.rect(0, H - 76, W, 76, fill=1, stroke=0)
        c.setFont('Helvetica-Bold', 16)
        c.setFillColor(HexColor('#ffffff'))
        c.drawString(M, H - 28, 'RIDGELINE SERVICES — CLEANING CHECKLIST')
        c.setFont('Helvetica-Bold', 11)
        c.drawString(M, H - 46, cl.get('site_name',''))
        # McDonald's logo on client PDFs
        if hide_employee and os.path.exists(_mcdo_logo):
            try:
                logo_img = ImageReader(_mcdo_logo)
                c.drawImage(logo_img, W - M - 56, H - 70, 56, 40,
                            preserveAspectRatio=True, mask='auto')
            except Exception:
                pass
        c.setFont('Helvetica', 9)
        c.setFillColor(HexColor('#ccffcc'))
        emp_label = "Staff Member" if hide_employee else cl.get('employee_name','')
        info = f"Date: {cl.get('date','')}  |  Day: {cl.get('day','').capitalize()}  |  Completed by: {emp_label}"
        c.drawString(M, H - 62, info)
        done  = cl.get('done_tasks', 0)
        total = cl.get('total_tasks', 0)
        pct   = f"{done}/{total} tasks complete"
        c.drawRightString(W - M, H - 46, pct)

    def draw_footer():
        c.setFillColor(HexColor('#f5f5f5'))
        c.rect(0, 0, W, 36, fill=1, stroke=0)
        c.setFont('Helvetica', 9)
        c.setFillColor(GREEN)
        c.drawCentredString(W/2, 14, 'Ridgeline Services  ·  0458 749 281  ·  Tick each item on completion')

    def new_page():
        draw_footer()
        c.showPage()
        draw_header_bar()
        return H - 90

    def chk(x, y, done):
        size = 9
        if done:
            c.setFillColor(DKGRN); c.setStrokeColor(DKGRN)
            c.rect(x, y - 1, size, size, fill=1, stroke=0)
            c.setStrokeColor(HexColor('#ffffff')); c.setLineWidth(1.3)
            c.line(x+1, y+3, x+3.5, y+6)
            c.line(x+3.5, y+6, x+8, y-1)
        else:
            c.setFillColor(HexColor('#ffffff')); c.setStrokeColor(LGREY)
            c.setLineWidth(0.8)
            c.rect(x, y - 1, size, size, fill=1, stroke=1)

    draw_header_bar()
    y = H - 90

    # Daily sections
    for section_name, tasks in cl.get('daily_items', {}).items():
        if y < 100:
            y = new_page()
        # Section header
        c.setFillColor(GREEN)
        c.rect(M, y - 3, W - 2*M, 15, fill=1, stroke=0)
        c.setFont('Helvetica-Bold', 8)
        c.setFillColor(HexColor('#ffffff'))
        c.drawString(M + 4, y, section_name.upper())
        y -= 19

        for task, done in tasks.items():
            if y < 55:
                y = new_page()
            chk(M + 2, y, done)
            c.setFont('Helvetica', 9)
            c.setFillColor(DARK)
            c.drawString(M + 16, y, task)
            y -= 13
            c.setStrokeColor(HexColor('#eeeeee')); c.setLineWidth(0.3)
            c.line(M, y + 5, W - M, y + 5)
        y -= 4

    # Weekly items
    weekly = cl.get('weekly_items', {})
    if weekly:
        if y < 80:
            y = new_page()
        y -= 4
        c.setFillColor(BLUE)
        c.rect(M, y - 3, W - 2*M, 15, fill=1, stroke=0)
        c.setFont('Helvetica-Bold', 8)
        c.setFillColor(HexColor('#ffffff'))
        c.drawString(M + 4, y, f"WEEKLY ITEMS — {cl.get('day','').upper()}")
        y -= 19

        for task, done in weekly.items():
            if y < 55:
                y = new_page()
            chk(M + 2, y, done)
            c.setFont('Helvetica', 9)
            c.setFillColor(DARK)
            c.drawString(M + 16, y, task)
            y -= 13
            c.setStrokeColor(HexColor('#eeeeee')); c.setLineWidth(0.3)
            c.line(M, y + 5, W - M, y + 5)

    # Proof photos (client PDF only)
    if include_photos:
        proof_photos = cl.get('proof_photos', {})
        proof_dir = os.path.join(PHOTOS_DIR, 'proofs')
        if proof_photos:
            if y < 80:
                y = new_page()
            y -= 6
            c.setFillColor(HexColor('#4a148c'))
            c.rect(M, y - 3, W - 2*M, 15, fill=1, stroke=0)
            c.setFont('Helvetica-Bold', 8)
            c.setFillColor(HexColor('#ffffff'))
            c.drawString(M + 4, y, 'PROOF PHOTOS')
            y -= 20

            photo_w = (W - 2*M - 20) / 2
            photo_h = photo_w * 0.65
            col = 0
            x_positions = [M, M + photo_w + 20]
            # Build label map from templates
            has_kitchen = get_templates().get(cl.get('site_id',''), {}).get('has_kitchen', False)
            proof_defs  = PROOF_PHOTOS_KITCHEN if has_kitchen else PROOF_PHOTOS_STANDARD
            label_map   = {p['key']: p['label'] for p in proof_defs}

            for key, fn in proof_photos.items():
                img_path = os.path.join(proof_dir, fn)
                if not os.path.exists(img_path):
                    continue
                if y - photo_h < 55:
                    y = new_page()
                px = x_positions[col]
                try:
                    img_obj = ImageReader(img_path)
                    c.setFillColor(HexColor('#f5f5f5'))
                    c.rect(px, y - photo_h, photo_w, photo_h, fill=1, stroke=0)
                    c.drawImage(img_obj, px, y - photo_h, photo_w, photo_h,
                                preserveAspectRatio=True, mask='auto')
                except Exception:
                    c.setStrokeColor(LGREY); c.setLineWidth(0.5)
                    c.rect(px, y - photo_h, photo_w, photo_h, fill=0, stroke=1)
                lbl = label_map.get(key, key.replace('_',' ').title())
                c.setFont('Helvetica-Bold', 8)
                c.setFillColor(DARK)
                c.drawString(px, y - photo_h - 10, lbl)
                col += 1
                if col >= 2:
                    col = 0
                    y -= photo_h + 22
            if col == 1:
                y -= photo_h + 22
            y -= 6

    # Manager sign-off block
    if y < 140:
        y = new_page()
    y -= 10

    # Sign-off header
    c.setFillColor(HexColor('#1a237e'))
    c.rect(M, y - 3, W - 2*M, 15, fill=1, stroke=0)
    c.setFont('Helvetica-Bold', 8)
    c.setFillColor(HexColor('#ffffff'))
    c.drawString(M + 4, y, 'MANAGER SIGN-OFF')
    y -= 22

    manager_name = cl.get('manager_name', '')
    sig_b64      = cl.get('manager_signature', '')

    # Manager name line
    c.setStrokeColor(HexColor('#cccccc')); c.setLineWidth(0.5)
    c.line(M, y, M + 240, y)
    c.setFont('Helvetica', 9); c.setFillColor(GREY)
    c.drawString(M, y - 12, f"Manager Name: {manager_name}" if manager_name else "Manager Name: ___________________________")
    submitted = cl.get('submitted_at','')[:19].replace('T',' ')
    c.setFont('Helvetica', 8)
    c.drawRightString(W - M, y - 12, f"Signed: {submitted}" if manager_name else '')
    y -= 28

    # Signature image or blank box
    sig_box_w = 220; sig_box_h = 70
    if sig_b64 and REPORTLAB:
        try:
            raw = sig_b64.split(',')[1] if ',' in sig_b64 else sig_b64
            sig_bytes = _b64.b64decode(raw)
            sig_buf   = io.BytesIO(sig_bytes)
            sig_img   = ImageReader(sig_buf)
            # White background box
            c.setFillColor(HexColor('#ffffff'))
            c.rect(M, y - sig_box_h, sig_box_w, sig_box_h, fill=1, stroke=0)
            c.drawImage(sig_img, M, y - sig_box_h, sig_box_w, sig_box_h,
                        mask='auto', preserveAspectRatio=True)
        except Exception:
            c.setStrokeColor(LGREY); c.setLineWidth(0.5)
            c.rect(M, y - sig_box_h, sig_box_w, sig_box_h, fill=0, stroke=1)
    else:
        c.setStrokeColor(LGREY); c.setLineWidth(0.5)
        c.rect(M, y - sig_box_h, sig_box_w, sig_box_h, fill=0, stroke=1)
    c.setFont('Helvetica', 8); c.setFillColor(GREY)
    c.drawString(M, y - sig_box_h - 10, "Signature")
    y -= sig_box_h + 20

    # Legacy plain signature line (kept for spacing / manager reference)
    if y < 50:
        y = new_page()
    c.setFont('Helvetica', 8); c.setFillColor(GREY)
    c.drawRightString(W - M, y, f"Submitted: {submitted}")

    draw_footer()
    c.save()
    buf.seek(0)
    return buf

@app.route('/api/checklist/<cid>/pdf')
@login_required
def api_checklist_pdf(cid):
    cls = load_json(CHECKLISTS_FILE)
    cl  = next((x for x in cls if x['id'] == cid), None)
    if not cl: return ('Not found', 404)
    buf = _build_checklist_pdf(cl)
    fn  = f"Checklist_{cl.get('site_id','')}_{cl.get('date','')}.pdf"
    return Response(buf, mimetype='application/pdf',
                    headers={'Content-Disposition': f'inline; filename={fn}'})

# ── Client auth ───────────────────────────────────────────────────────────────
@app.route('/client/login', methods=['GET','POST'])
def client_login():
    error = None
    if request.method == 'POST':
        username = request.form.get('username','').strip().lower()
        password = request.form.get('password','')
        clients  = load_json(CLIENTS_FILE)
        client   = next((c for c in clients
                         if c.get('username','').lower() == username
                         and c.get('password','') == password
                         and c.get('active', True)), None)
        if client:
            session.permanent = True
            session['client_id']    = client['id']
            session['client_name']  = client['name']
            session['client_sites'] = client.get('sites', [])
            logo = client.get('logo', '')
            session['client_logo']  = f'/client-logos/{logo}' if logo else ''
            return redirect(url_for('client_dashboard'))
        error = 'Invalid username or password'
    return render_template('client_login.html', error=error)

@app.route('/client/logout')
def client_logout():
    session.pop('client_id', None)
    session.pop('client_name', None)
    session.pop('client_sites', None)
    session.pop('client_logo', None)
    return redirect(url_for('client_login'))

@app.route('/client')
@client_required
def client_dashboard():
    return render_template('client.html',
                           client_logo=session.get('client_logo',''),
                           client_name=session.get('client_name',''))

@app.route('/client-logos/<filename>')
def serve_client_logo(filename):
    return send_from_directory(CLIENT_LOGOS_DIR, filename)

# ── Client stats API ──────────────────────────────────────────────────────────
@app.route('/api/client/stats')
@client_required
def api_client_stats():
    from datetime import timedelta
    today      = datetime.now()
    week_start = today - timedelta(days=today.weekday())
    week_end   = week_start + timedelta(days=6)
    ws = week_start.strftime('%Y-%m-%d')
    we = week_end.strftime('%Y-%m-%d')

    client_sites = session.get('client_sites', [])
    cls = load_json(CHECKLISTS_FILE)
    if client_sites:
        cls = [c for c in cls if c.get('site_id') in client_sites]

    week_cls = [c for c in cls if ws <= c.get('date','') <= we]
    pcts     = [c['done_tasks'] / c['total_tasks'] * 100
                for c in week_cls if c.get('total_tasks', 0) > 0]
    weekly_pct   = round(sum(pcts) / len(pcts)) if pcts else 0
    weekly_count = len(week_cls)

    all_sites     = [(sid, t) for sid, t in get_templates().items()
                     if not client_sites or sid in client_sites]
    total_sites   = len(all_sites)
    kitchen_sites = sum(1 for _, t in all_sites if t.get('has_kitchen', False))
    regular_sites = total_sites - kitchen_sites

    return jsonify({
        'weekly_count':  weekly_count,
        'weekly_pct':    weekly_pct,
        'total_sites':   total_sites,
        'regular_sites': regular_sites,
        'kitchen_sites': kitchen_sites,
        'week_start':    ws,
        'week_end':      we,
    })

# ── Client checklists API (no employee names) ─────────────────────────────────
@app.route('/api/client/checklists')
@client_required
def api_client_checklists():
    client_sites = session.get('client_sites', [])
    cls = load_json(CHECKLISTS_FILE)
    if client_sites:
        cls = [c for c in cls if c.get('site_id') in client_sites]

    site  = request.args.get('site')
    date  = request.args.get('date')
    month = request.args.get('month')
    if site:  cls = [c for c in cls if c.get('site_id') == site]
    if date:  cls = [c for c in cls if c.get('date') == date]
    if month: cls = [c for c in cls if c.get('date','').startswith(month)]

    return jsonify([{
        'id':           c['id'],
        'site_id':      c.get('site_id',''),
        'site_name':    c.get('site_name',''),
        'date':         c.get('date',''),
        'day':          c.get('day',''),
        'submitted_at': c.get('submitted_at',''),
        'done_tasks':   c.get('done_tasks', 0),
        'total_tasks':  c.get('total_tasks', 0),
        'manager_name': c.get('manager_name',''),
        'proof_photos': c.get('proof_photos', {}),
    } for c in cls])

# ── Client PDF (anonymous + photos) ──────────────────────────────────────────
@app.route('/api/client/checklist/<cid>/pdf')
@client_required
def api_client_checklist_pdf(cid):
    client_sites = session.get('client_sites', [])
    cls = load_json(CHECKLISTS_FILE)
    cl  = next((x for x in cls if x['id'] == cid), None)
    if not cl: return ('Not found', 404)
    if client_sites and cl.get('site_id') not in client_sites:
        return ('Forbidden', 403)
    buf = _build_checklist_pdf(cl, hide_employee=True, include_photos=True)
    fn  = f"Checklist_{cl.get('site_id','')}_{cl.get('date','')}.pdf"
    return Response(buf, mimetype='application/pdf',
                    headers={'Content-Disposition': f'inline; filename={fn}'})

# ── Client proof photo serve ──────────────────────────────────────────────────
@app.route('/client-proof-photos/<filename>')
@client_required
def client_proof_photo_serve(filename):
    return send_from_directory(os.path.join(PHOTOS_DIR, 'proofs'), filename)

# ── Admin: manage client accounts ─────────────────────────────────────────────
@app.route('/clients')
@login_required
def clients_page():
    return render_template('clients.html')

@app.route('/api/clients', methods=['GET'])
@login_required
def api_get_clients():
    return jsonify(load_json(CLIENTS_FILE))

@app.route('/api/clients', methods=['POST'])
@login_required
def api_create_client():
    ensure_dirs()
    data   = request.get_json()
    clients = load_json(CLIENTS_FILE)
    client = {
        'id':         str(uuid.uuid4()),
        'name':       data.get('name','').strip(),
        'username':   data.get('username','').strip().lower(),
        'password':   data.get('password','').strip(),
        'sites':      data.get('sites', []),
        'active':     True,
        'created_at': datetime.now().isoformat(),
    }
    clients.append(client)
    save_json(CLIENTS_FILE, clients)
    return jsonify(client), 201

@app.route('/api/clients/<cid>', methods=['PUT'])
@login_required
def api_update_client(cid):
    clients = load_json(CLIENTS_FILE)
    idx     = next((i for i,c in enumerate(clients) if c['id']==cid), None)
    if idx is None: return ('Not found', 404)
    clients[idx].update(request.get_json())
    save_json(CLIENTS_FILE, clients)
    return jsonify(clients[idx])

@app.route('/api/clients/<cid>', methods=['DELETE'])
@login_required
def api_delete_client(cid):
    clients = [c for c in load_json(CLIENTS_FILE) if c['id'] != cid]
    save_json(CLIENTS_FILE, clients)
    return jsonify({'ok': True})

@app.route('/api/clients/<cid>/logo', methods=['POST'])
@login_required
def api_upload_client_logo(cid):
    ensure_dirs()
    f = request.files.get('logo')
    if not f or not f.filename:
        return jsonify({'error': 'No file'}), 400
    ext = os.path.splitext(secure_filename(f.filename))[1].lower() or '.jpg'
    fn  = f'client_{cid}{ext}'
    f.save(os.path.join(CLIENT_LOGOS_DIR, fn))
    clients = load_json(CLIENTS_FILE)
    idx = next((i for i, c in enumerate(clients) if c['id'] == cid), None)
    if idx is not None:
        clients[idx]['logo'] = fn
        save_json(CLIENTS_FILE, clients)
    return jsonify({'logo_url': f'/client-logos/{fn}'})

@app.route('/api/live-status')
@login_required
def api_live_status():
    """TV dashboard: today's sites with employee check-in status and colour coding."""
    from datetime import timedelta
    now      = datetime.now()
    today    = now.strftime('%Y-%m-%d')
    today_abbr = now.strftime('%a').lower()  # mon, tue …

    templates  = get_templates()
    site_cfgs  = load_all_site_configs()
    arrivals   = [a for a in load_json(ARRIVALS_FILE) if a.get('arrived_at','').startswith(today)]
    employees  = {e['id']: e for e in load_json(EMPLOYEES_FILE)}

    # Read start_time / address from sites_config.json
    try:
        with open(SITES_CONFIG_FILE) as f: scfg = json.load(f)
    except: scfg = {}

    result = []
    for sid, tmpl in templates.items():
        cfg        = site_cfgs.get(sid, {})
        sc         = scfg.get(sid, {})
        assigned_days = cfg.get('assigned_days', list(templates.keys()))
        # Only include today's sites
        if today_abbr not in cfg.get('assigned_days', ['mon','tue','wed','thu','fri','sat','sun']):
            continue

        start_time_str = sc.get('start_time', '')  # "HH:MM"
        address        = sc.get('address', '')
        assigned_emps  = cfg.get('assigned_employees', [])  # [] = all

        # Site arrivals today
        site_arrivals = {a['employee_id']: a for a in arrivals if a.get('site_id') == sid}

        # Determine status per employee
        emp_statuses = []
        emp_pool = [e for e in employees.values()
                    if not assigned_emps or e['id'] in assigned_emps]

        for emp in emp_pool:
            arrival = site_arrivals.get(emp['id'])
            if arrival:
                status = 'green'
                arrival_time = arrival.get('arrived_at','')
            else:
                arrival_time = ''
                if start_time_str:
                    try:
                        h, m   = map(int, start_time_str.split(':'))
                        sched  = now.replace(hour=h, minute=m, second=0, microsecond=0)
                        mins_late = (now - sched).total_seconds() / 60
                        if mins_late < 0:        status = 'pending'   # start time not yet reached
                        elif mins_late < 30:     status = 'yellow'
                        elif mins_late < 60:     status = 'yellow'
                        else:                    status = 'red'
                    except: status = 'pending'
                else:
                    status = 'pending'

            photo_fn = emp.get('photo','')
            emp_statuses.append({
                'id':           emp['id'],
                'name':         emp['name'],
                'mobile':       emp.get('mobile',''),
                'photo':        f'/emp-photos/{photo_fn}' if photo_fn else '',
                'checked_in':   bool(arrival),
                'arrival_time': arrival_time,
                'lat':          arrival.get('lat','')  if arrival else '',
                'lng':          arrival.get('lng','')  if arrival else '',
                'status':       status,
            })

        result.append({
            'id':          sid,
            'name':        tmpl['name'],
            'has_kitchen': tmpl.get('has_kitchen', False),
            'start_time':  start_time_str,
            'address':     address,
            'employees':   emp_statuses,
            'checkin_count': len(site_arrivals),
        })

    return jsonify({'sites': result, 'now': now.strftime('%H:%M'), 'date': today})

@app.route('/api/sites-with-clients')
@employee_or_admin_required
def api_sites_with_clients():
    """Returns {site_id: {client_name, logo_url}} for all assigned sites."""
    clients = load_json(CLIENTS_FILE)
    result  = {}
    for client in clients:
        if not client.get('active', True):
            continue
        logo = client.get('logo', '')
        logo_url = f'/client-logos/{logo}' if logo else ''
        for sid in client.get('sites', []):
            result[sid] = {
                'client_name': client['name'],
                'logo_url':    logo_url,
            }
    return jsonify(result)

# ─────────────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    ensure_dirs()
    app.run(debug=True, port=5003)

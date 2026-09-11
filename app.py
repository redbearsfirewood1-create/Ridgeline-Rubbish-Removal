import os, json, uuid, shutil, io, smtplib
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
TRUCKS_FILE  = os.path.join(DATA_DIR, 'trucks.json')
ALLOWED_EXT  = {'png','jpg','jpeg','gif','webp','heic'}

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
    for d in (DATA_DIR, PHOTOS_DIR):
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
    txt('Thank you for choosing Ridgeline Rubbish Removal', W/2, 30,
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
        msg['From']    = f'Ridgeline Rubbish Removal <{SMTP_USER}>'
        msg['To']      = recipient
        msg['Subject'] = f'Invoice {qnum} — Ridgeline Rubbish Removal'

        total = q.get('total', 0)
        body  = (f"Hi {q.get('customer_name','')},\n\n"
                 f"Please find your invoice attached.\n\n"
                 f"Quote: {qnum}\n"
                 f"Address: {q.get('address','')}\n"
                 f"Total: ${total:.2f}\n\n"
                 f"Thank you for choosing Ridgeline Rubbish Removal!\n"
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

if __name__ == '__main__':
    ensure_dirs()
    app.run(debug=True, port=5003)

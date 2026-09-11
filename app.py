import os, json, uuid, shutil
from datetime import datetime
from functools import wraps
from flask import (Flask, render_template, request, redirect, url_for,
                   session, jsonify, send_from_directory)
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'ridgeline-secret-2024')
app.permanent_session_lifetime = 86400 * 30

# ── Config ────────────────────────────────────────────────────────────────────
USERNAME     = os.environ.get('APP_USER', 'admin')
PASSWORD     = os.environ.get('APP_PASS', 'ridgeline2024')
GOOGLE_MAPS  = os.environ.get('GOOGLE_MAPS_KEY', '')
BASE_ADDRESS = '620 Merrylands Road, Greystanes NSW 2145'

DATA_DIR     = os.path.join(os.path.dirname(__file__), 'data')
PHOTOS_DIR   = os.path.join(DATA_DIR, 'photos')
QUOTES_FILE  = os.path.join(DATA_DIR, 'quotes.json')
EXPENSES_FILE= os.path.join(DATA_DIR, 'expenses.json')
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

@app.route('/quote-photos/<filename>')
@login_required
def quote_photo(filename):
    return send_from_directory(PHOTOS_DIR, filename)

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

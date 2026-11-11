"""
Ridgeline pre-deploy validator
Run before every GitHub push:  python check_deploy.py
Catches the issues that make Render fail to build or start.
"""
import ast, sys, os, re

ROOT    = os.path.dirname(os.path.abspath(__file__))
APP_PY  = os.path.join(ROOT, "app.py")
REQ_TXT = os.path.join(ROOT, "requirements.txt")
RENDER  = os.path.join(ROOT, "render.yaml")
TMPL    = os.path.join(ROOT, "templates")

errors   = []
warnings = []

def ok(msg):  print(f"  ✅  {msg}")
def fail(msg): errors.append(msg);   print(f"  ❌  {msg}")
def warn(msg): warnings.append(msg); print(f"  ⚠️   {msg}")

print("\n── Ridgeline deploy check ──────────────────────────────────")

# 1 · Python syntax
print("\n[1] Syntax")
try:
    with open(APP_PY) as f:
        src = f.read()
    ast.parse(src)
    ok("app.py parses cleanly")
except SyntaxError as e:
    fail(f"Syntax error line {e.lineno}: {e.msg}")
    sys.exit(1)

# 2 · Flask app imports and starts (catches duplicate routes, bad imports)
print("\n[2] Flask startup")
try:
    sys.path.insert(0, ROOT)
    import importlib, app as _app
    importlib.reload(_app)   # reload in case cached
    n = len(_app.app.url_map._rules)
    ok(f"App loads cleanly — {n} routes registered")
except Exception as e:
    fail(f"Startup error: {e}")

# 3 · Duplicate @app.route endpoint names (the bug that broke us last time)
print("\n[3] Duplicate route endpoints")
route_re    = re.compile(r"@app\.route\(")
endpoint_re = re.compile(r"def\s+([a-zA-Z_]\w*)\s*\(")
lines       = src.split('\n')
seen        = {}
in_route    = False
dupes       = []
for i, line in enumerate(lines, 1):
    if route_re.search(line):
        in_route = True
    if in_route:
        m = endpoint_re.search(line)
        if m:
            name = m.group(1)
            if name in seen:
                dupes.append(f"'{name}' at lines {seen[name]} and {i}")
            else:
                seen[name] = i
            in_route = False
if dupes:
    for d in dupes:
        fail(f"Duplicate endpoint {d}")
else:
    ok(f"No duplicates ({len(seen)} unique endpoints)")

# 4 · All render_template() calls reference files that actually exist
print("\n[4] Template files")
tmpl_re    = re.compile(r'render_template\(\s*[\'"]([^\'"]+\.html)')
referenced = set(tmpl_re.findall(src))
existing   = set(os.listdir(TMPL)) if os.path.isdir(TMPL) else set()
missing    = sorted(referenced - existing)
if missing:
    for t in missing:
        fail(f"Missing template: {t}")
else:
    ok(f"All {len(referenced)} referenced templates present")

# 5 · requirements.txt covers common third-party imports
print("\n[5] requirements.txt")
pkg_map = {
    'flask': 'flask', 'werkzeug': 'werkzeug',
    'reportlab': 'reportlab', 'PIL': 'Pillow',
    'cv2': 'opencv-python', 'numpy': 'numpy',
    'pandas': 'pandas', 'requests': 'requests',
}
with open(REQ_TXT) as f:
    req = f.read().lower()
import_re = re.compile(r'^(?:import|from)\s+([a-zA-Z_]\w*)', re.M)
imported  = {m.lower() for m in import_re.findall(src)}
missed    = [pkg for imp, pkg in pkg_map.items()
             if imp.lower() in imported and pkg.lower() not in req]
if missed:
    for p in missed:
        warn(f"'{p}' imported but not in requirements.txt")
else:
    ok("All detected packages listed")

# 6 · render.yaml
print("\n[6] render.yaml")
if os.path.exists(RENDER):
    with open(RENDER) as f:
        ry = f.read()
    if 'gunicorn' in ry and 'startCommand' in ry:
        ok("render.yaml present with gunicorn startCommand")
    else:
        warn("render.yaml may be missing startCommand / gunicorn")
else:
    fail("render.yaml not found")

# ── Summary ──────────────────────────────────────────────────────────────────
print("\n────────────────────────────────────────────────────────────")
if errors:
    print(f"🔴  DEPLOY BLOCKED — {len(errors)} error(s) must be fixed:\n")
    for e in errors:
        print(f"    • {e}")
    sys.exit(1)
elif warnings:
    print(f"🟡  PROBABLY OK — {len(warnings)} warning(s) to review:\n")
    for w in warnings:
        print(f"    • {w}")
else:
    print("🟢  SAFE TO DEPLOY — all checks passed\n")

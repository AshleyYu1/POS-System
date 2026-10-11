"""Local F&B POS. Run: python app.py. Uses only the Python standard library."""
import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB = Path(os.environ.get('POS_DB', str(ROOT / 'data' / 'pos.sqlite3')))
LOCK = threading.RLock()
DEFAULT_COST = object()


class Connection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


def connect():
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB, factory=Connection)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    return c


def init():
    with connect() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS ingredients(id INTEGER PRIMARY KEY, name TEXT NOT NULL, sku TEXT UNIQUE NOT NULL, unit TEXT NOT NULL, minimum REAL NOT NULL DEFAULT 0, composite INTEGER NOT NULL DEFAULT 0, yield_qty REAL NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS recipes(parent INTEGER REFERENCES ingredients(id), child INTEGER REFERENCES ingredients(id), qty REAL NOT NULL, PRIMARY KEY(parent,child));
        CREATE TABLE IF NOT EXISTS lots(id INTEGER PRIMARY KEY, ingredient INTEGER NOT NULL REFERENCES ingredients(id), qty REAL NOT NULL, expiry TEXT, label TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS movements(id INTEGER PRIMARY KEY, at TEXT NOT NULL, ingredient INTEGER NOT NULL REFERENCES ingredients(id), delta REAL NOT NULL, kind TEXT NOT NULL, reason TEXT NOT NULL, reference TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS preparations(id INTEGER PRIMARY KEY, at TEXT NOT NULL, snapshot TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS menu(id INTEGER PRIMARY KEY, name TEXT NOT NULL, category TEXT NOT NULL, price INTEGER NOT NULL, available INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS menu_recipe(menu INTEGER REFERENCES menu(id), ingredient INTEGER REFERENCES ingredients(id), qty REAL NOT NULL, PRIMARY KEY(menu,ingredient));
        CREATE TABLE IF NOT EXISTS sales(id INTEGER PRIMARY KEY, at TEXT NOT NULL, total INTEGER NOT NULL, payment TEXT NOT NULL, tendered INTEGER NOT NULL, snapshot TEXT NOT NULL, allocations TEXT NOT NULL, void_reason TEXT, void_at TEXT);
        CREATE TABLE IF NOT EXISTS counts(id INTEGER PRIMARY KEY, at TEXT NOT NULL, ingredient INTEGER NOT NULL REFERENCES ingredients(id), expected REAL NOT NULL, counted REAL NOT NULL, reason TEXT NOT NULL, applied INTEGER NOT NULL);
        ''')
        # Additive migration: keep existing inventory and transaction history intact.
        for table, column, definition in (
            ('ingredients', 'item_type', "TEXT NOT NULL DEFAULT 'Food'"),
            ('ingredients', 'unit_cost', 'TEXT'), ('lots', 'unit_cost', 'TEXT'),
            ('sales', 'cogs', 'INTEGER')):
            columns = {r['name'] for r in c.execute(f'PRAGMA table_info({table})')}
            if column not in columns:
                c.execute(f'ALTER TABLE {table} ADD COLUMN {column} {definition}')


def now():
    return datetime.now().astimezone().isoformat(timespec='seconds')


def number(value, positive=False):
    try:
        n = Decimal(str(value))
    except InvalidOperation:
        raise ValueError('Enter a valid number.')
    if not n.is_finite() or n < 0 or (positive and n <= 0) or n > 100000000:
        raise ValueError('Quantity must be a finite positive number.' if positive else 'Enter a nonnegative number.')
    return float(n)


def money(value):
    number(value)
    return int((Decimal(str(value)) * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def required(d, key):
    value = str(d.get(key, '')).strip()
    if not value:
        raise ValueError(f'{key.replace("_", " ").title()} is required.')
    return value


def item(c, iid):
    row = c.execute('SELECT * FROM ingredients WHERE id=?', (int(iid),)).fetchone()
    if row is None:
        raise ValueError('Ingredient does not exist.')
    return dict(row)


def stock(c, iid):
    return c.execute('SELECT COALESCE(SUM(qty),0) FROM lots WHERE ingredient=?', (iid,)).fetchone()[0]


def movement(c, iid, delta, kind, reason, ref):
    c.execute('INSERT INTO movements(at,ingredient,delta,kind,reason,reference) VALUES(?,?,?,?,?,?)', (now(), iid, delta, kind, reason, str(ref)))


def cost_value(value):
    if value is None or value == '':
        return None
    number(value)
    return str(Decimal(str(value)))


def allocation_cost(allocations):
    if any(a['unit_cost'] is None for a in allocations):
        return None
    return sum((Decimal(str(a['qty'])) * Decimal(a['unit_cost']) for a in allocations), Decimal(0))


def add_stock(c, iid, qty, expiry, kind, reason, ref, unit_cost=DEFAULT_COST):
    if expiry:
        try:
            datetime.strptime(expiry, '%Y-%m-%d')
        except ValueError:
            raise ValueError('Expiry must be a valid date.')
    if unit_cost is DEFAULT_COST:
        unit_cost = item(c, iid)['unit_cost']
    c.execute('INSERT INTO lots(ingredient,qty,expiry,label,unit_cost) VALUES(?,?,?,?,?)', (iid, qty, expiry or None, reason, unit_cost))
    movement(c, iid, qty, kind, reason, ref)


def consume(c, iid, qty, kind, reason, ref):
    info = item(c, iid)
    if stock(c, iid) + 1e-8 < qty:
        raise ValueError(f'Insufficient stock: {info["name"]}. Need {qty:g} {info["unit"]}; have {stock(c,iid):g}.')
    allocations = []
    remaining = qty
    for lot in c.execute('SELECT * FROM lots WHERE ingredient=? AND qty>0 ORDER BY expiry IS NULL, expiry, id', (iid,)).fetchall():
        used = min(lot['qty'], remaining)
        c.execute('UPDATE lots SET qty=qty-? WHERE id=?', (used, lot['id']))
        allocations.append({'lot': lot['id'], 'ingredient': iid, 'qty': used, 'unit_cost': lot['unit_cost']})
        remaining -= used
        if remaining < 1e-8:
            break
    movement(c, iid, -qty, kind, reason, ref)
    return allocations


def recipe_rows(c, table, owner, owner_id):
    key = 'child' if table == 'recipes' else 'ingredient'
    return [dict(r) for r in c.execute(f'SELECT r.{key} AS ingredient,r.qty,i.name,i.unit FROM {table} r JOIN ingredients i ON i.id=r.{key} WHERE r.{owner}=?', (owner_id,))]


def inputs(c, rows):
    clean, seen = [], set()
    for row in rows:
        iid = int(row['ingredient'])
        item(c, iid)
        if iid in seen:
            raise ValueError('Each ingredient may appear only once in a recipe.')
        seen.add(iid)
        clean.append((iid, number(row['qty'], True)))
    return clean


def state(c):
    ingredients = []
    for r in c.execute('SELECT * FROM ingredients ORDER BY name COLLATE NOCASE'):
        d = dict(r)
        d['stock'] = stock(c, r['id'])
        d['recipe'] = recipe_rows(c, 'recipes', 'parent', r['id'])
        d['lots'] = [dict(x) for x in c.execute('SELECT * FROM lots WHERE ingredient=? AND qty>0 ORDER BY expiry IS NULL,expiry,id', (r['id'],))]
        ingredients.append(d)
    menus = []
    for r in c.execute('SELECT * FROM menu ORDER BY category,name'):
        d = dict(r)
        d['recipe'] = recipe_rows(c, 'menu_recipe', 'menu', r['id'])
        menus.append(d)
    return {'ingredients': ingredients, 'menu': menus,
            'sales': [dict(r) for r in c.execute('SELECT * FROM sales ORDER BY id DESC')],
            'movements': [dict(r) for r in c.execute('SELECT m.*,i.name,i.unit FROM movements m JOIN ingredients i ON i.id=m.ingredient ORDER BY m.id DESC')],
            'preparations': [dict(r) for r in c.execute('SELECT * FROM preparations ORDER BY id DESC')],
            'counts': [dict(r) for r in c.execute('SELECT c.*,i.name,i.unit FROM counts c JOIN ingredients i ON i.id=c.ingredient ORDER BY c.id DESC')]}


def action(c, path, d):
    if path == 'ingredient':
        name, unit = required(d, 'name'), required(d, 'unit')
        # Retain existing database identifiers without requiring staff to encode SKUs.
        iid = int(d.get('id') or 0)
        sku = item(c, iid)['sku'] if iid else str(d.get('sku') or 'ITEM-' + uuid.uuid4().hex)
        minimum = number(d.get('minimum', 0))
        item_type = d.get('item_type', item(c, iid)['item_type'] if iid else 'Food')
        if item_type not in ('Food', 'Non-food'):
            raise ValueError('Choose Food or Non-food.')
        unit_cost = cost_value(d.get('unit_cost', item(c, iid)['unit_cost'] if iid else None))
        composite = bool(d.get('composite'))
        output = number(d.get('yield_qty', 1), True)
        parts = inputs(c, d.get('recipe', [])) if composite else []
        if composite and not parts:
            raise ValueError('A composite ingredient needs at least one component.')
        if iid:
            old = item(c, iid)
            if old['unit'] != unit:
                raise ValueError('The unit cannot be changed after creation. Create a new item for a different unit.')
            c.execute('UPDATE ingredients SET name=?,sku=?,minimum=?,composite=?,yield_qty=? WHERE id=?', (name, sku, minimum, composite, output, iid))
            c.execute('UPDATE ingredients SET item_type=?,unit_cost=? WHERE id=?', (item_type, unit_cost, iid))
            c.execute('DELETE FROM recipes WHERE parent=?', (iid,))
        else:
            iid = c.execute('INSERT INTO ingredients(name,sku,unit,minimum,composite,yield_qty) VALUES(?,?,?,?,?,?)', (name, sku, unit, minimum, composite, output)).lastrowid
            c.execute('UPDATE ingredients SET item_type=?,unit_cost=? WHERE id=?', (item_type, unit_cost, iid))
            opening = number(d.get('opening', 0))
            if opening:
                add_stock(c, iid, opening, d.get('expiry'), 'Opening', 'Opening stock', iid)
        for child, qty in parts:
            c.execute('INSERT INTO recipes VALUES(?,?,?)', (iid, child, qty))
        def cycle(node, ancestors):
            if node in ancestors:
                raise ValueError('A recipe cannot contain itself, directly or through another recipe.')
            for r in c.execute('SELECT child FROM recipes WHERE parent=?', (node,)).fetchall():
                cycle(r[0], ancestors | {node})
        cycle(iid, set())
        return {'id': iid}
    if path == 'menu':
        name, category = required(d, 'name'), required(d, 'category')
        price = money(d.get('price', 0))
        parts = inputs(c, d.get('recipe', []))
        if not parts:
            raise ValueError('Add at least one inventory ingredient to the menu item.')
        mid = int(d.get('id') or 0)
        values = (name, category, price, int(bool(d.get('available', True))))
        if mid:
            if not c.execute('SELECT id FROM menu WHERE id=?', (mid,)).fetchone():
                raise ValueError('Menu item not found.')
            c.execute('UPDATE menu SET name=?,category=?,price=?,available=? WHERE id=?', (*values, mid))
            c.execute('DELETE FROM menu_recipe WHERE menu=?', (mid,))
        else:
            mid = c.execute('INSERT INTO menu(name,category,price,available) VALUES(?,?,?,?)', values).lastrowid
        for iid, qty in parts:
            c.execute('INSERT INTO menu_recipe VALUES(?,?,?)', (mid, iid, qty))
        return {'id': mid}
    if path == 'edit-stock':
        iid = int(d['ingredient']); item(c, iid)
        before = stock(c, iid)
        if abs(number(d['expected']) - before) > 1e-8:
            raise ValueError('Stock changed since this form opened. Reopen Edit stock.')
        after = number(d['qty'])
        reason = str(d.get('reason') or '').strip() or 'No reason provided'
        delta = after - before
        if delta > 1e-8:
            add_stock(c, iid, delta, d.get('expiry'), 'Stock edit +', reason, '')
        elif delta < -1e-8:
            consume(c, iid, -delta, 'Stock edit -', reason, '')
        return {'before': before, 'after': after, 'difference': delta}
    if path == 'stock':
        iid = int(d['ingredient']); item(c, iid)
        qty = number(d['qty'], True)
        kind, reason = required(d, 'kind'), required(d, 'reason')
        if kind in ('Stock-in', 'Adjustment +'):
            unit_cost = cost_value(d['unit_cost']) if 'unit_cost' in d else DEFAULT_COST
            add_stock(c, iid, qty, d.get('expiry'), kind, reason, '', unit_cost)
        elif kind in ('Waste', 'Adjustment -'):
            consume(c, iid, qty, kind, reason, '')
        else:
            raise ValueError('Invalid stock action.')
        return {'ok': True}
    if path in ('prepare-preview', 'prepare'):
        info = item(c, d['ingredient'])
        if not info['composite']:
            raise ValueError('Only composite ingredients can be prepared.')
        qty = number(d['qty'], True)
        parts = recipe_rows(c, 'recipes', 'parent', info['id'])
        scaled = [{**p, 'qty': p['qty'] * qty / info['yield_qty'], 'stock': stock(c, p['ingredient'])} for p in parts]
        snapshot = {'output': {'ingredient': info['id'], 'name': info['name'], 'unit': info['unit'], 'qty': qty}, 'inputs': scaled, 'recipe_yield': info['yield_qty']}
        if path == 'prepare-preview':
            return snapshot
        # Prevent confirming a stale preview after a recipe edit.
        if d.get('preview') != snapshot:
            raise ValueError('Recipe or stock changed. Preview the preparation again.')
        pid = c.execute('INSERT INTO preparations(at,snapshot) VALUES(?,?)', (now(), json.dumps(snapshot))).lastrowid
        used = []
        for part in scaled:
            used.extend(consume(c, part['ingredient'], part['qty'], 'Preparation use', info['name'], pid))
        total_cost = allocation_cost(used)
        output_cost = str(total_cost / Decimal(str(qty))) if total_cost is not None else None
        snapshot['input_allocations'] = used
        snapshot['output']['unit_cost'] = output_cost
        c.execute('UPDATE preparations SET snapshot=? WHERE id=?', (json.dumps(snapshot), pid))
        add_stock(c, info['id'], qty, d.get('expiry'), 'Preparation output', info['name'], pid, output_cost)
        return {'id': pid}
    if path == 'sale':
        payment = required(d, 'payment')
        if payment not in ('Cash', 'E-wallet'):
            raise ValueError('Choose Cash or E-wallet.')
        lines, needs, total = [], {}, 0
        for line in d.get('items', []):
            r = c.execute('SELECT * FROM menu WHERE id=?', (int(line['menu']),)).fetchone()
            if not r or not r['available']:
                raise ValueError('A menu item is unavailable. Refresh the cart.')
            qty = number(line['qty'], True)
            if qty != int(qty):
                raise ValueError('Sale quantities must be whole servings.')
            # Client expected price protects against price edits while a cart is open.
            if line.get('price') != r['price']:
                raise ValueError('A price changed. Refresh the cart before paying.')
            parts = recipe_rows(c, 'menu_recipe', 'menu', r['id'])
            for p in parts:
                needs[p['ingredient']] = needs.get(p['ingredient'], 0) + p['qty'] * qty
            lines.append({'menu': r['id'], 'name': r['name'], 'category': r['category'], 'price': r['price'], 'qty': int(qty), 'recipe': parts})
            total += r['price'] * int(qty)
        if not lines:
            raise ValueError('Add an item to the cart.')
        tendered = money(d.get('tendered', 0)) if payment == 'Cash' else total
        if tendered < total:
            raise ValueError('Cash received is less than the amount due.')
        sid = c.execute('INSERT INTO sales(at,total,payment,tendered,snapshot,allocations) VALUES(?,?,?,?,?,?)', (now(), total, payment, tendered, json.dumps(lines), '[]')).lastrowid
        allocated = []
        for iid, qty in needs.items():
            allocated.extend(consume(c, iid, qty, 'Sale', f'Sale #{sid}', sid))
        cost = allocation_cost(allocated)
        cogs = int((cost * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP)) if cost is not None else None
        c.execute('UPDATE sales SET allocations=?,cogs=? WHERE id=?', (json.dumps(allocated), cogs, sid))
        return {'id': sid, 'total': total, 'change': tendered - total}
    if path == 'void':
        sid = int(d['id']); reason = required(d, 'reason')
        sale = c.execute('SELECT * FROM sales WHERE id=?', (sid,)).fetchone()
        if not sale or sale['void_at']:
            raise ValueError('Sale is missing or already voided.')
        totals = {}
        for a in json.loads(sale['allocations']):
            c.execute('UPDATE lots SET qty=qty+? WHERE id=?', (a['qty'], a['lot']))
            totals[a['ingredient']] = totals.get(a['ingredient'], 0) + a['qty']
        for iid, qty in totals.items():
            movement(c, iid, qty, 'Sale void', reason, sid)
        c.execute('UPDATE sales SET void_at=?,void_reason=? WHERE id=?', (now(), reason, sid))
        return {'ok': True}
    if path == 'count':
        iid = int(d['ingredient']); item(c, iid)
        counted = number(d['qty']); expected = stock(c, iid)
        reason = required(d, 'reason'); apply = bool(d.get('apply'))
        cid = c.execute('INSERT INTO counts(at,ingredient,expected,counted,reason,applied) VALUES(?,?,?,?,?,?)', (now(), iid, expected, counted, reason, int(apply))).lastrowid
        diff = counted - expected
        if apply and abs(diff) > 1e-8:
            if diff > 0:
                add_stock(c, iid, diff, d.get('expiry'), 'Count adjustment', reason, cid)
            else:
                consume(c, iid, -diff, 'Count adjustment', reason, cid)
        return {'expected': expected, 'counted': counted, 'difference': diff, 'applied': apply}
    raise ValueError('Unknown action.')


class Handler(BaseHTTPRequestHandler):
    def respond(self, code, body, content_type='application/json'):
        raw = json.dumps(body).encode() if content_type == 'application/json' else body
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers(); self.wfile.write(raw)

    def do_GET(self):
        if self.path == '/api/state':
            with LOCK, connect() as c:
                self.respond(200, state(c))
            return
        assets = {'/': ('index.html', 'text/html; charset=utf-8'), '/app.js': ('app.js', 'text/javascript; charset=utf-8'), '/reports.js': ('reports.js', 'text/javascript; charset=utf-8'), '/style.css': ('style.css', 'text/css; charset=utf-8')}
        assets['/cafe.css'] = ('cafe.css', 'text/css; charset=utf-8')
        if self.path in assets:
            file, mime = assets[self.path]
            self.respond(200, (ROOT / 'static' / file).read_bytes(), mime)
        else:
            self.respond(404, {'error': 'Not found'})

    def do_POST(self):
        # Local app: reject cross-origin writes from other websites.
        origin = self.headers.get('Origin')
        if origin and origin not in ('http://127.0.0.1:8000', 'http://localhost:8000'):
            self.respond(403, {'error': 'Invalid origin'}); return
        if not self.path.startswith('/api/'):
            self.respond(404, {'error': 'Not found'}); return
        try:
            size = int(self.headers.get('Content-Length', 0))
            if size <= 0 or size > 1000000:
                raise ValueError('Invalid request size.')
            d = json.loads(self.rfile.read(size))
            with LOCK, connect() as c:
                c.execute('BEGIN IMMEDIATE')
                result = action(c, self.path[5:], d)
            self.respond(200, result)
        except sqlite3.IntegrityError:
            self.respond(400, {'error': 'SKU must be unique and all referenced ingredients must exist.'})
        except (ValueError, KeyError, TypeError, OverflowError) as e:
            self.respond(400, {'error': str(e)})


if __name__ == '__main__':
    init()
    server = ThreadingHTTPServer(('127.0.0.1', 8000), Handler)
    print('F&B POS running at http://127.0.0.1:8000 — Ctrl+C to stop', flush=True)
    server.serve_forever()

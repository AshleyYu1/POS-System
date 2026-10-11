import json
import tempfile
import unittest
from pathlib import Path
import app


class StockTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.previous = app.DB
        app.DB = Path(self.tmp.name) / 'test.sqlite3'
        app.init()
        self.eggs = self.do('ingredient', name='Eggs', sku='EGG', unit='piece', opening=20)['id']
        self.milk = self.do('ingredient', name='Milk', sku='MILK', unit='mL', opening=2000)['id']
        self.cream = self.do('ingredient', name='Cream', sku='CREAM', unit='mL', composite=True, yield_qty=250, recipe=[{'ingredient': self.eggs, 'qty': 2}, {'ingredient': self.milk, 'qty': 200}])['id']

    def tearDown(self):
        app.DB = self.previous
        self.tmp.cleanup()

    def do(self, endpoint, **body):
        with app.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            return app.action(c, endpoint, body)

    def stock(self, iid):
        with app.connect() as c:
            return app.stock(c, iid)

    def prepare(self, iid, qty):
        p = self.do('prepare-preview', ingredient=iid, qty=qty)
        return self.do('prepare', ingredient=iid, qty=qty, preview=p)

    def menu(self):
        return self.do('menu', name='Cream drink', category='Drinks', price='80.25', recipe=[{'ingredient': self.cream, 'qty': 50}])['id']

    def sale(self, mid, qty=1):
        return self.do('sale', payment='Cash', tendered=1000, items=[{'menu': mid, 'qty': qty, 'price': 8025}])

    def test_preparation_scaling_and_nested_stock(self):
        self.prepare(self.cream, 500)
        self.assertEqual(self.stock(self.eggs), 16)
        self.assertEqual(self.stock(self.milk), 1600)
        self.assertEqual(self.stock(self.cream), 500)
        frosting = self.do('ingredient', name='Frosting', sku='FROST', unit='g', composite=True, yield_qty=100, recipe=[{'ingredient': self.cream, 'qty': 80}])['id']
        self.prepare(frosting, 200)
        self.assertEqual(self.stock(self.cream), 340)
        self.assertEqual(self.stock(self.eggs), 16)

    def test_insufficient_preparation_rolls_back(self):
        with self.assertRaises(ValueError):
            self.prepare(self.cream, 5000)
        self.assertEqual(self.stock(self.eggs), 20)
        self.assertEqual(self.stock(self.cream), 0)
        with app.connect() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM preparations').fetchone()[0], 0)

    def test_sale_money_snapshot_and_void_after_recipe_edit(self):
        self.prepare(self.cream, 500)
        mid = self.menu()
        sale = self.sale(mid, 3)
        self.assertEqual(sale['total'], 24075)
        self.assertEqual(sale['change'], 75925)
        self.assertEqual(self.stock(self.cream), 350)
        self.do('menu', id=mid, name='New drink', category='Drinks', price=100, recipe=[{'ingredient': self.eggs, 'qty': 1}])
        self.do('ingredient', id=self.cream, name='Renamed cream', sku='CREAM', unit='mL', composite=True, yield_qty=100, recipe=[{'ingredient': self.milk, 'qty': 100}])
        with app.connect() as c:
            s = c.execute('SELECT * FROM sales WHERE id=?', (sale['id'],)).fetchone()
            self.assertEqual(json.loads(s['snapshot'])[0]['name'], 'Cream drink')
            self.assertEqual(json.loads(s['snapshot'])[0]['recipe'][0]['qty'], 50)
            prep = json.loads(c.execute('SELECT snapshot FROM preparations').fetchone()[0])
            self.assertEqual(prep['recipe_yield'], 250)
        self.do('void', id=sale['id'], reason='Mistake')
        self.assertEqual(self.stock(self.cream), 500)
        self.assertEqual(self.stock(self.eggs), 16)
        with self.assertRaises(ValueError):
            self.do('void', id=sale['id'], reason='Again')

    def test_failed_sale_does_not_save_or_change_stock(self):
        self.prepare(self.cream, 100)
        mid = self.menu()
        with self.assertRaises(ValueError):
            self.sale(mid, 3)
        self.assertEqual(self.stock(self.cream), 100)
        with app.connect() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM sales').fetchone()[0], 0)

    def test_count_waste_stock_and_expiry_order(self):
        self.do('stock', ingredient=self.milk, qty=100, expiry='2026-10-15', kind='Stock-in', reason='Delivery A')
        self.do('stock', ingredient=self.milk, qty=50, expiry='2026-10-13', kind='Stock-in', reason='Delivery B')
        self.do('stock', ingredient=self.milk, qty=60, kind='Waste', reason='Spill')
        with app.connect() as c:
            lots = list(c.execute('SELECT * FROM lots WHERE ingredient=? ORDER BY id', (self.milk,)))
            self.assertEqual(lots[-1]['qty'], 0)
            self.assertEqual(lots[-2]['qty'], 90)
        result = self.do('count', ingredient=self.milk, qty=2050, reason='Independent count', apply=False)
        self.assertEqual(result['difference'], -40)
        self.assertEqual(self.stock(self.milk), 2090)
        self.do('count', ingredient=self.milk, qty=2050, reason='Confirmed recount', apply=True)
        self.assertEqual(self.stock(self.milk), 2050)

    def test_cycle_validation_and_fixed_units(self):
        with self.assertRaises(ValueError):
            self.do('ingredient', id=self.eggs, name='Eggs', sku='EGG', unit='piece', composite=True, yield_qty=1, recipe=[{'ingredient': self.cream, 'qty': 1}])
        with self.assertRaises(ValueError):
            self.do('ingredient', id=self.eggs, name='Eggs', sku='EGG', unit='g')
        self.assertEqual(self.stock(self.eggs), 20)

    def test_stale_price_unavailable_and_stale_preparation(self):
        self.prepare(self.cream, 500)
        mid = self.menu()
        self.do('menu', id=mid, name='Drink', category='Drinks', price=90, available=False, recipe=[{'ingredient': self.cream, 'qty': 50}])
        with self.assertRaises(ValueError):
            self.sale(mid)
        self.do('menu', id=mid, name='Drink', category='Drinks', price=90, available=True, recipe=[{'ingredient': self.cream, 'qty': 50}])
        with self.assertRaises(ValueError):
            self.sale(mid)
        preview = self.do('prepare-preview', ingredient=self.cream, qty=250)
        self.do('stock', ingredient=self.milk, qty=1, kind='Waste', reason='Spill')
        with self.assertRaises(ValueError):
            self.do('prepare', ingredient=self.cream, qty=250, preview=preview)

    def test_invalid_and_persistent_quantities(self):
        for value in ('nan', 'inf', -1, 0):
            with self.assertRaises(ValueError):
                self.do('stock', ingredient=self.eggs, qty=value, kind='Stock-in', reason='Test')
        app.init()
        self.assertEqual(self.stock(self.eggs), 20)
        with app.connect() as c:
            s = app.state(c)
            self.assertEqual(len(s['ingredients']), 3)

    def test_inventory_without_user_sku_preserves_internal_id(self):
        iid = self.do('ingredient', name='Coffee beans', unit='g', opening=100)['id']
        with app.connect() as c:
            original = app.item(c, iid)['sku']
        self.do('ingredient', id=iid, name='Espresso beans', unit='g', minimum=20)
        with app.connect() as c:
            self.assertEqual(app.item(c, iid)['sku'], original)
        self.assertEqual(self.stock(iid), 100)

    def test_nonfood_stock_edit_optional_reason_and_stale_guard(self):
        cups = self.do('ingredient', name='Cup', unit='piece', item_type='Non-food', opening=10, unit_cost='2.5')['id']
        self.do('edit-stock', ingredient=cups, expected=10, qty=15)
        self.do('edit-stock', ingredient=cups, expected=15, qty=12, reason='Wastage')
        self.assertEqual(self.stock(cups), 12)
        with self.assertRaises(ValueError):
            self.do('edit-stock', ingredient=cups, expected=15, qty=30)
        with app.connect() as c:
            self.assertEqual(app.item(c, cups)['item_type'], 'Non-food')
            reasons = [r[0] for r in c.execute('SELECT reason FROM movements WHERE ingredient=?', (cups,))]
            self.assertIn('No reason provided', reasons)
            self.assertIn('Wastage', reasons)

    def test_lot_cogs_and_history_survive_default_cost_edit(self):
        cups = self.do('ingredient', name='Cup', unit='piece', item_type='Non-food', opening=2, unit_cost='2.125')['id']
        self.do('stock', ingredient=cups, qty=5, kind='Stock-in', reason='New delivery', unit_cost='3')
        mid = self.do('menu', name='Cup item', category='Supplies', price=10, recipe=[{'ingredient': cups, 'qty': 1}])['id']
        sale = self.do('sale', payment='E-wallet', items=[{'menu': mid, 'qty': 3, 'price': 1000}])
        with app.connect() as c:
            self.assertEqual(c.execute('SELECT cogs FROM sales WHERE id=?', (sale['id'],)).fetchone()[0], 725)
        self.do('ingredient', id=cups, name='Cup', unit='piece', item_type='Non-food', unit_cost=99)
        self.do('void', id=sale['id'], reason='Error')
        self.assertEqual(self.stock(cups), 7)
        with app.connect() as c:
            self.assertEqual(c.execute('SELECT cogs FROM sales WHERE id=?', (sale['id'],)).fetchone()[0], 725)

    def test_prepared_cogs_from_component_lots_and_unknown_costs(self):
        eggs = self.do('ingredient', name='Costed eggs', unit='piece', opening=10, unit_cost=5)['id']
        milk = self.do('ingredient', name='Costed milk', unit='mL', opening=1000, unit_cost='.1')['id']
        cream = self.do('ingredient', name='Costed cream', unit='mL', composite=True, yield_qty=250, recipe=[{'ingredient': eggs, 'qty': 2}, {'ingredient': milk, 'qty': 200}])['id']
        self.prepare(cream, 500)  # PHP 60 / 500 mL = PHP .12/mL
        mid = self.do('menu', name='Drink', category='Drinks', price=80, recipe=[{'ingredient': cream, 'qty': 50}])['id']
        sale = self.do('sale', payment='E-wallet', items=[{'menu': mid, 'qty': 2, 'price': 8000}])
        with app.connect() as c:
            self.assertEqual(c.execute('SELECT cogs FROM sales WHERE id=?', (sale['id'],)).fetchone()[0], 1200)
        self.prepare(self.cream, 250)
        unknown = self.sale(self.menu())
        with app.connect() as c:
            self.assertIsNone(c.execute('SELECT cogs FROM sales WHERE id=?', (unknown['id'],)).fetchone()[0])


if __name__ == '__main__':
    unittest.main()

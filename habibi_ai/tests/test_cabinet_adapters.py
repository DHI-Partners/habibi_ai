import frappe
from frappe.tests import IntegrationTestCase

from habibi_ai.cabinet.adapters import selling_price

PRICE_LIST = "Cabinet Test Selling"


class TestSellingPrice(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		if not frappe.db.exists("Price List", PRICE_LIST):
			frappe.get_doc(
				{"doctype": "Price List", "price_list_name": PRICE_LIST, "selling": 1, "currency": "KZT"}
			).insert()
		frappe.db.set_single_value("Selling Settings", "selling_price_list", PRICE_LIST)
		self.item = frappe.get_doc({
			"doctype": "Item", "item_code": "CAB-TEST-BURGER", "item_name": "Бургер",
			"item_group": frappe.db.get_value("Item Group", {"is_group": 0}), "stock_uom": "Nos",
		}).insert(ignore_if_duplicate=True)

	def tearDown(self):
		frappe.db.rollback()

	def test_читает_действующую_цену(self):
		# valid_from не задан явно в брифе, но у поля в Item Price default
		# "Today" — без явной даты начала более раннего периода вставка
		# просроченной цены упала бы на validate_from_to_dates (from > to).
		frappe.get_doc({"doctype": "Item Price", "item_code": self.item.name, "price_list": PRICE_LIST,
			"price_list_rate": 100, "valid_from": "2019-01-01", "valid_upto": "2020-01-01"}).insert()
		frappe.get_doc({"doctype": "Item Price", "item_code": self.item.name, "price_list": PRICE_LIST,
			"price_list_rate": 2490}).insert()
		self.assertEqual(selling_price.read([self.item.name]), {self.item.name: 2490.0})

	def test_запись_создаёт_цену(self):
		selling_price.write(self.item, 1990)
		self.assertEqual(selling_price.read([self.item.name]), {self.item.name: 1990.0})

	def test_запись_обновляет_цену_без_дубля(self):
		selling_price.write(self.item, 1990)
		selling_price.write(self.item, 2090)
		count = frappe.db.count("Item Price", {"item_code": self.item.name, "price_list": PRICE_LIST})
		self.assertEqual(count, 1)
		self.assertEqual(selling_price.read([self.item.name])[self.item.name], 2090.0)

	def test_без_прайс_листа_не_редактируется(self):
		frappe.db.set_single_value("Selling Settings", "selling_price_list", None)
		self.assertFalse(selling_price.editable())
		self.assertEqual(selling_price.read([self.item.name]), {})

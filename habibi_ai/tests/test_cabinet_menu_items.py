"""Позиция меню из кабинета — код рождается сам, без ввода owner'ом (задача 19).

save() без name зовёт get(section, doc.name) на возврате — а тот ищёт
документ через base_filters раздела ({"is_sales_item": 1}). Значит, сам факт
успешного save() без исключения уже подтверждает, что позиция видна в разделе
«Меню», а не только что она вставилась в базу.
"""

import frappe
from frappe.tests import IntegrationTestCase

from habibi_ai import presets
from habibi_ui.api.v1 import cabinet as cabinet_api

PRICE_LIST = "_Habibi Menu Items Test"


class TestCabinetMenuItemCode(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		presets.apply("food")

	def setUp(self):
		frappe.set_user("Administrator")
		if not frappe.db.exists("Price List", PRICE_LIST):
			frappe.get_doc(
				{
					"doctype": "Price List",
					"price_list_name": PRICE_LIST,
					"selling": 1,
					"currency": "KZT",
					"enabled": 1,
				}
			).insert()
		frappe.db.set_single_value("Selling Settings", "selling_price_list", PRICE_LIST)

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def test_позиция_создаётся_без_кода_с_ценой_и_группой(self):
		result = cabinet_api.save("menu", {"item_name": "Классик бургер", "selling_price": 2490})
		self.assertEqual(result["name"], "KLASSIK-BURGER")
		item = frappe.get_doc("Item", result["name"])
		self.assertTrue(item.item_group)
		self.assertEqual(frappe.db.get_value("Item Group", item.item_group, "is_group"), 0)
		self.assertTrue(frappe.db.exists("UOM", item.stock_uom))
		self.assertTrue(item.is_sales_item)
		self.assertEqual(result["selling_price"], 2490)

	def test_повтор_с_тем_же_именем_получает_код_с_суффиксом(self):
		first = cabinet_api.save("menu", {"item_name": "Классик бургер два", "selling_price": 1000})
		second = cabinet_api.save("menu", {"item_name": "Классик бургер два", "selling_price": 1100})
		self.assertNotEqual(first["name"], second["name"])
		self.assertEqual(second["name"], first["name"] + "-2")

	def test_явно_заданный_код_не_переписывается(self):
		"""Хук — только для пустого item_code: Desk и прочие места, где код
		вводят руками, свою вставку не должны почувствовать."""
		doc = frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": "_HB-MENU-MANUAL-CODE",
				"item_name": "Ручной код",
				"item_group": frappe.db.get_value("Item Group", {"is_group": 0}),
				"stock_uom": "Nos",
			}
		).insert()
		self.assertEqual(doc.name, "_HB-MENU-MANUAL-CODE")

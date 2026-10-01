"""Позиция меню из кабинета — код рождается сам, без ввода owner'ом (задача 19).

save() без name зовёт get(section, doc.name) на возврате — а тот ищёт
документ через base_filters раздела ({"is_sales_item": 1}). Значит, сам факт
успешного save() без исключения уже подтверждает, что позиция видна в разделе
«Меню», а не только что она вставилась в базу.
"""

from unittest.mock import patch

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

	def test_название_без_букв_получает_запасной_код(self):
		"""Эмодзи и одна пунктуация не транслитерируются — slug() отдаёт "".
		insert() всё равно должен пройти, а не упасть на «Item Code is
		required» из-за пустого item_code."""
		result = cabinet_api.save("menu", {"item_name": "🍔🍟", "selling_price": 500})
		self.assertEqual(result["name"], "ITEM")

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


class TestCabinetMenuItemPrice(IntegrationTestCase):
	"""Цена позиции из формы кабинета: пустая — не пишется, отрицательная — ошибка."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		presets.apply("food")

	def setUp(self):
		frappe.set_user("Administrator")
		if not frappe.db.exists("Price List", PRICE_LIST):
			frappe.get_doc(
				{"doctype": "Price List", "price_list_name": PRICE_LIST, "selling": 1, "currency": "KZT", "enabled": 1}
			).insert()
		frappe.db.set_single_value("Selling Settings", "selling_price_list", PRICE_LIST)

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def test_позиция_без_цены_сохраняется_без_item_price(self):
		result = cabinet_api.save("menu", {"item_name": "Позиция без цены", "selling_price": None})
		self.assertFalse(frappe.db.exists("Item Price", {"item_code": result["name"]}))
		self.assertIsNone(result["selling_price"])

	def test_отрицательная_цена_из_формы_отклоняется(self):
		with self.assertRaises(frappe.ValidationError):
			cabinet_api.save("menu", {"item_name": "Позиция с минусом", "selling_price": -5})


class TestCabinetMenuDelete(TestCabinetMenuItemCode):
	"""Позицию меню можно удалить — вместе с её ценой; если по ней были заказы, объясняем, что делать."""

	def setUp(self):
		super().setUp()
		# Откат после каждого теста сбрасывает пресет из setUpClass — применяем заново, чтобы раздел был свежим
		presets.apply("food")

	def test_раздел_меню_разрешает_удаление(self):
		menu = next(s for s in cabinet_api.config() if s["key"] == "menu")
		self.assertTrue(menu["can_delete"])

	def test_владелец_удаляет_позицию_и_её_цену(self):
		code = cabinet_api.save("menu", {"item_name": "Удаляемая позиция", "selling_price": 990})["name"]
		self.assertTrue(frappe.db.exists("Item Price", {"item_code": code}))
		cabinet_api.delete("menu", code)
		self.assertFalse(frappe.db.exists("Item", code))
		# Цена не остаётся висеть: иначе в прайс-листе «призрак» удалённого блюда
		self.assertFalse(frappe.db.exists("Item Price", {"item_code": code}))

	def test_позицию_из_заказа_удалить_нельзя_и_цена_остаётся(self):
		"""Цена удаляется первой — отказ позиции не должен оставить её без цены."""
		code = cabinet_api.save("menu", {"item_name": "Позиция из заказа", "selling_price": 500})["name"]
		real = frappe.delete_doc

		def delete(doctype, *args, **kwargs):
			if doctype == "Item":
				raise frappe.LinkExistsError("Item is linked with Sales Order")
			return real(doctype, *args, **kwargs)

		with patch("frappe.delete_doc", side_effect=delete):
			with self.assertRaises(frappe.ValidationError) as ctx:
				cabinet_api.delete("menu", code)
		self.assertIn("Снимите её с продажи", str(ctx.exception))
		self.assertTrue(frappe.db.exists("Item", code))
		self.assertTrue(frappe.db.exists("Item Price", {"item_code": code}))

	def test_сотрудник_удалять_позиции_не_может(self):
		code = cabinet_api.save("menu", {"item_name": "Чужая позиция", "selling_price": 100})["name"]
		# Сотруднику раздела «Меню» не видно вовсе — это строже, чем отказ в праве
		with patch("frappe.get_roles", return_value=["Habibi Staff"]), self.assertRaises((frappe.PermissionError, frappe.DoesNotExistError)):
			cabinet_api.delete("menu", code)
		self.assertTrue(frappe.db.exists("Item", code))

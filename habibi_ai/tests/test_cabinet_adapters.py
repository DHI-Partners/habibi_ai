from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from habibi_ai.cabinet.adapters import order_status, orders_count, selling_price, telegram_alias

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

	def test_пустая_цена_не_пишется(self):
		"""Форма шлёт цену при каждом сохранении — пусто значит «не задавали»,
		а не «бесплатно»."""
		for empty in (None, ""):
			with self.subTest(repr(empty)):
				selling_price.write(self.item, empty)
				self.assertFalse(frappe.db.exists("Item Price", {"item_code": self.item.name}))

	def test_та_же_цена_не_пишется_повторно(self):
		"""Действующая цена с датами из Desk не должна обрасти бессрочной копией
		лишь потому, что позицию сохранили в кабинете, не трогая цену."""
		frappe.get_doc({"doctype": "Item Price", "item_code": self.item.name, "price_list": PRICE_LIST,
			"price_list_rate": 1500, "valid_from": "2020-01-01", "valid_upto": "2099-01-01"}).insert()
		selling_price.write(self.item, "1500")
		self.assertEqual(frappe.db.count("Item Price", {"item_code": self.item.name}), 1)

	def test_отрицательная_цена_отклоняется(self):
		with self.assertRaises(frappe.ValidationError) as ctx:
			selling_price.write(self.item, -10)
		self.assertIn("отрицательной", str(ctx.exception))
		self.assertFalse(frappe.db.exists("Item Price", {"item_code": self.item.name}))


class TestOrderStatusFacets(IntegrationTestCase):
	"""Быстрые фильтры списка заказов: из состояний воркфлоу сайта, а без него — из docstatus."""

	def tearDown(self):
		frappe.db.rollback()

	def test_без_воркфлоу_по_docstatus(self):
		with patch("habibi_ai.cabinet.adapters.orders._workflow", return_value=None):
			facets = order_status.facets()
		self.assertEqual([f["key"] for f in facets], ["new", "accepted", "cancelled"])
		self.assertEqual(facets[0]["filters"], [["docstatus", "=", 0]])
		self.assertEqual(facets[1]["filters"], [["docstatus", "=", 1]])
		self.assertEqual([f["closed"] for f in facets], [False, False, True])

	def test_из_состояний_воркфлоу_в_его_порядке(self):
		states = [
			{"state": "New", "doc_status": "0", "terminal": False},
			{"state": "In Kitchen", "doc_status": "1", "terminal": False},
			{"state": "Delivered", "doc_status": "1", "terminal": True},
			{"state": "Cancelled", "doc_status": "2", "terminal": True},
			{"state": "Особое", "doc_status": "1", "terminal": False},
		]
		with (
			patch("habibi_ai.cabinet.adapters.orders._workflow", return_value="WF"),
			patch("habibi_ai.cabinet.adapters.orders._state_field", return_value="custom_order_status"),
			patch("habibi_ai.cabinet.adapters._workflow_states", return_value=states),
		):
			facets = order_status.facets()
		self.assertEqual([f["key"] for f in facets], ["New", "In Kitchen", "Delivered", "Cancelled", "Особое"])
		self.assertEqual([f["label"] for f in facets], ["Новые", "На кухне", "Выданные", "Отменённые", "Особое"])
		self.assertEqual(facets[1]["filters"], [["custom_order_status", "=", "In Kitchen"]])
		# Закрытые — конечные состояния и отмена: на доске их нет
		self.assertEqual([f["closed"] for f in facets], [False, False, True, True, False])

	def test_фильтры_годятся_для_запроса_заказов(self):
		with patch("habibi_ai.cabinet.adapters.orders._workflow", return_value=None):
			for f in order_status.facets():
				with self.subTest(f["key"]):
					frappe.get_list("Sales Order", filters=f["filters"], limit=1)


class TestOrdersCount(IntegrationTestCase):
	def tearDown(self):
		frappe.db.rollback()

	def test_считает_заказы_по_клиентам_без_отменённых(self):
		rows = [
			frappe._dict(customer="Иван", c=2),
			frappe._dict(customer="Пётр", c=1),
		]
		with patch("habibi_ai.cabinet.adapters.frappe.get_all", return_value=rows) as get_all:
			got = orders_count.read(["Иван", "Пётр", "Новый"])
		self.assertEqual(got, {"Иван": 2, "Пётр": 1, "Новый": 0})
		filters = get_all.call_args.kwargs["filters"]
		self.assertEqual(filters["customer"], ["in", ["Иван", "Пётр", "Новый"]])
		self.assertEqual(filters["docstatus"], ["<", 2])

	def test_настоящий_запрос_работает(self):
		self.assertEqual(orders_count.read(["нет такого клиента"]), {"нет такого клиента": 0})
		self.assertEqual(orders_count.read([]), {})

	def test_только_чтение(self):
		self.assertFalse(orders_count.editable())
		with self.assertRaises(frappe.PermissionError):
			orders_count.write(None, 1)


class TestTelegramAlias(IntegrationTestCase):
	"""Алиас клиента в списке клиентов: берём у Telegram-чата, привязанного к клиенту."""

	def tearDown(self):
		frappe.db.rollback()

	def test_алиас_клиента_через_привязанный_чат(self):
		customer = frappe.get_doc({"doctype": "Customer", "customer_name": "Алиас Тест", "customer_type": "Individual"}).insert()
		chat = frappe.get_doc({"doctype": "Telegram Chat", "chat_id": "8800001", "title": "Алиас Тест", "type": "private"}).insert()
		chat.append("links", {"link_doctype": "Customer", "link_name": customer.name})
		chat.save()
		frappe.get_doc(
			{"doctype": "Telegram User", "telegram_user_id": "8800001", "full_name": "Алиас Тест", "telegram_username": "alias_test"}
		).insert()
		got = telegram_alias.read([customer.name, "нет такого"])
		self.assertEqual(got[customer.name], "@alias_test")
		self.assertIsNone(got["нет такого"])

	def test_клиент_без_чата_без_алиаса_и_пустой_список(self):
		self.assertEqual(telegram_alias.read([]), {})
		customer = frappe.get_doc({"doctype": "Customer", "customer_name": "Без чата", "customer_type": "Individual"}).insert()
		self.assertIsNone(telegram_alias.read([customer.name])[customer.name])

	def test_только_чтение(self):
		self.assertFalse(telegram_alias.editable())
		with self.assertRaises(frappe.PermissionError):
			telegram_alias.write(None, "x")

"""Заказ целиком через ERPNext: расчёт, согласие в следующем ходе, черновик.

Проверяется то, что увидит модель, и то, что ляжет в ERP. Своя компания в KZT
и свой прайс-лист — чтобы не зависеть от того, что заведено на dev-сайте.
"""

import frappe
from frappe.tests import IntegrationTestCase

from habibi_ai import tools

CO = "_Habibi Test Co"
PRICE_LIST = "_Habibi Test Menu"
BURGER = "_HBT-BURGER"
COLA = "_HBT-COLA"
PHONE = "+77019990011"


def _setup_erp():
	if not frappe.db.exists("Company", CO):
		frappe.get_doc(
			{
				"doctype": "Company",
				"company_name": CO,
				"abbr": "HBTC",
				"default_currency": "KZT",
				"country": "Kazakhstan",
				"create_chart_of_accounts_based_on": "Standard Template",
				"chart_of_accounts": "Standard",
			}
		).insert()

	# Без финансового года ERPNext не принимает Sales Order. На рабочих сайтах
	# он заведён мастером настройки; на dev-сайте мастер не проходили.
	year = frappe.utils.getdate(frappe.utils.nowdate()).year
	fiscal_year = frappe.db.get_value(
		"Fiscal Year", {"year_start_date": f"{year}-01-01", "year_end_date": f"{year}-12-31"}
	)
	if not fiscal_year:
		fiscal_year = (
			frappe.get_doc(
				{
					"doctype": "Fiscal Year",
					"year": f"_Habibi {year}",
					"year_start_date": f"{year}-01-01",
					"year_end_date": f"{year}-12-31",
				}
			)
			.insert()
			.name
		)
	fy = frappe.get_doc("Fiscal Year", fiscal_year)
	if fy.companies and not any(row.company == CO for row in fy.companies):
		fy.append("companies", {"company": CO})
		fy.save()

	if not frappe.db.exists("Price List", PRICE_LIST):
		frappe.get_doc(
			{"doctype": "Price List", "price_list_name": PRICE_LIST, "currency": "KZT", "selling": 1, "enabled": 1}
		).insert()

	root_group = frappe.db.get_value("Item Group", {"lft": 1})
	for code, name, rate in ((BURGER, "Test Burger", 2490), (COLA, "Test Cola", 690)):
		if not frappe.db.exists("Item", code):
			frappe.get_doc(
				{
					"doctype": "Item",
					"item_code": code,
					"item_name": name,
					"item_group": root_group,
					"stock_uom": "Nos",
					"is_stock_item": 0,
					"is_sales_item": 1,
				}
			).insert()
		if not frappe.db.exists("Item Price", {"item_code": code, "price_list": PRICE_LIST}):
			frappe.get_doc(
				{"doctype": "Item Price", "item_code": code, "price_list": PRICE_LIST, "price_list_rate": rate}
			).insert()

	abbr = frappe.db.get_value("Company", CO, "abbr")
	account = f"_Test VAT 12% - {abbr}"
	if not frappe.db.exists("Account", account):
		parent = frappe.db.get_value("Account", {"company": CO, "account_type": "Tax", "is_group": 1}) or frappe.db.get_value(
			"Account", {"company": CO, "root_type": "Liability", "is_group": 1}
		)
		frappe.get_doc(
			{
				"doctype": "Account",
				"account_name": "_Test VAT 12%",
				"parent_account": parent,
				"company": CO,
				"account_type": "Tax",
				"tax_rate": 12,
			}
		).insert()
	if not frappe.db.exists("Sales Taxes and Charges Template", {"company": CO, "is_default": 1}):
		frappe.get_doc(
			{
				"doctype": "Sales Taxes and Charges Template",
				"title": "_Test KZ VAT",
				"company": CO,
				"is_default": 1,
				"taxes": [
					{
						"charge_type": "On Net Total",
						"account_head": account,
						"description": "VAT 12%",
						"rate": 12,
						"included_in_print_rate": 1,
					}
				],
			}
		).insert()

	frappe.db.set_single_value("Selling Settings", "selling_price_list", PRICE_LIST)
	frappe.db.set_single_value("Habibi AI Settings", "company", CO)


def ctx(turn, chat=501, channel=None):
	return {"turn_id": turn, "engine_chat_id": chat, "channel_chat": channel}


ORDER = {
	"items": [{"item_code": BURGER, "qty": 1}, {"item_code": "Test Cola", "qty": 2}],
	"fulfilment": "pickup",
	"customer_name": "Тест Клиент",
	"phone": "+7 (701) 999-00-11",
}


def quote_id(text):
	return text.split()[1].rstrip(",")


class TestЗаказ(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		_setup_erp()

	def _quote(self, turn="t1", **kw):
		return tools.execute("quote_order", {**ORDER, **kw}, ctx(turn))

	def test_расчёт_считает_итог_и_ндс_в_erp(self):
		text = self._quote()
		# 2490 + 2×690 = 3870; НДС 12% внутри цены = 3870×12/112 = 414.64
		self.assertIn("Итого: 3 870 KZT, включая VAT 12% — 414.64 KZT", text)
		self.assertIn("Test Cola × 2 — 1 380 KZT", text)
		self.assertIn("Клиент: Тест Клиент, +77019990011", text)
		quote = frappe.get_doc("AI Order Quote", quote_id(text))
		self.assertEqual((quote.grand_total, quote.engine_chat_id, quote.turn_id), (3870, 501, "t1"))

	def test_заказ_в_том_же_ходе_отклоняется(self):
		qid = quote_id(self._quote(turn="t1"))
		result = tools.execute("create_order", {"quote_id": qid}, ctx("t1"))
		self.assertIn("дождись", result)
		self.assertFalse(frappe.db.get_value("AI Order Quote", qid, "sales_order"))

	def test_заказ_создаёт_черновик_с_суммой_расчёта(self):
		qid = quote_id(self._quote())
		result = tools.execute("create_order", {"quote_id": qid}, ctx("t2"))
		so_name = frappe.db.get_value("AI Order Quote", qid, "sales_order")
		self.assertIn(f"Заказ {so_name} создан — черновик", result)
		so = frappe.get_doc("Sales Order", so_name)
		self.assertEqual((so.docstatus, so.company, so.grand_total), (0, CO, 3870))
		self.assertEqual(frappe.db.get_value("Customer", so.customer, "mobile_no"), PHONE)

	def test_повтор_не_создаёт_второй_заказ(self):
		qid = quote_id(self._quote())
		first = tools.execute("create_order", {"quote_id": qid}, ctx("t2"))
		before = frappe.db.count("Sales Order", {"company": CO})
		second = tools.execute("create_order", {"quote_id": qid}, ctx("t3"))
		self.assertEqual(frappe.db.count("Sales Order", {"company": CO}), before)
		self.assertIn("уже создан", second)
		self.assertEqual(first.split()[1], second.split()[1])

	def test_клиент_находится_по_телефону_а_не_заводится_заново(self):
		tools.execute("create_order", {"quote_id": quote_id(self._quote())}, ctx("t2"))
		customers_before = frappe.db.count("Customer")
		# Тот же номер, записанный иначе, — тот же человек
		qid = quote_id(self._quote(customer_name="Другое Имя", phone="+7 701 999 00 11"))
		tools.execute("create_order", {"quote_id": qid}, ctx("t4"))
		self.assertEqual(frappe.db.count("Customer"), customers_before)

	def test_чужой_чат_не_видит_расчёт(self):
		qid = quote_id(self._quote())
		result = tools.execute("create_order", {"quote_id": qid}, ctx("t2", chat=999))
		self.assertIn("не найден", result)

	def test_неизвестная_позиция_не_доходит_до_erp(self):
		before = frappe.db.count("AI Order Quote")
		result = self._quote(items=[{"item_code": "пицца", "qty": 1}])
		self.assertIn("Нет в меню: «пицца»", result)
		self.assertEqual(frappe.db.count("AI Order Quote"), before)

	def test_без_телефона_бот_просит_спросить(self):
		self.assertIn("имя и телефон", self._quote(phone=None))

	def test_без_компании_приём_не_настроен(self):
		frappe.db.set_single_value("Habibi AI Settings", "company", None)
		try:
			self.assertIn("не настроен", self._quote())
		finally:
			frappe.db.set_single_value("Habibi AI Settings", "company", CO)


class TestПривязкаЧата(IntegrationTestCase):
	"""Совмещённая схема: чат без привязки — имя и телефон, после заказа
	чат привязан, и следующий расчёт их уже не просит."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		_setup_erp()

	def test_после_заказа_чат_узнаёт_клиента(self):
		if "habibi_telegram" not in frappe.get_installed_apps():
			self.skipTest("habibi_telegram не установлен")
		chat = frappe.get_doc(
			{"doctype": "Telegram Chat", "chat_id": "990011", "type": "private", "title": "Тест"}
		).insert(ignore_permissions=True)
		channel = ("Telegram Chat", chat.name)

		text = tools.execute("quote_order", ORDER, ctx("t1", channel=channel))
		tools.execute("create_order", {"quote_id": quote_id(text)}, ctx("t2", channel=channel))

		links = frappe.get_doc("Telegram Chat", chat.name).links
		self.assertEqual([(r.link_doctype) for r in links], ["Customer"])

		again = tools.execute(
			"quote_order", {"items": ORDER["items"], "fulfilment": "pickup"}, ctx("t3", channel=channel)
		)
		self.assertIn("Клиент: Тест Клиент", again)

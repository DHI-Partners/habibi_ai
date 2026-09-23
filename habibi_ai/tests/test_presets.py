"""Применение пресета «общепит»: повторный запуск ничего не портит."""

import frappe
from frappe.tests import IntegrationTestCase

from habibi_ai import presets


class TestPresets(IntegrationTestCase):
	def tearDown(self):
		frappe.db.rollback()

	def test_повторное_применение_ничего_не_ломает(self):
		presets.apply("food")
		profile = frappe.get_single("Business Profile")
		profile.rules[0].text = "40 минут"
		profile.save()
		frappe.db.set_value("Telegram Message Template", "order_accepted", "default_template", "Свой текст")
		presets.apply("food")
		self.assertEqual(frappe.get_single("Business Profile").rules[0].text, "40 минут")
		self.assertEqual(frappe.db.get_value("Telegram Message Template", "order_accepted", "default_template"), "Свой текст")

	def test_отсутствующие_поля_вычищаются(self):
		summary = presets.apply("food")
		keys = [s.key for s in frappe.get_single("Cabinet Settings").sections]
		self.assertIn("menu", keys)
		if not frappe.db.exists("DocType", "Delivery Zone"):
			self.assertNotIn("zones", keys)
			self.assertIn("zones", summary["skipped"])

	def test_права_ролей_выданы(self):
		presets.apply("food")
		self.assertTrue(frappe.db.exists("Custom DocPerm", {"parent": "Item", "role": "Habibi Owner", "write": 1}))

	def test_статус_и_сумма_заказов_адаптерами(self):
		"""Статус — из поля воркфлоу сайта (на проде custom_order_status),
		сумма — с валютой заказа; docstatus остаётся для фильтра главной."""
		presets.apply("food")
		orders = next(s for s in frappe.get_single("Cabinet Settings").sections if s.key == "orders")
		lines = orders.list_fields.splitlines()
		self.assertIn("@order_status:Статус", lines)
		self.assertIn("@order_total:Сумма", lines)
		self.assertIn("docstatus:Проведён", lines)

	def test_роли_кабинета_читают_счета_для_проведения(self):
		"""Проведение Sales Order проверяет чтение Account (счёт налога) —
		без него «Принять» падает у владельца и сотрудника."""
		presets.apply("food")
		for role in ("Habibi Owner", "Habibi Staff"):
			self.assertTrue(frappe.db.exists("Custom DocPerm", {"parent": "Account", "role": role, "read": 1}))

	def test_флаги_и_действия_админа_не_перетираются(self):
		"""Выключенная доставка и своё действие отказа — решения админа, а не
		пробел в настройках: повторный пресет их не возвращает."""
		presets.apply("food")
		settings = frappe.get_single("Habibi AI Settings")
		settings.feature_delivery = 0
		settings.reject_action = "Reject"
		settings.save()
		presets.apply("food")
		settings = frappe.get_single("Habibi AI Settings")
		self.assertEqual(settings.feature_delivery, 0)
		self.assertEqual(settings.reject_action, "Reject")

	def test_несохранённые_флаги_и_действия_заполняются(self):
		frappe.db.delete("Singles", {"doctype": "Habibi AI Settings"})
		frappe.clear_document_cache("Habibi AI Settings", "Habibi AI Settings")
		presets.apply("food")
		saved = frappe.db.get_singles_dict("Habibi AI Settings")
		self.assertEqual(str(saved.get("feature_delivery")), "1")
		self.assertEqual(saved.get("accept_action"), "Confirm")
		self.assertEqual(saved.get("reject_action"), "Cancel Order")

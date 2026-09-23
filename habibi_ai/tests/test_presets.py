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

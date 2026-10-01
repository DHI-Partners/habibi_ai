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
		# Колонка — дата создания со временем: по ней же сортируется список («новые сверху»)
		self.assertIn("creation:Создан", lines)
		# Оплата — отдельная колонка и фильтр списка (Select-поле даёт выпадающий фильтр само)
		self.assertIn("custom_payment_status:Оплата", lines)
		self.assertNotIn("transaction_date:Дата", lines)

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

	def test_разделы_кухни_и_курьера_только_для_своих_ролей(self):
		presets.apply("food")
		sections = {s.key: s for s in frappe.get_single("Cabinet Settings").sections}
		self.assertEqual(
			(sections["kitchen"].kind, sections["kitchen"].screen, sections["kitchen"].roles),
			("custom", "kitchen", "Habibi Kitchen"),
		)
		self.assertEqual(
			(sections["courier"].kind, sections["courier"].screen, sections["courier"].roles),
			("custom", "courier", "Habibi Courier"),
		)
		self.assertEqual(sections["courier"].icon, "truck")
		self.assertEqual(sections["kitchen"].icon, "chef-hat")
		# Повторное применение не плодит копий
		presets.apply("food")
		keys = [s.key for s in frappe.get_single("Cabinet Settings").sections]
		self.assertEqual(keys.count("kitchen"), 1)
		self.assertEqual(keys.count("courier"), 1)

	def test_права_кухни_и_курьера_на_заказ(self):
		"""Воркфлоу проверяет write на заказ, orders.apply — read; проведение
		и сохранение подтягивают чтение связанных доктайпов."""
		presets.apply("food")
		for role in ("Habibi Kitchen", "Habibi Courier"):
			with self.subTest(role):
				# Переход проведённого заказа (1 → 1) Frappe считает update_after_submit
				# и требует право submit: без него «Готово» падает с 403
				self.assertTrue(
					frappe.db.exists(
						"Custom DocPerm",
						{"parent": "Sales Order", "role": role, "read": 1, "write": 1, "submit": 1},
					)
				)
				self.assertTrue(frappe.db.exists("Custom DocPerm", {"parent": "Account", "role": role, "read": 1}))
				# Ни удалять, ни отменять заказ они не вправе
				self.assertFalse(
					frappe.db.exists("Custom DocPerm", {"parent": "Sales Order", "role": role, "delete": 1})
				)
				self.assertFalse(
					frappe.db.exists("Custom DocPerm", {"parent": "Sales Order", "role": role, "cancel": 1})
				)
		if frappe.db.exists("DocType", "Employee"):
			self.assertTrue(
				frappe.db.exists("Custom DocPerm", {"parent": "Employee", "role": "Habibi Courier", "read": 1})
			)

	def test_клиенты_с_датой_регистрации_и_числом_заказов(self):
		presets.apply("food")
		customers = next(s for s in frappe.get_single("Cabinet Settings").sections if s.key == "customers")
		lines = customers.list_fields.splitlines()
		self.assertIn("creation:Регистрация", lines)
		self.assertIn("@orders_count:Заказов", lines)
		# Алиас в Telegram — и в списке, и в карточке клиента (только чтение)
		self.assertIn("@telegram_alias:Telegram", lines)
		self.assertIn("@telegram_alias:Telegram", customers.form_fields.splitlines())
		# Дата регистрации и счётчик заказов в форму не попадают
		self.assertNotIn("creation", customers.form_fields)
		self.assertNotIn("orders_count", customers.form_fields)


class TestСправочникиПресета(IntegrationTestCase):
	"""Справочники, которыми владелец управляет сам, умеют и добавлять, и удалять."""

	def _spec(self):
		import json
		from pathlib import Path

		return json.loads((Path(presets.__file__).parent / "presets" / "food.json").read_text(encoding="utf-8"))

	def test_зоны_доставки_создаются_и_удаляются(self):
		spec = self._spec()
		(zones,) = [s for s in spec["sections"] if s["key"] == "zones"]
		self.assertTrue(zones.get("can_create") and zones.get("can_edit") and zones.get("can_delete"))
		# без названия зону не создать: оно и есть имя записи
		self.assertIn("zone_name", zones["form_fields"])
		self.assertTrue({"create", "delete"} <= set(spec["permissions"]["Habibi Owner"]["Delivery Zone"]))

	def test_всё_что_можно_создавать_можно_и_удалять(self):
		for s in self._spec()["sections"]:
			if s["kind"] == "generic" and s.get("can_create"):
				self.assertTrue(s.get("can_delete"), f"раздел «{s['key']}» создаёт записи, но не удаляет")


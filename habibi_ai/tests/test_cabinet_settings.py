"""Режим работы, профиль и подключение Telegram — экраны кабинета «документ целиком»."""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from habibi_ai.cabinet import settings

CO = "_Habibi Settings Test Co"
NO_PROFILE_WRITE_ROLE = "_Habibi Cabinet No Profile Write"


def _ensure_company():
	"""Working Hours.company — Link на Company, реальную запись обязана
	видеть; своя компания, чтобы не зависеть от того, что заведено на сайте."""
	if not frappe.db.exists("Company", CO):
		frappe.get_doc(
			{
				"doctype": "Company",
				"company_name": CO,
				"abbr": "HBST",
				"default_currency": "KZT",
				"country": "Kazakhstan",
				"create_chart_of_accounts_based_on": "Standard Template",
				"chart_of_accounts": "Standard",
			}
		).insert()
	frappe.db.set_single_value("Habibi AI Settings", "company", CO)


def _ensure_role(role_name):
	if not frappe.db.exists("Role", role_name):
		frappe.get_doc({"doctype": "Role", "role_name": role_name, "desk_access": 0}).insert(
			ignore_permissions=True
		)


def _ensure_user(email, role_name):
	if not frappe.db.exists("User", email):
		user = frappe.get_doc(
			{"doctype": "User", "email": email, "first_name": role_name, "send_welcome_email": 0}
		).insert(ignore_permissions=True)
		user.add_roles(role_name)
	return email


class TestCabinetSettings(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		_ensure_company()

	def tearDown(self):
		# Компания и профиль — фикстуры класса (см. setUpClass): полный
		# frappe.db.rollback() здесь стёр бы их для всех тестов после первого,
		# кроме теста прав, который меняет пользователя и должен его вернуть.
		frappe.set_user("Administrator")

	def test_режим_работы_заменяется_целиком(self):
		settings.save_hours(
			[{"weekday": "Понедельник", "kind": "Работа", "opens": "10:00:00", "closes": "22:00:00"}], []
		)
		settings.save_hours(
			[{"weekday": "Вторник", "kind": "Работа", "opens": "11:00:00", "closes": "23:00:00"}], []
		)
		self.assertEqual([r["weekday"] for r in settings.get_hours()["schedule"]], ["Вторник"])

	def test_профиль_не_трогает_подсказки(self):
		doc = frappe.get_single("Business Profile")
		doc.rules = []
		doc.append("rules", {"title": "Доставка", "hint": "Сколько стоит?", "text": ""})
		doc.save()
		settings.save_profile(
			{"business_name": "Habibi", "rules": [{"title": "Доставка", "hint": "подмена", "text": "40 минут"}]}
		)
		rule = settings.get_profile()["rules"][0]
		self.assertEqual((rule["hint"], rule["text"]), ("Сколько стоит?", "40 минут"))

	def test_лишние_поля_профиля_отбрасываются(self):
		settings.save_profile({"business_name": "Habibi", "owner": "evil@x"})
		self.assertNotEqual(frappe.db.get_value("Business Profile", "Business Profile", "owner"), "evil@x")

	def test_подключение_telegram(self):
		with (
			patch(
				"habibi_telegram.habibi_telegram.doctype.telegram_bot.telegram_bot.TelegramBot.validate_api_token"
			),
			patch(
				"habibi_telegram.habibi_telegram.doctype.telegram_bot.telegram_bot.TelegramBot.set_webhook"
			) as hook,
		):
			status = settings.connect_telegram("123:ABC")
		self.assertTrue(status["connected"])
		hook.assert_called_once()

	def test_профиль_без_права_записи_запрещён(self):
		"""Своя роль без единой записи прав на Business Profile — доступа нет вовсе."""
		_ensure_role(NO_PROFILE_WRITE_ROLE)
		frappe.set_user(_ensure_user("cabinet-settings-no-write-test@example.com", NO_PROFILE_WRITE_ROLE))
		with self.assertRaises(frappe.PermissionError):
			settings.save_profile({"business_name": "Habibi"})

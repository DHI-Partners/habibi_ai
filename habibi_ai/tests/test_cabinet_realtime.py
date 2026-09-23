"""Realtime-события кабинета: кому и с чем шлём — и когда не шлём вовсе."""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from habibi_ai.cabinet import realtime


class TestCabinetRealtime(IntegrationTestCase):
	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def test_сообщение_шлёт_событие_чатов_адресно(self):
		"""publish_realtime без user/room шлёт всем на сайте, включая
		портальных пользователей — поэтому проверяем именно адресную
		рассылку, а не только имя события."""
		doc = frappe._dict(doctype="Telegram Message", chat="C1")
		with (
			patch("habibi_ai.cabinet.realtime._recipients", return_value=["owner@example.com"]),
			patch("habibi_ai.cabinet.realtime.frappe.publish_realtime") as pub,
		):
			realtime.on_change(doc)
		pub.assert_called_once_with(
			"habibi_cabinet",
			{"topic": "chats", "chat": "C1"},
			user="owner@example.com",
			after_commit=True,
		)

	def test_пауза_чата_шлёт_событие_чатов(self):
		doc = frappe._dict(doctype="AI Channel Chat", telegram_chat="C2")
		with (
			patch("habibi_ai.cabinet.realtime._recipients", return_value=["staff@example.com"]),
			patch("habibi_ai.cabinet.realtime.frappe.publish_realtime") as pub,
		):
			realtime.on_change(doc)
		pub.assert_called_once_with(
			"habibi_cabinet",
			{"topic": "chats", "chat": "C2"},
			user="staff@example.com",
			after_commit=True,
		)

	def test_заказ_от_бота_шлёт_событие_заказов(self):
		doc = frappe._dict(doctype="Sales Order", name="SO-BOT")
		with (
			patch("habibi_ai.cabinet.realtime.frappe.db.exists", return_value=True),
			patch("habibi_ai.cabinet.realtime._recipients", return_value=["owner@example.com"]),
			patch("habibi_ai.cabinet.realtime.frappe.publish_realtime") as pub,
		):
			realtime.on_change(doc)
		pub.assert_called_once_with(
			"habibi_cabinet",
			{"topic": "orders", "chat": None},
			user="owner@example.com",
			after_commit=True,
		)

	def test_заказ_не_от_бота_не_шумит(self):
		doc = frappe._dict(doctype="Sales Order", name="SO-X")
		with patch("habibi_ai.cabinet.realtime.frappe.publish_realtime") as pub:
			realtime.on_change(doc)
		pub.assert_not_called()

	def test_без_получателей_ничего_не_шлёт(self):
		with (
			patch("habibi_ai.cabinet.realtime._recipients", return_value=[]),
			patch("habibi_ai.cabinet.realtime.frappe.publish_realtime") as pub,
		):
			realtime.on_change(frappe._dict(doctype="Telegram Message", chat="C4"))
		pub.assert_not_called()

	def _dual_role_cabinet_user(self):
		"""Реальный пользователь сайта сразу с двумя ролями кабинета — чтобы
		проверить дедуп настоящим запросом _recipients, а не мок-версией."""
		email = "cabinet-realtime-dual-role-test@example.com"
		if frappe.db.exists("User", email):
			user = frappe.get_doc("User", email)
		else:
			user = frappe.get_doc(
				{"doctype": "User", "email": email, "first_name": "Dual Role", "send_welcome_email": 0}
			).insert(ignore_permissions=True)
		user.add_roles("Habibi Owner", "System Manager")
		return email

	def test_рассылка_без_дублей_при_нескольких_ролях(self):
		"""_recipients — один DISTINCT-запрос по всем ролям кабинета сразу, а
		не цикл по ролям: учётка с двумя ролями не должна получить событие
		дважды."""
		email = self._dual_role_cabinet_user()
		with patch("habibi_ai.cabinet.realtime.frappe.publish_realtime") as pub:
			realtime.on_change(frappe._dict(doctype="Telegram Message", chat="C-DUP"))
		calls_for_user = [c for c in pub.call_args_list if c.kwargs.get("user") == email]
		self.assertEqual(len(calls_for_user), 1)

	def test_ошибка_в_обработке_не_роняет_запись(self):
		"""on_change сидит в том же after_insert списке Telegram Message, что
		и on_message_insert, который специально не роняет запись сообщения —
		событие кабинета вторично и должно вести себя так же."""
		doc = frappe._dict(doctype="Telegram Message", chat="C-ERR")
		with (
			patch("habibi_ai.cabinet.realtime._recipients", side_effect=RuntimeError("сбой")),
			patch("habibi_ai.cabinet.realtime.frappe.log_error") as log_error,
		):
			realtime.on_change(doc)  # не должно поднять исключение
		log_error.assert_called_once()

	def test_дедлок_и_таймаут_блокировки_пробрасываются(self):
		# Транзакция уже откачена базой — проглоти мы ошибку, вызывающий
		# закоммитил бы пустоту и не узнал бы, что событие не ушло
		for error in (frappe.QueryDeadlockError, frappe.QueryTimeoutError):
			with self.subTest(error.__name__):
				doc = frappe._dict(doctype="Telegram Message", chat="C-DEADLOCK")
				with patch("habibi_ai.cabinet.realtime._on_change", side_effect=error("сбой")):
					with self.assertRaises(error):
						realtime.on_change(doc)

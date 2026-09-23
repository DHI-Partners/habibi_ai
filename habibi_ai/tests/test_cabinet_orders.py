"""Действия владельца над заказом бота и уведомление клиента в Telegram."""

from unittest.mock import patch

import frappe
from frappe.permissions import add_permission, update_permission_property
from frappe.tests import IntegrationTestCase

from habibi_ai.cabinet import orders

# Хелперы готового заказа — те же, что в test_orders.py (там уже есть
# сборка тестовой компании, прайс-листа и позиций). Импортируем, а не копируем.
from habibi_ai.tests.test_orders import OrderFixtures

NO_ACCESS_ROLE = "_Habibi Cabinet No Access"
NO_DELETE_ROLE = "_Habibi Cabinet No Delete"


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


class TestCabinetOrders(OrderFixtures, IntegrationTestCase):
	def setUp(self):
		super().setUp()
		self.so = self.make_bot_order()  # черновик SO + AI Order Quote с channel_doctype="Telegram Chat"
		for key, text in (("order_accepted", "Заказ {order} принят"), ("order_rejected", "Не сможем: {reason}")):
			if not frappe.db.exists("Telegram Message Template", key):
				frappe.get_doc({"doctype": "Telegram Message Template", "template_name": key, "default_template": text}).insert()

	def tearDown(self):
		frappe.set_user("Administrator")
		super().tearDown()

	def _no_access_user(self):
		"""Роль без единой записи прав на Sales Order — доступа нет вовсе."""
		_ensure_role(NO_ACCESS_ROLE)
		return _ensure_user("cabinet-no-access-test@example.com", NO_ACCESS_ROLE)

	def _no_delete_user(self):
		"""Роль с чтением/записью/проведением, но без права удалять Sales Order.

		add_permission при первом добавлении новой роли копирует существующие
		права (Sales User и т.п.) в Custom DocPerm — они не теряются."""
		_ensure_role(NO_DELETE_ROLE)
		add_permission("Sales Order", NO_DELETE_ROLE, 0, "read")
		update_permission_property("Sales Order", NO_DELETE_ROLE, 0, "write", 1)
		update_permission_property("Sales Order", NO_DELETE_ROLE, 0, "submit", 1)
		frappe.clear_cache(doctype="Sales Order")
		self.addCleanup(frappe.clear_cache, doctype="Sales Order")
		return _ensure_user("cabinet-no-delete-test@example.com", NO_DELETE_ROLE)

	def test_без_воркфлоу_принять_проводит(self):
		with patch("habibi_ai.cabinet.orders._workflow", return_value=None):
			self.assertEqual([a["kind"] for a in orders.actions(self.so.name)["actions"]], ["accept", "reject"])
			result = orders.apply(self.so.name, "submit")
		self.assertEqual(frappe.db.get_value("Sales Order", self.so.name, "docstatus"), 1)
		self.assertEqual(result["notify"]["text"], f"Заказ {self.so.name} принят")
		self.assertNotIn("chat", result["notify"])

	def test_без_воркфлоу_отклонить_удаляет_черновик(self):
		with patch("habibi_ai.cabinet.orders._workflow", return_value=None):
			result = orders.apply(self.so.name, "discard")
		self.assertFalse(frappe.db.exists("Sales Order", self.so.name))
		self.assertEqual(result["notify"]["kind"], "reject")

	def test_уведомление_уходит_в_чат_заказа(self):
		with patch("habibi_ai.cabinet.orders.telegram.send") as send:
			result = orders.notify(self.so.name, "Заказ принят")
		self.assertEqual(result, {"sent": True, "error": None})
		_channel, chat, text = send.call_args.args
		self.assertEqual(chat, self.quote_chat)
		self.assertEqual(text, "Заказ принят")

	def test_сорвавшаяся_отправка_не_откатывает_заказ(self):
		with patch("habibi_ai.cabinet.orders._workflow", return_value=None):
			orders.apply(self.so.name, "submit")
		with patch("habibi_ai.cabinet.orders.telegram.send", side_effect=Exception("403 Forbidden")):
			result = orders.notify(self.so.name, "Заказ принят")
		self.assertEqual(result["sent"], False)
		# Сырой текст исключения (сетевой) — не в ответе: только общая фраза
		self.assertNotIn("403", result["error"])
		self.assertIn("недоступен", result["error"])
		self.assertEqual(frappe.db.get_value("Sales Order", self.so.name, "docstatus"), 1)
		comments = frappe.get_all("Comment", filters={"reference_name": self.so.name}, pluck="content")
		self.assertTrue(any("не уведомлён" in c for c in comments))

	def test_ошибка_отправки_не_показывает_токен_бота(self):
		# requests кладёт в текст исключения полный URL с токеном — вида
		# https://api.telegram.org/bot123:SECRET/sendMessage
		leaking = "HTTPSConnectionPool: Max retries exceeded /bot123:SECRET/sendMessage"
		with patch("habibi_ai.cabinet.orders.telegram.send", side_effect=Exception(leaking)):
			result = orders.notify(self.so.name, "Заказ принят")
		self.assertFalse(result["sent"])
		self.assertNotIn("SECRET", result["error"])
		comments = frappe.get_all("Comment", filters={"reference_name": self.so.name}, pluck="content")
		self.assertTrue(comments)
		self.assertTrue(all("SECRET" not in c for c in comments))

	def test_заказ_без_чата_не_предлагает_уведомление(self):
		frappe.db.set_value("AI Order Quote", {"sales_order": self.so.name}, "sales_order", None)
		self.assertFalse(orders.actions(self.so.name)["can_notify"])
		with patch("habibi_ai.cabinet.orders._workflow", return_value=None):
			self.assertIsNone(orders.apply(self.so.name, "submit")["notify"])

	def test_уведомление_после_удаления_черновика(self):
		# Ссылка AI Order Quote.sales_order переживает discard (hooks.py:
		# ignore_links_on_delete) — notify() находит чат по имени и без doc'а
		with patch("habibi_ai.cabinet.orders._workflow", return_value=None):
			result = orders.apply(self.so.name, "discard", reason="закончилась булка")
		self.assertIn("закончилась булка", result["notify"]["text"])
		self.assertFalse(frappe.db.exists("Sales Order", self.so.name))
		with patch("habibi_ai.cabinet.orders.telegram.send") as send:
			sent = orders.notify(self.so.name, result["notify"]["text"])
		self.assertTrue(sent["sent"])
		self.assertEqual(send.call_args.args[1], self.quote_chat)

	def test_недоступное_действие_отклоняется(self):
		with patch("habibi_ai.cabinet.orders._workflow", return_value=None):
			with self.assertRaises(frappe.ValidationError):
				orders.apply(self.so.name, "launch_rocket")

	def test_воркфлоу_без_ветки_отклонить_предлагает_удаление_черновика(self):
		"""Прод-воркфлоу («Habibi Burger Order»): из New выхода нет, кроме
		«Confirm». Владельцу всё равно нужен способ закрыть черновик."""
		with (
			patch("habibi_ai.cabinet.orders._workflow", return_value="Habibi Burger Order"),
			patch(
				"habibi_ai.cabinet.orders.get_transitions",
				return_value=[{"action": "Confirm", "state": "New", "next_state": "Confirmed"}],
			),
		):
			self.assertEqual(
				sorted(a["kind"] for a in orders.actions(self.so.name)["actions"]), ["accept", "reject"]
			)
			result = orders.apply(self.so.name, "discard")
		self.assertFalse(frappe.db.exists("Sales Order", self.so.name))
		self.assertEqual(result["notify"]["kind"], "reject")

	def test_уведомление_без_доступа_к_заказу_запрещено(self):
		frappe.set_user(self._no_access_user())
		with patch("habibi_ai.cabinet.orders.telegram.send") as send:
			with self.assertRaises(frappe.PermissionError):
				orders.notify(self.so.name, "Заказ принят")
		send.assert_not_called()

	def test_уведомление_без_расчёта_запрещено(self):
		# Заказа уже нет, и ни один расчёт на него не ссылается — в отличие от
		# discard (там AI Order Quote.sales_order выживает), имя тут ничем не
		# подтверждено вовсе
		frappe.db.set_value("AI Order Quote", {"sales_order": self.so.name}, "sales_order", None)
		frappe.delete_doc("Sales Order", self.so.name, ignore_permissions=True)
		with patch("habibi_ai.cabinet.orders.telegram.send") as send:
			with self.assertRaises(frappe.PermissionError):
				orders.notify(self.so.name, "Заказ принят")
		send.assert_not_called()

	def test_без_права_удалять_отклонить_не_предлагается(self):
		frappe.set_user(self._no_delete_user())
		self.assertEqual([a["kind"] for a in orders.actions(self.so.name)["actions"]], ["accept"])

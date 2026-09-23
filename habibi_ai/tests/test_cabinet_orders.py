"""Действия владельца над заказом бота и уведомление клиента в Telegram."""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from habibi_ai.cabinet import orders

# Хелперы готового заказа — те же, что в test_orders.py (там уже есть
# сборка тестовой компании, прайс-листа и позиций). Импортируем, а не копируем.
from habibi_ai.tests.test_orders import OrderFixtures


class TestCabinetOrders(OrderFixtures, IntegrationTestCase):
	def setUp(self):
		super().setUp()
		self.so = self.make_bot_order()  # черновик SO + AI Order Quote с channel_doctype="Telegram Chat"
		for key, text in (("order_accepted", "Заказ {order} принят"), ("order_rejected", "Не сможем: {reason}")):
			if not frappe.db.exists("Telegram Message Template", key):
				frappe.get_doc({"doctype": "Telegram Message Template", "template_name": key, "default_template": text}).insert()

	def test_без_воркфлоу_принять_проводит(self):
		with patch("habibi_ai.cabinet.orders._workflow", return_value=None):
			self.assertEqual([a["kind"] for a in orders.actions(self.so.name)["actions"]], ["accept", "reject"])
			result = orders.apply(self.so.name, "submit")
		self.assertEqual(frappe.db.get_value("Sales Order", self.so.name, "docstatus"), 1)
		self.assertEqual(result["notify"]["text"], f"Заказ {self.so.name} принят")

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
		self.assertIn("403", result["error"])
		self.assertEqual(frappe.db.get_value("Sales Order", self.so.name, "docstatus"), 1)
		comments = frappe.get_all("Comment", filters={"reference_name": self.so.name}, pluck="content")
		self.assertTrue(any("не уведомлён" in c for c in comments))

	def test_заказ_без_чата_не_предлагает_уведомление(self):
		frappe.db.set_value("AI Order Quote", {"sales_order": self.so.name}, "sales_order", None)
		self.assertFalse(orders.actions(self.so.name)["can_notify"])
		with patch("habibi_ai.cabinet.orders._workflow", return_value=None):
			self.assertIsNone(orders.apply(self.so.name, "submit")["notify"])

	def test_уведомление_после_удаления_черновика(self):
		with patch("habibi_ai.cabinet.orders._workflow", return_value=None):
			result = orders.apply(self.so.name, "discard", reason="закончилась булка")
		self.assertIn("закончилась булка", result["notify"]["text"])
		with patch("habibi_ai.cabinet.orders.telegram.send") as send:
			sent = orders.notify(self.so.name, result["notify"]["text"], chat=result["notify"]["chat"])
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

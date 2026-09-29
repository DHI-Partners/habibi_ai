"""Журнал событий: запись, только-дописывание, выборка контекста."""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from habibi_ai import events

CHAT = 777001
OTHER_CHAT = 777002


class TestЖурнал(IntegrationTestCase):
	def tearDown(self):
		frappe.db.delete("AI Event", {"engine_chat_id": ["in", [CHAT, OTHER_CHAT]]})
		frappe.db.delete("Customer", {"name": "_Cust-1"})
		frappe.db.delete("AI Event", {"channel_name": "_c-777"})
		super().tearDown()

	def test_канал_из_контекста_пишется_в_событие(self):
		name = events.record("message_in", "Клиент написал сообщение", actor="Client", context={"channel_chat": ("Telegram Chat", "_c-777")})
		doc = frappe.get_doc("AI Event", name)
		self.assertEqual((doc.channel_doctype, doc.channel_name), ("Telegram Chat", "_c-777"))

	def test_события_канала_находятся_до_знакомства_с_клиентом(self):
		# Ни чата движка, ни клиента: единственный ключ — канальный чат
		events.record("message_in", "Клиент написал сообщение", actor="Client", context={"channel_chat": ("Telegram Chat", "_c-777")})
		rows = events.recent({"channel_chat": ("Telegram Chat", "_c-777")})
		self.assertEqual([r["event_type"] for r in rows], ["message_in"])

	def test_событие_записывается_с_полями_контекста(self):
		name = events.record(
			"quote_created",
			"Расчёт AIQ-1: 3 980 KZT",
			context={"engine_chat_id": CHAT, "turn_id": "t1"},
			ref=("AI Order Quote", "AIQ-1"),
			data={"grand_total": 3980},
		)
		doc = frappe.get_doc("AI Event", name)
		self.assertEqual(
			(doc.event_type, doc.actor, doc.engine_chat_id, doc.turn_id, doc.ref_name),
			("quote_created", "Bot", CHAT, "t1", "AIQ-1"),
		)
		self.assertIsNotNone(doc.occurred_at)

	def test_запись_не_меняется(self):
		name = events.record("quote_created", "Расчёт", context={"engine_chat_id": CHAT})
		doc = frappe.get_doc("AI Event", name)
		doc.summary = "подделка"
		with self.assertRaises(frappe.ValidationError):
			doc.save()

	def test_summary_одной_строкой_и_не_длиннее_200(self):
		name = events.record("x", "первая\nвторая " + "я" * 400, context={"engine_chat_id": CHAT})
		summary = frappe.db.get_value("AI Event", name, "summary")
		self.assertNotIn("\n", summary)
		self.assertLessEqual(len(summary), 200)

	def test_неизвестный_актор_отвергается(self):
		with patch("habibi_ai.events.frappe.log_error") as log_error:
			self.assertIsNone(events.record("x", "y", actor="Робот", context={"engine_chat_id": CHAT}))
		log_error.assert_called_once()

	def test_сбой_записи_не_роняет_вызывающего(self):
		with (
			patch("habibi_ai.events.frappe.get_doc", side_effect=RuntimeError("сбой")),
			patch("habibi_ai.events.frappe.log_error") as log_error,
		):
			self.assertIsNone(events.record("x", "y", context={"engine_chat_id": CHAT}))
		log_error.assert_called_once()

	def test_дедлок_пробрасывается(self):
		with patch("habibi_ai.events.frappe.get_doc", side_effect=frappe.QueryDeadlockError):
			with self.assertRaises(frappe.QueryDeadlockError):
				events.record("x", "y", context={"engine_chat_id": CHAT})

	def test_выборка_только_своего_чата_по_возрастанию(self):
		events.record("a", "первое", context={"engine_chat_id": CHAT})
		events.record("b", "второе", context={"engine_chat_id": CHAT})
		events.record("c", "чужое", context={"engine_chat_id": OTHER_CHAT})
		rows = events.recent({"engine_chat_id": CHAT})
		self.assertEqual([r["event_type"] for r in rows], ["a", "b"])

	def test_data_возвращается_словарём(self):
		events.record("a", "s", context={"engine_chat_id": CHAT}, data={"k": [1, 2]})
		self.assertEqual(events.recent({"engine_chat_id": CHAT})[0]["data"], {"k": [1, 2]})

	def test_без_чата_и_клиента_выборка_пуста(self):
		self.assertEqual(events.recent({}), [])

	def test_события_клиента_из_другого_чата_попадают_в_выборку(self):
		if not frappe.db.exists("Customer", "_Cust-1"):
			frappe.get_doc(
				{"doctype": "Customer", "customer_name": "_Cust-1", "customer_type": "Individual"}
			).insert(ignore_permissions=True)
		# Чат сменился (другой канал), клиент тот же: его прошлые события видны
		with patch("habibi_ai.events.customers.linked_customer", return_value="_Cust-1"):
			events.record("old", "прошлое", context={"engine_chat_id": OTHER_CHAT})
			events.record("new", "новое", context={"engine_chat_id": CHAT})
			rows = events.recent({"engine_chat_id": CHAT, "channel_chat": ("Telegram Chat", "c")})
		self.assertEqual({r["event_type"] for r in rows}, {"old", "new"})

"""Realtime-события кабинета: кому и с чем шлём — и когда не шлём вовсе."""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from habibi_ai.cabinet import realtime


class TestCabinetRealtime(IntegrationTestCase):
	def test_сообщение_шлёт_событие_чатов_адресно(self):
		"""publish_realtime без user/room шлёт всем на сайте, включая
		портальных пользователей — поэтому проверяем именно адресную
		рассылку, а не только имя события."""
		doc = frappe._dict(doctype="Telegram Message", chat="C1")
		with (
			patch(
				"habibi_ai.cabinet.realtime.get_users_with_role",
				return_value=["owner@example.com"],
			),
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
			patch(
				"habibi_ai.cabinet.realtime.get_users_with_role",
				return_value=["staff@example.com"],
			),
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
			patch(
				"habibi_ai.cabinet.realtime.get_users_with_role",
				return_value=["owner@example.com"],
			),
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

	def test_рассылка_ролям_кабинета_без_дублей(self):
		"""Owner входит и в System Manager на реальных сайтах — пересечение
		ролей не должно привести к двум событиям одному пользователю."""

		def fake_get_users_with_role(role):
			return ["owner@example.com"] if role in ("Habibi Owner", "System Manager") else []

		with (
			patch(
				"habibi_ai.cabinet.realtime.get_users_with_role",
				side_effect=fake_get_users_with_role,
			),
			patch("habibi_ai.cabinet.realtime.frappe.publish_realtime") as pub,
		):
			realtime.on_change(frappe._dict(doctype="AI Channel Chat", telegram_chat="C3"))
		pub.assert_called_once_with(
			"habibi_cabinet",
			{"topic": "chats", "chat": "C3"},
			user="owner@example.com",
			after_commit=True,
		)

	def test_без_получателей_ничего_не_шлёт(self):
		with (
			patch("habibi_ai.cabinet.realtime.get_users_with_role", return_value=[]),
			patch("habibi_ai.cabinet.realtime.frappe.publish_realtime") as pub,
		):
			realtime.on_change(frappe._dict(doctype="Telegram Message", chat="C4"))
		pub.assert_not_called()

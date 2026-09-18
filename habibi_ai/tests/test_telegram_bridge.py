"""Связка habibi_telegram → habibi_ai на живом сайте.

Нужен сайт с обоими приложениями (dev.localhost после Task 0). Движок и
Telegram подменяются: проверяется клей — поля, пары, пауза, задача ответа.
"""

from unittest.mock import Mock, patch

import frappe
from frappe.tests import IntegrationTestCase

from habibi_ai import setup
from habibi_ai.channels import decisions
from habibi_ai.channels import telegram as bridge
from habibi_ai.engine import BotNotFound

BOT = "ai-bridge-test-bot"


def make_bot(**values):
	if not frappe.db.exists("Telegram Bot", BOT):
		with patch(
			"habibi_telegram.telegram_api.TelegramBotAPI.get_me",
			return_value={"is_bot": True, "username": "ai_bridge_test_bot"},
		):
			frappe.get_doc({"doctype": "Telegram Bot", "title": BOT, "api_token": "1:test"}).insert()
	if values:
		frappe.db.set_value("Telegram Bot", BOT, values)
	return frappe.get_doc("Telegram Bot", BOT)


class TestПоляКанала(IntegrationTestCase):
	def test_поля_стоят_и_не_дублируются(self):
		setup.install_telegram_fields()
		setup.install_telegram_fields()
		for doctype in ("Telegram Bot", "Telegram Account"):
			meta = frappe.get_meta(doctype)
			self.assertTrue(meta.has_field("ai_enabled"), doctype)
			self.assertEqual(meta.get_field("ai_bot").fieldtype, "Autocomplete")
			self.assertEqual(
				frappe.db.count("Custom Field", {"dt": doctype, "fieldname": ["like", "ai_%"]}), 3
			)

	def test_без_telegram_ничего_не_ставится(self):
		with patch("frappe.get_installed_apps", return_value=["frappe", "habibi_ai"]):
			with patch("habibi_ai.setup.create_custom_fields") as create:
				setup.install_telegram_fields()
		create.assert_not_called()


class TestПроверкаКанала(IntegrationTestCase):
	def setUp(self):
		setup.install_telegram_fields()
		self.bot = make_bot(ai_enabled=0, ai_bot="")

	def test_без_бота_нельзя_включить(self):
		self.bot.ai_enabled = 1
		self.bot.ai_bot = ""
		with self.assertRaises(frappe.ValidationError):
			bridge.validate_channel(self.bot)

	def test_чужой_бот_не_проходит(self):
		self.bot.ai_enabled = 1
		self.bot.ai_bot = "77"
		client = Mock()
		client._check_bot = Mock(side_effect=BotNotFound(77))
		with patch("habibi_ai.api.get_client", return_value=client):
			with self.assertRaises(frappe.ValidationError):
				bridge.validate_channel(self.bot)

	def test_свой_бот_проходит(self):
		self.bot.ai_enabled = 1
		self.bot.ai_bot = "3"
		client = Mock()
		with patch("habibi_ai.api.get_client", return_value=client):
			bridge.validate_channel(self.bot)
		client._check_bot.assert_called_once_with(3)

	def test_выключенный_не_проверяется(self):
		with patch("habibi_ai.api.get_client") as get_client:
			bridge.validate_channel(self.bot)
		get_client.assert_not_called()


class TestПара(IntegrationTestCase):
	def test_имя_пары_и_снятие_паузы(self):
		make_bot()
		chat = frappe.get_doc({"doctype": "Telegram Chat", "chat_id": "5559001", "title": "Тест", "type": "private"})
		chat.insert(ignore_if_duplicate=True)
		pair = frappe.get_doc({
			"doctype": bridge.PAIR, "channel_doctype": "Telegram Bot", "channel_name": BOT,
			"telegram_chat": chat.name, "ai_paused": 1,
		}).insert()
		self.assertEqual(pair.name, decisions.pair_name("Telegram Bot", BOT, chat.name))
		self.assertEqual(pair.paused_reason, bridge.REASON_MANUAL)
		self.assertIsNotNone(pair.paused_on)

		pair.ai_paused = 0
		pair.save()
		self.assertFalse(pair.paused_reason)
		self.assertIsNone(pair.paused_on)

"""Когда ИИ отвечает в канал.

Без frappe: это решения, ошибка в которых означает либо ответ на вчерашнее
сообщение, либо переписку двух ботов по кругу, — проверяются они первыми и
быстро.
"""

import unittest
from datetime import datetime, timedelta

from habibi_ai.channels import decisions

NOW = datetime(2026, 9, 18, 12, 0, 0)
ON = {"ai_enabled": 1, "ai_bot": "3"}


def message(**overrides):
	base = {"direction": "Incoming", "content": "Здравствуйте", "sent_on": NOW - timedelta(seconds=5)}
	base.update(overrides)
	return base


class TestShouldReply(unittest.TestCase):
	def test_отвечаем(self):
		self.assertTrue(decisions.should_reply(message(), ON, None, False, NOW))

	def test_не_отвечаем(self):
		cases = {
			"канал выключен": (message(), {"ai_enabled": 0, "ai_bot": "3"}, None, False),
			"бот не выбран": (message(), {"ai_enabled": 1, "ai_bot": ""}, None, False),
			"настроек нет": (message(), None, None, False),
			"чат на паузе": (message(), ON, {"ai_paused": 1}, False),
			"пишет бот": (message(), ON, None, True),
			"исходящее": (message(direction="Outgoing"), ON, None, False),
			"пусто": (message(content=""), ON, None, False),
			"пробелы": (message(content="   "), ON, None, False),
			"голосовое": (message(content="[voice]"), ON, None, False),
			"служебное": (message(content="[chatjoinedbylink]"), ON, None, False),
			"старое": (message(sent_on=NOW - timedelta(minutes=6)), ON, None, False),
		}
		for name, (msg, channel, pair, is_bot) in cases.items():
			with self.subTest(name):
				self.assertFalse(decisions.should_reply(msg, channel, pair, is_bot, NOW))

	def test_граница_свежести_включительно(self):
		self.assertTrue(decisions.should_reply(message(sent_on=NOW - decisions.FRESH_FOR), ON, None, False, NOW))

	def test_без_даты_считается_свежим(self):
		# Вебхук бота приходит сразу; дата пуста только у записей до миграции
		self.assertTrue(decisions.should_reply(message(sent_on=None), ON, None, False, NOW))

	def test_пауза_снята(self):
		self.assertTrue(decisions.should_reply(message(), ON, {"ai_paused": 0}, False, NOW))

	def test_подпись_к_фото_это_текст(self):
		self.assertTrue(decisions.should_reply(message(content="Сколько стоит это?"), ON, None, False, NOW))


class TestCombine(unittest.TestCase):
	def test_склеивает_через_перевод_строки_без_пометок(self):
		self.assertEqual(
			decisions.combine(["Здравствуйте", "[photo]", " хочу пиццу ", ""]),
			"Здравствуйте\nхочу пиццу",
		)


class TestПраваНаЗапись(unittest.TestCase):
	def test_узнаёт_запрет(self):
		for text in (
			"Forbidden: bot was blocked by the user",
			"Bad Request: not enough rights to send text messages to the chat",
			"You can't write in this chat (caused by SendMessageRequest)",
			"CHAT_WRITE_FORBIDDEN",
			"Not enough rights in this chat",
		):
			with self.subTest(text):
				self.assertTrue(decisions.is_write_forbidden(text))

	def test_прочие_ошибки_не_запрет(self):
		for text in ("Telegram asks to wait 30 seconds before trying again", "Timeout", ""):
			with self.subTest(text):
				self.assertFalse(decisions.is_write_forbidden(text))


class TestИмена(unittest.TestCase):
	def test_имя_пары(self):
		self.assertEqual(decisions.pair_name("Telegram Bot", "shop", "555"), "Telegram Bot:shop:555")

	def test_внешний_пользователь_движка(self):
		self.assertEqual(
			decisions.external_user("Telegram Account", "manager", "555"),
			"telegram:Telegram Account:manager:555",
		)

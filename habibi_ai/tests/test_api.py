"""Тесты гейта на трассировку.

В отличие от test_engine.py, этот модуль frappe импортирует — api.py без него
не существует. Сайт при этом не нужен: и роли, и клиент движка подменяются,
к базе обращений нет. Проверяется ровно одно решение — просить трассировку
или нет, — и оно единственное, чья ошибка отдаёт тенанту чужой system prompt.
"""

import unittest
from unittest.mock import Mock, patch

import frappe

from habibi_ai import api


class TestГейтТрассировки(unittest.TestCase):
	def _вызвать_с_ролями(self, roles):
		client = Mock()
		client.send_message = Mock(return_value={"response": "ок"})
		with patch("frappe.get_roles", return_value=roles):
			with patch("habibi_ai.api.get_client", return_value=client):
				api.send_message(1, "привет")
		return client.send_message.call_args

	def test_без_роли_трассировка_не_запрашивается(self):
		args = self._вызвать_с_ролями(["System Manager"])
		self.assertFalse(args.kwargs["debug"])

	def test_с_ролью_трассировка_запрашивается(self):
		args = self._вызвать_с_ролями(["System Manager", api.DEBUG_ROLE])
		self.assertTrue(args.kwargs["debug"])

	def test_роль_не_подбирается_по_подстроке(self):
		# Проверка вхождения в список, а не поиск подстроки: роль с похожим
		# именем не должна открывать доступ.
		args = self._вызвать_с_ролями(["Habibi AI Debugging Assistant"])
		self.assertFalse(args.kwargs["debug"])


class TestГейтКонфигурации(unittest.TestCase):
	"""get_bot_config отдаёт те же тексты промптов, что и трассировка
	send_message — значит и гейт у него должен быть тот же: без роли — ничего,
	даже не урезанный ответ, а отказ, и клиент к движку вообще не должен
	вызываться.
	"""

	def _client(self):
		client = Mock()
		client.get_bot_config = Mock(
			return_value={
				"bot": {"id": 1, "name": "Бот", "global_system_prompt": "секрет"},
				"router_prompt": "правила роутера",
				"scenarios": [],
			}
		)
		return client

	def test_без_роли_конфигурация_не_отдаётся(self):
		client = self._client()
		with patch("frappe.get_roles", return_value=["System Manager"]):
			with patch("habibi_ai.api.get_client", return_value=client):
				with self.assertRaises(frappe.PermissionError):
					api.get_bot_config(1)
		client.get_bot_config.assert_not_called()

	def test_с_ролью_конфигурация_отдаётся(self):
		client = self._client()
		with patch("frappe.get_roles", return_value=["System Manager", api.DEBUG_ROLE]):
			with patch("habibi_ai.api.get_client", return_value=client):
				result = api.get_bot_config(1)
		client.get_bot_config.assert_called_once_with(1)
		self.assertEqual(result["bot"]["global_system_prompt"], "секрет")
		self.assertEqual(result["router_prompt"], "правила роутера")

	def test_роль_не_подбирается_по_подстроке(self):
		client = self._client()
		with patch("frappe.get_roles", return_value=["Habibi AI Debugging Assistant"]):
			with patch("habibi_ai.api.get_client", return_value=client):
				with self.assertRaises(frappe.PermissionError):
					api.get_bot_config(1)
		client.get_bot_config.assert_not_called()

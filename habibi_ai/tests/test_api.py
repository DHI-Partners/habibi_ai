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
		# Цикл теперь ведёт api.send_message через client.step, а не через
		# client.send_message — сигнатура и метод сменились в задаче 5.
		client = Mock()
		client.step = Mock(return_value={"type": "text", "content": "ок"})
		with patch("frappe.get_roles", return_value=roles):
			with patch("habibi_ai.api.get_client", return_value=client):
				api.send_message(1, "привет")
		return client.step.call_args

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

	def test_роль_не_подбирается_по_подстроке(self):
		client = self._client()
		with patch("frappe.get_roles", return_value=["Habibi AI Debugging Assistant"]):
			with patch("habibi_ai.api.get_client", return_value=client):
				with self.assertRaises(frappe.PermissionError):
					api.get_bot_config(1)
		client.get_bot_config.assert_not_called()


class TestЦиклИнструментов(unittest.TestCase):
	def _client_с_шагами(self, steps):
		client = Mock()
		client.step = Mock(side_effect=steps)
		return client

	def test_шаг_неизвестной_формы_даёт_внятную_ошибку(self):
		# Движок — соседний репозиторий со своим циклом релизов. Рассинхрон
		# контракта должен называть виновника, а не падать KeyError в прокси.
		client = self._client_с_шагами([{"type": "нечто"}])
		with patch("frappe.get_roles", return_value=[]):
			with patch("habibi_ai.api.get_client", return_value=client):
				with self.assertRaises(frappe.ValidationError) as cm:
					api.send_message(1, "привет")
		self.assertIn("неизвестной формы", str(cm.exception))

	def test_текст_с_первого_шага_отдаётся_как_есть(self):
		client = self._client_с_шагами([{"type": "text", "content": "привет"}])
		with patch("frappe.get_roles", return_value=[]):
			with patch("habibi_ai.api.get_client", return_value=client):
				result = api.send_message(1, "привет")
		self.assertEqual(result["response"], "привет")
		self.assertEqual(client.step.call_count, 1)

	def test_вызов_инструмента_исполняется_и_цикл_продолжается(self):
		client = self._client_с_шагами([
			{"type": "tool_use", "id": "t1", "name": "get_menu", "input": {}},
			{"type": "text", "content": "шаурма 350"},
		])
		with patch("frappe.get_roles", return_value=[]):
			with patch("habibi_ai.api.get_client", return_value=client):
				with patch("habibi_ai.tools.execute", return_value="шаурма — 350") as run:
					result = api.send_message(1, "что есть?")

		run.assert_called_once_with("get_menu", {})
		self.assertEqual(result["response"], "шаурма 350")
		# Результат инструмента ушёл во второй вызов движка.
		turn = client.step.call_args_list[1].kwargs["turn"]
		self.assertEqual(turn[1], {"type": "tool_result", "id": "t1", "content": "шаурма — 350"})

	def test_бесконечный_цикл_обрывается_ошибкой(self):
		# Модель, которая вызывает инструменты и не приходит к ответу, означает,
		# что задача ей не по силам. Молчаливая остановка скрыла бы это.
		steps = [{"type": "tool_use", "id": f"t{i}", "name": "get_menu", "input": {}} for i in range(api.MAX_LOOP + 1)]
		client = self._client_с_шагами(steps)
		with patch("frappe.get_roles", return_value=[]):
			with patch("habibi_ai.api.get_client", return_value=client):
				with patch("habibi_ai.tools.execute", return_value="[]"):
					with self.assertRaises(Exception):
						api.send_message(1, "зациклись")

	def test_инструменты_подаются_только_объявленные(self):
		client = self._client_с_шагами([{"type": "text", "content": "ок"}])
		with patch("frappe.get_roles", return_value=[]):
			with patch("habibi_ai.api.get_client", return_value=client):
				api.send_message(1, "привет")
		sent = client.step.call_args.kwargs["tools"]
		self.assertTrue(all("run" not in d for d in sent))

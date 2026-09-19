"""Тесты гейта на трассировку.

В отличие от test_engine.py, этот модуль frappe импортирует — api.py без него
не существует. Сайт при этом не нужен: и роли, и клиент движка подменяются,
к базе обращений нет. Проверяется ровно одно решение — просить трассировку
или нет, — и оно единственное, чья ошибка отдаёт тенанту чужой system prompt.
"""

import unittest
from datetime import datetime
from unittest.mock import Mock, patch

import frappe

from habibi_ai import api


class TestГейтТрассировки(unittest.TestCase):
	def _вызвать_с_ролями(self, roles):
		# Цикл теперь ведёт api.send_message через client.step, а не через
		# client.send_message — сигнатура и метод сменились в задаче 5.
		client = Mock()
		client.get_chat = Mock(return_value={"id": 1, "external_user": frappe.session.user})
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
		client.get_chat = Mock(return_value={"id": 1, "external_user": frappe.session.user})
		client.step = Mock(side_effect=steps)
		return client

	def test_лимит_витков_берётся_у_бота(self):
		# Поле бота, а не константа: сценарию с цепочкой инструментов витков
		# нужно больше. Проверяем через число обращений к движку, а не через
		# текст ошибки — текст можно подогнать, счётчик нет.
		client = self._client_с_шагами([{"type": "tool_use", "id": "t", "name": "нет_такого", "input": {}}] * 10)
		client.get_max_loop = Mock(return_value=2)
		with patch("frappe.get_roles", return_value=[]):
			with patch("habibi_ai.api.get_client", return_value=client):
				with self.assertRaises(frappe.ValidationError):
					api.send_message(1, "привет")
		self.assertEqual(client.step.call_count, 2)

	def test_непригодный_лимит_заменяется_значением_по_умолчанию(self):
		# Ноль из ручной правки выродил бы цикл в мгновенную ошибку «не смог
		# ответить», и причину искали бы в модели, а не в поле бота.
		client = self._client_с_шагами([{"type": "tool_use", "id": "t", "name": "нет_такого", "input": {}}] * 20)
		client.get_max_loop = Mock(return_value=0)
		with patch("frappe.get_roles", return_value=[]):
			with patch("habibi_ai.api.get_client", return_value=client):
				with self.assertRaises(frappe.ValidationError):
					api.send_message(1, "привет")
		self.assertEqual(client.step.call_count, api.MAX_LOOP)

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

		run.assert_called_once()
		name, args, context = run.call_args.args
		self.assertEqual((name, args), ("get_menu", {}))
		# Консоль отладки: канального чата нет, клиента узнают только по телефону
		self.assertEqual(context["engine_chat_id"], 1)
		self.assertIsNone(context["channel_chat"])
		self.assertTrue(context["turn_id"])
		# Консоль: сообщение человека пришло в момент хода
		self.assertIsNotNone(context["message_at"])
		self.assertEqual(result["response"], "шаурма 350")
		# Результат инструмента ушёл во второй вызов движка.
		turn = client.step.call_args_list[1].kwargs["turn"]
		self.assertEqual(turn[1], {"type": "tool_result", "id": "t1", "content": "шаурма — 350"})

	def test_каждый_ход_получает_свой_turn_id(self):
		# create_order отказывает в том же ходе, что quote_order. Совпади
		# turn_id у двух ходов — отказ сработал бы и там, где клиент уже
		# ответил «да».
		seen = []
		for _ in range(2):
			client = self._client_с_шагами([
				{"type": "tool_use", "id": "t1", "name": "get_menu", "input": {}},
				{"type": "text", "content": "ок"},
			])
			with patch("habibi_ai.tools.execute", return_value="меню") as run:
				api.run_turn(client, 7, "что есть?", channel_chat=("Telegram Chat", "c1"))
			seen.append(run.call_args.args[2])
		self.assertNotEqual(seen[0]["turn_id"], seen[1]["turn_id"])
		self.assertEqual(seen[0]["channel_chat"], ("Telegram Chat", "c1"))
		self.assertEqual(seen[0]["engine_chat_id"], 7)

	def test_время_сообщения_канала_доезжает_до_контекста(self):
		client = self._client_с_шагами([
			{"type": "tool_use", "id": "t1", "name": "get_menu", "input": {}},
			{"type": "text", "content": "ок"},
		])
		sent = datetime(2026, 9, 21, 12, 0)
		with patch("habibi_ai.tools.execute", return_value="меню") as run:
			api.run_turn(client, 7, "да", channel_chat=("Telegram Chat", "c1"), message_at=sent)
		self.assertEqual(run.call_args.args[2]["message_at"], sent)

	def test_raw_доезжает_до_следующего_шага(self):
		# Движок может прислать content-блоки ответа модели целиком (thinking
		# рядом с tool_use у моделей с адаптивным мышлением). Прокси их не
		# читает, но обязан довезти нетронутыми до следующего вызова step —
		# иначе провайдер отклонит виток ассистента, собранный заново.
		raw_blocks = [{"type": "thinking", "text": "..."}, {"type": "tool_use", "id": "t1"}]
		client = self._client_с_шагами([
			{"type": "tool_use", "id": "t1", "name": "get_menu", "input": {}, "raw": raw_blocks},
			{"type": "text", "content": "шаурма 350"},
		])
		with patch("frappe.get_roles", return_value=[]):
			with patch("habibi_ai.api.get_client", return_value=client):
				with patch("habibi_ai.tools.execute", return_value="шаурма — 350"):
					api.send_message(1, "что есть?")

		turn = client.step.call_args_list[1].kwargs["turn"]
		self.assertEqual(turn[0]["raw"], raw_blocks)

	def test_без_raw_ключ_не_появляется(self):
		# Отсутствие поля у шага — не то же самое, что пустое значение: ключ
		# raw не должен появляться в turn вовсе, если движок его не прислал.
		client = self._client_с_шагами([
			{"type": "tool_use", "id": "t1", "name": "get_menu", "input": {}},
			{"type": "text", "content": "шаурма 350"},
		])
		with patch("frappe.get_roles", return_value=[]):
			with patch("habibi_ai.api.get_client", return_value=client):
				with patch("habibi_ai.tools.execute", return_value="шаурма — 350"):
					api.send_message(1, "что есть?")

		turn = client.step.call_args_list[1].kwargs["turn"]
		self.assertNotIn("raw", turn[0])

	def test_имя_вне_предложенного_списка_не_исполняется(self):
		# Движок — соседний сервис: имя, которое он попросил исполнить, не
		# обязано совпадать с тем, что прокси готов исполнить. Решать это
		# должна сторона, владеющая правами тенанта. Отказ идёт моделью
		# текстом, а не падением хода.
		client = self._client_с_шагами([
			{"type": "tool_use", "id": "t1", "name": "cancel_all_orders", "input": {}},
			{"type": "text", "content": "готово"},
		])
		with patch("frappe.get_roles", return_value=[]):
			with patch("habibi_ai.api.get_client", return_value=client):
				with patch("habibi_ai.tools.execute") as run:
					result = api.send_message(1, "закажи")

		run.assert_not_called()
		self.assertEqual(result["response"], "готово")
		turn = client.step.call_args_list[1].kwargs["turn"]
		rejection = turn[1]
		self.assertEqual(rejection["type"], "tool_result")
		self.assertIn("cancel_all_orders", rejection["content"])

	def test_отказ_на_неразрешённое_имя_виден_в_трассировке(self):
		# Спека: отказ должен быть виден не только модели в tool_result, но и
		# человеку в трассировке хода.
		client = self._client_с_шагами([
			{"type": "tool_use", "id": "t1", "name": "cancel_all_orders", "input": {}},
			{"type": "text", "content": "готово"},
		])
		with patch("frappe.get_roles", return_value=[api.DEBUG_ROLE]):
			with patch("habibi_ai.api.get_client", return_value=client):
				with patch("habibi_ai.tools.execute") as run:
					result = api.send_message(1, "закажи")

		run.assert_not_called()
		rejected = [s for s in result["debug"] if s["step"] == "tool_rejected"]
		self.assertEqual(len(rejected), 1)
		self.assertEqual(rejected[0]["data"]["name"], "cancel_all_orders")

	def test_виток_цикла_виден_в_трассировке(self):
		# Спека §8: в трассировке должен быть виден номер витка и лимит, а
		# граница между витками движка — не размыта. Собственный шаг прокси
		# должен встать ПЕРЕД шагами движка за этот же виток.
		client = self._client_с_шагами([
			{
				"type": "tool_use",
				"id": "t1",
				"name": "get_menu",
				"input": {},
				"debug": [{"step": "engine_step_1", "data": {}}],
			},
			{"type": "text", "content": "готово", "debug": [{"step": "engine_step_2", "data": {}}]},
		])
		client.get_max_loop = Mock(return_value=5)
		with patch("frappe.get_roles", return_value=[api.DEBUG_ROLE]):
			with patch("habibi_ai.api.get_client", return_value=client):
				with patch("habibi_ai.tools.execute", return_value="меню"):
					result = api.send_message(1, "что есть?")

		loop_steps = [s for s in result["debug"] if s["step"] == "loop"]
		self.assertEqual([s["data"]["iteration"] for s in loop_steps], [1, 2])
		self.assertTrue(all(s["data"]["max_loop"] == 5 for s in loop_steps))
		# Первый шаг витка прокси должен идти раньше движковых шагов того же
		# витка, иначе номер витка подписывал бы чужие данные.
		self.assertLess(
			result["debug"].index(loop_steps[0]),
			[i for i, s in enumerate(result["debug"]) if s["step"] == "engine_step_1"][0],
		)

	def test_бесконечный_цикл_обрывается_ошибкой(self):
		# Модель, которая вызывает инструменты и не приходит к ответу, означает,
		# что задача ей не по силам. Молчаливая остановка скрыла бы это.
		#
		# assertRaises(Exception) прошёл бы и на StopIteration от исчерпанного
		# side_effect — то есть и на сломанном цикле, который вызвал step лишний
		# раз. Ловим конкретно frappe.ValidationError и считаем обращения к
		# движку, как в соседних тестах этого класса.
		steps = [{"type": "tool_use", "id": f"t{i}", "name": "get_menu", "input": {}} for i in range(api.MAX_LOOP + 1)]
		client = self._client_с_шагами(steps)
		with patch("frappe.get_roles", return_value=[]):
			with patch("habibi_ai.api.get_client", return_value=client):
				with patch("habibi_ai.tools.execute", return_value="[]"):
					with self.assertRaises(frappe.ValidationError):
						api.send_message(1, "зациклись")
		self.assertEqual(client.step.call_count, api.MAX_LOOP)

	def test_инструменты_подаются_только_объявленные(self):
		# Имя обещает проверку состава предложенных инструментов, не только
		# зачистку служебного ключа "run" — раньше тело этого не проверяло.
		client = self._client_с_шагами([{"type": "text", "content": "ок"}])
		with patch("frappe.get_roles", return_value=[]):
			with patch("habibi_ai.api.get_client", return_value=client):
				api.send_message(1, "привет")
		sent = client.step.call_args.kwargs["tools"]
		self.assertTrue(all("run" not in d for d in sent))
		self.assertEqual({d["name"] for d in sent}, set(api._tool_names()))


class TestЧужойЧат(unittest.TestCase):
	"""В чатах движка теперь и переписка клиентов из Telegram — её не должен
	читать и продолжать любой вошедший пользователь, подобрав номер чата.
	Чужой чат обязан выглядеть так же, как несуществующий: иначе перебором
	можно узнать, какие номера заняты.
	"""

	def _client(self, owner):
		client = Mock()
		client.get_chat = Mock(return_value={"id": 7, "external_user": owner})
		client.get_messages = Mock(return_value=[{"role": "user", "content": "привет"}])
		client.step = Mock(return_value={"type": "text", "content": "ок"})
		return client

	def test_свой_чат_открывается(self):
		client = self._client(frappe.session.user)
		with patch("habibi_ai.api.get_client", return_value=client):
			result = api.get_chat(7)
		self.assertEqual(result["chat"]["id"], 7)
		self.assertEqual(len(result["messages"]), 1)

	def test_чужой_чат_как_несуществующий(self):
		client = self._client("telegram:Telegram Bot:shop:5559100")
		with patch("habibi_ai.api.get_client", return_value=client):
			with self.assertRaises(frappe.DoesNotExistError) as cm:
				api.get_chat(7)
		self.assertIn("Чат не найден", str(cm.exception))
		client.get_messages.assert_not_called()

	def test_в_свой_чат_пишется(self):
		client = self._client(frappe.session.user)
		with patch("frappe.get_roles", return_value=[]):
			with patch("habibi_ai.api.get_client", return_value=client):
				result = api.send_message(7, "привет")
		self.assertEqual(result["response"], "ок")

	def test_в_чужой_чат_не_пишется(self):
		client = self._client("telegram:Telegram Account:manager:5559100")
		with patch("frappe.get_roles", return_value=[]):
			with patch("habibi_ai.api.get_client", return_value=client):
				with self.assertRaises(frappe.DoesNotExistError) as cm:
					api.send_message(7, "привет")
		self.assertIn("Чат не найден", str(cm.exception))
		client.step.assert_not_called()

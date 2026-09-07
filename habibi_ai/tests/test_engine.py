"""Тесты изоляции тенантов.

Не импортируют frappe: engine.py от него не зависит, и эти проверки должны
гоняться за секунды, без поднятия сайта. Изоляция — единственное, что здесь
по-настоящему опасно сломать, поэтому она покрыта первой.
"""

import unittest
from unittest.mock import Mock

from habibi_ai.engine import BotNotFound, ChatNotFound, EngineClient, EngineError, scoped_filter


class TestScopedFilter(unittest.TestCase):
	def test_приватные_данные_видны_только_своему_тенанту(self):
		self.assertEqual(
			scoped_filter("naqwa.habibi-erp.com"),
			{"tenant": {"_eq": "naqwa.habibi-erp.com"}},
		)

	def test_общие_записи_доступны_когда_разрешены(self):
		self.assertEqual(
			scoped_filter("naqwa.habibi-erp.com", allow_shared=True),
			{
				"_or": [
					{"tenant": {"_eq": "naqwa.habibi-erp.com"}},
					{"tenant": {"_null": True}},
				]
			},
		)

	def test_дополнительный_фильтр_соединяется_через_and(self):
		self.assertEqual(
			scoped_filter("a.example.com", extra={"bot_id": {"_eq": 3}}),
			{
				"_and": [
					{"tenant": {"_eq": "a.example.com"}},
					{"bot_id": {"_eq": 3}},
				]
			},
		)

	def test_пустой_тенант_отвергается(self):
		# Пустая строка дала бы фильтр, под который не попадает ничего, но
		# ошибку конфигурации лучше увидеть сразу, а не как пустой список.
		for value in ("", None):
			with self.assertRaises(ValueError):
				scoped_filter(value)


class TestChatOwnership(unittest.TestCase):
	def setUp(self):
		self.client = EngineClient("http://ai-engine:8055", "t", "a.example.com")
		self.client._items = Mock(return_value=[])

	def test_чужой_чат_не_отдаётся(self):
		# Движок вернул пустой список: фильтр по тенанту не пропустил чат.
		with self.assertRaises(ChatNotFound):
			self.client.get_chat(42)

	def test_запрос_чата_всегда_ограничен_тенантом(self):
		self.client._items = Mock(return_value=[{"id": 42}])
		self.client.get_chat(42)
		params = self.client._items.call_args.args[1]
		self.assertIn("a.example.com", str(params["filter"]))

	def test_фильтр_уходит_в_запрос_json_строкой(self):
		# Directus ждёт filter JSON-строкой. Если отдать словарь, requests
		# сериализует только ключи — получается filter=_or и ответ 400.
		captured = {}

		def fake_get(url, params=None, timeout=None):
			captured["params"] = params
			raise RuntimeError("stop")

		# Свежий клиент: в setUp подменён _items, а здесь проверяется именно он.
		client = EngineClient("http://ai-engine:8055", "t", "a.example.com")
		client.session.get = fake_get
		with self.assertRaises(RuntimeError):
			client.list_bots()
		self.assertIsInstance(captured["params"]["filter"], str)
		self.assertIn("a.example.com", captured["params"]["filter"])

	def test_создание_чата_проставляет_тенанта_и_пользователя(self):
		# Чат обязан создаваться через прокси: поле tenant в customer_chats
		# обязательное, а расширение движка о тенантах ничего не знает —
		# созданный им чат просто не пройдёт INSERT.
		self.client._items = Mock(return_value=[{"id": 3}])  # бот принят _check_bot
		self.client._post = Mock(return_value={"data": {"id": 7}})
		self.client.create_chat(bot_id=3, external_user="user@example.com")
		payload = self.client._post.call_args.args[1]
		self.assertEqual(payload["tenant"], "a.example.com")
		self.assertEqual(payload["external_user"], "user@example.com")
		self.assertEqual(payload["bot_id"], 3)

	def test_отправка_сообщения_проверяет_чат_до_обращения_к_движку(self):
		# Без этой проверки номер чужого чата ушёл бы в ai-process-message
		# в обход фильтра, и движок ответил бы по чужой переписке.
		self.client._post = Mock()
		with self.assertRaises(ChatNotFound):
			self.client.send_message(42, "привет")
		self.client._post.assert_not_called()


class TestBotOwnership(unittest.TestCase):
	"""bot_id приезжает из браузера непроверенным — как и chat_id.

	Без этих проверок тенант, угадавший чужой числовой bot_id, получил бы
	разговор, ведомый чужим global_system_prompt: create_chat завёл бы ему
	чат на чужом боте, а send_message с bot_id подменил бы бота прямо внутри
	своего чата.
	"""

	def setUp(self):
		self.client = EngineClient("http://ai-engine:8055", "t", "a.example.com")

	def test_create_chat_отвергает_чужого_бота(self):
		# Движок вернул пустой список: фильтр по тенанту (и allow_shared) не
		# пропустил бота — значит бот либо не существует, либо чужой.
		self.client._items = Mock(return_value=[])
		self.client._post = Mock()
		with self.assertRaises(BotNotFound):
			self.client.create_chat(bot_id=999, external_user="user@example.com")
		self.client._post.assert_not_called()

	def test_create_chat_проверяет_бота_фильтром_тенанта_с_общими(self):
		# Если этот фильтр когда-нибудь потеряет allow_shared или tenant,
		# результат на моках всё ещё будет выглядеть нормально — поэтому
		# смотрим на сам фильтр запроса, а не на то, что метод не упал.
		self.client._items = Mock(return_value=[{"id": 3}])
		self.client._post = Mock(return_value={"data": {"id": 7}})
		self.client.create_chat(bot_id=3, external_user="user@example.com")
		collection, params = self.client._items.call_args.args
		self.assertEqual(collection, "ai_bots")
		self.assertEqual(
			params["filter"],
			{
				"_and": [
					{
						"_or": [
							{"tenant": {"_eq": "a.example.com"}},
							{"tenant": {"_null": True}},
						]
					},
					{"id": {"_eq": 3}},
				]
			},
		)

	def test_create_chat_пропускает_своего_и_общего_бота(self):
		self.client._items = Mock(return_value=[{"id": 3}])
		self.client._post = Mock(return_value={"data": {"id": 7}})
		self.client.create_chat(bot_id=3, external_user="user@example.com")
		self.client._post.assert_called_once()

	def test_send_message_отвергает_чужого_бота(self):
		self.client.get_chat = Mock(return_value={"id": 42, "bot_id": 1})
		self.client._items = Mock(return_value=[])
		self.client._post = Mock()
		with self.assertRaises(BotNotFound):
			self.client.send_message(42, "привет", bot_id=999)
		self.client._post.assert_not_called()

	def test_send_message_без_bot_id_бота_не_проверяет(self):
		# bot_id не передан — движок возьмёт chat.bot_id, а он уже проверен
		# при создании чата. Лишний запрос к ai_bots здесь не нужен.
		self.client.get_chat = Mock(return_value={"id": 42, "bot_id": 1})
		self.client._items = Mock(return_value=[])
		self.client._post = Mock(return_value={"response": "ок"})
		self.client.send_message(42, "привет")
		self.client._items.assert_not_called()
		self.client._post.assert_called_once()

	def test_send_message_со_своим_bot_id_проходит(self):
		self.client.get_chat = Mock(return_value={"id": 42, "bot_id": 1})
		self.client._items = Mock(return_value=[{"id": 1}])
		self.client._post = Mock(return_value={"response": "ок"})
		self.client.send_message(42, "привет", bot_id=1)
		self.client._post.assert_called_once()


class TestBotConfig(unittest.TestCase):
	"""get_bot_config сводит три коллекции (ai_bots, chatbot_scenarios,
	ai_prompts) в один ответ с уже подставленным текстом промпта вместо его
	id. Гейт на эти тексты — забота api.py; здесь проверяется только сборка
	и то, что каждый новый запрос идёт через scoped_filter.
	"""

	def setUp(self):
		self.client = EngineClient("http://ai-engine:8055", "t", "a.example.com")

	def test_чужой_бот_отвергается(self):
		# Пустой список от первого запроса (ai_bots) — бот либо не существует,
		# либо принадлежит другому тенанту и не общий.
		self.client._items = Mock(return_value=[])
		with self.assertRaises(BotNotFound):
			self.client.get_bot_config(999)

	def test_бот_запрашивается_фильтром_тенанта_с_общими(self):
		self.client._items = Mock(side_effect=[[{"id": 1, "name": "Бот", "global_system_prompt": "будь вежлив"}], [], [], []])
		self.client.get_bot_config(1)
		collection, params = self.client._items.call_args_list[0][0]
		self.assertEqual(collection, "ai_bots")
		self.assertEqual(
			params["filter"],
			{
				"_and": [
					{
						"_or": [
							{"tenant": {"_eq": "a.example.com"}},
							{"tenant": {"_null": True}},
						]
					},
					{"id": {"_eq": 1}},
				]
			},
		)

	def test_сценарии_запрашиваются_фильтром_тенанта_с_общими(self):
		self.client._items = Mock(side_effect=[[{"id": 1, "name": "Бот", "global_system_prompt": None}], [], [], []])
		self.client.get_bot_config(1)
		collection, params = self.client._items.call_args_list[1][0]
		self.assertEqual(collection, "chatbot_scenarios")
		self.assertEqual(
			params["filter"],
			{
				"_and": [
					{
						"_or": [
							{"tenant": {"_eq": "a.example.com"}},
							{"tenant": {"_null": True}},
						]
					},
					{"bot_id": {"_eq": 1}},
				]
			},
		)

	def test_промпты_сценариев_запрашиваются_одним_батч_запросом(self):
		# Три сценария — один запрос к ai_prompts с фильтром id _in [...], а не
		# три отдельных. На моках результат выглядел бы нормально и с N+1,
		# поэтому смотрим на число вызовов и на аргументы, а не на итог.
		self.client._items = Mock(
			side_effect=[
				[{"id": 1, "name": "Бот", "global_system_prompt": None}],
				[
					{"scenario_key": "general", "description": "", "initial_prompt": 10, "max_history_messages": 15, "max_stack": 10},
					{"scenario_key": "order", "description": "", "initial_prompt": 11, "max_history_messages": 20, "max_stack": 10},
					{"scenario_key": "hours", "description": "", "initial_prompt": 12, "max_history_messages": 5, "max_stack": 10},
				],
				[
					{"id": 10, "system_prompt": "general prompt"},
					{"id": 11, "system_prompt": "order prompt"},
					{"id": 12, "system_prompt": "hours prompt"},
				],
				[],
			]
		)
		self.client.get_bot_config(1)
		self.assertEqual(self.client._items.call_count, 4)
		collection, params = self.client._items.call_args_list[2][0]
		self.assertEqual(collection, "ai_prompts")
		self.assertEqual(
			params["filter"],
			{
				"_and": [
					{
						"_or": [
							{"tenant": {"_eq": "a.example.com"}},
							{"tenant": {"_null": True}},
						]
					},
					{"id": {"_in": [10, 11, 12]}},
				]
			},
		)

	def test_без_сценариев_за_промптами_не_ходим(self):
		# Пустой _in дал бы Directus фильтр, под который не попадает ничего —
		# лишний запрос ради заведомо пустого ответа, как и в _previews.
		self.client._items = Mock(side_effect=[[{"id": 1, "name": "Бот", "global_system_prompt": None}], [], []])
		self.client.get_bot_config(1)
		self.assertEqual(self.client._items.call_count, 3)

	def test_роутер_запрашивается_по_имени_intent_router(self):
		self.client._items = Mock(side_effect=[[{"id": 1, "name": "Бот", "global_system_prompt": None}], [], []])
		self.client.get_bot_config(1)
		collection, params = self.client._items.call_args_list[2][0]
		self.assertEqual(collection, "ai_prompts")
		self.assertEqual(
			params["filter"],
			{
				"_and": [
					{
						"_or": [
							{"tenant": {"_eq": "a.example.com"}},
							{"tenant": {"_null": True}},
						]
					},
					{"bot_id": {"_eq": 1}, "name": {"_eq": "intent_router"}},
				]
			},
		)

	def test_ответ_собирается_с_текстом_промпта_вместо_id(self):
		self.client._items = Mock(
			side_effect=[
				[{"id": 1, "name": "Тестовый бот", "global_system_prompt": "факты о компании"}],
				[
					{
						"scenario_key": "general",
						"description": "Общий разговор",
						"initial_prompt": 10,
						"max_history_messages": 15,
						"max_stack": 10,
					}
				],
				[{"id": 10, "system_prompt": "текст промпта"}],
				[{"system_prompt": "правила роутера"}],
			]
		)
		config = self.client.get_bot_config(1)
		self.assertEqual(config["bot"]["global_system_prompt"], "факты о компании")
		self.assertEqual(config["router_prompt"], "правила роутера")
		self.assertEqual(len(config["scenarios"]), 1)
		scenario = config["scenarios"][0]
		self.assertEqual(scenario["scenario_key"], "general")
		self.assertEqual(scenario["prompt"], "текст промпта")
		self.assertNotIn("initial_prompt", scenario)

	def test_роутер_без_промпта_возвращает_none(self):
		self.client._items = Mock(side_effect=[[{"id": 1, "name": "Бот", "global_system_prompt": None}], [], []])
		config = self.client.get_bot_config(1)
		self.assertIsNone(config["router_prompt"])


class TestEngineErrors(unittest.TestCase):
	def setUp(self):
		self.client = EngineClient("http://ai-engine:8055", "t", "a.example.com")

	def _failing_post(self, status, body):
		import requests

		def fake_post(url, json=None, timeout=None):
			response = requests.Response()
			response.status_code = status
			response._content = body.encode()
			response.url = url
			return response

		return fake_post

	def test_ошибка_движка_приходит_читаемой(self):
		# Голое "500 Server Error" пользователю ничего не говорит: настоящая
		# причина лежит в теле ответа Directus.
		self.client.session.post = self._failing_post(
			500, '{"errors": [{"message": "API key not found. Set OPENAI_API_KEY"}]}'
		)
		with self.assertRaises(EngineError) as ctx:
			self.client._post("ai-process-message", {})
		self.assertIn("API key not found", str(ctx.exception))

	def test_ответ_без_json_не_роняет_обработчик(self):
		self.client.session.post = self._failing_post(502, "<html>Bad Gateway</html>")
		with self.assertRaises(EngineError) as ctx:
			self.client._post("ai-process-message", {})
		self.assertIn("502", str(ctx.exception))


class TestSendMessageDebug(unittest.TestCase):
	def _client(self):
		client = EngineClient("http://engine", "token", "naqwa.habibi-erp.com")
		client.get_chat = Mock(return_value={"id": 7})
		client._post = Mock(return_value={"response": "ок"})
		return client

	def test_без_флага_поле_debug_не_уходит(self):
		client = self._client()
		client.send_message(7, "привет")
		_, payload = client._post.call_args[0]
		self.assertNotIn("debug", payload)

	def test_с_флагом_поле_debug_уходит(self):
		client = self._client()
		client.send_message(7, "привет", debug=True)
		_, payload = client._post.call_args[0]
		self.assertTrue(payload["debug"])


class TestListChatsPreview(unittest.TestCase):
	def _client(self, chats, messages):
		client = EngineClient("http://engine", "token", "naqwa.habibi-erp.com")
		client._items = Mock(side_effect=[chats, messages])
		return client

	def test_заголовок_из_первого_сообщения_пользователя(self):
		client = self._client(
			[{"id": 7, "bot_id": 1, "current_scenario": None}],
			[
				{"chat_id": 7, "role": "user", "content": "хочу курс"},
				{"chat_id": 7, "role": "assistant", "content": "какой именно?"},
			],
		)
		(chat,) = client.list_chats("user@example.com")
		self.assertEqual(chat["title"], "хочу курс")
		self.assertEqual(chat["preview"], "какой именно?")

	def test_длинный_заголовок_обрезается(self):
		client = self._client(
			[{"id": 7, "bot_id": 1, "current_scenario": None}],
			[{"chat_id": 7, "role": "user", "content": "я" * 100}],
		)
		(chat,) = client.list_chats("user@example.com")
		self.assertEqual(len(chat["title"]), 60)

	def test_пустой_чат_не_ломает_список(self):
		client = self._client([{"id": 7, "bot_id": 1, "current_scenario": None}], [])
		(chat,) = client.list_chats("user@example.com")
		self.assertEqual(chat["title"], "")
		self.assertEqual(chat["preview"], "")

	def test_чаты_запрашиваются_только_своего_тенанта(self):
		# Основной запрос списка. Один и тот же человек может быть заведён у
		# нескольких тенантов под одним адресом почты, поэтому фильтр по
		# external_user сам по себе чужого не отсекает — отсекает тенант.
		client = self._client(
			[{"id": 7, "bot_id": 1, "current_scenario": None}],
			[{"chat_id": 7, "role": "user", "content": "привет"}],
		)
		client.list_chats("user@example.com")
		collection, params = client._items.call_args_list[0][0]
		self.assertEqual(collection, "customer_chats")
		self.assertEqual(
			params["filter"],
			{
				"_and": [
					{"tenant": {"_eq": "naqwa.habibi-erp.com"}},
					{"external_user": {"_eq": "user@example.com"}},
				]
			},
		)

	def test_сообщения_запрашиваются_только_своего_тенанта(self):
		# Проверяется не результат, а сам фильтр второго запроса. Номер чата —
		# просто число, а эндпоинт движка о тенантах не знает: пропади фильтр
		# отсюда, и по угаданному номеру вернулась бы чужая переписка. Результат
		# на моках выглядел бы при этом совершенно нормально, поэтому смотреть
		# надо на аргументы вызова.
		client = self._client(
			[{"id": 7, "bot_id": 1, "current_scenario": None}],
			[{"chat_id": 7, "role": "user", "content": "привет"}],
		)
		client.list_chats("user@example.com")
		collection, params = client._items.call_args_list[1][0]
		self.assertEqual(collection, "chat_messages")
		self.assertEqual(
			params["filter"],
			{
				"_and": [
					{"tenant": {"_eq": "naqwa.habibi-erp.com"}},
					{"chat_id": {"_in": [7]}},
				]
			},
		)

	def test_без_чатов_за_сообщениями_не_ходим(self):
		# Пустой _in дал бы Directus фильтр, под который не попадает ничего,
		# то есть лишний запрос ради заведомо пустого ответа.
		client = EngineClient("http://engine", "token", "naqwa.habibi-erp.com")
		client._items = Mock(return_value=[])
		self.assertEqual(client.list_chats("user@example.com"), [])
		self.assertEqual(client._items.call_count, 1)

	def test_запрос_сообщений_для_превью_ограничен_сверху(self):
		# limit: -1 тащил бы всю историю каждого чата целиком ради двух строк
		# по 60 символов — против продового Directus на каждый заход в раздел.
		from habibi_ai.engine import PREVIEW_MESSAGES_LIMIT

		client = self._client(
			[{"id": 7, "bot_id": 1, "current_scenario": None}],
			[{"chat_id": 7, "role": "user", "content": "привет"}],
		)
		client.list_chats("user@example.com")
		_, params = client._items.call_args_list[1][0]
		self.assertEqual(params["limit"], PREVIEW_MESSAGES_LIMIT)
		self.assertNotEqual(params["limit"], -1)


if __name__ == "__main__":
	unittest.main()

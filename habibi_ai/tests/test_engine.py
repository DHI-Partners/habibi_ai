"""Тесты изоляции тенантов.

Не импортируют frappe: engine.py от него не зависит, и эти проверки должны
гоняться за секунды, без поднятия сайта. Изоляция — единственное, что здесь
по-настоящему опасно сломать, поэтому она покрыта первой.
"""

import unittest
from unittest.mock import Mock

from habibi_ai.engine import ChatNotFound, EngineClient, scoped_filter


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

	def test_отправка_сообщения_проверяет_чат_до_обращения_к_движку(self):
		# Без этой проверки номер чужого чата ушёл бы в ai-process-message
		# в обход фильтра, и движок ответил бы по чужой переписке.
		self.client._post = Mock()
		with self.assertRaises(ChatNotFound):
			self.client.send_message(42, "привет")
		self.client._post.assert_not_called()


if __name__ == "__main__":
	unittest.main()

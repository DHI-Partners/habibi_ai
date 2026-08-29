"""Тесты изоляции тенантов.

Не импортируют frappe: engine.py от него не зависит, и эти проверки должны
гоняться за секунды, без поднятия сайта. Изоляция — единственное, что здесь
по-настоящему опасно сломать, поэтому она покрыта первой.
"""

import unittest

from habibi_ai.engine import scoped_filter


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


if __name__ == "__main__":
	unittest.main()

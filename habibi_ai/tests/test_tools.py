"""Тесты реестра инструментов.

Модуль импортирует frappe (через сами инструменты), но сайт не нужен: ERP-вызовы
подменяются. Проверяется реестр и поведение на неизвестном имени — то, из-за
чего диалог может оборваться на ровном месте.
"""

import unittest
from unittest.mock import patch

from habibi_ai import tools


class TestРеестр(unittest.TestCase):
	def test_get_menu_объявлен(self):
		self.assertIn("get_menu", tools.registry())

	def test_определения_отдаются_только_для_запрошенных(self):
		defs = tools.definitions(["get_menu"])
		self.assertEqual([d["name"] for d in defs], ["get_menu"])
		self.assertTrue(defs[0]["description"])
		self.assertEqual(defs[0]["input_schema"]["type"], "object")

	def test_неизвестное_имя_в_определениях_пропускается(self):
		# Имена приходят из конфигурации, которую правят в админке без ревью.
		# Опечатка не должна ронять диалог.
		self.assertEqual(tools.definitions(["get_menu", "опечатка"]), tools.definitions(["get_menu"]))

	def test_вызов_неизвестного_инструмента_даёт_отказ_текстом(self):
		# Отказ возвращается модели, чтобы она исправилась сама, а не исключение,
		# которое оборвало бы ход.
		result = tools.execute("нет_такого", {})
		self.assertIn("нет_такого", result)
		self.assertIn("Доступные", result)


class TestGetMenu(unittest.TestCase):
	def test_возвращает_позиции_с_ценами(self):
		items = [
			{"item_code": "BURGER-01", "item_name": "Сигнатурный бургер", "standard_rate": 3890},
			{"item_code": "FRIES-01", "item_name": "Картофель фри", "standard_rate": 990},
		]
		with patch("frappe.get_all", return_value=items):
			result = tools.execute("get_menu", {})

		self.assertIn("Сигнатурный бургер", result)
		self.assertIn("3890", result)

	def test_пустое_меню_говорит_об_этом_словами(self):
		# Пустая строка выглядела бы как сбой инструмента; модель должна понять,
		# что позиций действительно нет, и сказать это клиенту.
		with patch("frappe.get_all", return_value=[]):
			self.assertIn("пуст", tools.execute("get_menu", {}).lower())

	def test_переполнение_сообщается_модели_явно(self):
		# Молчаливая обрезка позволила бы модели решить, что позиции за
		# пределами среза не существует, и сказать это клиенту. Отказ должен
		# быть виден в самом содержимом ответа, а не только в логах.
		from habibi_ai.tools import menu as menu_module

		items = [
			{"item_code": f"IT-{i}", "item_name": f"Позиция {i}", "standard_rate": i}
			for i in range(menu_module.MENU_LIMIT + 1)
		]
		with patch("frappe.get_all", return_value=items):
			result = tools.execute("get_menu", {})

		self.assertIn("больше", result)
		self.assertEqual(result.count("Позиция"), menu_module.MENU_LIMIT)

	def test_ровно_лимит_позиций_не_считается_переполнением(self):
		# get_all вернул ровно то, что было запрошено (лимит + 1), но реальных
		# позиций оказалось ровно MENU_LIMIT — обрезки не было.
		from habibi_ai.tools import menu as menu_module

		items = [
			{"item_code": f"IT-{i}", "item_name": f"Позиция {i}", "standard_rate": i}
			for i in range(menu_module.MENU_LIMIT)
		]
		with patch("frappe.get_all", return_value=items):
			result = tools.execute("get_menu", {})

		self.assertNotIn("больше", result)
		self.assertEqual(result.count("Позиция"), menu_module.MENU_LIMIT)

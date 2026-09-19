"""Тесты реестра инструментов.

Модуль импортирует frappe (через сами инструменты), но сайт не нужен: ERP-вызовы
подменяются. Проверяется реестр и поведение на неизвестном имени — то, из-за
чего диалог может оборваться на ровном месте.
"""

import unittest
from unittest.mock import Mock, patch

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
	"""get_menu читает две таблицы: цены из прайс-листа и названия из номенклатуры.

	Хелпер подменяет frappe.get_all по имени doctype, а не по порядку вызовов:
	привязка к порядку сломалась бы от любой перестановки запросов внутри
	инструмента, ничего не говоря о поведении.
	"""

	def _frappe(self, prices, items, price_list="Habibi Menu"):
		def get_all(doctype, **kwargs):
			return list(prices) if doctype == "Item Price" else list(items)

		return patch.multiple(
			"frappe",
			get_all=Mock(side_effect=get_all),
			db=Mock(get_single_value=Mock(return_value=price_list)),
			utils=Mock(nowdate=Mock(return_value="2026-09-12")),
		)

	def test_цена_берётся_из_прайс_листа_а_не_из_карточки(self):
		# standard_rate — себестоимостный ориентир карточки. На инсталляции,
		# где заполнен прайс-лист, а карточка нет, бот назвал бы клиенту ноль
		# и взял бы заказ бесплатно.
		prices = [
			{"item_code": "BRG-HABIBI", "price_list_rate": 3890, "currency": "KZT",
			 "valid_from": None, "valid_upto": None},
		]
		items = [{"item_code": "BRG-HABIBI", "item_name": "Сигнатурный бургер", "standard_rate": 0}]
		with self._frappe(prices, items):
			result = tools.execute("get_menu", {})

		self.assertIn("Сигнатурный бургер", result)
		self.assertIn("3890", result)
		self.assertIn("KZT", result)

	def test_бессрочная_цена_не_отбрасывается(self):
		# Пустая дата окончания — обычный случай, а не просрочка. Сравнение с
		# NULL в SQL ложно, поэтому проверка срока живёт в Python.
		prices = [{"item_code": "A", "price_list_rate": 10, "currency": "KZT",
		           "valid_from": None, "valid_upto": None}]
		items = [{"item_code": "A", "item_name": "Позиция A"}]
		with self._frappe(prices, items):
			self.assertIn("Позиция A", tools.execute("get_menu", {}))

	def test_просроченная_цена_не_показывается(self):
		# Просроченная цена хуже отсутствующей: она выглядит настоящей.
		prices = [{"item_code": "A", "price_list_rate": 10, "currency": "KZT",
		           "valid_from": None, "valid_upto": "2026-01-01"}]
		items = [{"item_code": "A", "item_name": "Позиция A"}]
		with self._frappe(prices, items):
			self.assertIn("нет действующих цен", tools.execute("get_menu", {}))

	def test_без_настроенного_прайс_листа_бот_не_называет_цен(self):
		# Тихий откат на карточку назвал бы неверную цену и не сказал об этом
		# никому. Модель должна узнать, что цен у неё нет.
		with self._frappe([], [], price_list=None):
			result = tools.execute("get_menu", {})
		self.assertIn("Не называй клиенту цены", result)

	def test_пустое_меню_говорит_об_этом_словами(self):
		# Пустая строка выглядела бы как сбой инструмента; модель должна понять,
		# что позиций действительно нет, и сказать это клиенту.
		with self._frappe([], []):
			self.assertIn("нет действующих цен", tools.execute("get_menu", {}))

	def test_переполнение_сообщается_модели_явно(self):
		# Молчаливая обрезка позволила бы модели решить, что позиции за
		# пределами среза не существует, и сказать это клиенту. Отказ должен
		# быть виден в самом содержимом ответа, а не только в логах.
		from habibi_ai.tools import menu as menu_module

		prices = [
			{"item_code": f"IT-{i}", "price_list_rate": i, "currency": "KZT",
			 "valid_from": None, "valid_upto": None}
			for i in range(menu_module.MENU_LIMIT + 1)
		]
		items = [{"item_code": f"IT-{i}", "item_name": f"Позиция {i}"}
		         for i in range(menu_module.MENU_LIMIT + 1)]
		with self._frappe(prices, items):
			result = tools.execute("get_menu", {})

		self.assertIn("больше", result)

	def test_ровно_лимит_позиций_не_считается_переполнением(self):
		# get_all вернул меньше, чем запрошено (лимит + 1), значит обрезки не было.
		from habibi_ai.tools import menu as menu_module

		prices = [
			{"item_code": f"IT-{i}", "price_list_rate": i, "currency": "KZT",
			 "valid_from": None, "valid_upto": None}
			for i in range(menu_module.MENU_LIMIT)
		]
		items = [{"item_code": f"IT-{i}", "item_name": f"Позиция {i}"}
		         for i in range(menu_module.MENU_LIMIT)]
		with self._frappe(prices, items):
			result = tools.execute("get_menu", {})

		self.assertNotIn("больше", result)
		self.assertEqual(result.count("Позиция"), menu_module.MENU_LIMIT)


class TestGetDeliveryZones(unittest.TestCase):
	"""Зоны доставки заведены не приложением, а руками в конкретной инсталляции.

	Поэтому главный проверяемый случай — не «как красиво напечатали», а что
	инструмент делает там, где справочника нет вовсе.
	"""

	def _frappe(self, zones, doctype_exists=True, currency="KZT"):
		return patch.multiple(
			"frappe",
			get_all=Mock(return_value=list(zones)),
			db=Mock(
				exists=Mock(return_value=1 if doctype_exists else None),
				get_single_value=Mock(return_value="Habibi Menu"),
				get_value=Mock(return_value=currency),
			),
		)

	def test_без_справочника_бот_не_называет_условий(self):
		# У другого тенанта этого doctype нет. Обращение к нему бросило бы
		# исключение, и модель сказала бы клиенту про сбой — хотя правда в том,
		# что доставка просто не настроена.
		with self._frappe([], doctype_exists=False):
			result = tools.execute("get_delivery_zones", {})
		self.assertIn("не заведены", result)
		self.assertIn("Не называй", result)

	def test_зоны_отдаются_с_ценой_сроком_и_порогом(self):
		zones = [{
			"name": "Center", "delivery_fee": 800.0, "free_above": 10000.0,
			"eta_minutes": 25, "notes": None,
		}]
		with self._frappe(zones):
			result = tools.execute("get_delivery_zones", {})
		self.assertIn("Center", result)
		self.assertIn("800", result)
		self.assertIn("25", result)
		self.assertIn("10000", result)

	def test_валюта_берётся_из_прайс_листа_продаж(self):
		# Не из системной валюты и не из компании по умолчанию: в инсталляции
		# может быть несколько компаний с разными валютами, и на проде
		# умолчанием стоит тестовая (SAR), тогда как бургерная работает в KZT.
		# Бот, назвавший меню в тенге и доставку в риалах, хуже промолчавшего.
		zones = [{"name": "Z", "delivery_fee": 5.0, "free_above": None, "eta_minutes": None, "notes": None}]
		with self._frappe(zones, currency="AED"):
			self.assertIn("AED", tools.execute("get_delivery_zones", {}))

	def test_ни_одной_активной_зоны_говорит_словами(self):
		with self._frappe([]):
			result = tools.execute("get_delivery_zones", {})
		self.assertIn("Не называй", result)


class TestКонтекст(unittest.TestCase):
	"""Контекст хода кладёт сервер. Модель не может ни увидеть его, ни подменить:
	из контекста инструмент заказа узнаёт чат, а значит — клиента."""

	def setUp(self):
		self._saved = dict(tools._REGISTRY)

		@tools.tool("_с_контекстом", "тест", {"type": "object", "properties": {}}, context=True)
		def _with(context, x=None):
			return f"{context.get('turn_id')}|{x}"

		@tools.tool("_без_контекста", "тест", {"type": "object", "properties": {}})
		def _without(x=None):
			return f"{x}"

	def tearDown(self):
		tools._REGISTRY.clear()
		tools._REGISTRY.update(self._saved)

	def test_инструмент_с_контекстом_получает_серверный(self):
		self.assertEqual(tools.execute("_с_контекстом", {"x": 1}, {"turn_id": "t1"}), "t1|1")

	def test_context_от_модели_отбрасывается(self):
		result = tools.execute("_с_контекстом", {"context": {"turn_id": "чужой"}}, {"turn_id": "t1"})
		self.assertEqual(result, "t1|None")

	def test_инструмент_без_объявления_контекста_его_не_получает(self):
		self.assertEqual(tools.execute("_без_контекста", {"x": 2}, {"turn_id": "t1"}), "2")

	def test_контекст_не_виден_в_определениях(self):
		definition = tools.definitions(["_с_контекстом"])[0]
		self.assertNotIn("context", definition)
		self.assertNotIn("run", definition)

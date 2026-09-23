import unittest

from habibi_ai import features


class TestFeatures(unittest.TestCase):
	def test_несохранённые_настройки_значит_всё_включено(self):
		# erp.habibi-erp.com после миграции: полей ещё нет в tabSingles,
		# и бот не должен потерять quote_order.
		self.assertEqual(
			features.enabled({"feature_delivery": None, "feature_orders": None}), {"delivery", "orders"}
		)

	def test_выключенная_доставка(self):
		self.assertEqual(features.enabled({"feature_delivery": 0, "feature_orders": 1}), {"orders"})

	def test_пустая_строка_считается_выключенной(self):
		# tabSingles отдаёт сырые строки без cast (см. api.feature_values):
		# "" там такой же законный сырой вид «выключено», как и "0" — не
		# повод для ValueError из int("").
		self.assertEqual(features.enabled({"feature_delivery": "", "feature_orders": "1"}), {"orders"})

	def test_инструменты_выключенной_возможности_не_предлагаются(self):
		names = ["create_order", "get_delivery_zones", "get_menu", "quote_order"]
		self.assertEqual(features.offered(names, {"orders"}), ["create_order", "get_menu", "quote_order"])

	def test_инструмент_без_возможности_предлагается_всегда(self):
		self.assertEqual(
			features.offered(["get_menu", "get_working_hours"], set()), ["get_menu", "get_working_hours"]
		)

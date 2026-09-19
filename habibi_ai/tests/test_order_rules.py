"""Правила заказа без frappe: что модель может и чего не может передать.

Аргументы приходят от модели — это недоверенный ввод. Каждый отказ здесь —
текст, по которому модель исправится сама, а не исключение, которое
оборвало бы разговор.
"""

import unittest
from datetime import datetime, timedelta

from habibi_ai import order_rules as r

CATALOG = {
	"BRG-CLASSIC": {"item_name": "Classic Burger", "rate": 2490.0, "uom": "Nos"},
	"DRK-COLA": {"item_name": "Cola 0.5 L", "rate": 690.0, "uom": "Nos"},
}
ZONES = [
	{"name": "Center", "delivery_fee": 800.0, "free_above": 10000.0},
	{"name": "North", "delivery_fee": 1200.0, "free_above": None},
]
NOW = datetime(2026, 9, 21, 12, 0)
CTX = {"engine_chat_id": 5, "turn_id": "t2"}


def quote(**kw):
	base = {"engine_chat_id": 5, "turn_id": "t1", "expires_on": NOW + timedelta(minutes=10), "sales_order": None}
	base.update(kw)
	return base


class TestПозиции(unittest.TestCase):
	def test_позиции_берут_цену_из_каталога(self):
		lines = r.resolve_lines([{"item_code": "BRG-CLASSIC", "qty": 2}], CATALOG)
		self.assertEqual(lines, [{"item_code": "BRG-CLASSIC", "item_name": "Classic Burger", "qty": 2, "rate": 2490.0, "uom": "Nos"}])

	def test_позицию_можно_назвать_по_имени(self):
		# Модель часто передаёт то, что видела в меню, — название, а не код
		lines = r.resolve_lines([{"item_code": "classic burger", "qty": 1}], CATALOG)
		self.assertEqual(lines[0]["item_code"], "BRG-CLASSIC")

	def test_неизвестная_позиция_отказ_со_списком_доступных(self):
		with self.assertRaises(r.Refusal) as cm:
			r.resolve_lines([{"item_code": "пицца", "qty": 1}], CATALOG)
		self.assertIn("«пицца»", str(cm.exception))
		self.assertIn("Classic Burger (BRG-CLASSIC)", str(cm.exception))

	def test_повтор_позиции_складывается(self):
		lines = r.resolve_lines([{"item_code": "DRK-COLA", "qty": 1}, {"item_code": "DRK-COLA", "qty": 2}], CATALOG)
		self.assertEqual([(line["item_code"], line["qty"]) for line in lines], [("DRK-COLA", 3)])

	def test_количество_вне_границ_отказ(self):
		for qty in (0, -1, 1.5, 51, "2", True, None):
			with self.subTest(qty=qty), self.assertRaises(r.Refusal):
				r.resolve_lines([{"item_code": "DRK-COLA", "qty": qty}], CATALOG)

	def test_целое_во_float_принимается(self):
		self.assertEqual(r.resolve_lines([{"item_code": "DRK-COLA", "qty": 2.0}], CATALOG)[0]["qty"], 2)

	def test_пустой_состав_отказ(self):
		for items in ([], None, "бургер"):
			with self.subTest(items=items), self.assertRaises(r.Refusal):
				r.resolve_lines(items, CATALOG)


class TestДоставка(unittest.TestCase):
	def test_зона_по_имени_без_учёта_регистра(self):
		self.assertEqual(r.match_zone("center", ZONES)["name"], "Center")

	def test_неизвестная_зона_отказ_со_списком(self):
		with self.assertRaises(r.Refusal) as cm:
			r.match_zone("Луна", ZONES)
		self.assertIn("Center, North", str(cm.exception))

	def test_зона_не_названа_просит_спросить(self):
		with self.assertRaises(r.Refusal) as cm:
			r.match_zone(None, ZONES)
		self.assertIn("Спроси", str(cm.exception))

	def test_бесплатно_от_порога(self):
		self.assertEqual(r.delivery_line(ZONES[0], 10000.0, "Nos")["rate"], 0.0)
		self.assertEqual(r.delivery_line(ZONES[0], 9999.0, "Nos")["rate"], 800.0)

	def test_без_порога_всегда_платно(self):
		line = r.delivery_line(ZONES[1], 50000.0, "Nos")
		self.assertEqual((line["item_code"], line["rate"], line["item_name"]), ("SRV-DELIVERY", 1200.0, "Доставка (North)"))


class TestТелефон(unittest.TestCase):
	def test_нормализация(self):
		self.assertEqual(r.normalize_phone("+7 (701) 555-01-04"), "+77015550104")
		self.assertEqual(r.normalize_phone("87015550104"), "+87015550104")

	def test_короткий_или_пустой_номер(self):
		for raw in ("12345", "", None, "позвоните"):
			with self.subTest(raw=raw):
				self.assertIsNone(r.normalize_phone(raw))


class TestПроверкаРасчёта(unittest.TestCase):
	def test_расчёт_из_другого_чата_не_найден(self):
		with self.assertRaises(r.Refusal) as cm:
			r.check_quote(quote(engine_chat_id=6), CTX, NOW)
		self.assertIn("не найден", str(cm.exception))

	def test_нет_расчёта_не_найден(self):
		with self.assertRaises(r.Refusal):
			r.check_quote(None, CTX, NOW)

	def test_тот_же_ход_отказ(self):
		with self.assertRaises(r.Refusal) as cm:
			r.check_quote(quote(turn_id="t2"), CTX, NOW)
		self.assertIn("дождись", str(cm.exception))

	def test_просроченный_отказ(self):
		with self.assertRaises(r.Refusal) as cm:
			r.check_quote(quote(expires_on=NOW - timedelta(seconds=1)), CTX, NOW)
		self.assertIn("устарел", str(cm.exception))

	def test_уже_оформленный_возвращает_done_даже_в_том_же_ходе(self):
		self.assertEqual(r.check_quote(quote(turn_id="t2", sales_order="SO-1"), CTX, NOW), "done")

	def test_годный_расчёт(self):
		self.assertEqual(r.check_quote(quote(), CTX, NOW), "create")


class TestТекст(unittest.TestCase):
	def test_деньги(self):
		self.assertEqual(r.money(4670.0), "4 670")
		self.assertEqual(r.money(500.36), "500.36")
		self.assertEqual(r.money(100), "100")
		self.assertEqual(r.money(1234567.5), "1 234 567.50")

	def test_строки_позиций_и_доставки(self):
		rows = [
			{"item_code": "BRG-CLASSIC", "item_name": "Classic Burger", "qty": 1, "amount": 2490.0},
			{"item_code": "SRV-DELIVERY", "item_name": "Доставка (Center)", "qty": 1, "amount": 0.0},
		]
		self.assertEqual(
			r.item_lines(rows, "KZT"),
			["- Classic Burger × 1 — 2 490 KZT", "- Доставка (Center) — бесплатно"],
		)

	def test_итог_с_налогом(self):
		line = r.total_line(4670.0, [{"description": "VAT 12%", "amount": 500.36}], "KZT")
		self.assertEqual(line, "Итого: 4 670 KZT, включая VAT 12% — 500.36 KZT")

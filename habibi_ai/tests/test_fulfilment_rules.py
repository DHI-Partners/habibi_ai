import unittest
from datetime import datetime

from habibi_ai import fulfilment_rules as r

NOW = datetime(2026, 10, 1, 12, 30)
ORDER = {
	"name": "SAL-ORD-2026-00015",
	"modified": datetime(2026, 10, 1, 12, 18),
	"customer_name": "Динара",
	"shipping_address": None,
	"address_display": "мкр. Самал-2, д. 33<br>кв. 41<br>",
	"contact_mobile": "+77010001122",
	"custom_kitchen_notes": "  аллергия на кунжут ",
	"custom_delivery_zone": "Центр",
	"custom_whatsapp_number": "+77019990011",
	"custom_fulfilment_type": "Delivery",
}
ROWS = [
	{"item_code": "BURGER", "item_name": "Чизбургер", "qty": 2.0},
	{"item_code": "SRV-DELIVERY", "item_name": "Доставка", "qty": 1.0},
	{"item_code": "COLA", "item_name": "Кола", "qty": 1.0},
]


class TestAge(unittest.TestCase):
	def test_минуты(self):
		self.assertEqual(r.age_minutes(ORDER["modified"], NOW), 12)

	def test_меньше_минуты_это_ноль(self):
		self.assertEqual(r.age_minutes(datetime(2026, 10, 1, 12, 29, 30), NOW), 0)

	def test_modified_в_будущем_не_уходит_в_минус(self):
		# Часы сервера БД и приложения могут расходиться
		self.assertEqual(r.age_minutes(datetime(2026, 10, 1, 12, 45), NOW), 0)


class TestAddress(unittest.TestCase):
	def test_html_в_строку(self):
		self.assertEqual(r.plain_address("мкр. Самал-2, д. 33<br>кв. 41<br>"), "мкр. Самал-2, д. 33, кв. 41")

	def test_переводы_строк(self):
		self.assertEqual(r.plain_address("ул. Абая, 12\nАлматы\n"), "ул. Абая, 12, Алматы")

	def test_контакты_из_справочника_адресов_убираются(self):
		"""ERPNext кладёт в address_display строки «Phone: …», «Email: …»: телефон клиента
		курьер видит только после «Взять», поэтому из адреса он уходит всегда."""
		html = "Dostyk Ave 132, apt 45<br>Almaty, Kazakhstan<br>Phone: +77015550101<br>Email: a@b.kz<br>Fax: 123<br>"
		self.assertEqual(r.plain_address(html), "Dostyk Ave 132, apt 45, Almaty, Kazakhstan")
		self.assertEqual(r.plain_address("ул. Абая, 12<br>Алматы<br>Телефон: +77015550101<br>Эл. почта: a@b.kz"), "ул. Абая, 12, Алматы")

	def test_свободный_заказ_не_выдаёт_телефон_через_адрес(self):
		order = {**ORDER, "address_display": "ул. Абая, 12<br>Алматы<br>Phone: +77015550101"}
		card = r.courier_card(order, ROWS, NOW, own=False)
		self.assertNotIn("+7701", str(card))
		self.assertEqual(card["address"], "ул. Абая, 12, Алматы")

	def test_пусто_это_none(self):
		for value in (None, "", "  <br> ", "\n"):
			with self.subTest(value):
				self.assertIsNone(r.plain_address(value))


class TestLines(unittest.TestCase):
	def test_доставка_не_еда(self):
		self.assertEqual(
			r.lines(ROWS),
			[{"item_name": "Чизбургер", "qty": 2.0}, {"item_name": "Кола", "qty": 1.0}],
		)

	def test_только_доставка_даёт_пустой_состав(self):
		self.assertEqual(r.lines([ROWS[1]]), [])


class TestKitchenCard(unittest.TestCase):
	def test_карточка(self):
		card = r.kitchen_card(ORDER, ROWS, NOW)
		self.assertEqual(
			card,
			{
				"name": "SAL-ORD-2026-00015",
				"age": 12,
				"notes": "аллергия на кунжут",
				"fulfilment": "Delivery",
				"items": [{"item_name": "Чизбургер", "qty": 2.0}, {"item_name": "Кола", "qty": 1.0}],
			},
		)

	def test_кухне_не_уходит_лишнее(self):
		card = r.kitchen_card(ORDER, ROWS, NOW)
		for forbidden in ("phone", "address", "customer_name", "zone", "total", "rate", "amount"):
			self.assertNotIn(forbidden, card)
		for item in card["items"]:
			self.assertEqual(set(item), {"item_name", "qty"})

	def test_способ_получения_без_поля_на_сайте_это_none(self):
		order = {k: v for k, v in ORDER.items() if k != "custom_fulfilment_type"}
		self.assertIsNone(r.kitchen_card(order, ROWS, NOW)["fulfilment"])
		order = {**ORDER, "custom_fulfilment_type": ""}
		self.assertIsNone(r.kitchen_card(order, ROWS, NOW)["fulfilment"])

	def test_пустая_заметка_это_none(self):
		order = {**ORDER, "custom_kitchen_notes": "   "}
		self.assertIsNone(r.kitchen_card(order, ROWS, NOW)["notes"])
		order = {k: v for k, v in ORDER.items() if k != "custom_kitchen_notes"}
		self.assertIsNone(r.kitchen_card(order, ROWS, NOW)["notes"])


class TestCourierCard(unittest.TestCase):
	def test_чужой_заказ_без_телефона_и_состава(self):
		card = r.courier_card(ORDER, ROWS, NOW, own=False)
		self.assertEqual(
			card,
			{
				"name": "SAL-ORD-2026-00015",
				"age": 12,
				"customer_name": "Динара",
				"address": "мкр. Самал-2, д. 33, кв. 41",
				"zone": "Центр",
				"items_count": 2,
			},
		)

	def test_свой_заказ_с_телефоном_и_составом(self):
		card = r.courier_card(ORDER, ROWS, NOW, own=True)
		self.assertEqual(card["phone"], "+77019990011")
		self.assertEqual([i["item_name"] for i in card["items"]], ["Чизбургер", "Кола"])

	def test_телефон_запасной_и_отсутствующий(self):
		order = {**ORDER, "custom_whatsapp_number": None}
		self.assertEqual(r.courier_card(order, ROWS, NOW, own=True)["phone"], "+77010001122")
		order = {**order, "contact_mobile": None}
		self.assertIsNone(r.courier_card(order, ROWS, NOW, own=True)["phone"])

	def test_адрес_из_ссылки_если_текста_нет(self):
		order = {**ORDER, "address_display": None, "shipping_address": "Динара-Доставка"}
		self.assertEqual(r.courier_card(order, ROWS, NOW, own=False)["address"], "Динара-Доставка")

	def test_адреса_нет(self):
		order = {**ORDER, "address_display": None, "shipping_address": None}
		self.assertIsNone(r.courier_card(order, ROWS, NOW, own=False)["address"])

	def test_деньги_не_уходят_никогда(self):
		for own in (True, False):
			card = r.courier_card(ORDER, ROWS, NOW, own=own)
			for forbidden in ("total", "rate", "amount", "grand_total"):
				self.assertNotIn(forbidden, card)


if __name__ == "__main__":
	unittest.main()

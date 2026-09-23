import unittest

from habibi_ai.profile import render


class TestProfile(unittest.TestCase):
	def test_ядро_и_правила_по_порядку(self):
		text = render(
			{"business_name": "Habibi Burger", "business_kind": "бургерная", "address": "Абая 1",
			 "phone": "+77010000000", "description": "Жарим на углях.", "tone": "friendly"},
			[{"title": "Доставка", "text": "40–60 минут."}, {"title": "Оплата", "text": "Kaspi, наличные."}],
		)
		self.assertEqual(
			text,
			"О компании (данные владельца, отвечай по ним):\n"
			"Название: Habibi Burger\n"
			"Вид деятельности: бургерная\n"
			"Адрес: Абая 1\n"
			"Телефон: +77010000000\n"
			"Жарим на углях.\n"
			"Тон общения: дружелюбный, на «ты» не переходить без повода клиента.\n\n"
			"Доставка:\n40–60 минут.\n\n"
			"Оплата:\nKaspi, наличные.",
		)

	def test_пустое_правило_не_попадает(self):
		text = render({"business_name": "X"}, [{"title": "Залог", "text": "  "}])
		self.assertNotIn("Залог", text)

	def test_пустой_профиль_пустой_текст(self):
		self.assertEqual(render({}, []), "")

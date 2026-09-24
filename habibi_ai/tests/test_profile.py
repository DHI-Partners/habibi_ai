import unittest

from habibi_ai.profile import DESCRIPTION_MAX, RULE_TEXT_MAX, RULE_TITLE_MAX, RULES_MAX, render


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

	def test_сверхдлинное_из_desk_обрезается_с_многоточием(self):
		"""Данные, заведённые в Desk в обход кабинета, не раздувают промпт бота."""
		warnings = []
		text = render(
			{"description": "а" * 5000},
			[{"title": "б" * 200, "text": "в" * 5000}]
			+ [{"title": f"Блок {i}", "text": "x"} for i in range(30)],
			warn=warnings.append,
		)
		self.assertIn("а" * (DESCRIPTION_MAX - 1) + "…", text)
		self.assertNotIn("а" * DESCRIPTION_MAX, text)
		self.assertIn("б" * (RULE_TITLE_MAX - 1) + "…:\n" + "в" * (RULE_TEXT_MAX - 1) + "…", text)
		self.assertIn(f"Блок {RULES_MAX - 2}:", text)
		self.assertNotIn(f"Блок {RULES_MAX - 1}:", text)
		self.assertTrue(warnings)

	def test_в_пределах_лимитов_без_предупреждений(self):
		warnings = []
		render(
			{"description": "а" * DESCRIPTION_MAX},
			[{"title": "Оплата", "text": "в" * RULE_TEXT_MAX}],
			warn=warnings.append,
		)
		self.assertEqual(warnings, [])

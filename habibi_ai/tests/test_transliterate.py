import unittest

from habibi_ai.transliterate import slug, unique_code


class TestSlug(unittest.TestCase):
	def test_кириллица_транслитерируется_и_становится_кодом(self):
		self.assertEqual(slug("Классик бургер"), "KLASSIK-BURGER")

	def test_запятая_и_пробел_становятся_дефисом(self):
		self.assertEqual(slug("Кола 0,5 л"), "KOLA-0-5-L")

	def test_латиница_и_цифры_остаются_как_есть(self):
		self.assertEqual(slug("Burger 200g"), "BURGER-200G")

	def test_несколько_разделителей_подряд_схлопываются(self):
		self.assertEqual(slug("Кола  —  0.5л!!"), "KOLA-0-5L")

	def test_обрезка_по_длине_без_висящего_дефиса(self):
		text = "Очень длинное название позиции меню для проверки обрезки кода"
		code = slug(text, 20)
		self.assertLessEqual(len(code), 20)
		self.assertFalse(code.endswith("-"))

	def test_пустая_строка(self):
		self.assertEqual(slug(""), "")

	def test_только_разделители_дают_пустой_код(self):
		self.assertEqual(slug("!!! ??? ---"), "")

	def test_none_не_роняет(self):
		self.assertEqual(slug(None), "")

	def test_казахские_буквы_транслитерируются(self):
		self.assertEqual(slug("Қазақ бургер"), "KAZAK-BURGER")
		self.assertEqual(slug("Құрт"), "KURT")


class TestUniqueCode(unittest.TestCase):
	def test_свободный_код_возвращается_как_есть(self):
		self.assertEqual(unique_code("BURGER", exists=lambda c: False), "BURGER")

	def test_занятый_код_получает_суффикс(self):
		taken = {"BURGER"}
		self.assertEqual(unique_code("BURGER", exists=lambda c: c in taken), "BURGER-2")

	def test_несколько_суффиксов_подряд_заняты(self):
		taken = {"BURGER", "BURGER-2", "BURGER-3"}
		self.assertEqual(unique_code("BURGER", exists=lambda c: c in taken), "BURGER-4")

	def test_суффикс_не_оставляет_двойного_дефиса_на_границе_обрезки(self):
		# base — ровно там, где обрезка под max_len=10 с суффиксом "-2"
		# (10 - len("-2") = 8) отрежет по самому дефису: "AAAAAAA-BBBB"[:8]
		# == "AAAAAAA-" — без rstrip("-") получилось бы "AAAAAAA--2"
		base = "AAAAAAA-BBBB"
		result = unique_code(base, exists=lambda c: c == base, max_len=10)
		self.assertEqual(result, "AAAAAAA-2")
		self.assertNotIn("--", result)


if __name__ == "__main__":
	unittest.main()

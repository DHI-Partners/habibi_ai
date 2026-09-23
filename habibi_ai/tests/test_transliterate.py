import unittest

from habibi_ai.transliterate import slug


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


if __name__ == "__main__":
	unittest.main()

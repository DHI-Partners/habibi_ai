import unittest

from habibi_ai import preset_rules as p


class TestMergeSections(unittest.TestCase):
	def test_обновляет_свои_и_оставляет_чужие(self):
		current = [{"key": "menu", "label": "Старое"}, {"key": "custom_x", "label": "Своё"}]
		preset = [{"key": "orders", "label": "Заказы"}, {"key": "menu", "label": "Меню"}]
		self.assertEqual(
			p.merge_sections(current, preset),
			[{"key": "orders", "label": "Заказы"}, {"key": "menu", "label": "Меню"}, {"key": "custom_x", "label": "Своё"}],
		)

	def test_повтор_ничего_не_меняет(self):
		preset = [{"key": "menu", "label": "Меню"}]
		once = p.merge_sections([], preset)
		self.assertEqual(p.merge_sections(once, preset), once)


class TestMergeRules(unittest.TestCase):
	def test_заполненный_текст_не_перетирается(self):
		current = [{"title": "Доставка", "hint": "старая", "text": "40 минут"}]
		preset = [{"title": "Доставка", "hint": "Сколько стоит?"}, {"title": "Оплата", "hint": "Как платить?"}]
		self.assertEqual(
			p.merge_rules(current, preset),
			[{"title": "Доставка", "hint": "Сколько стоит?", "text": "40 минут"}, {"title": "Оплата", "hint": "Как платить?", "text": ""}],
		)

	def test_свои_правила_владельца_остаются(self):
		current = [{"title": "Парковка", "hint": "", "text": "Есть"}]
		self.assertEqual(p.merge_rules(current, [])[0]["title"], "Парковка")


if __name__ == "__main__":
	unittest.main()

import unittest

from habibi_ai import notify_rules as n


class TestRender(unittest.TestCase):
	def test_подстановки(self):
		self.assertEqual(
			n.render("Заказ {order} принят, {customer}! Итого {total}.", {"order": "SO-1", "customer": "Руслан", "total": "4670 KZT"}),
			"Заказ SO-1 принят, Руслан! Итого 4670 KZT.",
		)

	def test_неизвестная_подстановка_не_роняет(self):
		# Шаблон правит владелец: опечатка в скобках — не повод
		# не уведомить клиента.
		self.assertEqual(n.render("Привет {name}", {}), "Привет {name}")

	def test_пустое_значение(self):
		self.assertEqual(n.render("Причина: {reason}", {"reason": None}), "Причина: ")

	def test_сломанный_шаблон_открытая_скобка(self):
		# Опечатка владельца (открытая скобка) не роняет уведомление.
		self.assertEqual(n.render("Заказ {order} {", {"order": "SO-1"}), "Заказ SO-1 {")

	def test_сломанный_шаблон_закрытая_скобка(self):
		# Одна закрытая скобка остаётся как есть.
		self.assertEqual(n.render("}", {}), "}")

	def test_сломанный_шаблон_позиционный_аргумент(self):
		# Позиционный аргумент {0} не поддерживается, остаётся как есть.
		self.assertEqual(n.render("{0} {order}", {"order": "SO-1"}), "{0} SO-1")


class TestKind(unittest.TestCase):
	def test_виды(self):
		self.assertEqual(n.kind_of("Confirm", "Confirm", "Cancel"), "accept")
		self.assertEqual(n.kind_of("Cancel", "Confirm", "Cancel"), "reject")
		self.assertEqual(n.kind_of("Send to Kitchen", "Confirm", "Cancel"), "other")

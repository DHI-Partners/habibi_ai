"""Обязательство «заказ»: что считается утверждением и чем оно подтверждается.

Не импортирует frappe: страж должен проверяться за секунды.
"""

import unittest

from habibi_ai.agent import checks, registry
from habibi_ai.agent.orders import ORDER_COMMITMENT as C

N1 = "SAL-ORD-2026-00026"
N0 = "SAL-ORD-2026-00023"


def _turn(result, name="create_order"):
	return [
		{"type": "tool_use", "id": "a", "name": name, "input": {}},
		{"type": "tool_result", "id": "a", "content": result},
	]


CREATED = f"Заказ {N1} создан — черновик, ждёт подтверждения оператора.\nКлиент: Тимур"


class TestУтверждение(unittest.TestCase):
	def test_номер_заказа_это_утверждение(self):
		self.assertTrue(C.claims(f"Готово: {N1}"))

	def test_фраза_без_номера_это_утверждение(self):
		for text in ("Заказ оформлен.", "Ваш заказ создан!", "заказ **успешно** оформлен"):
			with self.subTest(text):
				self.assertTrue(C.claims(text), text)

	def test_далёкое_не_не_отменяет_утверждение(self):
		for text in (
			"Заказ на два бургера, не острых, оформлен",
			"Заказ, который вы не видели, создан",
			"Заказ принят, не волнуйтесь, оформлен",
		):
			with self.subTest(text):
				self.assertTrue(C.claims(text), text)

	def test_отрицание_не_утверждение(self):
		for text in (
			"Заказ не создан.",
			"Заказ ещё не оформлен, нужен ваш ответ",
			"Не удалось оформить заказ",
			"Заказ не был создан",
			"заказ ещё не полностью оформлен",
			"Заказ не был успешно оформлен",
		):
			with self.subTest(text):
				self.assertFalse(C.claims(text), text)

	def test_расчёт_не_утверждение(self):
		self.assertFalse(C.claims("Расчёт AIQ-00012, действует 30 минут. Оформлять заказ?"))

	def test_обычный_разговор_не_утверждение(self):
		self.assertFalse(C.claims("Есть Classic Burger за 2490 KZT."))


class TestПодтверждение(unittest.TestCase):
	def test_номер_из_результата_этого_хода_подтверждён(self):
		self.assertTrue(C.confirmed(f"Заказ {N1} создан", _turn(CREATED), frozenset()))

	def test_повтор_заказа_тоже_подтверждение(self):
		result = f"Заказ {N1} уже создан по этому расчёту — второй не создавался."
		self.assertTrue(C.confirmed(f"Заказ {N1}", _turn(result), frozenset()))

	def test_выдуманный_номер_не_подтверждён(self):
		self.assertFalse(C.confirmed(f"Заказ {N1} создан", [], frozenset({N0})))

	def test_настоящий_номер_рядом_с_выдуманным_не_подтверждён(self):
		self.assertFalse(
			C.confirmed(f"Заказ {N0} и {N1} создан", _turn(f"Заказ {N0} создан — x"), frozenset())
		)

	def test_прошлый_заказ_из_журнала_подтверждён(self):
		self.assertTrue(C.confirmed(f"Заказ {N0} уже создан как черновик", [], frozenset({N0})))

	def test_фраза_без_номера_нужен_результат_хода(self):
		self.assertFalse(C.confirmed("Заказ оформлен", [], frozenset({N0})))
		self.assertTrue(C.confirmed("Заказ оформлен", _turn(CREATED), frozenset()))

	def test_отказ_с_номером_в_тексте_не_подтверждение(self):
		refusal = f"Заказ {N1} по этому расчёту был удалён оператором. Не оформляй его заново сам."
		self.assertFalse(C.confirmed(f"Заказ {N1} создан", _turn(refusal), frozenset()))

	def test_результат_другого_инструмента_не_подтверждение(self):
		self.assertFalse(C.confirmed(f"Заказ {N1} создан", _turn(CREATED, name="quote_order"), frozenset()))


class TestПересказ(unittest.TestCase):
	def test_успех_пересказывается_первой_строкой(self):
		self.assertEqual(
			C.recap(_turn(CREATED)), f"Заказ {N1} создан — черновик, ждёт подтверждения оператора."
		)

	def test_отказ_не_отдаётся_клиенту(self):
		text = C.recap(_turn("Сначала зачитай расчёт клиенту и дождись согласия."))
		self.assertNotIn("зачитай", text)
		self.assertIn("оператору", text)

	def test_без_вызова_общая_фраза(self):
		self.assertIn("оператору", C.recap([]))


class TestРеестр(unittest.TestCase):
	def setUp(self):
		self._saved = list(registry._MODULES)
		registry._MODULES.clear()

	def tearDown(self):
		registry._MODULES[:] = self._saved

	def test_модуль_без_флага_активен_всегда(self):
		module = registry.Module(name="a", feature=None, stage=None, commitments=(), pin=())
		registry.register(module)
		self.assertEqual(registry.active(set()), [module])

	def test_модуль_с_выключенным_флагом_не_активен(self):
		module = registry.Module(name="a", feature="orders", stage=None, commitments=(), pin=())
		registry.register(module)
		self.assertEqual(registry.active({"delivery"}), [])
		self.assertEqual(registry.active({"orders"}), [module])


class TestВердикт(unittest.TestCase):
	def test_нет_утверждения_ok(self):
		self.assertEqual(checks.check_commitment(C, "Есть Classic Burger", [], frozenset()).status, checks.OK)

	def test_подтверждённое_утверждение_ok(self):
		verdict = checks.check_commitment(C, f"Заказ {N1} создан", _turn(CREATED), frozenset())
		self.assertEqual(verdict.status, checks.OK)

	def test_неподтверждённое_утверждение_нарушение_с_причиной(self):
		verdict = checks.check_commitment(C, f"Заказ {N1} создан", [], frozenset())
		self.assertEqual(verdict.status, checks.VIOLATED)
		self.assertIn(C.name, verdict.reason)

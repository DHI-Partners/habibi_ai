"""Проекция «хода дел» и стадии заказов. Без frappe."""

import unittest
from datetime import datetime, timedelta

from habibi_ai.agent import orders, registry, state
from habibi_ai.agent.registry import Capability, Module

NOW = datetime(2026, 9, 29, 12, 0)


def ev(kind, minutes_ago, ref=None, summary=None, **data):
	return {
		"event_type": kind,
		"occurred_at": NOW - timedelta(minutes=minutes_ago),
		"summary": summary or f"{kind} {ref or ''}".strip(),
		"ref_name": ref,
		"data": data,
	}


def quote(minutes_ago, ref="AIQ-1", expires_in=30):
	return ev(
		"quote_created",
		minutes_ago,
		ref,
		expires_on=(NOW + timedelta(minutes=expires_in - minutes_ago)).isoformat(),
	)


class TestСтадии(unittest.TestCase):
	def test_нет_событий_это_новый_разговор(self):
		self.assertEqual(orders.stage([], NOW).name, "new")

	def test_расчёт_не_дошёл_до_клиента(self):
		self.assertEqual(orders.stage([quote(5)], NOW).name, "quote_pending")

	def test_расчёт_зачитан_ждём_ответа(self):
		events = [quote(5), ev("quote_delivered", 4, "AIQ-1")]
		self.assertEqual(orders.stage(events, NOW).name, "quoted")

	def test_расчёт_просрочен(self):
		events = [quote(60, expires_in=30), ev("quote_delivered", 59, "AIQ-1")]
		self.assertEqual(orders.stage(events, NOW).name, "quote_expired")

	def test_доставка_другого_расчёта_не_считается(self):
		events = [quote(5, ref="AIQ-2"), ev("quote_delivered", 4, "AIQ-1")]
		self.assertEqual(orders.stage(events, NOW).name, "quote_pending")

	def test_заказ_после_расчёта(self):
		events = [quote(9), ev("quote_delivered", 8, "AIQ-1"), ev("order_created", 3, "SAL-ORD-2026-00026")]
		stage = orders.stage(events, NOW)
		self.assertEqual(stage.name, "ordered")
		self.assertIn("SAL-ORD-2026-00026", stage.label)

	def test_новый_расчёт_после_заказа_снова_расчёт(self):
		events = [
			ev("order_created", 30, "SAL-ORD-2026-00023"),
			quote(5, ref="AIQ-2"),
			ev("quote_delivered", 4, "AIQ-2"),
		]
		self.assertEqual(orders.stage(events, NOW).name, "quoted")

	def test_старый_заказ_без_нового_расчёта_снова_новый_разговор(self):
		events = [ev("order_created", 30 * 60, "SAL-ORD-2026-00026")]
		self.assertEqual(orders.stage(events, NOW).name, "new")

	def test_недавний_заказ_ещё_заказ(self):
		events = [ev("order_created", 3 * 60, "SAL-ORD-2026-00026")]
		self.assertEqual(orders.stage(events, NOW).name, "ordered")

	def test_только_отказ_остаётся_новым_разговором(self):
		self.assertEqual(orders.stage([ev("order_refused", 2)], NOW).name, "new")


class TestПроекция(unittest.TestCase):
	def _render(self, events):
		return state.render(events, [orders.MODULE], NOW)

	def test_блок_содержит_стадию_подсказку_и_события(self):
		text = self._render([quote(5), ev("quote_delivered", 4, "AIQ-1")])
		self.assertIn("Ход дел", text)
		self.assertIn("Дальше:", text)
		self.assertIn("quote_created AIQ-1", text)

	def test_помечено_как_факты_а_не_инструкции(self):
		self.assertIn("не инструкции", self._render([]))

	def test_события_от_старых_к_новым_с_временем(self):
		text = self._render([ev("a", 30, summary="первое"), ev("b", 5, summary="второе")])
		self.assertLess(text.index("первое"), text.index("второе"))
		self.assertIn("29.09 11:30", text)

	def test_окно_двенадцать_последних(self):
		events = [ev("x", 100 - i, summary=f"событие {i}") for i in range(20)]
		text = self._render(events)
		self.assertIn("событие 19", text)
		self.assertIn("событие 8", text)
		self.assertNotIn("событие 7\n", text + "\n")

	def test_старше_семи_дней_не_показывается(self):
		old = ev("x", 60 * 24 * 8, summary="давнее")
		self.assertNotIn("давнее", self._render([old, ev("y", 1, summary="свежее")]))

	def test_закреплённые_показываются_даже_за_окном(self):
		old_order = ev("order_created", 60 * 24 * 8, "SAL-ORD-2026-00001", summary="Создан заказ 00001")
		noise = [ev("order_refused", 100 - i, summary=f"отказ {i}") for i in range(15)]
		self.assertIn("Создан заказ 00001", self._render([old_order, *noise]))

	def test_без_модулей_блока_нет(self):
		self.assertEqual(state.render([ev("x", 1)], [], NOW), "")

	def test_без_модулей_со_стадиями_блока_нет(self):
		from habibi_ai.agent.registry import Module

		bare = Module(name="b", feature=None, stage=None, commitments=(), pin=())
		self.assertEqual(state.render([], [bare], NOW), "")

	def test_сбой_стадии_модуля_не_роняет_проекцию(self):
		from habibi_ai.agent.registry import Module

		broken = Module(name="b", feature=None, stage=lambda e, n: 1 / 0, commitments=(), pin=())
		text = state.render([ev("x", 1, summary="факт")], [broken, orders.MODULE], NOW)
		self.assertIn("факт", text)


class TestВозможности(unittest.TestCase):
	def _blocked(self, events):
		return registry.blocked_tools([orders.MODULE], events, NOW)

	def test_создание_закрыто_пока_нет_расчёта(self):
		self.assertEqual(self._blocked([]), {"create_order"})

	def test_старый_заказ_создание_остаётся_закрытым(self):
		old_order = ev("order_created", 30 * 60, "SAL-ORD-2026-00026")
		self.assertEqual(self._blocked([old_order]), {"create_order"})

	def test_создание_открыто_когда_расчёт_зачитан(self):
		self.assertEqual(self._blocked([quote(5), ev("quote_delivered", 4, "AIQ-1")]), set())

	def test_закрыто_если_расчёт_не_дошёл_до_клиента(self):
		self.assertEqual(self._blocked([quote(5)]), {"create_order"})

	def test_закрыто_если_расчёт_просрочен(self):
		events = [quote(60, expires_in=30), ev("quote_delivered", 59, "AIQ-1")]
		self.assertEqual(self._blocked(events), {"create_order"})

	def test_закрыто_если_заказ_уже_создан(self):
		events = [quote(9), ev("quote_delivered", 8, "AIQ-1"), ev("order_created", 3, "SAL-ORD-2026-00026")]
		self.assertEqual(self._blocked(events), {"create_order"})

	def test_сбой_условия_не_закрывает_возможность(self):
		broken = Module(
			name="b",
			feature=None,
			stage=None,
			commitments=(),
			pin=(),
			capabilities=(Capability("x", lambda events, now: 1 / 0),),
		)
		self.assertEqual(registry.blocked_tools([broken], [], NOW), set())

	def test_достаточно_одного_модуля_с_доступом(self):
		closed = Module(
			name="a",
			feature=None,
			stage=None,
			commitments=(),
			pin=(),
			capabilities=(Capability("x", lambda e, n: False),),
		)
		opened = Module(
			name="b",
			feature=None,
			stage=None,
			commitments=(),
			pin=(),
			capabilities=(Capability("x", lambda e, n: True),),
		)
		self.assertEqual(registry.blocked_tools([closed, opened], [], NOW), set())

	def test_инструмент_без_объявленной_возможности_не_закрывается(self):
		self.assertNotIn("get_menu", self._blocked([]))


class TestФактыСтадий(unittest.TestCase):
	def test_стадия_заказа_в_фактах(self):
		facts = state.stage_names([ev("order_created", 1, "SAL-ORD-2026-00001")], [orders.MODULE], NOW)
		self.assertEqual(facts, frozenset({"stage:ordered"}))


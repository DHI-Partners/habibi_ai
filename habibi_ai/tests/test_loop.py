"""Тесты цикла хода агента.

Не импортируют frappe, как и test_engine.py: loop.py от него не зависит, и
цикл должен проверяться за секунды — и переноситься в систему не на Frappe
вместе с engine.py.
"""

import unittest
from unittest.mock import Mock

from habibi_ai import loop
from habibi_ai.agent.commitments import Commitment


def _step(*results):
	return Mock(side_effect=list(results))


def _run(step, execute=None, offered=("get_menu",), max_loop=5, debug=False, **extra):
	return loop.run(
		step,
		"привет",
		offered=list(offered),
		definitions=[{"name": n} for n in offered],
		execute=execute or Mock(return_value="результат"),
		max_loop=max_loop,
		debug=debug,
		**extra,
	)


class TestЦикл(unittest.TestCase):
	def test_текст_с_первого_шага(self):
		step = _step({"type": "text", "content": "здравствуйте"})
		result = _run(step)
		self.assertEqual(result, {"response": "здравствуйте", "debug": []})
		self.assertEqual(step.call_count, 1)

	def test_шаг_получает_сообщение_turn_tools_debug(self):
		step = _step({"type": "text", "content": "ок"})
		_run(step, debug=True)
		args, kwargs = step.call_args
		self.assertEqual(args, ("привет",))
		self.assertEqual(kwargs["turn"], [])
		self.assertEqual(kwargs["tools"], [{"name": "get_menu"}])
		self.assertTrue(kwargs["debug"])

	def test_инструмент_исполняется_и_результат_уходит_в_следующий_шаг(self):
		step = _step(
			{"type": "tool_use", "id": "t1", "name": "get_menu", "input": {"q": 1}},
			{"type": "text", "content": "шаурма 350"},
		)
		execute = Mock(return_value="шаурма — 350")
		result = _run(step, execute=execute)
		execute.assert_called_once_with("get_menu", {"q": 1})
		self.assertEqual(result["response"], "шаурма 350")
		turn = step.call_args_list[1].kwargs["turn"]
		self.assertEqual(turn[0], {"type": "tool_use", "id": "t1", "name": "get_menu", "input": {"q": 1}})
		self.assertEqual(turn[1], {"type": "tool_result", "id": "t1", "content": "шаурма — 350"})

	def test_raw_передаётся_нетронутым(self):
		raw = [{"type": "thinking", "text": "..."}]
		step = _step(
			{"type": "tool_use", "id": "t1", "name": "get_menu", "input": {}, "raw": raw},
			{"type": "text", "content": "ок"},
		)
		_run(step)
		self.assertEqual(step.call_args_list[1].kwargs["turn"][0]["raw"], raw)

	def test_без_raw_ключа_нет(self):
		step = _step(
			{"type": "tool_use", "id": "t1", "name": "get_menu", "input": {}},
			{"type": "text", "content": "ок"},
		)
		_run(step)
		self.assertNotIn("raw", step.call_args_list[1].kwargs["turn"][0])

	def test_имя_вне_предложенного_не_исполняется(self):
		step = _step(
			{"type": "tool_use", "id": "t1", "name": "create_order", "input": {}},
			{"type": "text", "content": "готово"},
		)
		execute = Mock()
		result = _run(step, execute=execute, debug=True)
		execute.assert_not_called()
		rejection = step.call_args_list[1].kwargs["turn"][1]
		self.assertIn("create_order", rejection["content"])
		rejected = [s for s in result["debug"] if s["step"] == "tool_rejected"]
		self.assertEqual(rejected[0]["data"], {"name": "create_order", "offered": ["get_menu"]})

	def test_шаг_неизвестной_формы(self):
		with self.assertRaises(loop.BadStep) as cm:
			_run(_step({"type": "нечто"}))
		self.assertIn("неизвестной формы", str(cm.exception))

	def test_лимит_витков(self):
		step = _step(*[{"type": "tool_use", "id": f"t{i}", "name": "get_menu", "input": {}} for i in range(10)])
		with self.assertRaises(loop.LoopExhausted) as cm:
			_run(step, max_loop=3)
		self.assertEqual(step.call_count, 3)
		self.assertIn("3", str(cm.exception))

	def test_виток_в_трассировке_идёт_перед_шагами_движка(self):
		step = _step(
			{"type": "tool_use", "id": "t1", "name": "get_menu", "input": {}, "debug": [{"step": "engine_1", "data": {}}]},
			{"type": "text", "content": "ок", "debug": [{"step": "engine_2", "data": {}}]},
		)
		result = _run(step, debug=True, max_loop=4)
		steps = [s["step"] for s in result["debug"]]
		self.assertEqual(steps, ["loop", "engine_1", "loop", "engine_2"])
		self.assertEqual(result["debug"][2]["data"], {"iteration": 2, "max_loop": 4})

	def test_без_debug_трассировка_пуста(self):
		step = _step({"type": "text", "content": "ок", "debug": [{"step": "engine", "data": {}}]})
		self.assertEqual(_run(step)["debug"], [])


class TestЛимит(unittest.TestCase):
	def test_положительное_целое_берётся(self):
		self.assertEqual(loop.resolve_max_loop(3), 3)

	def test_непригодное_заменяется_значением_по_умолчанию(self):
		for bad in (None, 0, -1, "5", 2.5, True, Mock()):
			with self.subTest(bad=bad):
				self.assertEqual(loop.resolve_max_loop(bad), loop.DEFAULT_MAX_LOOP)


def _claimed_created(turn):
	# Подтверждает успешный результат вызова, а не сам вызов: довыполнение
	# стража кладёт в turn такой же tool_use, и отказ движка не должен
	# считаться подтверждением.
	created = {e["id"] for e in turn if e["type"] == "tool_use" and e["name"] == "create_order"}
	return any(e["type"] == "tool_result" and e["id"] in created and "создан" in e["content"] for e in turn)


ORDER = Commitment(
	name="order",
	claims=lambda text: "оформлен" in text,
	confirmed=lambda text, turn, known: _claimed_created(turn),
	fulfil="create_order",
	recap=lambda turn: "РЕКАП",
)
OFFERED = ("get_menu", "create_order")


class TestСтраж(unittest.TestCase):
	def _run(self, step, **extra):
		extra.setdefault("commitments", (ORDER,))
		extra.setdefault("offered", OFFERED)
		return _run(step, **extra)

	def test_подтверждённое_утверждение_уходит_как_есть(self):
		step = _step(
			{"type": "tool_use", "id": "t1", "name": "create_order", "input": {}},
			{"type": "text", "content": "Заказ оформлен"},
		)
		execute = Mock(return_value="Заказ N создан")
		result = self._run(step, execute=execute)
		self.assertEqual(result["response"], "Заказ оформлен")
		execute.assert_called_once_with("create_order", {})

	def test_утверждение_без_вызова_довыполняется(self):
		step = _step(
			{"type": "text", "content": "Заказ оформлен: SAL-ORD-1"},
			{"type": "text", "content": "Заказ SAL-ORD-2 создан"},
		)
		execute = Mock(return_value="Заказ SAL-ORD-2 создан")
		events = []
		result = self._run(step, execute=execute, on_event=events.append)
		execute.assert_called_once_with("create_order", {})
		self.assertEqual(result["response"], "Заказ SAL-ORD-2 создан")
		turn = step.call_args_list[1].kwargs["turn"]
		self.assertEqual(turn[0], {"type": "tool_use", "id": "guard-1", "name": "create_order", "input": {}})
		self.assertEqual(turn[1], {"type": "tool_result", "id": "guard-1", "content": "Заказ SAL-ORD-2 создан"})
		self.assertEqual([e["kind"] for e in events], ["violated", "fulfilled"])
		self.assertEqual(events[0]["commitment"], "order")

	def test_ложь_текст_модели_в_ответ_не_попадает(self):
		step = _step(
			{"type": "text", "content": "Заказ оформлен: SAL-ORD-1"},
			{"type": "text", "content": "Заказ создан по факту"},
		)
		result = self._run(step)
		self.assertNotIn("SAL-ORD-1", result["response"])

	def test_повторная_ложь_заменяется_пересказом(self):
		step = _step(
			{"type": "text", "content": "Заказ оформлен"},
			{"type": "text", "content": "Заказ оформлен, честно"},
		)
		execute = Mock(return_value="отказ")
		result = self._run(step, execute=execute)
		self.assertEqual(result["response"], "РЕКАП")
		execute.assert_called_once()

	def test_довыполнение_только_если_инструмент_предложен(self):
		step = _step({"type": "text", "content": "Заказ оформлен"})
		execute = Mock()
		events = []
		result = self._run(step, execute=execute, offered=("get_menu",), on_event=events.append)
		execute.assert_not_called()
		self.assertEqual(result["response"], "Заказ оформлен")
		self.assertEqual([e["kind"] for e in events], ["violated"])

	def test_на_последнем_витке_сразу_пересказ_без_вызова_движка(self):
		step = _step({"type": "text", "content": "Заказ оформлен"})
		execute = Mock(return_value="Заказ N создан")
		result = self._run(step, execute=execute, max_loop=1)
		self.assertEqual(result["response"], "РЕКАП")
		execute.assert_called_once()
		self.assertEqual(step.call_count, 1)

	def test_виток_довыполнения_тратит_лимит(self):
		step = _step(
			{"type": "text", "content": "Заказ оформлен"},
			{"type": "text", "content": "Заказ оформлен"},
		)
		result = self._run(step, max_loop=2)
		self.assertEqual(result["response"], "РЕКАП")
		self.assertEqual(step.call_count, 2)

	def test_сбой_проверки_не_роняет_ход(self):
		broken = Commitment(
			name="broken",
			claims=Mock(side_effect=RuntimeError("сбой")),
			confirmed=lambda *a: True,
			fulfil="create_order",
			recap=lambda turn: "",
		)
		events = []
		result = self._run(_step({"type": "text", "content": "привет"}), commitments=(broken,), on_event=events.append)
		self.assertEqual(result["response"], "привет")
		self.assertEqual([e["kind"] for e in events], ["guard_error"])

	def test_сбой_колбэка_не_роняет_ход(self):
		step = _step({"type": "text", "content": "Заказ оформлен"}, {"type": "text", "content": "ок"})
		result = self._run(step, on_event=Mock(side_effect=RuntimeError("сбой")))
		self.assertEqual(result["response"], "ок")

	def test_known_доезжает_до_проверки(self):
		seen = []
		spy = Commitment(
			name="spy",
			claims=lambda text: True,
			confirmed=lambda text, turn, known: seen.append(known) or True,
			fulfil="create_order",
			recap=lambda turn: "",
		)
		self._run(_step({"type": "text", "content": "x"}), commitments=(spy,), known=frozenset({"SAL-ORD-9"}))
		self.assertEqual(seen, [frozenset({"SAL-ORD-9"})])

	def test_без_обязательств_поведение_прежнее(self):
		result = _run(_step({"type": "text", "content": "Заказ оформлен"}))
		self.assertEqual(result, {"response": "Заказ оформлен", "debug": []})

	def test_нарушение_видно_в_трассировке(self):
		step = _step({"type": "text", "content": "Заказ оформлен"}, {"type": "text", "content": "ок"})
		result = self._run(step, debug=True)
		violations = [s for s in result["debug"] if s["step"] == "commitment_violation"]
		self.assertEqual(violations[0]["data"]["name"], "order")


class TestНесохранённыйОтвет(unittest.TestCase):
	def test_флаг_движка_доезжает_до_результата(self):
		result = _run(_step({"type": "text", "content": "ок", "persisted": False}))
		self.assertTrue(result["unpersisted"])

	def test_без_флага_ключа_нет(self):
		self.assertNotIn("unpersisted", _run(_step({"type": "text", "content": "ок"})))

	def test_флаг_относится_к_последнему_текстовому_шагу(self):
		step = _step(
			{"type": "text", "content": "Заказ оформлен", "persisted": False},
			{"type": "text", "content": "Заказ создан", "persisted": False},
		)
		result = _run(step, commitments=(ORDER,), offered=OFFERED)
		self.assertTrue(result["unpersisted"])

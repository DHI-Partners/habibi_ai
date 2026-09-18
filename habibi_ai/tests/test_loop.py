"""Тесты цикла хода агента.

Не импортируют frappe, как и test_engine.py: loop.py от него не зависит, и
цикл должен проверяться за секунды — и переноситься в систему не на Frappe
вместе с engine.py.
"""

import unittest
from unittest.mock import Mock

from habibi_ai import loop


def _step(*results):
	return Mock(side_effect=list(results))


def _run(step, execute=None, offered=("get_menu",), max_loop=5, debug=False):
	return loop.run(
		step,
		"привет",
		offered=list(offered),
		definitions=[{"name": n} for n in offered],
		execute=execute or Mock(return_value="результат"),
		max_loop=max_loop,
		debug=debug,
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

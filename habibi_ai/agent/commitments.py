"""Обязательство: утверждение бота о действии и то, чем оно подтверждается.

Модель ведёт разговор, но действие совершает код. Поэтому «заказ оформлен»
в её тексте — это утверждение, которое либо подтверждено результатом
инструмента, либо не должно дойти до клиента. Проверяет код: правило в
промпте модель уже нарушала.
"""

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class Commitment:
	"""claims(text) — считает ли текст действие совершённым;
	confirmed(text, turn, known) — подтверждено ли это результатами хода
	(turn) или журналом (known — множество известных номеров объектов);
	fulfil — инструмент без аргументов, безопасный для повторного вызова: им
	страж совершает недостающее действие; recap(turn) — что сказать клиенту,
	если и после довыполнения модель продолжает лгать."""

	name: str
	claims: Callable
	confirmed: Callable
	fulfil: str
	recap: Callable


def results_of(turn, tool):
	"""Тексты результатов вызовов инструмента в этом ходе, по порядку."""
	ids = {e["id"] for e in turn if e.get("type") == "tool_use" and e.get("name") == tool}
	return [e["content"] for e in turn if e.get("type") == "tool_result" and e.get("id") in ids]

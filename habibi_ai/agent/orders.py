"""Модуль «заказы» агента: обязательство «заказ оформлен» и (задача 7) стадии."""

import re

from habibi_ai.agent import registry
from habibi_ai.agent.commitments import Commitment, results_of

NUMBER = re.compile(r"SAL-ORD-\d{4}-\d+")
# «Заказ … оформлен/создан», но не «не создан»: честный отказ модели
# утверждением не считается, иначе он запускал бы довыполнение.
PHRASE = re.compile(r"заказ\w*[^.\n]{0,40}?(?<!не\s)\b(?:оформлен|создан)[аоы]?\b", re.IGNORECASE)
# Только успешный результат create_order. Наличие номера не годится: отказ
# «Заказ … был удалён оператором» тоже его содержит.
CREATED = re.compile(r"^Заказ (SAL-ORD-\d{4}-\d+) (?:уже )?создан")

FALLBACK = "Не удалось оформить заказ. Передаю ваш вопрос оператору."


def _created(turn):
	return {m.group(1) for r in results_of(turn, "create_order") if (m := CREATED.match(r))}


def _claims(text):
	return bool(NUMBER.search(text) or PHRASE.search(text))


def _confirmed(text, turn, known):
	created = _created(turn)
	numbers = set(NUMBER.findall(text))
	if numbers:
		# Номер либо создан в этом ходе, либо есть в журнале клиента; выдуманный не проходит
		return numbers <= (created | set(known))
	# Фраза без номера: ссылаться не на что, нужен результат этого хода
	return bool(created)


def _recap(turn):
	"""Первая строка успешного результата — её собрал код. Отказы обращены к
	модели («сделай новый quote_order») и клиенту не отдаются."""
	for result in reversed(results_of(turn, "create_order")):
		if CREATED.match(result):
			return result.splitlines()[0]
	return FALLBACK


ORDER_COMMITMENT = Commitment(
	name="order_created",
	claims=_claims,
	confirmed=_confirmed,
	fulfil="create_order",
	recap=_recap,
)

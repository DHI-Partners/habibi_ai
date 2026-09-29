"""Модуль «заказы» агента: обязательство «заказ оформлен» и стадии заказа."""

import re
from datetime import datetime

from habibi_ai.agent import registry
from habibi_ai.agent.commitments import Commitment, results_of
from habibi_ai.agent.registry import Capability, Module, Stage

NUMBER = re.compile(r"SAL-ORD-\d{4}-\d+")
# «Заказ … оформлен/создан». Группа between — текст между «заказ» и глаголом:
# близкое к глаголу «не» в нём делает фразу отрицанием («не был создан», «ещё не полностью
# оформлен»), а честный отказ утверждением не считается, иначе он запускал бы
# довыполнение. Lookbehind переменной ширины не бывает, поэтому проверка вторым шагом.
PHRASE = re.compile(r"заказ\w*(?P<between>[^.\n]{0,40}?)\b(?:оформлен|создан)[аоы]?\b", re.IGNORECASE)
# «не» отрицает только рядом с глаголом: без знаков препинания и не дальше двух
# слов от него. Иначе «Заказ на два бургера, не острых, оформлен» сошёл бы за отказ.
NEGATION = re.compile(r"\bне\b(?:\s+[\w-]+){0,2}\s*$", re.IGNORECASE)
# Только успешный результат create_order. Наличие номера не годится: отказ
# «Заказ … был удалён оператором» тоже его содержит.
CREATED = re.compile(r"^Заказ (SAL-ORD-\d{4}-\d+) (?:уже )?создан")

FALLBACK = "Не удалось оформить заказ. Передаю ваш вопрос оператору."


def _created(turn):
	return {m.group(1) for r in results_of(turn, "create_order") if (m := CREATED.match(r))}


def _claims(text):
	if NUMBER.search(text):
		return True
	return any(not NEGATION.search(m.group("between")) for m in PHRASE.finditer(text))


def _confirmed(text, turn, known):
	created = _created(turn)
	numbers = set(NUMBER.findall(text))
	if numbers:
		# Номер либо создан в этом ходе, либо есть в журнале клиента; выдуманный не проходит
		return numbers <= (created | set(known))
	# Фраза без номера: результат этого хода — или ссылка на уже существующий
	# заказ, когда он последнее событие (стадия ordered) и открытого расчёта нет
	return bool(created) or "stage:ordered" in known


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


def _when(value):
	"""expires_on из журнала: datetime или ISO-строка из JSON."""
	if isinstance(value, datetime):
		return value
	try:
		return datetime.fromisoformat(str(value))
	except ValueError:
		return None


def stage(events, now):
	"""Где клиент в заказе. Подсказка модели; принуждает не она, а страж."""
	quotes = [e for e in events if e["event_type"] == "quote_created"]
	created = [e for e in events if e["event_type"] == "order_created"]
	last_quote = quotes[-1] if quotes else None
	last_order = created[-1] if created else None

	if last_order and (not last_quote or last_order["occurred_at"] >= last_quote["occurred_at"]):
		return Stage(
			"ordered",
			f"заказ {last_order['ref_name']} создан, ждёт подтверждения оператора",
			"сообщить номер и что оператор подтвердит; про статус говорить только то, что есть в событиях, "
			"иначе направить к оператору",
		)
	if last_quote:
		delivered = any(
			e["event_type"] == "quote_delivered" and e.get("ref_name") == last_quote.get("ref_name")
			for e in events
		)
		if not delivered:
			return Stage(
				"quote_pending",
				"расчёт создан, но до клиента не дошёл",
				"зачитать расчёт клиенту заново",
			)
		expires = _when((last_quote.get("data") or {}).get("expires_on"))
		if expires and now > expires:
			return Stage("quote_expired", "расчёт зачитан, но устарел", "предложить пересчитать заказ")
		return Stage(
			"quoted",
			"расчёт зачитан, ждём ответа клиента",
			"«да» — оформить заказ; правка состава — новый расчёт; не оформлять без явного согласия",
		)
	return Stage("new", "новый разговор или заказа ещё нет", "выяснить, что хочет клиент")


MODULE = registry.register(
	Module(
		name="orders",
		feature="orders",
		stage=stage,
		commitments=(ORDER_COMMITMENT,),
		pin=("quote_created", "quote_delivered", "order_created"),
		labels={"quote_order": "расчёт", "create_order": "заказ"},
		capabilities=(Capability("create_order", lambda events, now: stage(events, now).name == "quoted"),),
	)
)

"""Журнал событий клиента: запись и выборка для «хода дел».

События пишет код — инструменты, канал и страж; модель их не создаёт и не
правит. Запись не имеет права стоить клиенту ответа или заказа: сбой уходит
в Error Log, а вызывающий идёт дальше.

Имя AI Event — хэш, а не нумерованный ряд: ряд держит блокировку строки в
tabSeries до коммита, то есть весь ход с обращением к LLM, и каждая вставка
события в другом ходе стояла бы в очереди за ним. Дедлок и таймаут — наружу, как в
api.features_hook: транзакция откачена, пусть вызывающий повторит.
"""

import json

import frappe

from habibi_ai import customers

DOCTYPE = "AI Event"
ACTORS = ("Bot", "Client", "Operator", "System")
SUMMARY_LIMIT = 200
SAVEPOINT = "ai_event"
FIELDS = [
	"name",
	"event_type",
	"occurred_at",
	"actor",
	"summary",
	"customer",
	"ref_doctype",
	"ref_name",
	"data",
]


def _one_line(text, limit=SUMMARY_LIMIT):
	return " ".join(str(text or "").split())[:limit]


def record(event_type, summary, *, context=None, actor="Bot", customer=None, ref=None, data=None):
	"""Записывает событие и возвращает его имя, а при сбое — None.

	Под своим savepoint: неудачная вставка не должна откатить заказ, в
	транзакции которого событие пишется. customer по умолчанию — клиент,
	привязанный к канальному чату хода: пока он неизвестен, событие
	находится по чату.
	"""
	context = context or {}
	frappe.db.savepoint(SAVEPOINT)
	try:
		if actor not in ACTORS:
			raise ValueError(f"актор {actor!r} не из {ACTORS}")
		ref_doctype, ref_name = ref or (None, None)
		channel = context.get("channel_chat") or (None, None)
		doc = frappe.get_doc(
			{
				"doctype": DOCTYPE,
				"event_type": event_type,
				"occurred_at": frappe.utils.now_datetime(),
				"actor": actor,
				"customer": customer or customers.linked_customer(context.get("channel_chat")),
				"engine_chat_id": context.get("engine_chat_id"),
				"turn_id": context.get("turn_id"),
				"channel_doctype": channel[0],
				"channel_name": channel[1],
				"ref_doctype": ref_doctype,
				"ref_name": ref_name,
				"summary": _one_line(summary),
				# default=str: в data кладут datetime и Decimal, а JSON-поле их не принимает
				"data": json.loads(json.dumps(data, default=str)) if data else None,
			}
		)
		# Ссылка на объект — след, а не связь: оператор мог удалить черновик заказа, а журнал
		# обязан остаться. Проверка существования цели при вставке тут не нужна.
		doc.flags.ignore_links = True
		doc.insert(ignore_permissions=True)
		return doc.name
	except (frappe.QueryDeadlockError, frappe.QueryTimeoutError):
		raise
	except Exception:
		frappe.db.rollback(save_point=SAVEPOINT)
		frappe.log_error(title="ИИ: журнал событий", message=frappe.get_traceback())
		return None


def recent(context, limit=100):
	"""События этого чата и, если клиент известен, всего клиента — по возрастанию.

	Клиент важнее чата: тот же человек может прийти из другого канала, и его
	прошлые заказы должны быть видны боту. Пока клиент не известен, ключ —
	чат.
	"""
	chat = context.get("engine_chat_id")
	customer = customers.linked_customer(context.get("channel_chat"))
	or_filters = []
	if chat:
		or_filters.append(["engine_chat_id", "=", chat])
	if customer:
		or_filters.append(["customer", "=", customer])
	channel = context.get("channel_chat")
	if channel:
		or_filters.append(["channel_name", "=", channel[1]])
	if not or_filters:
		return []

	rows = frappe.get_all(
		DOCTYPE,
		or_filters=or_filters,
		fields=FIELDS,
		order_by="occurred_at desc, creation desc",
		limit=limit,
	)
	rows.reverse()
	for row in rows:
		if isinstance(row.get("data"), str):
			row["data"] = json.loads(row["data"])
		row["data"] = row.get("data") or {}
	return rows


GUARD_EVENTS = {
	"violated": ("commitment_violated", "Бот написал утверждение без действия ({name})"),
	"fulfilled": ("commitment_fulfilled", "Действие выполнено стражем по согласию клиента ({name})"),
}


def record_guard(event, context):
	"""Событие стража цикла → запись журнала. guard_error — только в Error Log:
	это сбой механизма, а не что-то, что случилось с клиентом."""
	spec = GUARD_EVENTS.get(event.get("kind"))
	if spec is None:
		frappe.log_error(title="ИИ: сбой стража", message=f"{event.get('commitment')}: {event.get('detail')}")
		return None
	event_type, template = spec
	return record(
		event_type,
		template.format(name=event["commitment"]),
		context=context,
		actor="System",
		data={"commitment": event["commitment"], "detail": event.get("detail")},
	)


# Заголовки Error Log, которые пишет сам механизм: по ним считаются его сбои
ERROR_TITLES = ("ИИ: журнал событий", "ИИ: ход дел", "ИИ: сбой стража", "ИИ: история ответа")


def guard_stats(hours=24):
	"""Сводка стража и журнала за период: сколько раз он ловил ложь и сколько раз ломался."""
	since = frappe.utils.add_to_date(frappe.utils.now_datetime(), hours=-hours)
	def count(event_type):
		return frappe.db.count(DOCTYPE, {"occurred_at": [">=", since], "event_type": event_type})

	errors = frappe.db.count("Error Log", {"creation": [">=", since], "method": ["in", list(ERROR_TITLES)]})
	return {
		"hours": hours,
		"violated": count("commitment_violated"),
		"fulfilled": count("commitment_fulfilled"),
		"errors": errors,
	}

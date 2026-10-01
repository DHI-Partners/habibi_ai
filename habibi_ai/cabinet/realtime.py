"""События для кабинета: новое сообщение, новый или изменённый заказ от бота.

after_commit — иначе фронт перечитал бы данные раньше, чем они записаны, и
показал бы старое состояние до следующего события.

publish_realtime без room/user транслирует всем, кто подключён к сайту —
включая портальных/website-пользователей (frappe.realtime.get_site_room()).
Кабинет им не адресован, а в payload и так утекает только «что-то изменилось
в чате X», без текста — но всё равно рассылаем прицельно: по одному
publish_realtime(user=...) на каждого пользователя с ролью Habibi Owner,
Habibi Staff или System Manager. В малом бизнесе это одна-три учётные записи,
так что цикл дешевле отдельной комнаты, которой во Frappe для ролей и нет.

Кухня и курьеры получают отдельное событие fulfilment на любой заказ — без
данных, только сигнал перечитать очередь.
"""

import frappe
from frappe.query_builder import DocType

from habibi_ai.cabinet import scope

EVENT = "habibi_cabinet"
CABINET_ROLES = ("Habibi Owner", "Habibi Staff", "System Manager")
# Кухня и курьеры слушают свой канал: любое изменение заказа, а не только
# заказов бота — кухне нужны и заказы, заведённые оператором руками
FLOOR_ROLES = ("Habibi Kitchen", "Habibi Courier")


def _recipients(roles=CABINET_ROLES):
	"""Пользователи кабинета без дублей — одна и та же учётка может держать
	сразу несколько из перечисленных ролей.

	Один запрос с DISTINCT вместо frappe.utils.user.get_users_with_role в
	цикле по ролям (там — отдельный запрос на роль): семантика та же, что и у
	него — enabled == 1, Administrator не считается (у него роли не через
	Has Role, и рассылка ему как техническому пользователю не нужна)."""
	User = DocType("User")
	HasRole = DocType("Has Role")
	return (
		frappe.qb.from_(HasRole)
		.from_(User)
		.where(
			HasRole.role.isin(roles)
			& (User.name != "Administrator")
			& (User.enabled == 1)
			& (HasRole.parent == User.name)
		)
		.select(User.name)
		.distinct()
		.run(pluck=True)
	)


def on_change(doc, method=None):
	"""Хук на after_insert/on_update/on_submit/on_cancel нескольких доктайпов.

	Ошибка здесь не должна ронять запись сообщения, обновление заказа или
	паузу чата — событие кабинета вторично по отношению к самой операции.
	Дедлок и таймаут блокировки — исключение: после них транзакция уже
	откатилась базой, и проглоти мы ошибку — вызывающий счёл бы запись
	состоявшейся. Пусть он узнает и повторит (как в on_message_insert)."""
	try:
		_on_change(doc)
	except (frappe.QueryDeadlockError, frappe.QueryTimeoutError):
		raise
	except Exception:
		frappe.log_error(title="Кабинет: realtime-событие", message=frappe.get_traceback())


def _publish(payload, roles):
	for user in _recipients(roles):
		frappe.publish_realtime(EVENT, payload, user=user, after_commit=True)


def _on_change(doc):
	if doc.doctype == "Sales Order":
		# Сигнал «перечитай очередь» — без данных, на любой заказ
		_publish({"topic": "fulfilment", "chat": None}, FLOOR_ROLES)
		if not frappe.db.exists("AI Order Quote", {"sales_order": doc.name}):
			return
		payload = {"topic": "orders", "chat": None}
	else:
		chat = doc.telegram_chat if doc.doctype == "AI Channel Chat" else doc.chat
		# То же правило, что у списка переписок: сообщения групп, служебного
		# чата и синхронизации истории чужих диалогов кабинет не будят
		if not scope.in_scope(chat):
			return
		payload = {"topic": "chats", "chat": chat}
	_publish(payload, CABINET_ROLES)

"""Какие чаты Telegram — переписки кабинета.

Аккаунт Telegram видит все свои диалоги: служебный чат 777000 с кодами входа,
«Избранное» (чат с самим собой), личные переписки владельца, группы. Кабинету
из них нужны только диалоги с клиентами, которые ведёт ИИ-канал, — это чаты с
парой AI Channel Chat. Одно правило для списка, ленты, ответа, паузы и
realtime-событий: иначе чат, скрытый из списка, открывался бы по имени, а
синхронизация истории чужих групп будила бы кабинет впустую.
"""

import frappe

from habibi_ai.channels.decisions import SERVICE_CHAT_IDS

PAIR = "AI Channel Chat"
CHAT_TYPE = "private"


def excluded_chat_ids():
	"""chat_id, которых в кабинете не бывает никогда, даже с парой.

	Служебные чаты Telegram (коды входа) и «Избранное» каждого аккаунта: у
	чата с самим собой chat_id равен account_id аккаунта. get_all, а не
	get_list: это правило сервера, а не выборка от имени пользователя."""
	own = frappe.get_all("Telegram Account", filters={"account_id": ["is", "set"]}, pluck="account_id")
	return sorted(SERVICE_CHAT_IDS | {str(a) for a in own if a})


def list_filters():
	"""Фильтры get_list по Telegram Chat — то же правило, что in_scope, для выборки."""
	paired = frappe.get_all(PAIR, pluck="telegram_chat", distinct=True)
	return [
		["type", "=", CHAT_TYPE],
		["chat_id", "not in", excluded_chat_ids()],
		["name", "in", paired or [""]],
	]


def in_scope(chat):
	"""Переписка ли кабинета этот чат: личный, с парой ИИ-канала, не служебный
	и не «Избранное». Точечная проверка — без выборки всех пар: её зовёт хук
	на каждое сообщение Telegram."""
	if not chat or not frappe.db.exists(PAIR, {"telegram_chat": chat}):
		return False
	row = frappe.db.get_value("Telegram Chat", chat, ["type", "chat_id"], as_dict=True)
	if not row or row.type != CHAT_TYPE:
		return False
	return str(row.chat_id or "") not in excluded_chat_ids()


def require(chat):
	"""Чат вне кабинета — как несуществующий: не подсказываем, что он есть."""
	if not in_scope(chat):
		frappe.throw(frappe._("Чат не найден"), frappe.DoesNotExistError)

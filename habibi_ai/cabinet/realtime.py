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
"""

import frappe
from frappe.utils.user import get_users_with_role

EVENT = "habibi_cabinet"
CABINET_ROLES = ("Habibi Owner", "Habibi Staff", "System Manager")


def _recipients():
	"""Пользователи кабинета без дублей — одна и та же учётка может держать
	сразу несколько из перечисленных ролей."""
	users = set()
	for role in CABINET_ROLES:
		users.update(get_users_with_role(role))
	return users


def on_change(doc, method=None):
	if doc.doctype == "Sales Order":
		if not frappe.db.exists("AI Order Quote", {"sales_order": doc.name}):
			return
		payload = {"topic": "orders", "chat": None}
	elif doc.doctype == "AI Channel Chat":
		payload = {"topic": "chats", "chat": doc.telegram_chat}
	else:
		payload = {"topic": "chats", "chat": doc.chat}
	for user in _recipients():
		frappe.publish_realtime(EVENT, payload, user=user, after_commit=True)

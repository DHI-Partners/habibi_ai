"""Whitelisted-методы модуля.

Единственное место, где берётся имя сайта. Ни один параметр запроса на tenant
не влияет: клиент не может назваться чужим тенантом, потому что tenant вообще
не читается из запроса.
"""

import frappe

from habibi_ai.engine import ChatNotFound, EngineClient


def get_client():
	url = frappe.conf.get("habibi_ai_engine_url")
	token = frappe.conf.get("habibi_ai_engine_token")
	if not url or not token:
		frappe.throw(
			"Движок ИИ не настроен: habibi_ai_engine_url и habibi_ai_engine_token "
			"задаются в common_site_config.json"
		)
	return EngineClient(url, token, frappe.local.site)


@frappe.whitelist()
def list_bots():
	return get_client().list_bots()


@frappe.whitelist()
def list_chats():
	return get_client().list_chats(frappe.session.user)


@frappe.whitelist()
def get_chat(chat_id):
	client = get_client()
	try:
		chat = client.get_chat(int(chat_id))
		return {"chat": chat, "messages": client.get_messages(int(chat_id))}
	except ChatNotFound:
		frappe.throw("Чат не найден", frappe.DoesNotExistError)


@frappe.whitelist()
def send_message(chat_id, message, bot_id=None):
	try:
		return get_client().send_message(int(chat_id), message, bot_id)
	except ChatNotFound:
		frappe.throw("Чат не найден", frappe.DoesNotExistError)

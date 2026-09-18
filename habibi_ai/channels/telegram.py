"""Telegram как канал ИИ: входящее → движок → ответ тем же каналом.

habibi_telegram про ИИ не знает: сюда приходят его события (after_insert у
Telegram Message), отсюда вызываются его функции отправки. Решения без
frappe — в decisions.py, здесь только клей.
"""

import frappe

from habibi_ai.channels import decisions
from habibi_ai.engine import BotNotFound
from habibi_ai.habibi_ai.doctype.ai_channel_chat.ai_channel_chat import REASON_MANUAL

PAIR = "AI Channel Chat"
REASON_OPERATOR = "Оператор ответил вручную"
REASON_FORBIDDEN = "Нет прав писать"


def validate_channel(doc, method=None):
	"""Включить ИИ можно только с существующим ботом тенанта.

	Иначе ошибка всплыла бы в фоновой задаче на первом же сообщении клиента —
	в логе, который никто не читает, а клиент остался бы без ответа.
	"""
	if not doc.get("ai_enabled"):
		return

	if not doc.get("ai_bot"):
		frappe.throw("Выберите ИИ-бота, который будет отвечать в этом канале")

	try:
		bot_id = int(doc.ai_bot)
	except (TypeError, ValueError):
		frappe.throw(f"ИИ-бот указан неверно: {doc.ai_bot}")

	from habibi_ai import api

	try:
		api.get_client()._check_bot(bot_id)
	except BotNotFound:
		frappe.throw("ИИ-бот не найден")

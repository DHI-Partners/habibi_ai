"""Пара «канал + Telegram-чат»: чат в движке и пауза ИИ.

Отдельный доктайп, а не поля на Telegram Chat: чат именуется по chat_id, и
личный диалог с одним человеком через бота и через личный аккаунт — один и
тот же документ, а решение «отвечать ли ИИ» у этих каналов разное.
"""

from frappe.model.document import Document
from frappe.utils import now_datetime

from habibi_ai.channels.decisions import pair_name

REASON_MANUAL = "Выключено вручную"


class AIChannelChat(Document):
	def autoname(self):
		# Имя — ключ уникальности: две задачи, заводящие одну пару, упрутся в
		# первичный ключ, а не создадут дубль
		self.name = pair_name(self.channel_doctype, self.channel_name, self.telegram_chat)

	def validate(self):
		if self.ai_paused:
			self.paused_reason = self.paused_reason or REASON_MANUAL
			self.paused_on = self.paused_on or now_datetime()
		else:
			self.paused_reason = None
			self.paused_on = None

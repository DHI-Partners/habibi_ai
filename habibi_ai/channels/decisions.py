"""Решения канального адаптера, не зависящие от frappe.

Отвечать ли на сообщение, что склеить в одно, считать ли ошибку отправки
запретом писать. Здесь, а не в telegram.py: цена ошибки — ответ на вчерашнее
или два бота, переписывающиеся по кругу, и проверяться это должно за
секунды. Заодно это переносится в систему не на Frappe вместе с loop.py.
"""

import re
from datetime import timedelta

# Старше этого не отвечаем: подтянутая история MTProto, переполнение
# getDifference, повторная доставка. Ответ на вчерашнее хуже молчания.
FRESH_FOR = timedelta(minutes=5)

# Пометка вложения или служебного события вместо текста: [voice], [photo],
# [chatjoinedbylink]. Такие записи пишет habibi_telegram, когда текста нет.
_MARKER = re.compile(r"\[[\w ]+\]")

# Ошибки, после которых писать в чат бессмысленно, пока человек не вмешается.
# Сравнение по подстроке в нижнем регистре: Bot API и Telethon формулируют
# их по-разному, а повторять отправку в чат, где нас заблокировали, — спам.
WRITE_FORBIDDEN_MARKERS = (
	"chat_write_forbidden",
	"write in this chat",
	"bot was blocked",
	"bot was kicked",
	"not enough rights",
	"have no rights",
	"user is deactivated",
	"chat_admin_required",
	"user_banned_in_channel",
	"chat not found",
)


def text_of(content):
	"""Текст сообщения без пометок вложений; пустая строка, если текста нет."""
	text = (content or "").strip()
	if not text or _MARKER.fullmatch(text):
		return ""
	return text


def is_replyable(message, sender_is_bot, now):
	"""Годится ли само сообщение для ответа — без учёта настроек канала."""
	if message.get("direction") != "Incoming":
		return False
	# Два бота в группе иначе переписывались бы бесконечно
	if sender_is_bot:
		return False
	if not text_of(message.get("content")):
		return False
	sent_on = message.get("sent_on")
	# Без даты — только записи бота до миграции; вебхук приходит сразу
	if sent_on is not None and now - sent_on > FRESH_FOR:
		return False
	return True


def should_reply(message, channel, pair, sender_is_bot, now):
	"""Ставить ли задачу ответа на только что записанное сообщение."""
	if not channel or not channel.get("ai_enabled") or not channel.get("ai_bot"):
		return False
	if pair and pair.get("ai_paused"):
		return False
	return is_replyable(message, sender_is_bot, now)


def combine(contents):
	"""Несколько сообщений подряд — одно сообщение для движка.

	Люди пишут «Здравствуйте», «хочу заказать», «пиццу» тремя сообщениями;
	ответ на каждое по отдельности выглядел бы как три ответа невпопад.
	"""
	return "\n".join(text for text in (text_of(c) for c in contents) if text)


def is_write_forbidden(error_text):
	lowered = (error_text or "").lower()
	return any(marker in lowered for marker in WRITE_FORBIDDEN_MARKERS)


def pair_name(channel_doctype, channel_name, telegram_chat):
	"""Имя записи AI Channel Chat — оно же ключ уникальности пары."""
	return f"{channel_doctype}:{channel_name}:{telegram_chat}"


def external_user(channel_doctype, channel_name, chat_id):
	"""external_user чата в движке: по нему видно, откуда чат, в админке Directus."""
	return f"telegram:{channel_doctype}:{channel_name}:{chat_id}"

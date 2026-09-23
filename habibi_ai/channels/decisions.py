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
	# Так Telethon пересказывает USER_BANNED_IN_CHANNEL человеческим языком
	"banned from sending",
	"chat not found",
)

# Служебный чат Telegram: в нём приходят коды входа. Коды не должны попасть
# к LLM ни при каких настройках канала.
SERVICE_CHAT_IDS = frozenset({"777000"})

# Групповые чаты: ИИ в них отвечает только с разрешения канала
GROUP_CHAT_TYPES = frozenset({"group", "supergroup"})

# Предел длины текста одного сообщения в Telegram
TELEGRAM_TEXT_LIMIT = 4096


def text_of(content):
	"""Текст сообщения без пометок вложений; пустая строка, если текста нет."""
	text = (content or "").strip()
	if not text or _MARKER.fullmatch(text):
		return ""
	return text


def is_fresh(message, now):
	"""Не старше FRESH_FOR. Без даты — только записи бота до миграции;
	вебхук приходит сразу, так что такие считаем свежими."""
	sent_on = message.get("sent_on")
	return sent_on is None or now - sent_on <= FRESH_FOR


def is_replyable(message, sender_is_bot, now, sender_in_dialogue=False):
	"""Годится ли само сообщение для ответа — без учёта настроек канала.

	sender_in_dialogue — у отправителя идёт диалог с обработчиком бота
	(авторизация): его сообщения — ответы на вопросы обработчика, а не ИИ.
	"""
	if message.get("direction") != "Incoming":
		return False
	# Два бота в группе иначе переписывались бы бесконечно
	if sender_is_bot:
		return False
	if sender_in_dialogue:
		return False
	text = text_of(message.get("content"))
	if not text:
		return False
	# Команды разбирают обработчики бота; ответ ИИ поверх них — второй ответ
	if text.startswith("/"):
		return False
	return is_fresh(message, now)


def should_reply(message, channel, pair, sender_is_bot, now, sender_in_dialogue=False):
	"""Ставить ли задачу ответа на только что записанное сообщение."""
	if not channel or not channel.get("ai_enabled") or not channel.get("ai_bot"):
		return False
	if pair and pair.get("ai_paused"):
		return False
	return is_replyable(message, sender_is_bot, now, sender_in_dialogue)


def should_pause(message, now):
	"""Ставить ли чат на паузу из-за исходящего.

	Только свежий ответ человека: импорт истории пишет старые ответы
	оператора, и пауза на каждый выключила бы ИИ почти во всех диалогах.
	"""
	if message.get("direction") != "Outgoing" or message.get("is_automated"):
		return False
	return is_fresh(message, now)


def is_service_chat(chat_id):
	return chat_id is not None and str(chat_id) in SERVICE_CHAT_IDS


def chat_allows_ai(chat_id, chat_type, reply_in_groups=False):
	"""Уместен ли ИИ в этом чате вообще.

	Личный чат — да. Группа — только если канал явно разрешил: живой номер
	состоит в чужих барахолках и новостных чатах, и ответ на каждое сообщение
	каждого участника — спам от имени владельца и бан номера. Канал вещания и
	служебный чат Telegram — никогда.

	Тип бывает пуст, когда MTProto не прислал сущность чата; тогда решает знак
	id: положительный в Telegram бывает только у пользователя, а неизвестную
	группу считаем группой.
	"""
	if is_service_chat(chat_id):
		return False
	kind = chat_type
	if not kind:
		try:
			kind = "private" if int(chat_id) > 0 else None
		except (TypeError, ValueError):
			kind = None
	if kind == "private":
		return True
	return bool(reply_in_groups) and kind in GROUP_CHAT_TYPES


def combine(contents):
	"""Несколько сообщений подряд — одно сообщение для движка.

	Люди пишут «Здравствуйте», «хочу заказать», «пиццу» тремя сообщениями;
	ответ на каждое по отдельности выглядел бы как три ответа невпопад.
	"""
	return "\n".join(text for text in (text_of(c) for c in contents) if text)


# Пометка ручного ответа в истории движка: модель должна отличать, что
# обещал человек, от того, что говорила сама, — иначе «я уже говорил» про
# слова сотрудника.
STAFF_MARK = "[Ответил сотрудник] "


def history_from_pause(messages):
	"""Переписка за время паузы → реплики [(роль, текст)] для истории движка.

	Входящее — user, ручной ответ — assistant с пометкой STAFF_MARK. Ответы ИИ
	(is_automated) пропускаются: они уже в истории движка, и второй раз там
	был бы повтором. Подряд идущие реплики одной роли склеиваются, как в
	combine: история чередует роли, и три «user» подряд рвали бы её.
	"""
	history = []
	for message in messages:
		text = text_of(message.get("content"))
		if not text:
			continue
		if message.get("direction") == "Incoming":
			# Чего бот не видит при ответе (is_replyable), того нет и в его
			# истории: команды — обработчику бота, боты и ответы на вопросы
			# входа — не к ИИ
			if text.startswith("/") or message.get("sender_is_bot") or message.get("sender_in_dialogue"):
				continue
			role = "user"
		elif message.get("is_automated"):
			continue
		else:
			role = "assistant"

		if history and history[-1][0] == role:
			history[-1] = (role, f"{history[-1][1]}\n{text}")
		else:
			history.append((role, STAFF_MARK + text if role == "assistant" else text))
	return history


def split_pause_window(rows):
	"""Сообщения после паузы → (переписка сотрудника, хвост после неё).

	Окно — до последнего ручного ответа включительно: на это сотрудник уже
	ответил, оно уходит в историю бота и считается отвеченным. Хвост —
	то, что клиент написал после, — остаётся боту: иначе вопрос, заданный
	после последнего ответа сотрудника, не получил бы ответа вовсе. Без
	ручного ответа всё — хвост. rows — по возрастанию времени.
	"""
	last = None
	for i, row in enumerate(rows):
		if row.get("direction") == "Outgoing" and not row.get("is_automated"):
			last = i
	if last is None:
		return [], list(rows)
	return list(rows[: last + 1]), list(rows[last + 1 :])


def is_write_forbidden(error_text):
	lowered = (error_text or "").lower()
	return any(marker in lowered for marker in WRITE_FORBIDDEN_MARKERS)


def split_text(text, limit=TELEGRAM_TEXT_LIMIT):
	"""Длинный ответ — несколько сообщений не длиннее limit.

	Режем по последнему переводу строки в пределах лимита, чтобы не рвать
	абзац посреди фразы; без переводов строки — ровно по лимиту. Перевод
	строки на месте разреза уходит: он и так стал границей сообщений.
	"""
	chunks = []
	while len(text) > limit:
		cut = text.rfind("\n", 0, limit + 1)
		if cut > 0:
			chunks.append(text[:cut])
			text = text[cut + 1 :]
		else:
			chunks.append(text[:limit])
			text = text[limit:]
	if text:
		chunks.append(text)
	return chunks


# Markdown модели → HTML Telegram. Telegram сам Markdown модели не понимает:
# без перевода клиент видит **звёздочки** и решётки заголовков.
_FENCE = re.compile(r"```[^\n`]*\n?(.*?)```", re.S)
_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_LINK = re.compile(r"\[([^\]\n]+)\]\((https?://[^)\s]+)\)")
_HEADING = re.compile(r"^[ \t]*#{1,6}[ \t]+(.+?)[ \t#]*$", re.M)
_BULLET = re.compile(r"^([ \t]*)[-*+][ \t]+", re.M)
_BOLD = re.compile(r"\*\*(?!\s)(.+?)(?<!\s)\*\*|__(?!\s)(.+?)(?<!\s)__")
# Курсив — только слово, обнятое с обеих сторон: иначе «2 * 3 * 4» и
# snake_case превращались бы в курсив
_ITALIC = re.compile(
	r"(?<![\w*])\*(?![\s*])([^*\n]+?)(?<!\s)\*(?![\w*])|(?<![\w_])_(?![\s_])([^_\n]+?)(?<!\s)_(?![\w_])"
)
_TRAILING_SPACES = re.compile(r"[ \t]+$", re.M)
_STASHED = re.compile("\x00(\\d+)\x00")

PARSE_ERROR_MARKERS = ("can't parse entities", "can not parse entities", "unsupported start tag")


def to_telegram_html(text):
	"""Разметка модели → подмножество HTML, которое понимает Telegram.

	Экранирование < и & здесь не делается: его делает strip_unsupported_html_tags
	habibi_telegram, через который проходит любой HTML перед отправкой, —
	экранируй мы здесь, амперсанды экранировались бы дважды.

	Код и ссылки прячутся до разбора остального, чтобы звёздочки внутри них не
	стали жирным.
	"""
	stash = []

	def keep(html):
		stash.append(html)
		return f"\x00{len(stash) - 1}\x00"

	text = _FENCE.sub(lambda m: keep(f"<pre>{m.group(1).rstrip()}</pre>"), text)
	text = _INLINE_CODE.sub(lambda m: keep(f"<code>{m.group(1)}</code>"), text)
	text = _LINK.sub(lambda m: keep(f'<a href="{m.group(2)}">{m.group(1)}</a>'), text)
	text = _HEADING.sub(r"<b>\1</b>", text)
	text = _BULLET.sub("\\1• ", text)
	text = _BOLD.sub(lambda m: f"<b>{m.group(1) or m.group(2)}</b>", text)
	text = _ITALIC.sub(lambda m: f"<i>{m.group(1) or m.group(2)}</i>", text)
	text = _TRAILING_SPACES.sub("", text)
	return _STASHED.sub(lambda m: stash[int(m.group(1))], text)


def is_parse_error(error_text):
	"""Telegram не принял разметку — тогда тот же текст уходит без неё."""
	lowered = (error_text or "").lower()
	return any(marker in lowered for marker in PARSE_ERROR_MARKERS)


def pair_name(channel_doctype, channel_name, telegram_chat):
	"""Имя записи AI Channel Chat — оно же ключ уникальности пары."""
	return f"{channel_doctype}:{channel_name}:{telegram_chat}"


def external_user(channel_doctype, channel_name, chat_id):
	"""external_user чата в движке: по нему видно, откуда чат, в админке Directus."""
	return f"telegram:{channel_doctype}:{channel_name}:{chat_id}"

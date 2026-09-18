"""Telegram как канал ИИ: входящее → движок → ответ тем же каналом.

habibi_telegram про ИИ не знает: сюда приходят его события (after_insert у
Telegram Message), отсюда вызываются его функции отправки. Решения без
frappe — в decisions.py, здесь только клей.
"""

import time

import frappe
import requests
from frappe.utils import get_datetime, now_datetime

from habibi_ai.channels import decisions
from habibi_ai.engine import BotNotFound, ChatNotFound, EngineError
from habibi_ai.habibi_ai.doctype.ai_channel_chat.ai_channel_chat import REASON_MANUAL
from habibi_ai.loop import LoopExhausted

PAIR = "AI Channel Chat"
REASON_OPERATOR = "Оператор ответил вручную"
REASON_FORBIDDEN = "Нет прав писать"

# Поле сообщения → доктайп канала. У входящего заполнено ровно одно.
CHANNEL_FIELDS = {"telegram_bot": "Telegram Bot", "telegram_account": "Telegram Account"}

# Люди пишут несколькими сообщениями подряд — ждём, пока допишут
DEBOUNCE_SECONDS = 3
RETRY_DELAY_SECONDS = 30
# Сколько раз подряд отвечать в одной задаче, если пока шла генерация,
# пришло ещё. Больше — это уже не «дописал», а живой диалог: то, что
# осталось после последнего раунда, ждёт, пока клиент напишет снова.
MAX_ROUNDS = 5
LOCK_TIMEOUT = 600
PENDING_LIMIT = 20


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


def channel_of(message):
	"""(доктайп, имя) канала, через который прошло сообщение, или None."""
	for field, doctype in CHANNEL_FIELDS.items():
		if message.get(field):
			return doctype, message.get(field)
	return None


def channel_settings(channel_doctype, channel_name):
	"""Настройки ИИ канала; None, если полей ещё нет (не было миграции)."""
	if not frappe.get_meta(channel_doctype).has_field("ai_enabled"):
		return None
	return frappe.db.get_value(
		channel_doctype, channel_name, ["ai_enabled", "ai_bot", "notify_user"], as_dict=True
	)


def on_message_insert(doc, method=None):
	"""after_insert у Telegram Message.

	Ошибка здесь не должна ронять запись сообщения: хук выполняется внутри
	вебхука и синхронизации, и упавший ИИ стоил бы потерянной истории.
	"""
	try:
		_on_message_insert(doc)
	except Exception:
		frappe.log_error(title="ИИ: разбор сообщения Telegram", message=frappe.get_traceback())


def _on_message_insert(doc):
	channel = channel_of(doc)
	if not channel:
		return

	settings = channel_settings(*channel)
	if not settings or not settings.ai_enabled:
		return

	if doc.direction == "Outgoing":
		# Написал человек — дальше диалог ведёт он, ИИ не перебивает
		if not doc.get("is_automated") and not _is_our_reply_again(doc):
			pause(channel, doc.chat, REASON_OPERATOR)
		return

	pair = frappe.db.get_value(PAIR, decisions.pair_name(*channel, doc.chat), ["ai_paused"], as_dict=True)
	message = {
		"direction": doc.direction,
		"content": doc.content,
		"sent_on": get_datetime(doc.sent_on) if doc.sent_on else None,
	}
	if decisions.should_reply(message, settings, pair, _sender_is_bot(doc.from_user), now_datetime()):
		enqueue_reply(channel, doc.chat)


def _is_our_reply_again(doc):
	"""Это исходящее — повторная запись нашего же ответа?

	Синхронизация MTProto открывает снимок REPEATABLE READ до сетевых
	вызовов: если reply_job закоммитил ответ в этом окне, дедупликация по
	(chat, message_id) его не видит, и тот же ответ пишется второй раз, уже
	без is_automated. Блокирующее чтение видит последние закоммиченные
	строки — без него ИИ ставил бы паузу на собственный ответ.
	"""
	if not doc.get("message_id"):
		return False
	return bool(
		frappe.db.sql(
			"""select name from `tabTelegram Message`
			where chat=%s and message_id=%s and name!=%s and is_automated=1
			limit 1 lock in share mode""",
			(doc.chat, doc.message_id, doc.name),
		)
	)


def _sender_is_bot(telegram_user):
	return bool(telegram_user and frappe.db.get_value("Telegram User", telegram_user, "is_bot"))


def enqueue_reply(channel, chat):
	"""Одна задача на пару: пока она ждёт или идёт, новых не ставим — новое
	сообщение подберёт она сама (reply_job, следующий раунд).

	enqueue_after_commit: если вебхук откатится, ответа на несуществующее
	сообщение не будет.
	"""
	frappe.enqueue(
		"habibi_ai.channels.telegram.reply_job",
		queue="long",
		job_id=f"ai-reply:{decisions.pair_name(*channel, chat)}",
		deduplicate=True,
		enqueue_after_commit=True,
		channel_doctype=channel[0],
		channel_name=channel[1],
		chat=chat,
	)


def get_or_create_pair(channel, chat):
	name = decisions.pair_name(*channel, chat)
	if frappe.db.exists(PAIR, name):
		return frappe.get_doc(PAIR, name)
	doc = frappe.get_doc(
		{"doctype": PAIR, "channel_doctype": channel[0], "channel_name": channel[1], "telegram_chat": chat}
	)
	try:
		doc.insert(ignore_permissions=True)
	except frappe.DuplicateEntryError:
		# Пару одновременно завела параллельная задача — берём её
		return frappe.get_doc(PAIR, name)
	return doc


def pause(channel, chat, reason):
	"""Выключить ИИ в чате. Уже стоящую паузу не трогаем — первая причина
	важнее: «оператор ответил» не должно затираться «нет прав»."""
	pair = get_or_create_pair(channel, chat)
	if pair.ai_paused:
		return
	frappe.db.set_value(PAIR, pair.name, {"ai_paused": 1, "paused_reason": reason, "paused_on": now_datetime()})


def pending_messages(channel, chat, last_processed=None):
	"""Входящие этого канала в чате после последнего отвеченного.

	Берём последние PENDING_LIMIT и отбрасываем несвежие: на первом сообщении
	пары last_processed пуст, и без этого в движок ушла бы вся история.
	"""
	field = next(f for f, doctype in CHANNEL_FIELDS.items() if doctype == channel[0])
	filters = {"chat": chat, field: channel[1], "direction": "Incoming"}
	if last_processed:
		created = frappe.db.get_value("Telegram Message", last_processed, "creation")
		if created:
			filters["creation"] = (">", created)

	rows = frappe.get_all(
		"Telegram Message",
		filters=filters,
		fields=["name", "direction", "content", "sent_on", "from_user"],
		order_by="creation desc",
		limit=PENDING_LIMIT,
	)
	now = now_datetime()
	return [
		row
		for row in reversed(rows)
		if decisions.is_replyable(
			{"direction": row.direction, "content": row.content, "sent_on": row.sent_on},
			_sender_is_bot(row.from_user),
			now,
		)
	]


def reply_job(channel_doctype, channel_name, chat):
	"""Фоновая задача: ответить на всё, что накопилось в чате.

	От Administrator: задачу ставит вебхук от имени Guest, а инструментам
	нужен понятный пользователь. Сейчас они только читают.
	"""
	frappe.set_user("Administrator")
	channel = (channel_doctype, channel_name)
	time.sleep(DEBOUNCE_SECONDS)

	lock = frappe.cache().lock(
		f"{frappe.local.site}:ai-reply:{decisions.pair_name(*channel, chat)}", timeout=LOCK_TIMEOUT
	)
	if not lock.acquire(blocking=True, blocking_timeout=LOCK_TIMEOUT):
		return

	try:
		for _round in range(MAX_ROUNDS):
			if not _reply_round(channel, chat):
				break
	finally:
		try:
			lock.release()
		except Exception:
			# Истёк по таймауту — отпускать нечего
			pass


def _reply_round(channel, chat):
	"""Один ответ на накопленное. True — ответ ушёл, стоит проверить ещё раз."""
	# Свежий снимок: в REPEATABLE READ задача иначе не увидит ни сообщений,
	# пришедших за время генерации, ни паузы, выставленной оператором
	frappe.db.rollback()

	settings = channel_settings(*channel)
	if not settings or not settings.ai_enabled or not settings.ai_bot:
		return False

	pair = get_or_create_pair(channel, chat)
	if pair.ai_paused:
		return False

	pending = pending_messages(channel, chat, pair.last_processed_message)
	if not pending:
		return False

	text = decisions.combine(row.content for row in pending)
	last = pending[-1].name

	try:
		reply = _generate(pair, int(settings.ai_bot), text, chat)
	except LoopExhausted as e:
		_report(channel, chat, str(e), notify_user=settings.notify_user)
		_mark_processed(pair, last)
		return False
	except Exception:
		# Клиенту ничего не пишем: пусть лучше ответит оператор, чем бот
		# пришлёт «ошибка»
		_report(channel, chat, frappe.get_traceback())
		_mark_processed(pair, last)
		return False

	if not (reply or "").strip():
		_mark_processed(pair, last)
		return False

	try:
		send(channel, chat, reply)
	except Exception as e:
		if decisions.is_write_forbidden(str(e)):
			pause(channel, chat, REASON_FORBIDDEN)
		_report(channel, chat, frappe.get_traceback())
		_mark_processed(pair, last)
		return False

	_mark_processed(pair, last)
	return True


def _generate(pair, bot_id, text, chat):
	"""Ход агента с одной повторной попыткой на сбой движка.

	Чат движка мог быть удалён в админке — тогда заводим новый: история
	переписки в движке потеряна, но клиент ответ получит.
	"""
	from habibi_ai import api

	client = api.get_client()
	for attempt in (1, 2):
		try:
			engine_chat_id = _engine_chat(client, pair, bot_id, chat)
			return api.run_turn(client, engine_chat_id, text, bot_id)["response"]
		except ChatNotFound:
			if attempt == 2:
				raise
			pair.db_set("engine_chat_id", None)
			frappe.db.commit()
		except (EngineError, requests.RequestException):
			if attempt == 2:
				raise
			time.sleep(RETRY_DELAY_SECONDS)


def _engine_chat(client, pair, bot_id, chat):
	if pair.engine_chat_id:
		return pair.engine_chat_id
	chat_id = frappe.db.get_value("Telegram Chat", chat, "chat_id")
	created = client.create_chat(bot_id, decisions.external_user(pair.channel_doctype, pair.channel_name, chat_id))
	pair.db_set("engine_chat_id", created["id"])
	# Сразу: следующий раунд начинается с rollback, и без коммита чат в
	# движке заводился бы заново на каждое сообщение
	frappe.db.commit()
	return created["id"]


def send(channel, chat, text):
	"""Ответ тем же каналом, с пометкой «не человек» — иначе наш же ответ
	поставил бы чат на паузу."""
	chat_id = frappe.db.get_value("Telegram Chat", chat, "chat_id")
	if channel[0] == "Telegram Bot":
		from habibi_telegram.client import send_message

		send_message(text, from_bot=channel[1], chat_id=chat_id, automated=True)
	else:
		from habibi_telegram.user_client import send_message

		send_message(channel[1], chat_id, text, automated=True)


def _mark_processed(pair, last):
	frappe.db.set_value(PAIR, pair.name, "last_processed_message", last)
	frappe.db.commit()


def _report(channel, chat, detail, notify_user=None):
	frappe.log_error(title=f"ИИ не ответил в Telegram ({channel[1]})", message=f"Чат: {chat}\n\n{detail}")
	if notify_user:
		frappe.get_doc(
			{
				"doctype": "Notification Log",
				"for_user": notify_user,
				"type": "Alert",
				"subject": f"ИИ не смог ответить в чате {chat}",
				"email_content": detail,
				"document_type": "Telegram Chat",
				"document_name": chat,
			}
		).insert(ignore_permissions=True)
	frappe.db.commit()

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
#
# Аккаунт проверяется первым: у сообщений аккаунтов, записанных до 1.3.8,
# в telegram_bot стоит бот по умолчанию (frappe подставлял одноимённый
# глобальный default), и ответ уходил бы ботом, которому писать нельзя.
CHANNEL_FIELDS = {"telegram_account": "Telegram Account", "telegram_bot": "Telegram Bot"}

# Люди пишут несколькими сообщениями подряд — ждём, пока допишут
DEBOUNCE_SECONDS = 3
RETRY_DELAY_SECONDS = 30
# Сколько раз подряд отвечать в одной задаче, если пока шла генерация,
# пришло ещё. Больше — это уже не «дописал», а живой диалог: то, что
# осталось после последнего раунда, ждёт, пока клиент напишет снова.
MAX_ROUNDS = 5
LOCK_TIMEOUT = 600
PENDING_LIMIT = 20
# Сколько живёт отметка «ИИ отправляет в этот чат». С запасом на отправку и
# запись: слушатель MTProto успевает записать наш ответ раньше reply_job.
SENDING_MARKER_TTL = 60


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

	# Движок спрашиваем, только когда меняли ИИ: иначе упавший движок не
	# давал бы сохранить карточку канала ради любого другого поля
	if not (doc.has_value_changed("ai_enabled") or doc.has_value_changed("ai_bot")):
		return

	from habibi_ai import api

	try:
		api.get_client()._check_bot(bot_id)
	except BotNotFound:
		frappe.throw("ИИ-бот не найден")
	except (EngineError, requests.RequestException) as e:
		frappe.throw(f"Движок ИИ недоступен: {e}")


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

	Кроме дедлока и таймаута блокировки: после них база уже откатила всю
	транзакцию, и проглоти мы ошибку — вызывающий закоммитил бы пустоту,
	посчитав сообщения записанными. Пусть он узнает и повторит.
	"""
	try:
		_on_message_insert(doc)
	except (frappe.QueryDeadlockError, frappe.QueryTimeoutError):
		raise
	except Exception:
		frappe.log_error(title="ИИ: разбор сообщения Telegram", message=frappe.get_traceback())


def _on_message_insert(doc):
	channel = channel_of(doc)
	if not channel:
		return

	settings = channel_settings(*channel)
	if not settings or not settings.ai_enabled:
		return

	if not _chat_is_answerable(doc.chat):
		return

	message = {
		"direction": doc.direction,
		"content": doc.content,
		"is_automated": doc.get("is_automated"),
		"sent_on": get_datetime(doc.sent_on) if doc.sent_on else None,
	}
	now = now_datetime()

	if doc.direction == "Outgoing":
		# Написал человек — дальше диалог ведёт он, ИИ не перебивает. Пока
		# ИИ сам отправляет в чат, «ручное» исходящее — это наш же ответ,
		# записанный слушателем MTProto раньше, чем reply_job его пометил
		if decisions.should_pause(message, now) and not _ai_is_sending(channel, doc.chat):
			pause(channel, doc.chat, REASON_OPERATOR)
		return

	pair = frappe.db.get_value(PAIR, decisions.pair_name(*channel, doc.chat), ["ai_paused"], as_dict=True)
	is_bot, in_dialogue = _sender_flags(doc.from_user)
	if decisions.should_reply(message, settings, pair, is_bot, now, sender_in_dialogue=in_dialogue):
		enqueue_reply(channel, doc.chat)


def _chat_is_answerable(chat):
	"""Чат, в котором ИИ вообще уместен.

	Служебный чат Telegram (коды входа) — никогда: коды не должны попасть к
	LLM. Канал вещания — тоже: ответить подписчику там невозможно.
	"""
	row = frappe.db.get_value("Telegram Chat", chat, ["chat_id", "type"], as_dict=True)
	if not row:
		return False
	return not decisions.is_service_chat(row.chat_id) and row.type != "channel"


def sending_marker(pair):
	return f"ai-sending:{pair}"


def _ai_is_sending(channel, chat):
	# Мимо локального кэша процесса: он не знает об отметке, поставленной
	# задачей в другом процессе, и о её истечении
	return bool(
		frappe.cache().get_value(sending_marker(decisions.pair_name(*channel, chat)), use_local_cache=False)
	)


def _sender_flags(telegram_user):
	"""(бот ли отправитель, идёт ли у него диалог с обработчиком бота).

	Непустой conversation_state — habibi_telegram ведёт с человеком диалог
	(авторизация): его сообщения — ответы обработчику, а не вопросы ИИ.
	"""
	if not telegram_user:
		return False, False
	row = frappe.db.get_value("Telegram User", telegram_user, ["is_bot", "conversation_state"], as_dict=True)
	if not row:
		return False, False
	return bool(row.is_bot), (row.conversation_state or "").strip() not in ("", "{}")


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
	if not _chat_is_answerable(chat):
		return []

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
	pending = []
	for row in reversed(rows):
		is_bot, in_dialogue = _sender_flags(row.from_user)
		message = {"direction": row.direction, "content": row.content, "sent_on": row.sent_on}
		if decisions.is_replyable(message, is_bot, now, sender_in_dialogue=in_dialogue):
			pending.append(row)
	return pending


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

	# До отправки, а не после: слушатель MTProto может записать наш ответ
	# раньше, чем send() вернётся, и хук поставил бы паузу на него
	frappe.cache().set_value(sending_marker(pair.name), 1, expires_in_sec=SENDING_MARKER_TTL)
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
	поставил бы чат на паузу.

	Аккаунтом — частями не длиннее 4096 символов: user_client шлёт текст
	как есть, и длинный ответ Telegram не принял бы целиком.
	"""
	chat_id = frappe.db.get_value("Telegram Chat", chat, "chat_id")
	if channel[0] == "Telegram Bot":
		from habibi_telegram.client import send_message

		send_message(text, from_bot=channel[1], chat_id=chat_id, automated=True)
	else:
		from habibi_telegram.user_client import send_message

		for part in decisions.split_text(text):
			send_message(channel[1], chat_id, part, automated=True)


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

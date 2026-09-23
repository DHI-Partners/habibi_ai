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
# Длина части ответа до перевода в HTML: теги добавляют символы, а Telegram
# не примет сообщение длиннее 4096
PART_LIMIT = 3500
# Сколько последних сообщений паузы дописывать в историю движка. Пауза в
# часы — десятки сообщений; предел — на случай, когда отметка пары застряла
# далеко позади (ИИ неделями был выключен на канале), чтобы в историю бота
# не ушёл целый архив переписки.
SYNC_LIMIT = 100


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
		channel_doctype,
		channel_name,
		["ai_enabled", "ai_bot", "ai_reply_in_groups", "notify_user"],
		as_dict=True,
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

	if not _chat_is_answerable(doc.chat, settings):
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
	is_bot, in_dialogue = _sender_flags(doc.from_user, channel)
	if decisions.should_reply(message, settings, pair, is_bot, now, sender_in_dialogue=in_dialogue):
		enqueue_reply(channel, doc.chat)


def _chat_is_answerable(chat, settings):
	"""Чат, в котором ИИ вообще уместен — см. decisions.chat_allows_ai."""
	row = frappe.db.get_value("Telegram Chat", chat, ["chat_id", "type"], as_dict=True)
	if not row:
		return False
	return decisions.chat_allows_ai(row.chat_id, row.type, (settings or {}).get("ai_reply_in_groups"))


def sending_marker(pair):
	return f"ai-sending:{pair}"


def _ai_is_sending(channel, chat):
	# Мимо локального кэша процесса: он не знает об отметке, поставленной
	# задачей в другом процессе, и о её истечении
	return bool(
		frappe.cache().get_value(sending_marker(decisions.pair_name(*channel, chat)), use_local_cache=False)
	)


def _sender_flags(telegram_user, channel):
	"""(бот ли отправитель, идёт ли у него диалог с обработчиком бота).

	Непустой conversation_state — habibi_telegram ведёт с человеком диалог
	(авторизация): его сообщения — ответы обработчику, а не вопросы ИИ.

	Только для канала-бота: диалог ведёт обработчик бота, а живой аккаунт его
	не видит. Telegram User общий для бота и аккаунта, и клиент, когда-то
	начавший вход в боте, иначе навсегда остался бы без ответа в аккаунте.
	"""
	if not telegram_user:
		return False, False
	row = frappe.db.get_value("Telegram User", telegram_user, ["is_bot", "conversation_state"], as_dict=True)
	if not row:
		return False, False
	in_dialogue = channel[0] == "Telegram Bot" and (row.conversation_state or "").strip() not in ("", "{}")
	return bool(row.is_bot), in_dialogue


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


def _channel_field(channel):
	return next(f for f, doctype in CHANNEL_FIELDS.items() if doctype == channel[0])


def on_pair_update(doc, method=None):
	"""on_update у AI Channel Chat: паузу сняли галочкой в Desk.

	paused_on берётся из версии до сохранения: validate пары его уже стёр.
	Кабинет снимает паузу через db.set_value — сюда не попадает и зовёт
	sync_paused_history сам, так что двойной синхронизации нет.
	"""
	before = doc.get_doc_before_save()
	if not before or not before.ai_paused or doc.ai_paused:
		return
	sync_paused_history(
		(doc.channel_doctype, doc.channel_name), doc.telegram_chat, paused_on=before.paused_on
	)


def sync_paused_history(channel, chat, paused_on=None):
	"""Снятие паузы: переписка без бота — в историю движка, её вопросы — в отвеченные.

	Бот иначе не знал бы, что обещал сотрудник, и ответил бы второй раз на
	свежие вопросы клиента, которые сотрудник уже закрыл.

	Отметка сдвигается всегда, даже если движок не ответил: лучше бот без
	контекста, чем второй ответ клиенту. Коммита здесь нет — сохранение пары
	или запрос кабинета закоммитят снятие паузы и отметку вместе.
	"""
	pair = get_or_create_pair(channel, chat)
	newest = frappe.get_all(
		"Telegram Message",
		filters={"chat": chat, _channel_field(channel): channel[1]},
		order_by="creation desc",
		limit=1,
		pluck="name",
	)
	if not newest:
		return

	since = None
	if pair.last_processed_message:
		since = frappe.db.get_value("Telegram Message", pair.last_processed_message, "creation")
	since = since or paused_on
	# Ни отметки, ни начала паузы — неизвестно, с какого места диалог шёл
	# мимо бота; вся история чата в движок не уходит
	if since:
		try:
			_push_history(pair, channel, chat, since)
		except (frappe.QueryDeadlockError, frappe.QueryTimeoutError):
			raise
		except Exception:
			frappe.log_error(
				title="ИИ: переписка паузы не ушла в движок",
				message=f"Чат: {chat}\n\n{frappe.get_traceback()}",
			)

	frappe.db.set_value(PAIR, pair.name, "last_processed_message", newest[0])


def _push_history(pair, channel, chat, since):
	settings = channel_settings(*channel)
	if not settings or not settings.ai_enabled or not settings.ai_bot:
		return
	# Служебный чат с кодами входа и чужие группы не должны попасть к LLM
	# и так — как и в pending_messages
	if not _chat_is_answerable(chat, settings):
		return

	rows = frappe.get_all(
		"Telegram Message",
		filters={
			"chat": chat,
			_channel_field(channel): channel[1],
			"is_deleted": 0,
			"creation": (">", since),
		},
		fields=["direction", "content", "is_automated"],
		order_by="creation desc",
		limit=SYNC_LIMIT,
	)
	history = decisions.history_from_pause(reversed(rows))
	if not history:
		return

	from habibi_ai import api

	client = api.get_client()
	bot_id = int(settings.ai_bot)
	try:
		client.add_messages(_engine_chat(client, pair, bot_id, chat, commit=False), history)
	except ChatNotFound:
		# Чат движка удалили в админке — как в _generate, заводим новый
		pair.db_set("engine_chat_id", 0)
		client.add_messages(_engine_chat(client, pair, bot_id, chat, commit=False), history)


def pending_messages(channel, chat, last_processed=None, settings=None):
	"""Входящие этого канала в чате после последнего отвеченного.

	Берём последние PENDING_LIMIT и отбрасываем несвежие: на первом сообщении
	пары last_processed пуст, и без этого в движок ушла бы вся история.
	"""
	if settings is None:
		settings = channel_settings(*channel)
	if not _chat_is_answerable(chat, settings):
		return []

	filters = {"chat": chat, _channel_field(channel): channel[1], "direction": "Incoming"}
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
		is_bot, in_dialogue = _sender_flags(row.from_user, channel)
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

	pending = pending_messages(channel, chat, pair.last_processed_message, settings)
	if not pending:
		return False

	text = decisions.combine(row.content for row in pending)
	last = pending[-1].name
	# Время записи, а не sent_on: сравнивается с созданием расчёта, и оба
	# должны быть по одним часам — серверным
	message_at = frappe.db.get_value("Telegram Message", last, "creation")

	try:
		result = _generate(pair, int(settings.ai_bot), text, chat, message_at)
		reply = result["response"]
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

	# Записанное ходом (расчёт заказа) фиксируем до отправки, и отправка идёт
	# в свежем снимке. Генерация длится секунды, и за это время слушатель
	# MTProto успевает обновить превью того же Telegram Chat; habibi_telegram,
	# записывая наш ответ в журнал, правил бы строку по устаревшему снимку —
	# MariaDB отвергает это (1020) и откатывает весь раунд.
	frappe.db.commit()

	# До отправки, а не после: слушатель MTProto может записать наш ответ
	# раньше, чем send() вернётся, и хук поставил бы паузу на него
	frappe.cache().set_value(sending_marker(pair.name), 1, expires_in_sec=SENDING_MARKER_TTL)
	try:
		send(channel, chat, reply)
	except (frappe.QueryDeadlockError, frappe.QueryTimeoutError):
		# Отправка в Telegram до базы не пишет — сбой случился уже в записи
		# копии ответа в журнал. Клиент ответ получил: откатываем только
		# несостоявшуюся запись и считаем ответ отправленным, иначе на «да»
		# create_order не увидит отметки и бот повторит расчёт вместо заказа.
		# Копию в журнал запишет слушатель MTProto.
		frappe.db.rollback()
		frappe.log_error(title="ИИ: ответ ушёл, журнал Telegram не записан", message=frappe.get_traceback())
	except Exception as e:
		if decisions.is_write_forbidden(str(e)):
			pause(channel, chat, REASON_FORBIDDEN)
		_report(channel, chat, frappe.get_traceback())
		_mark_processed(pair, last)
		return False

	# После отправки, а не до: не дошедший до клиента расчёт оформлять нельзя
	from habibi_ai.tools.orders import mark_answered

	mark_answered(result.get("turn_id"))
	_mark_processed(pair, last)
	return True


def _generate(pair, bot_id, text, chat, message_at=None):
	"""Ход агента с одной повторной попыткой на сбой движка.

	Чат движка мог быть удалён в админке — тогда заводим новый: история
	переписки в движке потеряна, но клиент ответ получит.
	"""
	from habibi_ai import api

	client = api.get_client()
	for attempt in (1, 2):
		try:
			engine_chat_id = _engine_chat(client, pair, bot_id, chat)
			return api.run_turn(
				client,
				engine_chat_id,
				text,
				bot_id,
				channel_chat=("Telegram Chat", chat),
				message_at=message_at,
			)
		except ChatNotFound:
			if attempt == 2:
				raise
			# 0, а не None: поле Int, колонка NOT NULL — None ронял бы запись
			pair.db_set("engine_chat_id", 0)
			frappe.db.commit()
		except (EngineError, requests.RequestException):
			if attempt == 2:
				raise
			time.sleep(RETRY_DELAY_SECONDS)


def _engine_chat(client, pair, bot_id, chat, commit=True):
	if pair.engine_chat_id:
		return pair.engine_chat_id
	chat_id = frappe.db.get_value("Telegram Chat", chat, "chat_id")
	created = client.create_chat(bot_id, decisions.external_user(pair.channel_doctype, pair.channel_name, chat_id))
	pair.db_set("engine_chat_id", created["id"])
	# Сразу: следующий раунд начинается с rollback, и без коммита чат в
	# движке заводился бы заново на каждое сообщение. Вне reply_job (снятие
	# паузы) коммитит вызывающий — вместе со своей записью.
	if commit:
		frappe.db.commit()
	return created["id"]


def send(channel, chat, text):
	"""Ответ тем же каналом, с пометкой «не человек» — иначе наш же ответ
	поставил бы чат на паузу.

	Модель пишет Markdown, Telegram его не понимает — текст уходит HTML.
	Частями, чтобы с тегами уложиться в 4096 символов. Если Telegram не
	разобрал разметку, та же часть уходит простым текстом: лучше звёздочки,
	чем клиент без ответа.
	"""
	chat_id = frappe.db.get_value("Telegram Chat", chat, "chat_id")
	for part in decisions.split_text(text, PART_LIMIT):
		try:
			_send_part(channel, chat_id, decisions.to_telegram_html(part), "HTML")
		except Exception as e:
			if not decisions.is_parse_error(str(e)):
				raise
			_send_part(channel, chat_id, part, None)


def _send_part(channel, chat_id, text, parse_mode):
	if channel[0] == "Telegram Bot":
		from habibi_telegram.client import send_message

		# HTML чистит и экранирует сам client.send_message
		send_message(text, parse_mode=parse_mode, from_bot=channel[1], chat_id=chat_id, automated=True)
	else:
		from habibi_telegram.user_client import send_message
		from habibi_telegram.utils.formatting import strip_unsupported_html_tags

		if parse_mode:
			text = strip_unsupported_html_tags(text)
		send_message(channel[1], chat_id, text, parse_mode=parse_mode, automated=True)


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

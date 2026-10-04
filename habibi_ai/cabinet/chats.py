"""Переписки в кабинете: кто что написал и кто сейчас отвечает — бот или человек.

Экран не знает про Telegram: канал — деталь сервера. WhatsApp ляжет рядом
вторым адаптером, форма ответа не изменится.
"""

import frappe
from frappe import _
from frappe.utils import now_datetime

from habibi_telegram import user_client

from habibi_ai import api
from habibi_ai.cabinet import realtime, scope
from habibi_ai.channels import decisions, telegram

PAIR = "AI Channel Chat"
PAUSED_BY_STAFF = "Выключено вручную"
# Чистить переписку вправе только владелец: это стирание истории, а не рабочее действие
DELETE_ROLES = ("Habibi Owner", "System Manager")
# Сколько минут клиент может ждать, прежде чем переписка помечается «без ответа»
UNANSWERED_AFTER_MIN = 5
# Дольше этого срока молчание уже не срочное: старые переписки шумом не показываем
UNANSWERED_WINDOW_H = 24
LIST_LIMIT = 100
PAGE = 50


def _check_list_permission():
	"""Право видеть переписки вообще — без него нет смысла отдавать список."""
	if not frappe.has_permission("Telegram Chat", "read"):
		frappe.throw(_("Нет доступа к перепискам"), frappe.PermissionError)


def _check_chat_permission(chat):
	"""Плюс к общему праву — право именно на этот документ: User Permission
	режет доступ по конкретным чатам, а не только по типу документа."""
	_check_list_permission()
	scope.require(chat)
	if not frappe.has_permission("Telegram Chat", "read", doc=chat):
		frappe.throw(_("Нет доступа к этому чату"), frappe.PermissionError)


def _pair(chat):
	"""Пара канал+чат, которой ведётся диалог с этим чатом.

	Служебный чат и «Избранное» отсекаются и здесь: пара у них может быть
	(канал на аккаунте), но ни ответить туда, ни снять паузу из кабинета
	нельзя — см. cabinet.scope."""
	scope.require(chat)
	name = frappe.db.get_value(PAIR, {"telegram_chat": chat}, "name", order_by="modified desc")
	if not name:
		frappe.throw(_("Чат не подключён к боту"), frappe.DoesNotExistError)
	return frappe.get_doc(PAIR, name)


def _pair_for_write(chat):
	"""Право отвечать в чате — право писать саму пару, а не чат Telegram:
	сотрудник может видеть переписки, но не каждую вести."""
	pair = _pair(chat)
	pair.check_permission("write")
	return pair


def _waiting_chats(rows, paused):
	"""Чаты, где последнее сообщение от клиента и он ждёт дольше UNANSWERED_AFTER_MIN минут.

	Признак строится по самой переписке, а не по списку известных ошибок: так видно
	любую причину молчания, в том числе ту, о которой система не узнала. Чат на паузе
	не в счёт — там ответ намеренно за человеком, и его показывает «ждёт человека».
	"""
	candidates = [
		r.name
		for r in rows
		if r.name not in paused
		and r.last_message_on
		and UNANSWERED_AFTER_MIN * 60
		<= frappe.utils.time_diff_in_seconds(frappe.utils.now_datetime(), r.last_message_on)
		<= UNANSWERED_WINDOW_H * 3600
	]
	if not candidates:
		return set()
	last = frappe.db.sql(
		"""
		select m.chat, m.direction from `tabTelegram Message` m
		join (select chat, max(creation) as at from `tabTelegram Message` where chat in %(chats)s group by chat) x
			on x.chat = m.chat and x.at = m.creation
		""",
		{"chats": candidates},
	)
	return {chat for chat, direction in last if direction == "Incoming"}


@frappe.whitelist()
def list():
	_check_list_permission()
	rows = frappe.get_list(
		"Telegram Chat",
		fields=["name", "title", "chat_id", "last_message_content", "last_message_on"],
		# Только переписки кабинета — без служебного чата с кодами входа,
		# «Избранного» и личных диалогов без ИИ-канала (cabinet.scope)
		filters=scope.list_filters(),
		order_by="last_message_on desc",
		limit=LIST_LIMIT,
	)
	names = [r.name for r in rows]
	pairs = {
		p.telegram_chat: p
		for p in frappe.get_all(
			PAIR,
			filters={"telegram_chat": ["in", names]},
			fields=["telegram_chat", "ai_paused", "channel_doctype"],
			order_by="modified asc",
		)
	}
	paused = {chat: p.ai_paused for chat, p in pairs.items()}
	# Буквенное имя собеседника (@username): в личном чате id чата — это id пользователя
	usernames = {
		u.telegram_user_id: u.telegram_username
		for u in frappe.get_all(
			"Telegram User",
			filters={"telegram_user_id": ["in", [r.chat_id for r in rows if r.chat_id]]},
			fields=["telegram_user_id", "telegram_username"],
		)
	}
	customers = dict(
		frappe.get_all(
			"Dynamic Link",
			filters={"parenttype": "Telegram Chat", "parent": ["in", names], "link_doctype": "Customer"},
			fields=["parent", "link_name"],
			as_list=True,
		)
	)
	waiting = _waiting_chats(rows, {chat for chat, is_paused in paused.items() if is_paused})
	return [
		{
			"chat": r.name,
			"title": r.title or r.name,
			"preview": (r.last_message_content or "")[:80],
			"last_at": str(r.last_message_on or ""),
			"paused": bool(paused.get(r.name)),
			# Клиент написал последним и ждёт ответа дольше порога — бот молчит или не смог ответить
			"unanswered": r.name in waiting,
			# Под именем в шапке: числовой ID и @username — их копируют, чтобы найти человека в Telegram
			"telegram_id": r.chat_id,
			"username": (usernames.get(r.chat_id) or "").lstrip("@") or None,
			# Чат ведёт личный аккаунт — тогда переписку можно стереть и в самом Telegram
			"via_account": bool(r.name in pairs and pairs[r.name].channel_doctype == "Telegram Account"),
			"customer": customers.get(r.name),
		}
		for r in rows
	]


def _author(m):
	if m.direction == "Incoming":
		return "client"
	return "bot" if m.is_automated else "staff"


@frappe.whitelist()
def messages(chat, before=None):
	_check_chat_permission(chat)
	filters = {"chat": chat, "is_deleted": 0}
	if before:
		filters["creation"] = ["<", before]
	rows = frappe.get_list(
		"Telegram Message",
		fields=["name", "content", "creation", "direction", "is_automated"],
		filters=filters,
		order_by="creation desc",
		limit=PAGE,
	)
	return [
		{"name": m.name, "text": m.content or "", "at": str(m.creation), "author": _author(m)}
		for m in reversed(rows)
	]


def _notify_chat_changed(chat):
	"""pause/resume/send пишут через frappe.db.set_value — он не зовёт
	on_update, поэтому хук в hooks.py тут не сработает: шлём событие сами."""
	realtime.on_change(frappe._dict(doctype="AI Channel Chat", telegram_chat=chat))


@frappe.whitelist(methods=["POST"])
def pause(chat):
	p = _pair_for_write(chat)
	telegram.pause((p.channel_doctype, p.channel_name), chat, PAUSED_BY_STAFF)
	_notify_chat_changed(chat)


@frappe.whitelist(methods=["POST"])
def resume(chat):
	p = _pair_for_write(chat)
	# Под блокировкой строки пары: два resume подряд иначе оба увидели бы
	# паузу и дописали бы переписку в историю бота дважды
	row = frappe.db.get_value(
		PAIR, p.name, ["ai_paused", "paused_on", "last_processed_message"], as_dict=True, for_update=True
	)
	if not row.ai_paused:
		# Чат и так у бота: сдвиг отметки пометил бы отвеченным то, на что
		# бот вот-вот ответит
		return
	values = {"ai_paused": 0, "paused_reason": None, "paused_on": None}
	# Переписка сотрудника — в историю бота, её вопросы — в отвеченные.
	# db.set_value не зовёт on_update, так что хук Desk второй раз этого не сделает
	mark = telegram.release_pause(
		(p.channel_doctype, p.channel_name), chat, row.paused_on, row.last_processed_message
	)
	if mark:
		values["last_processed_message"] = mark
	frappe.db.set_value(PAIR, p.name, values)
	_notify_chat_changed(chat)


@frappe.whitelist(methods=["POST"])
def send(chat, text):
	"""Ответ сотрудника. Пауза — до отправки: иначе бот мог бы ответить поверх.

	Пустой текст отбрасываем первым делом: это ошибка ввода, а не вопрос
	доступа, и должна быть одной и той же для всех, независимо от прав.
	"""
	if not (text or "").strip():
		frappe.throw(_("Пустое сообщение"))
	p = _pair_for_write(chat)
	telegram.pause((p.channel_doctype, p.channel_name), chat, PAUSED_BY_STAFF)
	_notify_chat_changed(chat)
	sent = text.strip()
	# Метка времени — сразу перед отправкой, а не раньше: пауза уже могла
	# впустить бот-раунд, начатый до неё, и его сообщение не должно попасть
	# в диапазон «наше».
	sent_at = now_datetime()
	# habibi_telegram сам делает frappe.throw(describe_error(e)) на сбое
	# отправки — это сообщение уже легло в message_log и ушло бы клиенту
	# первым, в обход safe-текста ниже (тот же приём, что в
	# cabinet.settings._call_telegram)
	log = frappe.local.message_log
	mark = len(log)
	try:
		telegram.send((p.channel_doctype, p.channel_name), chat, sent)
	except Exception as e:
		del log[mark:]
		# Полный текст — только в лог: у сетевых ошибок Telegram-клиента в
		# сообщении зашит URL вида /bot<TOKEN>/..., и это утекло бы наружу.
		# description — safe-текст от самого Telegram (TelegramAPIError, когда
		# он ответил ok=false); во всех прочих случаях — общая фраза.
		frappe.log_error(
			title=f"Не удалось отправить ответ сотрудника в чат {chat}", message=frappe.get_traceback()
		)
		safe = getattr(e, "description", None) or _("Telegram недоступен, попробуйте позже")
		frappe.throw(safe)
	# send помечает сообщение automated=True — это защита от самопаузы для
	# ответов бота. Здесь писал человек, и лента должна это показать — но
	# только у сообщений, которые действительно наш ответ: если параллельно
	# успел ответить бот (его раунд стартовал до пары), его сообщение не
	# должно перекраситься в «сотрудник» лишь потому, что оно свежее нашего
	# или начинается так же (короткое «Да» бота — не префикс нашего «Да,
	# сейчас уточню», раз он весь целиком отдельное сообщение).
	#
	# Сравниваем с точными частями, на которые telegram.send() режет текст
	# (decisions.split_text с тем же лимитом) — длинный ответ уходит
	# несколькими сообщениями. Каждая часть могла лечь в историю в одном из
	# двух видов: как HTML-разметка (её и хранит content — Telegram
	# возвращает результат без тегов, только оформленный текст) либо как
	# обычный текст, если разметка не прошла и telegram.send() откатился на
	# него (decisions.is_parse_error).
	parts = decisions.split_text(sent, telegram.PART_LIMIT)
	expected = {part for part in parts} | {decisions.to_telegram_html(part) for part in parts}
	candidates = frappe.get_all(
		"Telegram Message",
		filters={"chat": chat, "direction": "Outgoing", "creation": [">=", sent_at]},
		fields=["name", "content"],
	)
	ours = [c.name for c in candidates if c.content in expected]
	for name in ours:
		frappe.db.set_value("Telegram Message", name, "is_automated", 0, update_modified=False)


CONFLICT_RETRIES = 3


def _retry_on_conflict(fn):
	"""Повторить fn, если слушатель Telegram успел изменить ту же строку (1020 / QueryDeadlockError).

	Перед повтором откатываем транзакцию: её снимок устарел, и без отката конфликт повторился бы."""
	for attempt in range(CONFLICT_RETRIES):
		try:
			return fn()
		except frappe.QueryDeadlockError:
			frappe.db.rollback()
			if attempt == CONFLICT_RETRIES - 1:
				frappe.throw(_("Чат занят: идёт обмен сообщениями. Попробуйте ещё раз через несколько секунд."), frappe.ValidationError)


def _clear_local(chat, pair):
	"""Стереть переписку у нас: сообщения и превью чата. Привязка ссылается на сообщение — отпускаем её первой."""
	frappe.db.set_value(PAIR, pair.name, {"last_processed_message": None})
	frappe.db.delete("Telegram Message", {"chat": chat})
	frappe.db.set_value("Telegram Chat", chat, {"last_message_on": None, "last_message_content": None}, update_modified=False)


@frappe.whitelist(methods=["POST"])
def delete_conversation(chat, in_telegram=0):
	"""Очистить переписку: сообщения у нас и память ИИ о диалоге. Чат и подключение бота остаются.

	in_telegram — стереть и в самом Telegram у всех участников. Возможно только для
	чатов личного аккаунта: бот через Bot API не стирает чужие и старые сообщения.
	Telegram идёт первым: не принял — у нас ничего не удаляем, чтобы переписка не
	разошлась (у нас пусто, в Telegram есть)."""
	if not set(DELETE_ROLES) & set(frappe.get_roles()):
		frappe.throw(_("Очищать переписки может только владелец"), frappe.PermissionError)
	_check_chat_permission(chat)
	pair = _pair(chat)
	in_telegram = bool(int(in_telegram or 0))
	if in_telegram and pair.channel_doctype != "Telegram Account":
		frappe.throw(
			_("Бот не может удалять сообщения в Telegram — только в чатах личного аккаунта"), frappe.ValidationError
		)

	message_count = frappe.db.count("Telegram Message", {"chat": chat})
	found = left = 0
	if in_telegram:
		# Историю перечисляет сам Telegram, а не наша база: после её чистки старые сообщения известны
		# только ему. Он идёт первым — не принял, у нас ничего не удаляем, чтобы не разойтись
		chat_id = frappe.db.get_value("Telegram Chat", chat, "chat_id")
		cleared = user_client.clear_history(pair.channel_name, chat_id, revoke=True)
		found, left = cleared["found"], cleared["left"]

	# Слушатель Telegram меняет строку чата одновременно с нами — MariaDB отвечает 1020
	# (QueryDeadlockError). Telegram уже стёр сообщения, поэтому свою часть повторяем
	# с чистой транзакцией, а не падаем: иначе у них пусто, а у нас всё осталось
	_retry_on_conflict(lambda: _clear_local(chat, pair))

	engine = "none"
	if pair.engine_chat_id:
		try:
			engine = "cleared" if api.get_client().delete_chat(pair.engine_chat_id) else "none"
		except Exception:
			# Память ИИ — вторична: переписка у нас уже стёрта, причину оставляем в журнале ошибок
			frappe.log_error(title="Не удалось стереть диалог в движке ИИ", message=frappe.get_traceback())
			engine = "error"
		if engine != "error":
			# Int без NULL: 0 — «диалога в движке нет», при следующем сообщении заведут новый
			frappe.db.set_value(PAIR, pair.name, {"engine_chat_id": 0})
	_notify_chat_changed(chat)
	return {
		"messages": message_count,
		"telegram": in_telegram,
		# Сколько сообщений нашлось в Telegram и сколько он не дал стереть (чужие в группе, недавние и т.п.)
		"telegram_found": found,
		"telegram_left": left,
		"engine": engine,
	}

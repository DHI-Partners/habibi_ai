"""Переписки в кабинете: кто что написал и кто сейчас отвечает — бот или человек.

Экран не знает про Telegram: канал — деталь сервера. WhatsApp ляжет рядом
вторым адаптером, форма ответа не изменится.
"""

import frappe
from frappe import _
from frappe.utils import now_datetime

from habibi_ai.cabinet import realtime, scope
from habibi_ai.channels import decisions, telegram

PAIR = "AI Channel Chat"
PAUSED_BY_STAFF = "Выключено вручную"
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


@frappe.whitelist()
def list():
	_check_list_permission()
	rows = frappe.get_list(
		"Telegram Chat",
		fields=["name", "title", "last_message_content", "last_message_on"],
		# Только переписки кабинета — без служебного чата с кодами входа,
		# «Избранного» и личных диалогов без ИИ-канала (cabinet.scope)
		filters=scope.list_filters(),
		order_by="last_message_on desc",
		limit=LIST_LIMIT,
	)
	names = [r.name for r in rows]
	paused = dict(
		frappe.get_all(
			PAIR,
			filters={"telegram_chat": ["in", names]},
			fields=["telegram_chat", "ai_paused"],
			as_list=True,
		)
	)
	customers = dict(
		frappe.get_all(
			"Dynamic Link",
			filters={"parenttype": "Telegram Chat", "parent": ["in", names], "link_doctype": "Customer"},
			fields=["parent", "link_name"],
			as_list=True,
		)
	)
	return [
		{
			"chat": r.name,
			"title": r.title or r.name,
			"preview": (r.last_message_content or "")[:80],
			"last_at": str(r.last_message_on or ""),
			"paused": bool(paused.get(r.name)),
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
	frappe.db.set_value(PAIR, p.name, {"ai_paused": 0, "paused_reason": None, "paused_on": None})
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

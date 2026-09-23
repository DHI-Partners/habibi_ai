"""Настройки кабинета, которые не укладываются в «список + форма».

Каждый метод — один экран: читает и пишет документ целиком, чтобы экран не
собирал его из кусков и не мог сохранить половину.
"""

import frappe
from frappe import _
from frappe.utils import get_time

PROFILE_FIELDS = ("business_name", "business_kind", "address", "phone", "description", "tone")
SLOT_FIELDS = ("weekday", "kind", "opens", "closes")
EXCEPTION_FIELDS = ("date", "closed", "opens", "closes", "note")


def _time_str(value):
	"""Time-поля приходят из ORM timedelta'ми — приводим к «HH:MM:SS» для JSON."""
	return get_time(value).strftime("%H:%M:%S") if value else ""


def _check_create_or_write(doc, ptype="write"):
	"""Прав на несохранённый документ ещё нет — спрашиваем create на доктайпе;
	у существующего — обычное право на сам документ."""
	if doc.is_new():
		if not frappe.has_permission(doc.doctype, "create"):
			frappe.throw(_("Недостаточно прав"), frappe.PermissionError)
	else:
		doc.check_permission(ptype)


def _hours_doc():
	"""Единственная запись Working Hours для компании из настроек ИИ.

	Нет записи — несохранённый документ: save_hours его insert()'ит при
	первом сохранении, а get_hours() до этого просто отдаёт пустое расписание.
	"""
	company = frappe.db.get_single_value("Habibi AI Settings", "company")
	if not company:
		frappe.throw(_("Не выбрана компания в настройках ИИ"))
	name = frappe.db.get_value("Working Hours", {"company": company})
	if name:
		return frappe.get_doc("Working Hours", name)
	return frappe.new_doc("Working Hours", company=company)


@frappe.whitelist()
def get_hours():
	doc = _hours_doc()
	doc.check_permission("read")
	return {
		"time_zone": doc.time_zone or "",
		"schedule": [
			{f: (_time_str(r.get(f)) if f in ("opens", "closes") else (r.get(f) or "")) for f in SLOT_FIELDS}
			for r in doc.schedule
		],
		"exceptions": [
			{
				f: (
					bool(r.get(f))
					if f == "closed"
					else _time_str(r.get(f))
					if f in ("opens", "closes")
					else str(r.get(f) or "")
				)
				for f in EXCEPTION_FIELDS
			}
			for r in doc.exceptions
		],
	}


@frappe.whitelist(methods=["POST"])
def save_hours(schedule, exceptions):
	"""Обе таблицы — целиком: экран режима работы шлёт весь список сразу,
	частичное обновление тут не имеет смысла и рискует оставить хвосты."""
	doc = _hours_doc()
	_check_create_or_write(doc)
	doc.schedule, doc.exceptions = [], []
	for row in frappe.parse_json(schedule) or []:
		doc.append("schedule", {f: row.get(f) or None for f in SLOT_FIELDS})
	for row in frappe.parse_json(exceptions) or []:
		doc.append("exceptions", {f: row.get(f) or None for f in EXCEPTION_FIELDS})
	if doc.is_new():
		doc.insert()
	else:
		doc.save()
	return get_hours()


@frappe.whitelist()
def get_profile():
	doc = frappe.get_single("Business Profile")
	doc.check_permission("read")
	return {
		**{f: doc.get(f) or "" for f in PROFILE_FIELDS},
		"rules": [{"title": r.title, "hint": r.hint or "", "text": r.text or ""} for r in doc.rules],
	}


@frappe.whitelist(methods=["POST"])
def save_profile(values):
	"""Подсказки правил — из пресета, владелец их не правит: сохраняем свои."""
	values = frappe.parse_json(values)
	doc = frappe.get_single("Business Profile")
	doc.check_permission("write")
	for f in PROFILE_FIELDS:
		if f in values:
			doc.set(f, values[f])
	if "rules" in values:
		hints = {r.title: r.hint for r in doc.rules}
		doc.rules = []
		for r in values["rules"]:
			title = (r.get("title") or "").strip()
			if title:
				doc.append("rules", {"title": title, "hint": hints.get(title), "text": r.get("text") or ""})
	doc.save()
	return get_profile()


def _bot_name():
	"""Бот по умолчанию; если ни один не отмечен — любой (для сайтов, где
	бот один и заведён руками до кабинета)."""
	return frappe.db.get_value("Telegram Bot", {"is_default": 1}) or frappe.db.get_value("Telegram Bot", {})


@frappe.whitelist()
def telegram_status():
	if not frappe.has_permission("Telegram Bot", "read"):
		frappe.throw(_("Нет доступа к Telegram"), frappe.PermissionError)
	name = _bot_name()
	if not name:
		return {"connected": False, "username": None, "last_message_at": None}
	bot = frappe.db.get_value("Telegram Bot", name, ["username", "webhook_enabled"], as_dict=True)
	last = frappe.db.get_value(
		"Telegram Message", {"telegram_bot": name}, "creation", order_by="creation desc"
	)
	return {
		"connected": bool(bot.webhook_enabled),
		"username": bot.username,
		"last_message_at": str(last) if last else None,
	}


@frappe.whitelist(methods=["POST"])
def connect_telegram(token):
	"""Создаёт или обновляет бота по умолчанию и ставит вебхук.

	Токен теряем сразу за пределами этой функции: ошибки Telegram-клиента
	(и его же сетевые сбои) несут в тексте адрес вида /bot<TOKEN>/..., поэтому
	наружу уходит только safe-текст, полный traceback — в лог. Тот же приём,
	что в cabinet.orders/chats для send().
	"""
	token = (token or "").strip()
	if not token:
		frappe.throw(_("Токен не может быть пустым"))

	name = _bot_name()
	bot = frappe.get_doc("Telegram Bot", name) if name else frappe.new_doc("Telegram Bot", title="Бот компании")
	_check_create_or_write(bot)
	bot.api_token = token
	bot.webhook_enabled = 1
	try:
		if bot.is_new():
			bot.insert()
		else:
			bot.save()
		bot.set_webhook()
	except Exception as e:
		frappe.log_error(title="Не удалось подключить Telegram-бота", message=frappe.get_traceback())
		safe = getattr(e, "description", None) or _("Telegram недоступен: проверьте токен и повторите позже")
		frappe.throw(safe)
	return telegram_status()

"""Настройки кабинета, которые не укладываются в «список + форма».

Каждый метод — один экран: читает и пишет документ целиком, чтобы экран не
собирал его из кусков и не мог сохранить половину.
"""

import re

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


# -- Telegram-аккаунт ---------------------------------------------------------
#
# Клиенты пишут в настоящий Telegram-аккаунт бизнеса (Telegram Account, MTProto
# из habibi_telegram), и ИИ отвечает там же. Telegram Bot — бот уведомлений
# сотрудникам, в кабинете его нет.
#
# Вход — как на форме аккаунта: телефон → код → (пароль двухэтапной проверки).
# Сами шаги делают методы документа; здесь — права, ключи платформы, включение
# ИИ после входа и перевод ошибок Telethon на человеческий русский.

ACCOUNT = "Telegram Account"

# status доктайпа → состояние экрана кабинета
STATES = {"Code Sent": "code_sent", "Password Required": "password_needed", "Connected": "connected"}

AI_NOT_READY = "ИИ-бот не выбран — обратитесь к администратору"

# Имя класса исключения Telethon → что показать владельцу. Сверяем по имени,
# а не isinstance: user_client оборачивает их во frappe.throw, и до нас
# доходит ValidationError, у которого исходное исключение лежит в __context__.
TELEGRAM_ERRORS = {
	"PhoneNumberInvalidError": "Telegram не знает такой номер — проверьте его",
	"PhoneNumberBannedError": "Этот номер заблокирован в Telegram",
	"PhoneNumberUnoccupiedError": "На этот номер не зарегистрирован Telegram",
	"PhoneNumberFloodError": "Слишком много попыток входа с этого номера — попробуйте позже",
	"PhoneCodeInvalidError": "Неверный код",
	"PhoneCodeEmptyError": "Введите код из Telegram",
	"PhoneCodeExpiredError": "Код истёк — запросите новый",
	"PasswordHashInvalidError": "Неверный пароль двухэтапной проверки",
	"AuthKeyUnregisteredError": "Telegram завершил сессию — войдите заново",
	"SessionRevokedError": "Telegram завершил сессию — войдите заново",
}


def _account_name():
	"""Аккаунт кабинета.

	На сайте бизнеса аккаунт один. Если их несколько (заведены руками из
	десктопа), кабинет показывает тот, в котором включён ИИ, — именно в нём
	отвечает бот; иначе самый ранний: он и был «аккаунтом компании» до кабинета.
	"""
	return frappe.db.get_value(ACCOUNT, {"ai_enabled": 1}, order_by="creation asc") or frappe.db.get_value(
		ACCOUNT, {}, order_by="creation asc"
	)


def _names_in_chain(error):
	"""Имена классов исключения и всех его причин (__cause__ / __context__)."""
	seen = []
	while error is not None and error not in seen:
		seen.append(error)
		error = error.__cause__ or error.__context__
	return [(e, {c.__name__ for c in type(e).__mro__}) for e in seen]


def _safe_telegram_error(error):
	"""Русский текст для ошибки входа. Сам текст исключения наружу не идёт:
	в нём бывают номер, phone_code_hash и прочее, что показывать не нужно."""
	for e, names in _names_in_chain(error):
		if names & {"FloodWaitError", "FloodPremiumWaitError"}:
			seconds = getattr(e, "seconds", None)
			if seconds:
				return _("Слишком много попыток — подождите {0} мин.").format(max(1, round(seconds / 60)))
			return _("Слишком много попыток — попробуйте позже")
		for name, text in TELEGRAM_ERRORS.items():
			if name in names:
				return _(text)
	return _("Telegram не принял запрос — попробуйте ещё раз позже")


def _call_telegram(title, fn):
	"""Вызов метода документа с чистым выходом наружу.

	user_client сам делает frappe.throw(describe_error(e)): это сообщение уже
	лежит в message_log и ушло бы клиенту в _server_messages первым, поэтому
	всё, что метод успел туда положить, выбрасываем. Полный traceback — в лог,
	как в cabinet.orders/chats.
	"""
	log = frappe.local.message_log
	mark = len(log)
	try:
		return fn()
	except frappe.PermissionError:
		raise
	except Exception as e:
		del log[mark:]
		frappe.log_error(title=title, message=frappe.get_traceback())
		frappe.throw(_safe_telegram_error(e))


def _get_account(ptype):
	name = _account_name()
	if not name:
		frappe.throw(_("Telegram-аккаунт ещё не подключён — начните с номера телефона"))
	doc = frappe.get_doc(ACCOUNT, name)
	doc.check_permission(ptype)
	return doc


@frappe.whitelist()
def telegram_status():
	"""Состояние входа для экрана. api_hash, session_string, phone_code_hash
	и сырой last_error наружу не отдаются никогда."""
	if not frappe.has_permission(ACCOUNT, "read"):
		frappe.throw(_("Нет доступа к Telegram"), frappe.PermissionError)
	status = {
		"state": "none",
		"phone": None,
		"full_name": None,
		"username": None,
		"last_message_at": None,
		"error": None,
		"ai_ready": False,
		"ai_note": None,
	}
	name = _account_name()
	if not name:
		return status
	doc = frappe.get_doc(ACCOUNT, name)
	doc.check_permission("read")
	state = STATES.get(doc.status, "none")
	if state == "none" and doc.last_error and doc.account_id:
		# Был подключён, а теперь нет и с ошибкой: сессию отозвали из
		# приложения Telegram (синхронизация пишет это в last_error)
		state = "error"
	ai_ready = bool(doc.get("ai_enabled") and doc.get("ai_bot"))
	status.update(
		state=state,
		phone=doc.phone or None,
		full_name=doc.full_name or None,
		username=doc.username or None,
		ai_ready=ai_ready,
		ai_note=None if ai_ready else _(AI_NOT_READY),
	)
	if state == "error":
		status["error"] = _("Telegram завершил сессию — войдите заново")
	elif state == "connected" and not (doc.enabled and doc.sync_enabled):
		status["error"] = _("Приём сообщений выключен — обратитесь к администратору")
	elif state == "connected" and doc.last_error:
		status["error"] = _("Не удалось забрать новые сообщения — повторим автоматически")
	last = frappe.db.get_value(
		"Telegram Message", {"telegram_account": name}, "creation", order_by="creation desc"
	)
	status["last_message_at"] = str(last) if last else None
	return status


@frappe.whitelist(methods=["POST"])
def request_code(phone):
	"""Первый шаг входа: завести аккаунт (или сменить номер) и запросить код.

	api_id/api_hash — одни на платформу (приложение с my.telegram.org), лежат в
	common_site_config: владелец бизнеса их не знает и знать не должен.
	"""
	phone = (phone or "").strip()
	if not phone:
		frappe.throw(_("Введите номер телефона"))
	api_id = frappe.conf.get("telegram_api_id")
	api_hash = frappe.conf.get("telegram_api_hash")

	name = _account_name()
	doc = (
		frappe.get_doc(ACCOUNT, name)
		if name
		else frappe.new_doc(ACCOUNT, title="Telegram " + re.sub(r"[^\d+]", "", phone))
	)
	_check_create_or_write(doc)
	if doc.status == "Connected":
		frappe.throw(_("Аккаунт уже подключён — сначала отключите его"))
	if not (api_id and api_hash):
		frappe.throw(_("Подключение Telegram не настроено на сервере — обратитесь к администратору"))

	doc.phone = phone
	doc.api_id = str(api_id)
	doc.api_hash = api_hash
	# Входящие забирает cron/слушатель habibi_telegram только у включённых
	# аккаунтов с sync_enabled — см. user_client.sync_all_accounts
	doc.enabled = 1
	doc.sync_enabled = 1
	if doc.is_new():
		doc.insert()
	else:
		doc.save()
	_call_telegram(f"Не удалось запросить код Telegram ({doc.name})", doc.request_code)
	return telegram_status()


@frappe.whitelist(methods=["POST"])
def sign_in(code=None, password=None):
	"""Второй шаг: код, а если Telegram попросил — пароль двухэтапной проверки.

	Код и пароль никуда не пишутся: уходят прямо в метод документа.
	"""
	code = (code or "").strip()
	if not code and not password:
		frappe.throw(_("Введите код из Telegram"))
	doc = _get_account("write")
	result = _call_telegram(
		f"Не удалось войти в Telegram ({doc.name})",
		lambda: doc.sign_in(code=code or None, password=password or None),
	)
	if not (result or {}).get("password_required"):
		_enable_ai(doc.name)
	return telegram_status()


def _enable_ai(name):
	"""После входа — ИИ отвечает в личных переписках аккаунта.

	Заданного бота не трогаем. Не задан — ставим, только если у тенанта он
	ровно один: выбирать за владельца из нескольких нельзя. Движок недоступен
	или бот не прошёл validate_channel — вход всё равно состоялся: сессию уже
	выдал Telegram, и откатывать её из-за ИИ нельзя. ИИ тогда остаётся
	выключенным, экран покажет ai_ready: false.
	"""
	log = frappe.local.message_log
	mark = len(log)
	frappe.db.savepoint("cabinet_telegram_ai")
	try:
		doc = frappe.get_doc(ACCOUNT, name)
		doc.enabled = 1
		doc.sync_enabled = 1
		doc.ai_reply_in_groups = 0
		doc.save()
		if not doc.get("ai_bot"):
			from habibi_ai import api

			bots = api.get_client().list_bots()
			if len(bots) != 1:
				return
			doc.ai_bot = str(bots[0]["id"])
		doc.ai_enabled = 1
		doc.save()
	except Exception:
		frappe.db.rollback(save_point="cabinet_telegram_ai")
		del log[mark:]
		frappe.log_error(
			title=f"Не удалось включить ИИ на Telegram-аккаунте {name}", message=frappe.get_traceback()
		)


@frappe.whitelist(methods=["POST"])
def disconnect():
	doc = _get_account("write")
	_call_telegram(f"Не удалось отключить Telegram ({doc.name})", doc.log_out)
	# Старая ошибка синхронизации после ручного выхода — уже не новость:
	# иначе экран показал бы «сессия завершена» вместо обычного входа
	doc = frappe.get_doc(ACCOUNT, doc.name)
	if doc.last_error:
		doc.last_error = ""
		doc.save()
	return telegram_status()

"""Заказы в кабинете: решение владельца и уведомление клиента.

Порядок жёсткий: сначала переход заказа, потом сообщение. Не прошёл переход —
клиенту нечего сообщать. Не ушло сообщение — заказ всё равно принят: кухня
уже работает, а клиенту можно написать повторно.
"""

import frappe
from frappe import _
from frappe.model.workflow import apply_workflow, get_transitions, get_workflow_name

from habibi_ai import notify_rules
from habibi_ai.channels import telegram

TEMPLATES = {"accept": "order_accepted", "reject": "order_rejected"}


def _workflow(doc):
	return get_workflow_name("Sales Order")


def _names():
	s = frappe.get_cached_doc("Habibi AI Settings")
	return s.accept_action or "Confirm", s.reject_action or "Cancel Order"


def _quote(name):
	return frappe.db.get_value(
		"AI Order Quote", {"sales_order": name}, ["channel_doctype", "channel_name", "customer_name"], as_dict=True
	)


def _pair(telegram_chat):
	"""Канал бота, которым писать в этот чат; None — чат не подключён к боту."""
	pair = frappe.db.get_value(
		"AI Channel Chat", {"telegram_chat": telegram_chat}, ["channel_doctype", "channel_name"],
		as_dict=True, order_by="modified desc",
	)
	return (pair.channel_doctype, pair.channel_name) if pair else None


def _chat(name):
	"""Канальный чат заказа; None — заказ не из канала или чат не подключён."""
	quote = _quote(name)
	if not quote or quote.channel_doctype != "Telegram Chat" or not quote.channel_name:
		return None
	return quote.channel_name if _pair(quote.channel_name) else None


def _state(doc):
	if _workflow(doc):
		return doc.get("workflow_state") or ""
	return {0: _("Черновик"), 1: _("Принят"), 2: _("Отменён")}[doc.docstatus]


def _may_discard(doc):
	"""Синтетическое «отклонить» удаляет черновик — тут ровно то право, что
	apply() потом проверит перед frappe.delete_doc."""
	return frappe.has_permission("Sales Order", "delete", doc=doc)


def _available(doc):
	if _workflow(doc):
		accept, reject = _names()
		transitions = [
			{"action": t["action"], "kind": notify_rules.kind_of(t["action"], accept, reject)}
			for t in get_transitions(doc)
		]
		# Прод-воркфлоу («Habibi Burger Order») не выпускает черновик иначе,
		# чем через «Confirm» — выхода в «отклонить» из New там нет вовсе.
		# Не даём владельцу зависнуть с черновиком без единого способа его
		# закрыть: удаление черновика доступно всегда, как и без воркфлоу —
		# но только тому, у кого есть право его удалить.
		if (
			doc.docstatus == 0
			and not any(t["kind"] == "reject" for t in transitions)
			and _may_discard(doc)
		):
			transitions.append({"action": notify_rules.NO_WORKFLOW_REJECT, "kind": "reject"})
		return transitions
	if doc.docstatus == 0:
		available = [{"action": notify_rules.NO_WORKFLOW_ACCEPT, "kind": "accept"}]
		if _may_discard(doc):
			available.append({"action": notify_rules.NO_WORKFLOW_REJECT, "kind": "reject"})
		return available
	return []


@frappe.whitelist()
def actions(name):
	doc = frappe.get_doc("Sales Order", name)
	doc.check_permission("read")
	return {"state": _state(doc), "actions": _available(doc), "can_notify": _chat(name) is not None}


def _draft_text(kind, doc, reason=None):
	template = frappe.db.get_value("Telegram Message Template", TEMPLATES[kind], "default_template")
	if not template:
		return ""
	return notify_rules.render(
		template,
		{
			"order": doc.name,
			"customer": doc.customer_name,
			"total": frappe.utils.fmt_money(doc.grand_total, currency=doc.currency),
			"time": doc.get("custom_requested_time") or "",
			"reason": reason,
		},
	)


@frappe.whitelist(methods=["POST"])
def apply(name, action, reason=None):
	"""Переход заказа. Чат читаем до перехода, хоть это и не обязательно:
	ссылка AI Order Quote → Sales Order переживает discard (в hooks.py —
	ignore_links_on_delete), так что найти чат можно и по уже удалённому
	заказу — см. notify()."""
	doc = frappe.get_doc("Sales Order", name)
	doc.check_permission("read")
	allowed = {a["action"]: a["kind"] for a in _available(doc)}
	if action not in allowed:
		frappe.throw(_("Действие «{0}» сейчас недоступно").format(action))
	kind = allowed[action]
	chat = _chat(name)
	snapshot = frappe._dict(
		name=doc.name, customer_name=doc.customer_name, grand_total=doc.grand_total,
		currency=doc.currency, custom_requested_time=doc.get("custom_requested_time"),
	)

	# «Отклонить» без реальной ветки воркфлоу — тот же синтетический
	# NO_WORKFLOW_REJECT, что и на сайте без воркфлоу вовсе: удаляем черновик,
	# а не зовём apply_workflow с действием, которого воркфлоу не знает.
	if _workflow(doc) and action != notify_rules.NO_WORKFLOW_REJECT:
		doc = apply_workflow(doc, action)
		state = _state(doc)
	elif action == notify_rules.NO_WORKFLOW_ACCEPT:
		doc.submit()
		state = _state(doc)
	else:
		doc.check_permission("delete")
		frappe.delete_doc("Sales Order", name)
		state = _("Отклонён")

	notify = None
	if chat and kind in TEMPLATES:
		notify = {"kind": kind, "text": _draft_text(kind, snapshot, reason)}
	return {"state": state, "notify": notify}


def _check_notify_permission(name):
	"""Кто может писать клиенту от имени этого заказа.

	Заказ ещё жив — обычное «право писать» в него. Черновик уже удалён
	(discard) — самого документа для check_permission нет, а право нужно то
	же, что позволило бы его удалить; вдобавок должен существовать расчёт
	(AI Order Quote) с этим заказом — иначе имя ничем не подтверждено вовсе.
	"""
	if frappe.db.exists("Sales Order", name):
		frappe.get_doc("Sales Order", name).check_permission("write")
		return
	if frappe.db.exists("AI Order Quote", {"sales_order": name}) and frappe.has_permission(
		"Sales Order", "delete"
	):
		return
	raise frappe.PermissionError


@frappe.whitelist(methods=["POST"])
def notify(name, text):
	"""Отправить клиенту; результат — в таймлайн заказа, если заказ ещё есть.

	Чат ищем по имени заказа, не по значению от клиента: AI Order Quote
	хранит ссылку на Sales Order даже после его удаления (в hooks.py —
	ignore_links_on_delete), так что _chat() находит чат и после discard.
	Принять чат аргументом означало бы, что любой вызывающий может указать
	чужой чат и отправить туда что угодно от имени бота.
	"""
	_check_notify_permission(name)
	text = (text or "").strip()
	if not text:
		frappe.throw(_("Текст уведомления не может быть пустым"))

	chat = _chat(name)
	channel = _pair(chat) if chat else None
	if not channel:
		frappe.throw(_("У заказа нет чата с клиентом"))
	try:
		telegram.send(channel, chat, text)
		result = {"sent": True, "error": None}
		note = _("Клиент уведомлён: {0}").format(text)
	except Exception as e:
		# Полный текст — только в лог: у сетевых ошибок Telegram-клиента в
		# сообщении зашит URL вида /bot<TOKEN>/..., и это утекло бы в ответ
		# API и в таймлайн заказа. description — safe-текст от самого
		# Telegram (TelegramAPIError, когда он ответил ok=false); во всех
		# прочих случаях (сеть недоступна, не JSON и т.п.) — общая фраза.
		frappe.log_error(title=f"Не удалось уведомить клиента о заказе {name}", message=frappe.get_traceback())
		safe = getattr(e, "description", None) or _("Telegram недоступен, попробуйте позже")
		result = {"sent": False, "error": safe}
		note = _("Клиент не уведомлён: {0}").format(safe)
	if frappe.db.exists("Sales Order", name):
		frappe.get_doc("Sales Order", name).add_comment("Comment", note)
	return result

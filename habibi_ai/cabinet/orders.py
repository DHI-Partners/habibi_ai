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
		# закрыть: удаление черновика доступно всегда, как и без воркфлоу.
		if doc.docstatus == 0 and not any(t["kind"] == "reject" for t in transitions):
			transitions.append({"action": notify_rules.NO_WORKFLOW_REJECT, "kind": "reject"})
		return transitions
	if doc.docstatus == 0:
		return [
			{"action": notify_rules.NO_WORKFLOW_ACCEPT, "kind": "accept"},
			{"action": notify_rules.NO_WORKFLOW_REJECT, "kind": "reject"},
		]
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
	"""Переход заказа. Чат вычисляется до перехода: «отклонить» без воркфлоу
	удаляет черновик, и ссылка расчёта на заказ после этого обнулится."""
	doc = frappe.get_doc("Sales Order", name)
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
		notify = {"kind": kind, "text": _draft_text(kind, snapshot, reason), "chat": chat}
	return {"state": state, "notify": notify}


@frappe.whitelist(methods=["POST"])
def notify(name, text, chat=None):
	"""Отправить клиенту; результат — в таймлайн заказа, если заказ ещё есть.

	chat приходит из ответа apply: после отклонения черновика заказа уже нет,
	и найти чат по нему нельзя.
	"""
	chat = chat or _chat(name)
	channel = _pair(chat) if chat else None
	if not channel:
		frappe.throw(_("У заказа нет чата с клиентом"))
	try:
		telegram.send(channel, chat, text)
		result = {"sent": True, "error": None}
		note = _("Клиент уведомлён: {0}").format(text)
	except Exception as e:
		result = {"sent": False, "error": str(e)}
		note = _("Клиент не уведомлён: {0}").format(e)
	if frappe.db.exists("Sales Order", name):
		frappe.get_doc("Sales Order", name).add_comment("Comment", note)
	return result

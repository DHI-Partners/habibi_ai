"""Заказы в кабинете: решение владельца и уведомление клиента.

Порядок жёсткий: сначала переход заказа, потом сообщение. Не прошёл переход —
клиенту нечего сообщать. Не ушло сообщение — заказ всё равно принят: кухня
уже работает, а клиенту можно написать повторно.
"""

import re

import frappe
from frappe import _
from frappe.model.workflow import WorkflowStateError, apply_workflow, get_transitions, get_workflow_name
from frappe.utils.caching import request_cache

from habibi_ai import notify_rules
from habibi_ai.cabinet import scope
from habibi_ai.cabinet.money import money
from habibi_ai.channels import telegram
from habibi_ai.order_rules import DELIVERY_ITEM
from habibi_ai.tools.orders import SOURCE_BY_CHANNEL, payable

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


@request_cache
def _state_field(workflow):
	"""Поле, где воркфлоу хранит состояние. Не всегда workflow_state: прод-
	воркфлоу «Habibi Burger Order» пишет его в custom_order_status."""
	return frappe.db.get_value("Workflow", workflow, "workflow_state_field") or "workflow_state"


@request_cache
def _targets(workflow):
	"""Состояние → действия, которые в него ведут: по ним видно, что заказ
	именно принят (или отклонён), а не ушёл дальше — на кухню, в доставку."""
	result = {}
	for t in frappe.get_all(
		"Workflow Transition", filters={"parent": workflow, "parenttype": "Workflow"}, fields=["action", "next_state"]
	):
		result.setdefault(t.next_state, set()).add(t.action)
	return result


def _state(doc):
	workflow = _workflow(doc)
	if workflow:
		return doc.get(_state_field(workflow)) or ""
	return {0: _("Черновик"), 1: _("Принят"), 2: _("Отменён")}[doc.docstatus]


def _kind(doc):
	"""Смысл состояния для бейджа: new | accepted | rejected | other.

	Имена состояний у каждого воркфлоу свои, поэтому смысл берём из
	docstatus и из того, каким действием (accept/reject из настроек) в это
	состояние приходят; остальное после подтверждения — «other».
	"""
	if doc.docstatus == 0:
		return "new"
	if doc.docstatus == 2:
		return "rejected"
	workflow = _workflow(doc)
	if not workflow:
		return "accepted"
	accept, reject = _names()
	via = _targets(workflow).get(_state(doc), set())
	if reject in via:
		return "rejected"
	if accept in via:
		return "accepted"
	return "other"


def _may_discard(doc):
	"""Синтетическое «отклонить» удаляет черновик — тут ровно то право, что
	apply() потом проверит перед frappe.delete_doc."""
	return frappe.has_permission("Sales Order", "delete", doc=doc)


def _available(doc):
	if _workflow(doc):
		accept, reject = _names()
		try:
			allowed = get_transitions(doc)
		except WorkflowStateError:
			# Заказ без состояния (заведён до воркфлоу) — переходов нет, но
			# экран заказа должен открыться, а не упасть
			frappe.clear_last_message()
			allowed = []
		# Переходы разрешены ролям воркфлоу (на проде — Burger Order Desk и
		# т.п.). Без такой роли список пуст, и экран подсказывает, к кому идти.
		transitions = [
			{"action": t["action"], "kind": notify_rules.kind_of(t["action"], accept, reject)} for t in allowed
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


def _optional(doc, meta, fieldname):
	"""Поле, заведённое руками на конкретном сайте: нет в мете — None."""
	return doc.get(fieldname) if meta.has_field(fieldname) else None


def _symbol(currency):
	return frappe.db.get_value("Currency", currency, "symbol") or currency


def _number(name):
	"""«SAL-ORD-2026-00018» → «18»: владельцу длинное имя ни к чему."""
	match = re.search(r"(\d+)$", name)
	return str(int(match.group(1))) if match else name


@frappe.whitelist()
def details(name):
	"""Всё, что нужно экрану заказа: состав с ценами, доставка отдельно,
	итог в валюте заказа, клиент и ссылка на переписку.

	Отдельный метод, а не поля раздела: позиции — дочерняя таблица, а валюта
	и чат живут в других документах. Отдаём только перечисленное здесь.
	"""
	doc = frappe.get_doc("Sales Order", name)
	doc.check_permission("read")
	doc.apply_fieldlevel_read_permissions()
	meta = frappe.get_meta("Sales Order")
	quote = _quote(name)

	items, delivery = [], None
	for row in doc.items:
		if row.item_code == DELIVERY_ITEM:
			delivery = {"label": row.item_name, "amount": float(row.amount or 0)}
			continue
		items.append(
			{"item_name": row.item_name, "qty": row.qty, "rate": float(row.rate or 0), "amount": float(row.amount or 0)}
		)

	# Ссылка «Переписка» — только на чат, который кабинет откроет: групповой
	# чат или чат без пары ИИ-канала раздел переписок покажет как «не найден»
	chat = None
	if (
		quote
		and quote.channel_doctype == "Telegram Chat"
		and frappe.has_permission("Telegram Chat", "read")
		and scope.in_scope(quote.channel_name)
	):
		chat = quote.channel_name

	return {
		"name": doc.name,
		"number": _number(doc.name),
		"created": str(doc.creation),
		"source": _optional(doc, meta, "custom_order_source")
		or (SOURCE_BY_CHANNEL.get(quote.channel_doctype) if quote else None),
		"state": _state(doc),
		"state_kind": _kind(doc),
		"customer_name": doc.customer_name,
		"phone": _optional(doc, meta, "custom_whatsapp_number") or doc.get("contact_mobile") or None,
		"fulfilment": _optional(doc, meta, "custom_fulfilment_type"),
		"zone": _optional(doc, meta, "custom_delivery_zone"),
		"address": doc.get("shipping_address") or doc.get("address_display") or None,
		"notes": _optional(doc, meta, "custom_kitchen_notes"),
		"items": items,
		"delivery": delivery,
		# Как у бота (tools.orders.payable): округлённый итог, если на сайте
		# включено округление, — клиенту названа именно эта сумма
		"total": payable(doc),
		"taxes": float(doc.total_taxes_and_charges or 0),
		"currency": doc.currency,
		"currency_symbol": _symbol(doc.currency),
		"chat": chat,
	}


def _draft_text(kind, doc, reason=None):
	template = frappe.db.get_value("Telegram Message Template", TEMPLATES[kind], "default_template")
	if not template:
		return ""
	return notify_rules.render(
		template,
		{
			# Номер и сумма — как на экране заказа («№18», «4 670 ₸», итог к оплате),
			# а не имя документа и fmt_money сайта: клиент и владелец видят одно
			"order": f"№{_number(doc.name)}",
			"customer": doc.customer_name,
			"total": money(payable(doc), _symbol(doc.currency)),
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
		rounded_total=doc.get("rounded_total"), currency=doc.currency, custom_requested_time=doc.get("custom_requested_time"),
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
		# Причина — только для отказа: фронт шлёт её с любым действием, а
		# клиенту в «принят» чужая причина отказа ни к чему
		why = reason if kind == "reject" else None
		notify = {"kind": kind, "text": _draft_text(kind, snapshot, why)}
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
	# habibi_telegram сам делает frappe.throw(describe_error(e)) на сбое
	# отправки — это сообщение уже легло в message_log и утекло бы в ответ
	# (даже несмотря на то, что ниже мы саму ошибку гасим и возвращаем обычный
	# результат); приём — тот же, что в cabinet.settings._call_telegram
	log = frappe.local.message_log
	mark = len(log)
	try:
		telegram.send(channel, chat, text)
		result = {"sent": True, "error": None}
		note = _("Клиент уведомлён: {0}").format(text)
	except Exception as e:
		del log[mark:]
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

"""Кухня и курьер: очереди заказов для их экранов кабинета.

Методы режут поля сами (см. fulfilment_rules): кухне не нужны ни цены, ни
клиент, курьеру — ни цены, ни чужие телефоны. Переходы «Готово» и
«Доставлено» идут через orders.apply (воркфлоу проверяет роль воркфлоу), здесь
— чтение очередей и «Взять».

Имена состояний и действия — прод-воркфлоу «Habibi Burger Order» (см.
burger-workshop/BUILD-LOG.md); состояние читаем из поля, которое воркфлоу
сайта выбрал для себя, как и orders._state_field.
"""

from collections import defaultdict

import frappe
from frappe import _
from frappe.model.workflow import apply_workflow, get_workflow_name
from frappe.utils import now_datetime

from habibi_ai import fulfilment_rules as rules
from habibi_ai.cabinet import orders

KITCHEN_ROLE = "Habibi Kitchen"
COURIER_ROLE = "Habibi Courier"
IN_KITCHEN = "In Kitchen"
READY = "Ready"
OUT = "Out for Delivery"
TAKE_ACTION = "Dispatch"
DELIVERY = "Delivery"

ORDER_FIELDS = ("name", "modified", "customer_name", "shipping_address", "address_display", "contact_mobile")
# Заведены руками на конкретном сайте: нет в мете — не просим, как orders._optional
CUSTOM_FIELDS = (
	"custom_kitchen_notes",
	"custom_courier",
	"custom_fulfilment_type",
	"custom_delivery_zone",
	"custom_whatsapp_number",
)


def _require(role):
	roles = set(frappe.get_roles())
	if role not in roles and "System Manager" not in roles:
		frappe.throw(_("Нет доступа"), frappe.PermissionError)


def _state_field():
	workflow = get_workflow_name("Sales Order")
	return orders._state_field(workflow) if workflow else None


def _has(fieldname):
	return frappe.get_meta("Sales Order").has_field(fieldname)


def _my_employee():
	return frappe.db.get_value("Employee", {"user_id": frappe.session.user, "status": "Active"}, "name")


def _need_employee():
	employee = _my_employee()
	if not employee:
		frappe.throw(_("Вам не назначен профиль курьера"))
	return employee


def _orders(state, *, courier=None, unassigned=False, delivery_only=False):
	"""Проведённые заказы в состоянии, старые первыми.

	Нет воркфлоу или поля, по которому надо отбирать, — пустой список, а не
	ошибка: сайт без доставки курьерами просто не имеет такой очереди."""
	field = _state_field()
	if not field:
		return []
	filters = {field: state, "docstatus": 1}
	if courier is not None or unassigned:
		if not _has("custom_courier"):
			return []
		filters["custom_courier"] = courier if courier is not None else ["is", "not set"]
	if delivery_only:
		if not _has("custom_fulfilment_type"):
			return []
		filters["custom_fulfilment_type"] = DELIVERY
	fields = [*ORDER_FIELDS, *(f for f in CUSTOM_FIELDS if _has(f))]
	return frappe.get_all("Sales Order", filters=filters, fields=fields, order_by="modified asc")


def _items(names):
	if not names:
		return {}
	rows = frappe.get_all(
		"Sales Order Item",
		filters={"parent": ["in", names], "parenttype": "Sales Order"},
		fields=["parent", "item_code", "item_name", "qty"],
		order_by="idx asc",
	)
	grouped = defaultdict(list)
	for row in rows:
		grouped[row.parent].append(row)
	return grouped


@frappe.whitelist()
def kitchen_queue():
	_require(KITCHEN_ROLE)
	found = _orders(IN_KITCHEN)
	items = _items([o.name for o in found])
	now = now_datetime()
	return [rules.kitchen_card(o, items.get(o.name, []), now) for o in found]


@frappe.whitelist()
def courier_mine():
	_require(COURIER_ROLE)
	found = _orders(OUT, courier=_need_employee())
	items = _items([o.name for o in found])
	now = now_datetime()
	return [rules.courier_card(o, items.get(o.name, []), now, own=True) for o in found]


@frappe.whitelist()
def courier_free():
	_require(COURIER_ROLE)
	found = _orders(READY, unassigned=True, delivery_only=True)
	items = _items([o.name for o in found])
	now = now_datetime()
	return [rules.courier_card(o, items.get(o.name, []), now, own=False) for o in found]


@frappe.whitelist(methods=["POST"])
def courier_take(name):
	"""Взять свободный заказ: курьер и переход Dispatch одним сохранением.

	Строку блокируем и перечитываем: два курьера, нажавшие одновременно, не
	возьмут один заказ. «Не вышло» — это {"taken": True}, а не ошибка: экран
	скажет «уже взят» и перечитает список."""
	_require(COURIER_ROLE)
	employee = _need_employee()
	field = _state_field()
	if not field or not _has("custom_courier") or not _has("custom_fulfilment_type"):
		frappe.throw(_("Доставка курьерами на сайте не настроена"))
	row = frappe.db.get_value(
		"Sales Order",
		name,
		["docstatus", field, "custom_courier", "custom_fulfilment_type"],
		as_dict=True,
		for_update=True,
	)
	if not row:
		frappe.throw(_("Заказ не найден"), frappe.DoesNotExistError)
	if (
		row.docstatus != 1
		or row[field] != READY
		or row.custom_courier
		or row.custom_fulfilment_type != DELIVERY
	):
		return {"taken": True}
	# Курьера пишем в БД до перехода: apply_workflow перечитывает документ
	# (doc.load_from_db) и теряет несохранённое назначение, а условие Dispatch
	# «доставка и курьер заданы» читает именно его. Строка уже заблокирована;
	# не прошёл переход — запрос откатится вместе с назначением.
	frappe.db.set_value("Sales Order", name, "custom_courier", employee)
	# Воркфлоу проверит роль перехода и это условие
	apply_workflow(frappe.get_doc("Sales Order", name), TAKE_ACTION)
	return {"taken": False}

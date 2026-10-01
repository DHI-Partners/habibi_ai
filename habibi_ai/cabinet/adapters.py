"""Поля кабинета, которых нет в самом DocType.

Статус и сумма заказа в списке — тоже здесь: откуда читать состояние, знает
воркфлоу сайта, а символ валюты — сам заказ.

Цена позиции живёт не в Item, а в Item Price по прайс-листу продаж. Правила
действующей цены — те же, что у бота (valid_prices): иначе владелец видел бы
в кабинете одну цену, а бот называл бы клиенту другую.
"""

import frappe
from frappe import _
from frappe.model import default_fields
from frappe.utils import flt

from habibi_ai.cabinet import orders
from habibi_ai.cabinet.money import money
from habibi_ai.tools.menu import valid_prices

_KNOWN_FIELDS = set(default_fields)


class SellingPrice:
	doctype = "Item"
	label = "Цена"
	fieldtype = "Currency"

	def _price_list(self):
		return frappe.db.get_single_value("Selling Settings", "selling_price_list")

	def editable(self):
		return bool(self._price_list())

	def read(self, names):
		price_list = self._price_list()
		if not price_list or not names:
			return {}
		rows = frappe.get_all(
			"Item Price",
			filters={"price_list": price_list, "selling": 1, "item_code": ["in", names]},
			fields=["item_code", "price_list_rate", "valid_from", "valid_upto"],
			order_by="valid_from desc",
		)
		result = {}
		for r in valid_prices(rows, frappe.utils.nowdate()):
			result.setdefault(r["item_code"], float(r["price_list_rate"]))
		return result

	def write(self, doc, value):
		"""Обновить действующую бессрочную цену или завести новую.

		Историю цен с датами владелец в кабинете не ведёт: ему нужна «цена
		сейчас». Цены с датами из Desk не трогаем — их правят там же.

		Форма присылает цену при каждом сохранении позиции, даже если её не
		трогали: пустое значение — «цену не задавали», а не «цена 0» (иначе
		сохранение позиции без цены заводило бы Item Price с нулём, и бот
		предлагал бы блюдо бесплатно); то же, что уже действует, — не повод
		писать (у цены с датами из Desk появилась бы бессрочная копия).
		"""
		if value is None or value == "":
			return
		value = flt(value)
		if value < 0:
			frappe.throw(_("Цена не может быть отрицательной"))
		current = self.read([doc.name]).get(doc.name)
		if current is not None and flt(current) == value:
			return
		price_list = self._price_list()
		existing = frappe.db.get_value(
			"Item Price",
			{"item_code": doc.name, "price_list": price_list, "selling": 1, "valid_upto": ["is", "not set"]},
			"name",
		)
		if existing:
			price = frappe.get_doc("Item Price", existing)
			price.price_list_rate = value
			price.save()
			return
		frappe.get_doc(
			{"doctype": "Item Price", "item_code": doc.name, "price_list": price_list, "price_list_rate": value}
		).insert()


selling_price = SellingPrice()


# Подписи состояний прод-воркфлоу «Habibi Burger Order»; незнакомое состояние
# остаётся как названо в воркфлоу — у каждого сайта оно своё.
STATE_LABELS = {
	"New": _("Новые"),
	"Confirmed": _("Приняты"),
	"In Kitchen": _("На кухне"),
	"Ready": _("Готовы"),
	"Out for Delivery": _("В пути"),
	"Delivered": _("Выданные"),
	"Cancelled": _("Отменённые"),
}


def _workflow_states(workflow):
	"""Состояния воркфлоу в его порядке; terminal — из него нет ни одного перехода."""
	states = frappe.get_all(
		"Workflow Document State",
		filters={"parent": workflow, "parenttype": "Workflow"},
		fields=["state", "doc_status"],
		order_by="idx asc",
	)
	moving = set(
		frappe.get_all("Workflow Transition", filters={"parent": workflow, "parenttype": "Workflow"}, pluck="state")
	)
	return [{"state": s.state, "doc_status": s.doc_status, "terminal": s.state not in moving} for s in states]


class OrderStatus:
	"""Статус заказа в списке — тем же словом, что на экране заказа.

	Поле состояния у воркфлоу своё (на проде — custom_order_status), а без
	воркфлоу статус — это docstatus. Колонкой списка ни то ни другое не
	выразить, поэтому адаптер: он знает, откуда читать (cabinet.orders._state).
	"""

	doctype = "Sales Order"
	label = "Статус"
	# Не тип Frappe: знак списку кабинета, что значение — {"state", "kind"} и
	# рисуется бейджем. kind — тот же orders._kind, что у экрана заказа: цвет
	# бейджа в списке и на экране решает одно правило, а не два словаря.
	fieldtype = "Status"

	def editable(self):
		return False

	def read(self, names):
		if not names:
			return {}
		fields = ["name", "docstatus"]
		workflow = orders._workflow(None)
		if workflow:
			field = orders._state_field(workflow)
			if field in _KNOWN_FIELDS or frappe.get_meta(self.doctype).has_field(field):
				fields.append(field)
		rows = frappe.get_all(self.doctype, filters={"name": ["in", names]}, fields=fields)
		return {r.name: {"state": orders._state(r), "kind": orders._kind(r)} for r in rows}

	def write(self, doc, value):
		raise frappe.PermissionError

	def facets(self):
		"""Быстрые фильтры списка: по одному на состояние воркфлоу сайта, в его
		порядке; без воркфлоу — «Новые / Приняты / Отменённые» по docstatus.

		closed — состояние, из которого заказ уже никуда не идёт (выдан, отменён):
		доска открытых заказов их не показывает, список — показывает."""
		workflow = orders._workflow(None)
		if not workflow:
			return [
				{"key": "new", "label": _("Новые"), "filters": [["docstatus", "=", 0]], "closed": False},
				{"key": "accepted", "label": _("Приняты"), "filters": [["docstatus", "=", 1]], "closed": False},
				{"key": "cancelled", "label": _("Отменённые"), "filters": [["docstatus", "=", 2]], "closed": True},
			]
		field = orders._state_field(workflow)
		return [
			{
				"key": s["state"],
				"label": STATE_LABELS.get(s["state"], s["state"]),
				"filters": [[field, "=", s["state"]]],
				"closed": bool(s["terminal"]) or str(s["doc_status"]) == "2",
			}
			for s in _workflow_states(workflow)
		]


class OrderTotal:
	"""Сумма заказа с символом его валюты: «4 670 ₸».

	У раздела нет поля валюты, а валюта компании по умолчанию бывает не той,
	в которой пришёл заказ, — поэтому строка собирается здесь, по валюте
	каждого заказа. Разряды — как у Intl ru-RU на экране заказа.
	"""

	doctype = "Sales Order"
	label = "Сумма"
	# Значение — готовая строка, но тип Currency намеренно: по нему список
	# кабинета ставит сумму справа (карточка телефона, колонка таблицы), а
	# money() фронта строку-не-число отдаёт как есть.
	fieldtype = "Currency"

	def editable(self):
		return False

	def read(self, names):
		if not names:
			return {}
		rows = frappe.get_all(
			self.doctype,
			filters={"name": ["in", names]},
			fields=["name", "currency", "grand_total", "rounded_total"],
		)
		symbols = dict(
			frappe.get_all(
				"Currency", filters={"name": ["in", list({r.currency for r in rows})]}, fields=["name", "symbol"],
				as_list=True,
			)
		)
		# rounded_total or grand_total — как tools.orders.payable: сумма, названная клиенту
		return {
			r.name: money(r.rounded_total or r.grand_total, symbols.get(r.currency) or r.currency) for r in rows
		}

	def write(self, doc, value):
		raise frappe.PermissionError


class OrdersCount:
	"""Сколько заказов у клиента в списке клиентов; отменённые не считаем."""

	doctype = "Customer"
	label = "Заказов"
	fieldtype = "Int"

	def editable(self):
		return False

	def read(self, names):
		if not names:
			return {}
		rows = frappe.get_all(
			"Sales Order",
			filters={"customer": ["in", names], "docstatus": ["<", 2]},
			fields=["customer", {"COUNT": "name", "as": "c"}],
			group_by="customer",
		)
		counts = {r.customer: r.c for r in rows}
		return {name: counts.get(name, 0) for name in names}

	def write(self, doc, value):
		raise frappe.PermissionError


order_status = OrderStatus()
order_total = OrderTotal()
orders_count = OrdersCount()

"""Поля кабинета, которых нет в самом DocType.

Цена позиции живёт не в Item, а в Item Price по прайс-листу продаж. Правила
действующей цены — те же, что у бота (valid_prices): иначе владелец видел бы
в кабинете одну цену, а бот называл бы клиенту другую.
"""

import frappe

from habibi_ai.tools.menu import valid_prices


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
		"""
		price_list = self._price_list()
		existing = frappe.db.get_value(
			"Item Price",
			{"item_code": doc.name, "price_list": price_list, "valid_upto": ["is", "not set"]},
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

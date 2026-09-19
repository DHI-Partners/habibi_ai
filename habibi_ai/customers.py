"""Клиент заказа: по привязке чата, по телефону или новый.

Клиента никогда не называет модель — только сервер по чату, из которого
пришёл ход, или по телефону, который клиент продиктовал. Иначе заказ можно
было бы оформить на чужое имя.
"""

import frappe

from habibi_ai.order_rules import normalize_phone


def linked_customer(channel_chat):
	"""Customer, к которому привязан канальный чат (строкой links), или None."""
	if not channel_chat:
		return None
	doctype, name = channel_chat
	if not frappe.db.exists(doctype, name):
		return None
	for row in frappe.get_doc(doctype, name).get("links") or []:
		if row.link_doctype == "Customer" and frappe.db.get_value("Customer", row.link_name, "disabled") == 0:
			return row.link_name
	return None


def find_by_phone(phone, customer_name):
	"""Customer с тем же номером и тем же именем, или None.

	Имя обязательно совпадает: номер телефона — не пароль. По одному
	продиктованному чужому номеру нельзя получить ни заказ на чужую карточку,
	ни её данные в следующих расчётах. Не совпало — заводится новый клиент,
	дубль сольёт оператор.

	В базе номера лежат как ввели: «+7 701 555-01-04», «+77015550104», поэтому
	сравниваются нормализованные формы, а не строки в запросе.
	"""
	wanted = str(customer_name or "").strip().casefold()
	candidates = frappe.get_all(
		"Customer",
		filters={"disabled": 0, "mobile_no": ["is", "set"]},
		fields=["name", "customer_name", "mobile_no"],
		order_by="creation asc",
		limit_page_length=0,
	)
	for candidate in candidates:
		if (
			normalize_phone(candidate.mobile_no) == phone
			and str(candidate.customer_name or "").strip().casefold() == wanted
		):
			return candidate.name
	return None


def create(customer_name, phone):
	"""Новый Customer. mobile_no на вставке ERPNext превращает в основной контакт."""
	group = frappe.db.get_single_value("Selling Settings", "customer_group") or _first_leaf("Customer Group")
	territory = frappe.db.get_single_value("Selling Settings", "territory") or _first_leaf("Territory")
	doc = frappe.get_doc(
		{
			"doctype": "Customer",
			"customer_name": customer_name,
			"customer_type": "Individual",
			"customer_group": group,
			"territory": territory,
			"mobile_no": phone,
		}
	)
	doc.insert(ignore_permissions=True)
	return doc.name


def _first_leaf(doctype):
	"""Первая не групповая запись дерева — запасной вариант, когда в Selling
	Settings умолчание не задано.

	Не корень: «All Customer Groups» — папка, и ERPNext не пускает в неё
	клиента. На erp.habibi-erp.com умолчание пустое, и без этого первый же
	новый клиент получал бы отказ вместо заказа.
	"""
	return frappe.db.get_value(doctype, {"is_group": 0}, "name", order_by="lft asc")


def link_chat(channel_chat, customer):
	"""Привязать канальный чат к клиенту — в следующий раз бот узнает его сам.

	Той же таблицей links, что правит карточка собеседника в консоли чатов
	habibi_telegram: оператор видит связь и может её снять.
	"""
	doctype, name = channel_chat
	if not frappe.db.exists(doctype, name) or not frappe.get_meta(doctype).has_field("links"):
		return
	doc = frappe.get_doc(doctype, name)
	if any(r.link_doctype == "Customer" and r.link_name == customer for r in doc.links):
		return
	doc.append("links", {"link_doctype": "Customer", "link_name": customer})
	doc.save(ignore_permissions=True)

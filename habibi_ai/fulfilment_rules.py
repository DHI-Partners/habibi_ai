"""Карточки кухни и курьера, не зависящие от frappe.

Решают, что именно уходит на экран: кухне — состав и заметка, курьеру — адрес
и состав, телефон клиента — только тому, чей заказ. Цен нет нигде: поля
режутся здесь, чтобы лишнее не попало на экран, даже если запрос завтра
вытащит больше. ERP-связка — в cabinet/fulfilment.py.
"""

import re

from habibi_ai.order_rules import DELIVERY_ITEM


def age_minutes(modified, now):
	# Часы БД и приложения могут расходиться: не показываем «−3 мин»
	return max(0, int((now - modified).total_seconds() // 60))


def _text(value):
	value = (value or "").strip()
	return value or None


# Справочник адресов ERPNext дописывает к адресу контакты («Phone: …», «Email: …»).
# Телефон клиента курьер видит только после «Взять», поэтому из адреса они уходят.
_CONTACT = re.compile(r"^\s*(phone|mobile|tel|fax|e-?mail|телефон|тел|факс|эл\.?\s*почта|почта)\b", re.IGNORECASE)


def plain_address(html):
	"""Адрес Frappe хранит HTML («улица<br>город<br>»): курьеру нужна строка."""
	text = re.sub(r"<br\s*/?>|\n", ",", html or "", flags=re.IGNORECASE)
	text = re.sub(r"<[^>]+>", "", text)
	parts = [part.strip() for part in text.split(",")]
	return ", ".join(part for part in parts if part and not _CONTACT.match(part)) or None


PAYMENT_FIELD = "custom_payment_status"


def payment(order):
	"""Оплата заказа: «Paid» / «Unpaid»; None — на сайте нет такого поля.

	Поле есть, а значения нет — заказ создан до него: считаем «не оплачен»."""
	if PAYMENT_FIELD not in order:
		return None
	return _text(order.get(PAYMENT_FIELD)) or "Unpaid"


def lines(rows):
	"""Состав без строки доставки: она не еда, кухня и курьер её не готовят и не несут."""
	return [{"item_name": r["item_name"], "qty": r["qty"]} for r in rows if r["item_code"] != DELIVERY_ITEM]


def kitchen_card(order, rows, now):
	return {
		"name": order["name"],
		"age": age_minutes(order["modified"], now),
		"notes": _text(order.get("custom_kitchen_notes")),
		# Доставка или самовывоз — кухне знать полезно (как собирать), и это не данные клиента
		"fulfilment": _text(order.get("custom_fulfilment_type")),
		"payment": payment(order),
		"items": lines(rows),
	}


def _address(order):
	return plain_address(order.get("address_display")) or _text(order.get("shipping_address"))


def courier_card(order, rows, now, own):
	"""own — заказ уже у этого курьера: только тогда телефон и состав."""
	items = lines(rows)
	card = {
		"name": order["name"],
		"age": age_minutes(order["modified"], now),
		"customer_name": _text(order.get("customer_name")),
		"address": _address(order),
		"zone": _text(order.get("custom_delivery_zone")),
		"items_count": len(items),
		# Курьеру важно знать, оплачен ли заказ: нет — деньги отдаёт клиент на месте
		"payment": payment(order),
	}
	if own:
		card["phone"] = _text(order.get("custom_whatsapp_number")) or _text(order.get("contact_mobile"))
		card["items"] = items
	return card

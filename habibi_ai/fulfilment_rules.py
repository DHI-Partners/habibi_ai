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


def plain_address(html):
	"""Адрес Frappe хранит HTML («улица<br>город<br>»): курьеру нужна строка."""
	text = re.sub(r"<br\s*/?>|\n", ",", html or "", flags=re.IGNORECASE)
	text = re.sub(r"<[^>]+>", "", text)
	return ", ".join(part.strip() for part in text.split(",") if part.strip()) or None


def lines(rows):
	"""Состав без строки доставки: она не еда, кухня и курьер её не готовят и не несут."""
	return [{"item_name": r["item_name"], "qty": r["qty"]} for r in rows if r["item_code"] != DELIVERY_ITEM]


def kitchen_card(order, rows, now):
	return {
		"name": order["name"],
		"age": age_minutes(order["modified"], now),
		"notes": _text(order.get("custom_kitchen_notes")),
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
	}
	if own:
		card["phone"] = _text(order.get("custom_whatsapp_number")) or _text(order.get("contact_mobile"))
		card["items"] = items
	return card

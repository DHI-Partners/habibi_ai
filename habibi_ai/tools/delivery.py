"""Чтение условий доставки из ERPNext.

Условия доставки — факт, а не инструкция: у них есть владелец и место, где их
поддерживают. Записанные в промпт, они становятся копией, которая устаревает
молча, — так в персоне бота однажды и осталось «минимальный заказ 500 рублей»
при меню в тенге.
"""

import frappe

from habibi_ai.tools import tool

ZONE_DOCTYPE = "Delivery Zone"


@tool(
	name="get_delivery_zones",
	description=(
		"Зоны доставки: стоимость, срок и сумма, с которой доставка бесплатна. "
		"Вызывай, когда спрашивают о цене или сроке доставки, а также перед тем, "
		"как назвать итог заказа с доставкой — условия меняются, помнить их нельзя."
	),
	input_schema={"type": "object", "properties": {}},
)
def get_delivery_zones():
	# Справочник зон заведён не приложением, а руками в конкретной инсталляции,
	# поэтому у другого тенанта его может не быть вовсе. Обращение к
	# несуществующему doctype бросает исключение — реестр вернул бы модели
	# «инструмент завершился ошибкой», и она сказала бы клиенту что-то про сбой.
	# А правда здесь другая: доставка просто не настроена.
	if not frappe.db.exists("DocType", ZONE_DOCTYPE):
		return (
			"Зоны доставки в этой системе не заведены. Не называй стоимость и "
			"сроки доставки, предложи уточнить у оператора."
		)

	zones = frappe.get_all(
		ZONE_DOCTYPE,
		filters={"is_active": 1},
		fields=["name", "delivery_fee", "free_above", "eta_minutes", "notes"],
		order_by="delivery_fee",
		limit_page_length=0,
	)
	if not zones:
		return (
			"Ни одна зона доставки не активна. Не называй стоимость и сроки "
			"доставки, предложи уточнить у оператора."
		)

	# Валюта берётся из настроек системы, а не пишется в коде: инструмент общий
	# для всех тенантов, а тенге зашит только в одном из них.
	currency = frappe.db.get_single_value("Global Defaults", "default_currency") or ""

	lines = []
	for z in zones:
		part = f"{z['name']} — доставка {z['delivery_fee']:g} {currency}".rstrip()
		if z.get("eta_minutes"):
			part += f", около {z['eta_minutes']} мин"
		if z.get("free_above"):
			part += f", бесплатно от {z['free_above']:g} {currency}".rstrip()
		if z.get("notes"):
			part += f" ({z['notes']})"
		lines.append(part)

	return "\n".join(lines)

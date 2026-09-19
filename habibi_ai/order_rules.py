"""Правила заказа, не зависящие от frappe.

Всё, что проверяет аргументы модели и решает судьбу расчёта, — здесь, без
ERP: цена ошибки — заказ не того состава или второй заказ вместо одного, и
проверяться это должно за секунды. ERP-связка — в tools/orders.py.
"""

import re
from datetime import timedelta

MAX_QTY = 50
QUOTE_TTL = timedelta(minutes=30)
DELIVERY_ITEM = "SRV-DELIVERY"


class Refusal(Exception):
	"""Отказ, который модель увидит текстом и по которому исправится сама."""


def normalize_phone(raw):
	"""+ и цифры; None, если на номер не похоже.

	Клиент пишет номер как угодно: «+7 (701) 555-01-04», «8701…». Сравнивать
	и хранить надо одну форму — иначе один человек станет двумя клиентами.
	"""
	digits = re.sub(r"\D", "", str(raw or ""))
	if not 10 <= len(digits) <= 15:
		return None
	return "+" + digits


def money(value):
	"""4 670 и 500.36: без копеек, когда их нет, — так пишут цены в меню."""
	whole, frac = f"{float(value or 0):,.2f}".split(".")
	whole = whole.replace(",", " ")
	return whole if frac == "00" else f"{whole}.{frac}"


def _valid_qty(qty):
	# bool — подкласс int: True прошёл бы как «одна штука»
	if isinstance(qty, bool) or not isinstance(qty, (int, float)):
		return False
	return qty == int(qty) and 1 <= qty <= MAX_QTY


def resolve_lines(requested, catalog):
	"""Состав от модели → строки с ценой из каталога.

	Цена берётся только из каталога: в аргументах её нет вовсе. Позицию можно
	назвать кодом или названием — модель видела в меню и то и другое.
	"""
	if not isinstance(requested, list) or not requested:
		raise Refusal(
			"Состав заказа пуст. Передай в items весь заказ целиком: [{item_code, qty}] — "
			"код или название позиции из get_menu и количество."
		)

	by_name = {v["item_name"].casefold(): code for code, v in catalog.items()}
	quantities = {}
	unknown = []
	for row in requested:
		if not isinstance(row, dict):
			raise Refusal("Каждая позиция — объект {item_code, qty}.")
		key = str(row.get("item_code") or "").strip()
		code = key if key in catalog else by_name.get(key.casefold())
		if code is None:
			unknown.append(key or "(пусто)")
			continue
		qty = row.get("qty")
		if not _valid_qty(qty):
			raise Refusal(
				f"Количество «{qty}» для {catalog[code]['item_name']} не подходит: нужно целое от 1 до {MAX_QTY}."
			)
		quantities[code] = quantities.get(code, 0) + int(qty)

	if unknown:
		available = ", ".join(f"{v['item_name']} ({code})" for code, v in sorted(catalog.items()))
		missing = ", ".join(f"«{name}»" for name in unknown)
		raise Refusal(f"Нет в меню: {missing}. Доступно: {available}. Уточни у клиента, что он имел в виду.")

	for code, qty in quantities.items():
		if qty > MAX_QTY:
			raise Refusal(f"{catalog[code]['item_name']}: больше {MAX_QTY} штук бот не оформляет — предложи оператора.")

	return [
		{
			"item_code": code,
			"item_name": catalog[code]["item_name"],
			"qty": qty,
			"rate": float(catalog[code]["rate"]),
			"uom": catalog[code]["uom"],
		}
		for code, qty in quantities.items()
	]


def match_zone(requested, zones):
	if not zones:
		raise Refusal("Ни одна зона доставки не активна — доставку оформить нельзя. Предложи самовывоз или оператора.")
	names = ", ".join(z["name"] for z in zones)
	if not requested:
		raise Refusal(f"Спроси у клиента район доставки. Доступны: {names}.")
	for zone in zones:
		if zone["name"].casefold() == str(requested).strip().casefold():
			return zone
	raise Refusal(f"Зоны «{requested}» нет. Доступны: {names}. Уточни у клиента.")


def delivery_line(zone, goods_total, uom):
	"""Доставка — строкой заказа: кухня и касса видят её там же, где еду."""
	fee = float(zone.get("delivery_fee") or 0)
	free_above = zone.get("free_above")
	if free_above and goods_total >= float(free_above):
		fee = 0.0
	return {
		"item_code": DELIVERY_ITEM,
		"item_name": f"Доставка ({zone['name']})",
		"qty": 1,
		"rate": fee,
		"uom": uom,
	}


def check_quote(quote, context, now):
	"""Можно ли оформить заказ по расчёту: "create", "done" (уже оформлен) или Refusal.

	Порядок важен. Сначала чат: чужой расчёт не существует, даже если он
	оформлен. Затем оформленный — повтор возвращает тот же заказ в любом ходе.
	И только потом ход и срок.
	"""
	if not quote or quote.get("engine_chat_id") != context.get("engine_chat_id"):
		raise Refusal("Расчёт не найден. Сделай новый quote_order по составу, который назвал клиент.")
	if quote.get("sales_order"):
		return "done"
	if quote.get("turn_id") == context.get("turn_id"):
		# Главное правило: между расчётом и заказом должно быть сообщение
		# клиента. Проверяет код, а не инструкция, которую модель может забыть.
		raise Refusal(
			"Сначала зачитай расчёт клиенту и дождись его явного согласия. "
			"create_order вызывается после ответа клиента, а не в том же ответе, что quote_order."
		)
	if now > quote["expires_on"]:
		raise Refusal("Расчёт устарел — цены могли измениться. Сделай новый quote_order и зачитай его клиенту.")
	return "create"


def item_lines(rows, currency):
	lines = []
	for row in rows:
		if row["item_code"] == DELIVERY_ITEM:
			price = f"{money(row['amount'])} {currency}" if row["amount"] else "бесплатно"
			lines.append(f"- {row['item_name']} — {price}")
		else:
			lines.append(f"- {row['item_name']} × {money(row['qty'])} — {money(row['amount'])} {currency}")
	return lines


def total_line(payable, taxes, currency):
	line = f"Итого: {money(payable)} {currency}"
	for tax in taxes:
		if tax["amount"]:
			line += f", включая {tax['description']} — {money(tax['amount'])} {currency}"
	return line

"""Чтение меню из ERPNext.

Меню принадлежит ERPNext, а не боту: правило, соблюдаемое в промпте, — это
правило, от которого модель можно отговорить. Бот не помнит цены, а спрашивает.
"""

import frappe

from habibi_ai.tools import tool


@tool(
	name="get_menu",
	description=(
		"Актуальные позиции меню с ценами. Вызывай перед тем, как называть "
		"клиенту состав или стоимость — цены меняются, помнить их нельзя."
	),
	input_schema={"type": "object", "properties": {}},
)
def get_menu():
	# get_all применяет права текущего пользователя и работает в рамках текущего
	# сайта Frappe: аргументов у инструмента нет вообще, поэтому подставить
	# тенант через вызов модели невозможно — изоляция здесь на уровне процесса,
	# а не фильтра, который можно забыть добавить.
	items = frappe.get_all(
		"Item",
		filters={"is_sales_item": 1, "disabled": 0},
		fields=["item_code", "item_name", "standard_rate"],
		limit_page_length=100,
		order_by="item_name",
	)

	if not items:
		return "Меню пусто — позиций для продажи не заведено."

	lines = [
		f"{i['item_name']} ({i['item_code']}) — {i['standard_rate']}"
		for i in items
	]
	return "\n".join(lines)

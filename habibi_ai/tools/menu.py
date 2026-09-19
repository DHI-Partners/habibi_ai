"""Чтение меню из ERPNext.

Меню принадлежит ERPNext, а не боту: правило, соблюдаемое в промпте, — это
правило, от которого модель можно отговорить. Бот не помнит цены, а спрашивает.
"""

import frappe

from habibi_ai.order_rules import DELIVERY_ITEM
from habibi_ai.tools import tool

# Верхняя граница на число позиций в ответе. Тихо обрезанный список опаснее
# отсутствия ответа: модель приняла бы часть меню за всё меню и на вопрос про
# позицию за пределами среза сказала бы клиенту, что её не существует, хотя
# она просто не попала в лимит. Поэтому переполнение не молчит — get_menu
# сообщает о нём прямо в тексте ответа, который видит модель.
MENU_LIMIT = 100


def valid_prices(rows, today):
	"""Цены, действующие сегодня.

	Срок действия проверяем здесь, а не условием в запросе: сравнение с NULL
	в SQL ложно, и фильтр «valid_upto >= сегодня» выбросил бы как раз обычный
	случай — цену без даты окончания, то есть бессрочную.
	"""
	return [
		r
		for r in rows
		if (not r.get("valid_from") or str(r["valid_from"]) <= today)
		and (not r.get("valid_upto") or str(r["valid_upto"]) >= today)
	]


def sellable_catalog(price_list):
	"""Что можно заказать: те же правила, что у get_menu, но без лимита.

	Бот, показавший позицию в меню и не сумевший её оформить, — или
	оформивший то, чего в меню нет, — хуже бота без заказов. Доставка из
	каталога исключена: её строку добавляет код по зоне, а не модель.
	"""
	rows = frappe.get_all(
		"Item Price",
		filters={"price_list": price_list, "selling": 1},
		fields=["item_code", "price_list_rate", "valid_from", "valid_upto"],
		order_by="item_code",
		limit_page_length=0,
	)
	by_code = {}
	for p in valid_prices(rows, frappe.utils.nowdate()):
		by_code.setdefault(p["item_code"], p)
	by_code.pop(DELIVERY_ITEM, None)
	if not by_code:
		return {}

	items = frappe.get_all(
		"Item",
		filters={"item_code": ["in", list(by_code)], "is_sales_item": 1, "disabled": 0},
		fields=["item_code", "item_name", "stock_uom"],
		limit_page_length=0,
	)
	return {
		i["item_code"]: {
			"item_name": i["item_name"],
			"rate": float(by_code[i["item_code"]]["price_list_rate"]),
			"uom": i["stock_uom"],
		}
		for i in items
		if i["item_code"] != DELIVERY_ITEM
	}


@tool(
	name="get_menu",
	description=(
		"Актуальные позиции меню с ценами. Вызывай перед тем, как называть "
		"клиенту состав или стоимость — цены меняются, помнить их нельзя."
	),
	input_schema={"type": "object", "properties": {}},
)
def get_menu():
	# Цена берётся из прайс-листа, а не из Item.standard_rate: standard_rate —
	# это себестоимостный ориентир карточки, продажная цена живёт в Item Price.
	# На инсталляции, где прайс-лист заполнен, а standard_rate нет, бот называл
	# бы клиенту нули и брал бы заказы бесплатно.
	#
	# Какой именно прайс-лист — решает настройка продаж сайта, а не код: у
	# каждого тенанта он свой, и захардкоженное имя работало бы ровно у одного.
	price_list = frappe.db.get_single_value("Selling Settings", "selling_price_list")
	if not price_list:
		# Тихо откатиться на standard_rate значило бы назвать неверную цену и
		# не сказать об этом никому. Пусть модель знает, что цен у неё нет.
		return (
			"Прайс-лист продаж не настроен в системе, актуальных цен нет. "
			"Не называй клиенту цены и предложи дождаться сотрудника."
		)

	# get_all применяет права текущего пользователя и работает в рамках текущего
	# сайта Frappe: аргументов у инструмента нет вообще, поэтому подставить
	# тенант через вызов модели невозможно — изоляция здесь на уровне процесса,
	# а не фильтра, который можно забыть добавить.
	#
	# Запрашиваем на одну позицию больше лимита — не чтобы показать её, а чтобы
	# узнать, было ли что обрезать. Иначе список ровно из MENU_LIMIT позиций
	# нельзя было бы отличить от списка, который на самом деле длиннее.
	rows = frappe.get_all(
		"Item Price",
		filters={"price_list": price_list, "selling": 1},
		fields=["item_code", "price_list_rate", "currency", "valid_from", "valid_upto"],
		limit_page_length=MENU_LIMIT + 1,
		order_by="item_code",
	)

	prices = valid_prices(rows, frappe.utils.nowdate())

	if not prices:
		return f"В прайс-листе «{price_list}» нет действующих цен."

	# У позиции может быть несколько строк цены (разные единицы, покупатели).
	# Берём первую: меню — это ответ «сколько стоит вообще», а не расчёт цены
	# под конкретного клиента, которым занимается сам заказ.
	by_code = {}
	for p in prices:
		by_code.setdefault(p["item_code"], p)

	items = frappe.get_all(
		"Item",
		filters={"item_code": ["in", list(by_code)], "is_sales_item": 1, "disabled": 0},
		fields=["item_code", "item_name"],
		limit_page_length=0,
	)
	if not items:
		return "Меню пусто — позиций для продажи не заведено."

	lines = sorted(
		f"{i['item_name']} ({i['item_code']}) — "
		f"{by_code[i['item_code']]['price_list_rate']} "
		f"{by_code[i['item_code']]['currency']}"
		for i in items
	)

	truncated = len(rows) > MENU_LIMIT
	if truncated:
		lines.append(
			f"\n[Показаны первые {MENU_LIMIT} позиций — в меню их больше. "
			"Нельзя утверждать, что какой-то позиции нет, если её не видно "
			"в этом списке.]"
		)

	return "\n".join(lines)

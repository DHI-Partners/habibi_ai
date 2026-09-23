"""Возможности бизнеса: какие инструменты бота включены.

Без frappe. Флаг отвечает сразу за раздел кабинета и за инструмент: у
автопроката нет доставки — нет ни раздела, ни get_delivery_zones, и бот о
доставке не заговорит.
"""

FEATURES = {
	"delivery": ("feature_delivery", ("get_delivery_zones",)),
	"orders": ("feature_orders", ("quote_order", "create_order")),
}


def _on(value):
	"""None — поле ни разу не сохраняли: считаем включённым.

	Иначе сайт, где бот уже принимал заказы, после миграции молча потерял бы
	инструменты заказа — флаги появились позже, чем заказы.

	"" — то же самое, что 0, а не повод упасть: значения из tabSingles
	приходят сырыми строками (frappe.db.get_singles_dict без cast, чтобы не
	тянуть устаревший cast_fieldtype), и пустая строка там такой же законный
	сырой вид «выключено», как и "0" — int("") бросил бы ValueError.
	"""
	if value is None:
		return True
	if value == "":
		return False
	return bool(int(value))


def enabled(values):
	return {key for key, (field, _tools) in FEATURES.items() if _on(values.get(field))}


def offered(names, enabled_keys):
	blocked = {t for key, (_f, tools) in FEATURES.items() if key not in enabled_keys for t in tools}
	return [n for n in names if n not in blocked]

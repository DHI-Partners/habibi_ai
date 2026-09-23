"""Возможности бизнеса: какие инструменты бота включены.

Без frappe. Флаг отвечает сразу за раздел кабинета и за инструмент: у
автопроката нет доставки — нет ни раздела, ни get_delivery_zones, и бот о
доставке не заговорит.
"""

FEATURES = {
	"delivery": ("feature_delivery", ("get_delivery_zones",)),
	"orders": ("feature_orders", ("quote_order", "create_order")),
}


def enabled(values):
	"""None — поле ни разу не сохраняли: считаем включённым.

	Иначе сайт, где бот уже принимал заказы, после миграции молча потерял бы
	инструменты заказа — флаги появились позже, чем заказы.
	"""
	return {key for key, (field, _tools) in FEATURES.items() if values.get(field) is None or int(values[field])}


def offered(names, enabled_keys):
	blocked = {t for key, (_f, tools) in FEATURES.items() if key not in enabled_keys for t in tools}
	return [n for n in names if n not in blocked]

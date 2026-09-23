app_name = "habibi_ai"
app_title = "Habibi AI"
app_publisher = "Habeebe"
app_description = "ИИ-модуль: раздел ИИ в интерфейсе habibi_ui поверх движка Directus"
app_email = "dosnet2200@gmail.com"
app_license = "mit"

# Интерфейс модуля — раздел в habibi_ui (/ui/ai). Своей страницы в Desk у него
# больше нет, поэтому без оболочки ставить его некуда.
# erpnext — потому что инструменты заказа и справочники ссылаются на Company,
# Customer, Sales Order: без него миграция приложения падает на Link.
required_apps = ["habibi_ui", "erpnext"]

# Плитка модуля на рабочем столе создаётся кодом: из фикстур приложения
# Frappe Desktop Icon не создаёт. Тем же хуком снимается пустой Workspace
# "Habibi AI" на сайтах, где он остался от старой версии модуля. Подробности —
# в habibi_ai/setup.py.
after_install = "habibi_ai.setup.after_install"
after_migrate = "habibi_ai.setup.after_migrate"

# Роль-переключатель трассировки приезжает фикстурой: без неё в api.py
# ссылка на несуществующую роль, и выдать право некому.
fixtures = [
	{"dt": "Role", "filters": [["name", "in", ["Habibi AI Debug"]]]},
]

after_app_install = "habibi_ai.setup.after_app_install"

# Telegram как канал ИИ. Хуки на доктайпы habibi_telegram безвредны там, где
# его нет: событий этих доктайпов на таком сайте просто не бывает.
doc_events = {
	# Код позиции — из названия, если владелец (кабинет) его не ввёл; см.
	# habibi_ai/items.py. Хук общий для любой вставки Item, не только из
	# кабинета.
	"Item": {"before_insert": "habibi_ai.items.before_insert"},
	"Telegram Message": {
		"after_insert": [
			"habibi_ai.channels.telegram.on_message_insert",
			"habibi_ai.cabinet.realtime.on_change",
		],
	},
	"Telegram Bot": {"validate": "habibi_ai.channels.telegram.validate_channel"},
	"Telegram Account": {"validate": "habibi_ai.channels.telegram.validate_channel"},
	"Sales Order": {
		"on_update": "habibi_ai.cabinet.realtime.on_change",
		"on_submit": "habibi_ai.cabinet.realtime.on_change",
		# Воркфлоу двигает проведённый заказ через on_update_after_submit, отказ —
		# отменой: без них кабинет узнавал бы о смене статуса только по F5
		"on_update_after_submit": "habibi_ai.cabinet.realtime.on_change",
		"on_cancel": "habibi_ai.cabinet.realtime.on_change",
	},
	"AI Channel Chat": {
		"on_update": [
			"habibi_ai.cabinet.realtime.on_change",
			# Пауза снята галочкой в Desk — переписка без бота уходит в его историю
			"habibi_ai.channels.telegram.on_pair_update",
		]
	},
}

doctype_js = {
	"Telegram Bot": "public/js/telegram_channel_ai.js",
	"Telegram Account": "public/js/telegram_channel_ai.js",
}

# Черновик заказа, созданный ботом, оператор должен мочь удалить: ссылка из
# расчёта AI Order Quote иначе блокирует удаление Sales Order.
ignore_links_on_delete = ["AI Order Quote"]

# Кабинет (habibi_ui) не импортирует habibi_ai — связь только через хуки:
# адаптер полей, которых нет в самом DocType, и флаги включённых возможностей.
habibi_cabinet_adapters = {
	"selling_price": "habibi_ai.cabinet.adapters.selling_price",
	"order_status": "habibi_ai.cabinet.adapters.order_status",
	"order_total": "habibi_ai.cabinet.adapters.order_total",
}
habibi_cabinet_features = ["habibi_ai.api.features_hook"]

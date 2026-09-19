"""Демонтаж плитки модуля и пустого Workspace в Desk.

Чат живёт разделом в habibi_ui (/ui/ai), а не страницей Desk. Плитка Desktop
Icon вела на удалённую страницу ai-chat, поэтому её надо убрать — иначе на
сайтах, где модуль уже стоял, в лаунчере остаётся ссылка в пустоту. Тот же
довод — для Workspace "Habibi AI": шорткат и ссылка в сайдбаре на ai-chat
убраны, а сам Workspace остался пустым (один заголовок), и habibi_ui
показывает его в списке разделов как ссылку всё в ту же пустоту.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

WORKSPACE = "Habibi AI"


def after_install():
	drop_desktop_icon()
	drop_workspace()
	install_telegram_fields()
	install_order_source_option()


def after_migrate():
	drop_desktop_icon()
	drop_workspace()
	install_telegram_fields()
	install_order_source_option()


def drop_desktop_icon():
	"""Идемпотентно: after_migrate вызывается при каждой миграции сайта."""
	if frappe.db.exists("Desktop Icon", WORKSPACE):
		frappe.delete_doc("Desktop Icon", WORKSPACE, ignore_permissions=True)


def drop_workspace():
	"""Идемпотентно: фикстуры в репозитории больше нет, но на сайтах, где
	модуль уже стоял, документ остался от прошлой миграции.
	"""
	if frappe.db.exists("Workspace", WORKSPACE):
		frappe.delete_doc("Workspace", WORKSPACE, ignore_permissions=True, force=True)


TELEGRAM_CHANNELS = ("Telegram Bot", "Telegram Account")


def after_app_install(app_name):
	"""habibi_telegram поставили после habibi_ai — поля нужны сразу, а не с
	ближайшей миграцией: без них канал нельзя включить."""
	if app_name == "habibi_telegram":
		install_telegram_fields()


def install_telegram_fields():
	"""Поля ИИ на каналах Telegram.

	Живут в habibi_ai, а не в доктайпах habibi_telegram: Telegram ставится и
	без ИИ, и знать о нём не должен. Идемпотентно — вызывается на каждой
	миграции; update=True обновляет подписи, если они поменялись.
	"""
	if "habibi_telegram" not in frappe.get_installed_apps():
		return

	# Обе формы кончаются полем notify_role — после него и встаёт секция
	fields = [
		{"fieldname": "ai_section", "fieldtype": "Section Break", "label": "ИИ", "insert_after": "notify_role"},
		{
			"fieldname": "ai_enabled",
			"fieldtype": "Check",
			"label": "Использовать ИИ для ответа",
			"insert_after": "ai_section",
		},
		{
			"fieldname": "ai_bot",
			"fieldtype": "Autocomplete",
			"label": "ИИ-бот",
			"insert_after": "ai_enabled",
			"depends_on": "ai_enabled",
			"mandatory_depends_on": "ai_enabled",
			"description": "Бот движка ИИ, который отвечает в этом канале",
		},
		{
			# По умолчанию выключено: живой номер состоит в чужих группах, и
			# ответ на каждое сообщение там — спам от имени владельца
			"fieldname": "ai_reply_in_groups",
			"fieldtype": "Check",
			"label": "Отвечать в группах",
			"insert_after": "ai_bot",
			"depends_on": "ai_enabled",
			"default": "0",
			"description": "Без галки ИИ отвечает только в личных чатах",
		},
	]
	create_custom_fields({doctype: fields for doctype in TELEGRAM_CHANNELS}, update=True)


ORDER_SOURCE = "Telegram"


def install_order_source_option():
	"""Вариант Telegram в источнике заказа — там, где поле источника есть.

	Не фикстурой: custom_order_source заведён руками на одном сайте, и
	фикстура Property Setter разъехалась бы на все, где такого поля нет.
	Идемпотентно — вызывается на каждой миграции.
	"""
	name = frappe.db.get_value("Custom Field", {"dt": "Sales Order", "fieldname": "custom_order_source"})
	if not name:
		return
	field = frappe.get_doc("Custom Field", name)
	options = [o for o in (field.options or "").split("\n") if o]
	if ORDER_SOURCE in options:
		return
	field.options = "\n".join([*options, ORDER_SOURCE])
	field.save(ignore_permissions=True)

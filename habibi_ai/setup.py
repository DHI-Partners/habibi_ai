"""Демонтаж плитки модуля в Desk.

Чат живёт разделом в habibi_ui (/ui/ai), а не страницей Desk. Плитка Desktop
Icon вела на удалённую страницу ai-chat, поэтому её надо убрать — иначе на
сайтах, где модуль уже стоял, в лаунчере остаётся ссылка в пустоту.
"""

import frappe

WORKSPACE = "Habibi AI"


def after_install():
	drop_desktop_icon()


def after_migrate():
	drop_desktop_icon()


def drop_desktop_icon():
	"""Идемпотентно: after_migrate вызывается при каждой миграции сайта."""
	if frappe.db.exists("Desktop Icon", WORKSPACE):
		frappe.delete_doc("Desktop Icon", WORKSPACE, ignore_permissions=True)

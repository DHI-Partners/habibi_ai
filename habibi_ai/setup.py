"""Демонтаж плитки модуля и пустого Workspace в Desk.

Чат живёт разделом в habibi_ui (/ui/ai), а не страницей Desk. Плитка Desktop
Icon вела на удалённую страницу ai-chat, поэтому её надо убрать — иначе на
сайтах, где модуль уже стоял, в лаунчере остаётся ссылка в пустоту. Тот же
довод — для Workspace "Habibi AI": шорткат и ссылка в сайдбаре на ai-chat
убраны, а сам Workspace остался пустым (один заголовок), и habibi_ui
показывает его в списке разделов как ссылку всё в ту же пустоту.
"""

import frappe

WORKSPACE = "Habibi AI"


def after_install():
	drop_desktop_icon()
	drop_workspace()


def after_migrate():
	drop_desktop_icon()
	drop_workspace()


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

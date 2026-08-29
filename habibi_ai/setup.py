"""Регистрация модуля на рабочем столе тенанта.

Плитку на главной habibi_ui (/ui) даёт не Workspace и не Workspace Sidebar
сами по себе, а Desktop Icon, который на этот sidebar ссылается: список
плиток строится в habibi_ui.api.v1.workspaces.modules() поверх
frappe.desk.doctype.desktop_icon.get_desktop_icons().

Из фикстур приложения Frappe такую иконку не создаёт — у соседних
приложений она заведена вручную (standard = 0). Поэтому создаём её сами
при установке и миграции: иначе каждому новому тенанту пришлось бы
добавлять модуль на рабочий стол руками, а без этого страница доступна
только по прямой ссылке /app/ai-chat.
"""

import frappe

WORKSPACE = "Habibi AI"


def after_install():
	ensure_desktop_icon()


def after_migrate():
	ensure_desktop_icon()


def ensure_desktop_icon():
	"""Заводит плитку модуля, если её ещё нет.

	Идемпотентно: after_migrate вызывается при каждой миграции сайта.
	"""
	if frappe.db.exists("Desktop Icon", WORKSPACE):
		return

	if not frappe.db.exists("Workspace Sidebar", WORKSPACE):
		# Sidebar приезжает фикстурой приложения. Если его нет, миграция ещё
		# не дошла до синхронизации — на следующем прогоне иконка появится.
		return

	icon = frappe.new_doc("Desktop Icon")
	icon.label = WORKSPACE
	icon.icon_type = "Link"
	icon.link_type = "Workspace Sidebar"
	icon.link_to = WORKSPACE
	icon.insert(ignore_permissions=True)

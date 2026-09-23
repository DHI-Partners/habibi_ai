"""Item.before_insert: код позиции и обязательные поля — из минимума формы меню.

Кабинет создаёт Item без item_code (в форме раздела «Меню» пресета food.json
этого поля нет, см. presets/food.json) — при Item Naming By = "Item Code"
(значение по умолчанию ERPNext) insert() иначе падает с «Item Code is
required». Хук навешен на любую вставку Item, а не вызывается из
habibi_ui.api.v1.cabinet.save предметной логикой: это чинит проблему там же,
где она возникает (autoname берёт item_code следующим шагом после
before_insert), и работает не только для кабинета.

Условие узкое: трогаем только пустые поля. item_code/item_group/stock_uom,
заполненные явно (например, из Desk), хук не переписывает.
"""

import frappe

from habibi_ai.transliterate import slug, unique_code

MAX_CODE_LEN = 40
FALLBACK_UOM = "Nos"
# Название без транслитерируемых символов (эмодзи, одна пунктуация) даёт
# пустой slug() — код всё равно должен родиться, а не оставить insert() падать
# на «Item Code is required»
FALLBACK_CODE = "ITEM"


def _default_item_group():
	"""Группа по умолчанию из Stock Settings; нет своей — первая не групповая."""
	default = frappe.db.get_single_value("Stock Settings", "item_group")
	if default and frappe.db.exists("Item Group", default):
		return default
	return frappe.db.get_value("Item Group", {"is_group": 0}, "name", order_by="lft asc")


def _default_stock_uom():
	return frappe.db.get_single_value("Stock Settings", "stock_uom") or FALLBACK_UOM


def _unique_code(base):
	"""Обёртка transliterate.unique_code настоящей проверкой в базе.

	Между exists() и вставкой — окно для гонки: два владельца одновременно
	создают позицию с одним названием. Для маленького бизнеса это редкость,
	а не то, что нужно ловить отдельно: совпавший code просто всплывёт как
	обычная ошибка БД (Duplicate entry) на самом insert().
	"""
	return unique_code(base, lambda code: frappe.db.exists("Item", {"item_code": code}), MAX_CODE_LEN)


def before_insert(doc, method=None):
	if not doc.item_code and doc.item_name and frappe.db.get_default("item_naming_by") != "Naming Series":
		doc.item_code = _unique_code(slug(doc.item_name, MAX_CODE_LEN) or FALLBACK_CODE)
	if not doc.item_group:
		doc.item_group = _default_item_group()
	if not doc.stock_uom:
		doc.stock_uom = _default_stock_uom()
	# ERPNext и так проставляет сюда дефолт 1 через Document._set_defaults()
	# (он отрабатывает раньше before_insert) — строка ниже просто не даёт
	# разделу «Меню» (base_filters is_sales_item=1) зависеть от этого чужого
	# поведения; явный 0 она не трогает.
	if doc.is_sales_item is None:
		doc.is_sales_item = 1

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

from habibi_ai.transliterate import slug

MAX_CODE_LEN = 40
FALLBACK_UOM = "Nos"


def _default_item_group():
	"""Группа по умолчанию из Stock Settings; нет своей — первая не групповая."""
	default = frappe.db.get_single_value("Stock Settings", "item_group")
	if default and frappe.db.exists("Item Group", default):
		return default
	return frappe.db.get_value("Item Group", {"is_group": 0}, "name", order_by="lft asc")


def _default_stock_uom():
	return frappe.db.get_single_value("Stock Settings", "stock_uom") or FALLBACK_UOM


def _unique_code(base):
	"""base занят — суффикс -2, -3… пока не найдётся свободный код."""
	if not frappe.db.exists("Item", {"item_code": base}):
		return base
	n = 2
	while True:
		suffix = f"-{n}"
		candidate = base[: MAX_CODE_LEN - len(suffix)] + suffix
		if not frappe.db.exists("Item", {"item_code": candidate}):
			return candidate
		n += 1


def before_insert(doc, method=None):
	if not doc.item_code and doc.item_name and frappe.db.get_default("item_naming_by") != "Naming Series":
		base = slug(doc.item_name, MAX_CODE_LEN)
		if base:
			doc.item_code = _unique_code(base)
	if not doc.item_group:
		doc.item_group = _default_item_group()
	if not doc.stock_uom:
		doc.stock_uom = _default_stock_uom()
	if doc.is_sales_item is None:
		doc.is_sales_item = 1

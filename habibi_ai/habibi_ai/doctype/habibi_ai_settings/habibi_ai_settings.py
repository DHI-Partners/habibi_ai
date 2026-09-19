"""Настройки ИИ на сайте.

Компания — отдельной настройкой, а не из Global Defaults: на тестовом сайте
умолчанием стоит Test (Demo) в SAR, и бот оформлял бы заказы бургерной в
риалах, не сказав никому.
"""

import frappe
from frappe.model.document import Document

DOCTYPE = "Habibi AI Settings"


class HabibiAISettings(Document):
	pass


def get_company():
	return frappe.db.get_single_value(DOCTYPE, "company") or None

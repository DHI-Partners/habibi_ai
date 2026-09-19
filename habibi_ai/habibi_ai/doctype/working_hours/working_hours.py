"""Режим работы заведения — справочник, из которого бот отвечает «до скольки».

Живёт в приложении, а не заводится руками на сайте: он нужен каждому
заведению, и руками на третьем сайте он разошёлся бы полями с первыми двумя.
"""

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import frappe
from frappe import _
from frappe.model.document import Document


class WorkingHours(Document):
	def validate(self):
		if self.time_zone:
			try:
				ZoneInfo(self.time_zone)
			except (ZoneInfoNotFoundError, ValueError):
				frappe.throw(_("Неизвестный часовой пояс {0}. Пример: Asia/Almaty").format(self.time_zone))

		for row in self.exceptions:
			if not row.closed and not (row.opens and row.closes):
				frappe.throw(_("Строка {0} исключений: либо «Закрыто», либо часы «С» и «До»").format(row.idx))

		for row in self.schedule:
			if row.opens == row.closes:
				frappe.throw(_("Строка {0} расписания: «С» и «До» совпадают").format(row.idx))

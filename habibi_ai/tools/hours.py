"""Режим работы из справочника Working Hours.

Последний факт, который жил в персоне бота. Записанный в промпт, он
устаревает молча: график сменился, а бот продолжает звать клиентов к девяти.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import frappe

from habibi_ai import schedule
from habibi_ai.habibi_ai.doctype.habibi_ai_settings.habibi_ai_settings import get_company
from habibi_ai.tools import tool

DOCTYPE = "Working Hours"

NOT_CONFIGURED = (
	"Режим работы в системе не настроен. Не называй часы работы и доставки, "
	"предложи уточнить у оператора."
)


def load_hours():
	"""(расписание, исключения, пояс) компании из настроек, или None — не настроено."""
	company = get_company()
	name = company and frappe.db.get_value(DOCTYPE, {"company": company})
	if not name:
		return None

	doc = frappe.get_doc(DOCTYPE, name)
	if not doc.schedule:
		return None

	rows = [{"weekday": r.weekday, "kind": r.kind, "opens": r.opens, "closes": r.closes} for r in doc.schedule]
	exceptions = [
		{
			"date": frappe.utils.getdate(r.date),
			"closed": r.closed,
			"opens": r.opens,
			"closes": r.closes,
			"note": r.note,
		}
		for r in doc.exceptions
	]
	# Пояс заведения, а не сайта: erp.habibi-erp.com стоит в Asia/Riyadh, а
	# бургерная в Казахстане — по поясу сайта бот ошибался бы на два часа
	tz = doc.time_zone or frappe.db.get_single_value("System Settings", "time_zone") or "UTC"
	return rows, exceptions, tz


def local_now(tz):
	return datetime.now(ZoneInfo(tz)).replace(tzinfo=None)


@tool(
	name="get_working_hours",
	description=(
		"Режим работы и доставки: открыто ли сейчас, до скольки, и расписание на неделю. "
		"Вызывай, когда спрашивают, работаете ли, до скольки, когда откроетесь, — "
		"часы меняются, помнить их нельзя."
	),
	input_schema={"type": "object", "properties": {}},
)
def get_working_hours():
	loaded = load_hours()
	if loaded is None:
		return NOT_CONFIGURED
	rows, exceptions, tz = loaded
	return schedule.describe(local_now(tz), rows, exceptions)


def closed_warning(fulfilment):
	"""Предупреждение для расчёта заказа, если сейчас его не выполнят; None — всё в порядке.

	Не запрет: заказ на утро — нормальный заказ. Но клиент должен услышать,
	что ночью его не привезут, до того, как скажет «да».
	"""
	loaded = load_hours()
	if loaded is None:
		return None
	rows, exceptions, tz = loaded

	kind = schedule.KIND_WORK
	if fulfilment == "delivery" and schedule.has_kind(rows, schedule.KIND_DELIVERY):
		kind = schedule.KIND_DELIVERY

	now = local_now(tz)
	if schedule.open_interval(now, kind, rows, exceptions):
		return None
	return (
		f"Внимание: сейчас {schedule.status_line(now, kind, rows, exceptions)}. "
		"Предупреди клиента, что заказ выполнят, когда откроемся."
	)

"""Ежедневная сводка стража и журнала событий.

Страж и журнал при сбое молча отключаются — ход идёт без них, а клиент ничего
не замечает. Поэтому их собственные сбои и число нарушений за сутки
поднимаются в Error Log, где их видит администратор.
"""

import frappe

from habibi_ai import events


def daily_report():
	stats = events.guard_stats(hours=24)
	frappe.logger("habibi_ai").info(f"ИИ: страж за сутки: {stats}")
	if stats["errors"]:
		frappe.log_error(
			title="ИИ: страж и журнал за сутки",
			message=(
				f"Сбоев механизма: {stats['errors']}. Нарушений, пойманных стражем: {stats['violated']}, "
				f"довыполнено: {stats['fulfilled']}. Пока механизм сломан, ходы идут без защиты."
			),
		)

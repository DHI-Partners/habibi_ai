"""get_working_hours и предупреждение для расчёта заказа.

Сайт не нужен: справочник подменяется через load_hours. Главный случай —
не настроено: бот не должен назвать часы, которых нет.
"""

import unittest
from datetime import datetime
from unittest.mock import patch

from habibi_ai import schedule as s
from habibi_ai import tools
from habibi_ai.tools import hours

WEEK = [{"weekday": s.WEEKDAYS[d], "kind": s.KIND_WORK, "opens": "09:00", "closes": "21:00"} for d in range(7)] + [
	{"weekday": s.WEEKDAYS[d], "kind": s.KIND_DELIVERY, "opens": "10:00", "closes": "23:00"} for d in range(7)
]


class TestGetWorkingHours(unittest.TestCase):
	def test_не_настроено_бот_не_называет_часов(self):
		with patch.object(hours, "load_hours", return_value=None):
			result = tools.execute("get_working_hours", {})
		self.assertIn("не настроен", result)
		self.assertIn("Не называй", result)

	def test_настроено_отдаёт_состояние_и_неделю(self):
		with (
			patch.object(hours, "load_hours", return_value=(WEEK, [], "Asia/Almaty")),
			patch.object(hours, "local_now", return_value=datetime(2026, 9, 21, 12, 0)),
		):
			result = tools.execute("get_working_hours", {})
		self.assertIn("открыто до 21:00", result)
		self.assertIn("доставка работает до 23:00", result)


class TestПредупреждение(unittest.TestCase):
	def _warning(self, now, fulfilment):
		with (
			patch.object(hours, "load_hours", return_value=(WEEK, [], "Asia/Almaty")),
			patch.object(hours, "local_now", return_value=now),
		):
			return hours.closed_warning(fulfilment)

	def test_в_рабочее_время_предупреждения_нет(self):
		self.assertIsNone(self._warning(datetime(2026, 9, 21, 12, 0), "delivery"))

	def test_доставка_ночью_предупреждает(self):
		warning = self._warning(datetime(2026, 9, 21, 23, 30), "delivery")
		self.assertIn("доставка не работает", warning)

	def test_самовывоз_смотрит_часы_работы_а_не_доставки(self):
		# 22:00 — доставка ещё работает, заведение уже закрыто
		self.assertIn("закрыто", self._warning(datetime(2026, 9, 21, 22, 0), "pickup"))

	def test_без_справочника_предупреждения_нет(self):
		with patch.object(hours, "load_hours", return_value=None):
			self.assertIsNone(hours.closed_warning("delivery"))

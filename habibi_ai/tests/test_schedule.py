"""Режим работы: открыто ли сейчас и что сказать про неделю.

Без frappe: время модели считают плохо, поэтому считает код, и проверяться
это должно за секунды — особенно полночь и дни-исключения.
"""

import unittest
from datetime import date, datetime, timedelta

from habibi_ai import schedule as s

# 2026-09-21 — понедельник
MON = date(2026, 9, 21)


def row(weekday, opens, closes, kind=s.KIND_WORK):
	return {"weekday": s.WEEKDAYS[weekday], "kind": kind, "opens": opens, "closes": closes}


WEEK = [row(d, "09:00", "21:00") for d in range(7)] + [row(d, "10:00", "23:00", s.KIND_DELIVERY) for d in range(7)]


def at(day, hh, mm=0):
	return datetime.combine(day, datetime.min.time()) + timedelta(hours=hh, minutes=mm)


class TestОткрытоЛи(unittest.TestCase):
	def test_внутри_интервала_открыто(self):
		self.assertEqual(s.open_interval(at(MON, 12), s.KIND_WORK, WEEK, [])[1], at(MON, 21))

	def test_граница_закрытия_уже_закрыто(self):
		self.assertIsNone(s.open_interval(at(MON, 21), s.KIND_WORK, WEEK, []))

	def test_работа_через_полночь_открыто_после_полуночи(self):
		# Пятница 18:00–02:00: в субботу в 01:00 ещё открыто — по вчерашней строке
		night = [row(4, "18:00", "02:00")]
		saturday = MON + timedelta(days=5)
		self.assertIsNotNone(s.open_interval(at(saturday, 1), s.KIND_WORK, night, []))
		self.assertIsNone(s.open_interval(at(saturday, 3), s.KIND_WORK, night, []))

	def test_исключение_закрыто_перекрывает_расписание(self):
		exc = [{"date": MON, "closed": 1, "opens": None, "closes": None, "note": "Праздник"}]
		self.assertIsNone(s.open_interval(at(MON, 12), s.KIND_WORK, WEEK, exc))

	def test_особые_часы_действуют_на_работу_и_доставку(self):
		exc = [{"date": MON, "closed": 0, "opens": "12:00", "closes": "16:00", "note": None}]
		self.assertIsNone(s.open_interval(at(MON, 10), s.KIND_WORK, WEEK, exc))
		self.assertEqual(s.open_interval(at(MON, 13), s.KIND_DELIVERY, WEEK, exc)[1], at(MON, 16))

	def test_время_из_базы_приходит_timedelta(self):
		# Frappe отдаёт поле Time как timedelta — это не должно ломать расчёт
		rows = [row(0, timedelta(hours=9), timedelta(hours=21))]
		self.assertIsNotNone(s.open_interval(at(MON, 10), s.KIND_WORK, rows, []))


class TestТекст(unittest.TestCase):
	def test_закрыто_говорит_когда_откроется(self):
		self.assertIn("откроется сегодня в 09:00", s.status_line(at(MON, 7), s.KIND_WORK, WEEK, []))

	def test_открыто_говорит_до_скольки(self):
		self.assertIn("открыто до 21:00", s.status_line(at(MON, 12), s.KIND_WORK, WEEK, []))

	def test_неделя_начинается_с_сегодня_и_содержит_исключение(self):
		exc = [{"date": MON + timedelta(days=1), "closed": 1, "opens": None, "closes": None, "note": "Праздник"}]
		text = s.describe(at(MON, 12), WEEK, exc)
		lines = [line for line in text.splitlines() if line.startswith("- ")]
		self.assertEqual(len(lines), 7)
		self.assertTrue(lines[0].startswith("- Пн 21.09"))
		self.assertIn("выходной (Праздник)", lines[1])
		self.assertIn("доставка 10:00–23:00", lines[0])

	def test_без_строк_доставки_про_доставку_не_говорит(self):
		work_only = [row(d, "09:00", "21:00") for d in range(7)]
		self.assertNotIn("доставка", s.describe(at(MON, 12), work_only, []).lower())

	def test_неделя_без_работы_говорит_словами(self):
		self.assertIn("в ближайшие 7 дней", s.status_line(at(MON, 12), s.KIND_WORK, [], []))

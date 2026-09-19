"""Режим работы заведения: открыто ли сейчас и что сказать про неделю.

Без frappe, как loop.py и decisions.py: время модели считают плохо — путают
полночь, день недели, «через час». Поэтому «открыто ли» считает код, а
модель пересказывает готовую фразу. И проверяется это за секунды.

now везде — наивное время в поясе заведения; перевод из пояса сайта делает
вызывающий (tools/hours.py).
"""

from datetime import date, datetime, time, timedelta

KIND_WORK = "Работа"
KIND_DELIVERY = "Доставка"

WEEKDAYS = ("Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье")
SHORT = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")

# Сколько дней вперёд ищем ближайшее открытие и сколько показываем в неделе
HORIZON_DAYS = 7


def _to_time(value):
	"""Время из поля Time: Frappe отдаёт timedelta, форма — строку, тесты — что удобно."""
	if isinstance(value, time):
		return value
	if isinstance(value, timedelta):
		seconds = int(value.total_seconds()) % 86400
		return time(seconds // 3600, seconds % 3600 // 60)
	hh, mm, *_ = str(value).split(":")
	return time(int(hh), int(mm))


def _interval(day, opens, closes):
	start = datetime.combine(day, _to_time(opens))
	end = datetime.combine(day, _to_time(closes))
	# Закрытие раньше открытия — работа через полночь: 18:00–02:00
	if end <= start:
		end += timedelta(days=1)
	return start, end


def _exception(day, exceptions):
	return next((e for e in exceptions if e["date"] == day), None)


def has_kind(schedule, kind):
	return any(r["kind"] == kind for r in schedule)


def intervals_for(day, kind, schedule, exceptions):
	"""Интервалы вида kind, которые начинаются в дату day.

	Особые часы исключения действуют и на работу, и на доставку: в
	сокращённый день доставка не может работать дольше самого заведения.
	"""
	exc = _exception(day, exceptions)
	if exc:
		if exc.get("closed"):
			return []
		if exc.get("opens") and exc.get("closes"):
			return [_interval(day, exc["opens"], exc["closes"])] if has_kind(schedule, kind) else []
	weekday = WEEKDAYS[day.weekday()]
	return sorted(
		_interval(day, r["opens"], r["closes"]) for r in schedule if r["weekday"] == weekday and r["kind"] == kind
	)


def open_interval(now, kind, schedule, exceptions):
	"""Интервал, внутри которого now, или None.

	Смотрим и вчерашний день: интервал пятницы 18:00–02:00 в субботу в 01:00
	ещё идёт, а в субботних строках его нет.
	"""
	for day in (now.date() - timedelta(days=1), now.date()):
		for start, end in intervals_for(day, kind, schedule, exceptions):
			if start <= now < end:
				return start, end
	return None


def _next_opening(now, kind, schedule, exceptions):
	for offset in range(HORIZON_DAYS + 1):
		for start, _ in intervals_for(now.date() + timedelta(days=offset), kind, schedule, exceptions):
			if start > now:
				return start
	return None


def status_line(now, kind, schedule, exceptions):
	"""Фраза о текущем состоянии: «открыто до 21:00», «закрыто, откроется завтра…»."""
	is_work = kind == KIND_WORK
	current = open_interval(now, kind, schedule, exceptions)
	if current:
		return f"{'открыто' if is_work else 'доставка работает'} до {current[1]:%H:%M}"

	subject = "закрыто" if is_work else "доставка не работает"
	verb = "откроется" if is_work else "начнётся"
	start = _next_opening(now, kind, schedule, exceptions)
	if start is None:
		return f"{subject}, в ближайшие {HORIZON_DAYS} дней не {'открываемся' if is_work else 'работает'}"
	if start.date() == now.date():
		when = f"сегодня в {start:%H:%M}"
	elif start.date() == now.date() + timedelta(days=1):
		when = f"завтра в {start:%H:%M}"
	else:
		when = f"{SHORT[start.weekday()]} {start:%d.%m} в {start:%H:%M}"
	return f"{subject}, {verb} {when}"


def describe(now, schedule, exceptions, days=HORIZON_DAYS):
	"""Ответ get_working_hours: состояние сейчас и расписание на неделю с сегодня."""
	kinds = [KIND_WORK] + ([KIND_DELIVERY] if has_kind(schedule, KIND_DELIVERY) else [])
	head = "; ".join(status_line(now, kind, schedule, exceptions) for kind in kinds)
	lines = [f"Сейчас {SHORT[now.weekday()]} {now:%d.%m %H:%M}: {head}.", "Расписание:"]

	for offset in range(days):
		day = now.date() + timedelta(days=offset)
		parts = []
		for kind in kinds:
			spans = intervals_for(day, kind, schedule, exceptions)
			if spans:
				parts.append(f"{kind.lower()} " + ", ".join(f"{a:%H:%M}–{b:%H:%M}" for a, b in spans))
		text = "; ".join(parts) or "выходной"
		exc = _exception(day, exceptions)
		if exc and exc.get("note"):
			text += f" ({exc['note']})"
		lines.append(f"- {SHORT[day.weekday()]} {day:%d.%m}: {text}")

	return "\n".join(lines)

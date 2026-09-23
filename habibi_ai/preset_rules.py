"""Слияние пресета вертикали с тем, что уже настроено на сайте. Без frappe.

Пресет можно применять повторно — после обновления кода или по ошибке.
Поэтому он ничего не удаляет и не перетирает то, что написал владелец.
"""


def merge_sections(current, preset):
	by_key = {s["key"]: s for s in current}
	result = [{**by_key.get(s["key"], {}), **s} for s in preset]
	keys = {s["key"] for s in preset}
	return result + [s for s in current if s["key"] not in keys]


def merge_rules(current, preset):
	by_title = {r["title"]: r for r in current}
	result = [
		{"title": r["title"], "hint": r.get("hint", ""), "text": (by_title.get(r["title"]) or {}).get("text", "")}
		for r in preset
	]
	titles = {r["title"] for r in preset}
	return result + [r for r in current if r["title"] not in titles]

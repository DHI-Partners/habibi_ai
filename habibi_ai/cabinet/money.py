"""Сумма для людей: «4 670 ₸». Одна на экран заказа, список и сообщение клиенту.

Без frappe: только строка. Разряды — неразрывными пробелами, как Intl ru-RU
на фронте кабинета, иначе владелец и клиент видели бы одну сумму по-разному.
"""


def money(value, symbol):
	"""3870 → «3 870 ₸», 12.5 → «12,50 ₸»."""
	value = float(value or 0)
	text = f"{value:,.0f}" if value == int(value) else f"{value:,.2f}"
	return text.replace(",", " ").replace(".", ",") + " " + symbol

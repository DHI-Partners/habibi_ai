"""Приём заказа: расчёт, который слышит клиент, и заказ ровно по нему.

Модель ведёт разговор, но цифр не называет и заказ не собирает: состав
сверяется с каталогом, итог и налог считает ERPNext, клиент берётся из чата
или телефона. create_order получает только номер расчёта — в ERP уходит то,
что зачитали клиенту, — и не срабатывает в том же ходе, что расчёт.
"""

import frappe
from frappe.utils import add_to_date, now_datetime, nowdate, strip_html

from habibi_ai import customers
from habibi_ai import order_rules as rules
from habibi_ai.habibi_ai.doctype.habibi_ai_settings.habibi_ai_settings import get_company
from habibi_ai.order_rules import Refusal
from habibi_ai.tools import tool
from habibi_ai.tools.hours import closed_warning
from habibi_ai.tools.menu import sellable_catalog

QUOTE = "AI Order Quote"
ZONE_DOCTYPE = "Delivery Zone"
FULFILMENT = {"delivery": "Delivery", "pickup": "Pickup"}
# Источник заказа по типу канального чата. Консоли отладки здесь нет
# намеренно: заказ из неё — проверка, а не заказ из канала.
SOURCE_BY_CHANNEL = {"Telegram Chat": "Telegram"}


def load_settings():
	company = get_company()
	if not company:
		raise Refusal(
			"Приём заказов в этой системе не настроен. Не оформляй заказ, предложи связаться с оператором."
		)
	price_list = frappe.db.get_single_value("Selling Settings", "selling_price_list")
	if not price_list:
		raise Refusal("Прайс-лист продаж не настроен — оформить заказ нельзя. Предложи оператора.")

	currency = frappe.db.get_value("Price List", price_list, "currency")
	company_currency = frappe.db.get_value("Company", company, "default_currency")
	if currency != company_currency:
		raise Refusal(
			f"Валюта прайс-листа «{price_list}» ({currency}) не совпадает с валютой компании "
			f"«{company}» ({company_currency}). Приём заказов настроен неверно — предложи оператора."
		)

	tax = frappe.db.get_value(
		"Sales Taxes and Charges Template", {"company": company, "is_default": 1, "disabled": 0}, "name"
	)
	return frappe._dict(company=company, price_list=price_list, currency=currency, tax=tax)


def build_sales_order(settings, lines, customer=None):
	"""Sales Order с посчитанными налогами и итогом — ещё не сохранённый.

	Один и тот же сборщик для расчёта и для заказа: иначе клиенту зачитали бы
	одну сумму, а в ERP легла бы другая. Цены — из строк (каталога или
	расчёта); ignore_pricing_rule — потому что расчёт правил цен не видит, и
	скидка, применённая только в заказе, разошлась бы с услышанным итогом.
	"""
	from erpnext.controllers.accounts_controller import get_taxes_and_charges

	today = nowdate()
	so = frappe.new_doc("Sales Order")
	so.update(
		{
			"company": settings.company,
			"customer": customer,
			"currency": settings.currency,
			"conversion_rate": 1,
			"selling_price_list": settings.price_list,
			"price_list_currency": settings.currency,
			"plc_conversion_rate": 1,
			"ignore_pricing_rule": 1,
			"transaction_date": today,
			"delivery_date": today,
			"taxes_and_charges": settings.tax,
		}
	)
	for line in lines:
		so.append(
			"items",
			{
				"item_code": line["item_code"],
				"item_name": line["item_name"],
				"qty": line["qty"],
				"rate": line["rate"],
				"price_list_rate": line["rate"],
				"uom": line["uom"],
				"stock_uom": line["uom"],
				"conversion_factor": 1,
				"delivery_date": today,
			},
		)
	if settings.tax:
		so.set("taxes", get_taxes_and_charges("Sales Taxes and Charges Template", settings.tax))
	so.calculate_taxes_and_totals()
	return so


def payable(so):
	"""К оплате: округлённый итог, если округление на сайте включено."""
	return float(so.rounded_total or so.grand_total)


def taxes_of(so):
	return [{"description": t.description, "amount": float(t.tax_amount or 0)} for t in so.taxes]


def rows_of(so):
	return [
		{"item_code": i.item_code, "item_name": i.item_name, "qty": i.qty, "amount": float(i.amount)}
		for i in so.items
	]


def _customer_line(name, phone):
	return f"Клиент: {name}, {phone}" if phone else f"Клиент: {name}"


def _delivery(zone, goods_total):
	if not frappe.db.exists("DocType", ZONE_DOCTYPE):
		raise Refusal("Доставка в этой системе не настроена. Предложи самовывоз или оператора.")
	zones = frappe.get_all(
		ZONE_DOCTYPE,
		filters={"is_active": 1},
		fields=["name", "delivery_fee", "free_above"],
		order_by="delivery_fee",
		limit_page_length=0,
	)
	matched = rules.match_zone(zone, zones)
	uom = frappe.db.get_value("Item", rules.DELIVERY_ITEM, "stock_uom")
	if not uom:
		raise Refusal("Позиция доставки не заведена — доставку оформить нельзя. Предложи оператора.")
	return rules.delivery_line(matched, goods_total, uom), matched["name"]


def _who(context, customer_name, phone):
	"""(Customer | None, имя, телефон) — из привязки чата или от клиента."""
	linked = customers.linked_customer(context.get("channel_chat"))
	if linked:
		name, mobile = frappe.db.get_value("Customer", linked, ["customer_name", "mobile_no"])
		return linked, name, rules.normalize_phone(mobile) or mobile

	name = str(customer_name or "").strip()
	if not name or not phone:
		raise Refusal("Для заказа нужны имя и телефон клиента. Спроси их и повтори quote_order.")
	normalized = rules.normalize_phone(phone)
	if not normalized:
		raise Refusal(f"«{phone}» не похоже на номер телефона. Переспроси номер вместе с кодом страны.")
	return None, name, normalized


QUOTE_SCHEMA = {
	"type": "object",
	"properties": {
		"items": {
			"type": "array",
			"description": "Весь заказ целиком, а не добавка к прошлому расчёту.",
			"items": {
				"type": "object",
				"properties": {
					"item_code": {"type": "string", "description": "Код или название позиции из get_menu"},
					"qty": {"type": "integer", "minimum": 1, "maximum": rules.MAX_QTY},
				},
				"required": ["item_code", "qty"],
			},
		},
		"fulfilment": {"type": "string", "enum": ["delivery", "pickup"]},
		"zone": {"type": "string", "description": "Зона доставки из get_delivery_zones; для самовывоза не нужна"},
		"customer_name": {"type": "string", "description": "Имя клиента, если система его ещё не знает"},
		"phone": {"type": "string", "description": "Телефон клиента, если система его ещё не знает"},
		"notes": {"type": "string", "description": "Пожелания для кухни, если клиент их назвал"},
	},
	"required": ["items", "fulfilment"],
}


@tool(
	name="quote_order",
	description=(
		"Расчёт заказа: сверяет состав с меню, считает доставку, налог и итог в ERP. "
		"Вызывай, когда клиент назвал, что хочет, и способ получения, — до того как "
		"называть итог. Ответ зачитай клиенту целиком и спроси, оформлять ли. "
		"Если система не знает клиента, ответ попросит имя и телефон."
	),
	input_schema=QUOTE_SCHEMA,
	context=True,
)
def quote_order(context, items=None, fulfilment=None, zone=None, customer_name=None, phone=None, notes=None):
	try:
		return _quote(context, items, fulfilment, zone, customer_name, phone, notes)
	except Refusal as e:
		return str(e)


def _quote(context, items, fulfilment, zone, customer_name, phone, notes):
	settings = load_settings()
	catalog = sellable_catalog(settings.price_list)
	if not catalog:
		raise Refusal("В меню нет позиций с действующими ценами — оформить заказ нельзя. Предложи оператора.")
	if fulfilment not in FULFILMENT:
		raise Refusal("Уточни у клиента: доставка или самовывоз? fulfilment — delivery или pickup.")

	lines = rules.resolve_lines(items, catalog)
	zone_name = None
	if fulfilment == "delivery":
		delivery, zone_name = _delivery(zone, sum(line["qty"] * line["rate"] for line in lines))
		lines.append(delivery)

	customer, name, phone = _who(context, customer_name, phone)
	so = build_sales_order(settings, lines, customer)
	channel = context.get("channel_chat") or (None, None)
	notes = str(notes or "").strip() or None

	quote = frappe.get_doc(
		{
			"doctype": QUOTE,
			"engine_chat_id": context.get("engine_chat_id"),
			"channel_doctype": channel[0],
			"channel_name": channel[1],
			"turn_id": context.get("turn_id"),
			"customer": customer,
			"customer_name": name,
			"phone": phone,
			"fulfilment": FULFILMENT[fulfilment],
			"delivery_zone": zone_name,
			"notes": notes,
			"items": [
				{
					"item_code": i.item_code,
					"item_name": i.item_name,
					"qty": i.qty,
					"uom": i.uom,
					"rate": i.rate,
					"amount": i.amount,
				}
				for i in so.items
			],
			"currency": settings.currency,
			"total_taxes": so.total_taxes_and_charges,
			"grand_total": payable(so),
			"expires_on": add_to_date(now_datetime(), minutes=int(rules.QUOTE_TTL.total_seconds() // 60)),
		}
	).insert(ignore_permissions=True)

	parts = [
		f"Расчёт {quote.name}, действует {int(rules.QUOTE_TTL.total_seconds() // 60)} минут.",
		_customer_line(name, phone),
		*rules.item_lines(rows_of(so), settings.currency),
		rules.total_line(payable(so), taxes_of(so), settings.currency),
	]
	if notes:
		parts.append(f"Пожелания: {notes}")
	warning = closed_warning(fulfilment)
	if warning:
		parts.append(warning)
	parts.append(
		"Зачитай клиенту состав и итог и спроси, оформлять ли. После его явного согласия вызови "
		f"create_order с quote_id «{quote.name}». Если клиент что-то меняет — сделай новый quote_order."
	)
	return "\n".join(parts)


@tool(
	name="create_order",
	description=(
		"Оформляет заказ по расчёту quote_order — черновиком, который подтвердит оператор. "
		"Вызывай только после явного согласия клиента на зачитанный расчёт. "
		"Говори «заказ оформлен» только пересказом ответа этого инструмента."
	),
	input_schema={
		"type": "object",
		"properties": {"quote_id": {"type": "string", "description": "Номер расчёта из ответа quote_order"}},
		"required": ["quote_id"],
	},
	context=True,
)
def create_order(context, quote_id=None):
	try:
		return _create(context, quote_id)
	except Refusal as e:
		return str(e)


def _create(context, quote_id):
	quote = frappe.get_doc(QUOTE, quote_id) if quote_id and frappe.db.exists(QUOTE, quote_id) else None
	verdict = rules.check_quote(quote.as_dict() if quote else None, context, now_datetime())
	if verdict == "done":
		return _order_text(frappe.get_doc("Sales Order", quote.sales_order), quote, repeated=True)

	settings = load_settings()
	# Клиент, привязка чата и заказ — одно целое: упал заказ — не должно
	# остаться ни нового клиента, ни привязки к нему
	frappe.db.savepoint("create_order")
	try:
		customer = quote.customer or customers.find_by_phone(quote.phone) or customers.create(
			quote.customer_name, quote.phone
		)
		if quote.channel_doctype and quote.channel_name:
			customers.link_chat((quote.channel_doctype, quote.channel_name), customer)
		lines = [
			{"item_code": r.item_code, "item_name": r.item_name, "qty": r.qty, "rate": r.rate, "uom": r.uom}
			for r in quote.items
		]
		so = build_sales_order(settings, lines, customer)
		_set_optional_fields(so, quote)
		so.insert(ignore_permissions=True)
		quote.db_set("sales_order", so.name)
	except Exception as e:
		frappe.db.rollback(save_point="create_order")
		frappe.log_error(title="create_order", message=frappe.get_traceback())
		raise Refusal(
			f"Не удалось оформить заказ: {strip_html(str(e))[:200]}. Не говори клиенту, что заказ создан; "
			"предложи связаться с оператором."
		) from e

	text = _order_text(so, quote)
	if abs(payable(so) - float(quote.grand_total)) >= 0.01:
		text += (
			f"\nВнимание: итог изменился — в расчёте было {rules.money(quote.grand_total)}, в заказе "
			f"{rules.money(payable(so))} {so.currency}. Назови клиенту новую сумму."
		)
	return text


def _set_optional_fields(so, quote):
	"""Поля, заведённые руками на конкретном сайте: ставим только существующие.

	custom_* на erp.habibi-erp.com есть, на naqwa их нет — там заказ должен
	создаваться так же, просто без этих пометок.
	"""
	meta = frappe.get_meta("Sales Order")
	values = {
		"custom_fulfilment_type": quote.fulfilment,
		"custom_agent_handled": 1,
		"custom_whatsapp_number": quote.phone,
		"custom_kitchen_notes": quote.notes,
	}
	if quote.delivery_zone and frappe.db.exists("DocType", ZONE_DOCTYPE) and frappe.db.exists(
		ZONE_DOCTYPE, quote.delivery_zone
	):
		values["custom_delivery_zone"] = quote.delivery_zone

	source = SOURCE_BY_CHANNEL.get(quote.channel_doctype)
	field = meta.get_field("custom_order_source")
	if source and field and source in (field.options or "").split("\n"):
		values["custom_order_source"] = source

	for fieldname, value in values.items():
		if value and meta.has_field(fieldname):
			so.set(fieldname, value)


def _order_text(so, quote, repeated=False):
	head = (
		f"Заказ {so.name} уже создан по этому расчёту — второй не создавался."
		if repeated
		else f"Заказ {so.name} создан — черновик, ждёт подтверждения оператора."
	)
	return "\n".join(
		[
			head,
			_customer_line(quote.customer_name, quote.phone),
			*rules.item_lines(rows_of(so), so.currency),
			rules.total_line(payable(so), taxes_of(so), so.currency),
			*([f"Пожелания: {quote.notes}"] if quote.notes else []),
			"Сообщи клиенту номер заказа и что оператор его подтвердит. Не обещай, что заказ уже готовят.",
		]
	)

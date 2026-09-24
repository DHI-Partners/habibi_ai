"""Действия владельца над заказом бота и уведомление клиента в Telegram."""

from unittest.mock import patch

import frappe
from frappe.permissions import add_permission, update_permission_property
from frappe.tests import IntegrationTestCase

from habibi_ai import presets
from habibi_ai.cabinet import orders
from habibi_ai.cabinet.adapters import order_status, order_total

# Хелперы готового заказа — те же, что в test_orders.py (там уже есть
# сборка тестовой компании, прайс-листа и позиций). Импортируем, а не копируем.
from habibi_ai.tests.test_orders import OrderFixtures

NO_ACCESS_ROLE = "_Habibi Cabinet No Access"
NO_DELETE_ROLE = "_Habibi Cabinet No Delete"


def _ensure_role(role_name):
	if not frappe.db.exists("Role", role_name):
		frappe.get_doc({"doctype": "Role", "role_name": role_name, "desk_access": 0}).insert(
			ignore_permissions=True
		)


def _ensure_user(email, role_name):
	if not frappe.db.exists("User", email):
		user = frappe.get_doc(
			{"doctype": "User", "email": email, "first_name": role_name, "send_welcome_email": 0}
		).insert(ignore_permissions=True)
		user.add_roles(role_name)
	return email


class TestCabinetOrders(OrderFixtures, IntegrationTestCase):
	def setUp(self):
		super().setUp()
		self.so = self.make_bot_order()  # черновик SO + AI Order Quote с channel_doctype="Telegram Chat"
		# Явно перезаписываем, а не только заводим при отсутствии: на сайте с
		# применённым пресетом вертикали (presets.apply) шаблон уже есть — со
		# своим текстом. Тест держит собственный простой текст, а откат
		# транзакции после теста возвращает шаблон пресета.
		for key, text in (("order_accepted", "Заказ {order} принят"), ("order_rejected", "Не сможем: {reason}")):
			if frappe.db.exists("Telegram Message Template", key):
				frappe.db.set_value("Telegram Message Template", key, "default_template", text)
			else:
				frappe.get_doc({"doctype": "Telegram Message Template", "template_name": key, "default_template": text}).insert()

	def tearDown(self):
		frappe.set_user("Administrator")
		super().tearDown()

	def _no_access_user(self):
		"""Роль без единой записи прав на Sales Order — доступа нет вовсе."""
		_ensure_role(NO_ACCESS_ROLE)
		return _ensure_user("cabinet-no-access-test@example.com", NO_ACCESS_ROLE)

	def _no_delete_user(self):
		"""Роль с чтением/записью/проведением, но без права удалять Sales Order.

		add_permission при первом добавлении новой роли копирует существующие
		права (Sales User и т.п.) в Custom DocPerm — они не теряются."""
		_ensure_role(NO_DELETE_ROLE)
		add_permission("Sales Order", NO_DELETE_ROLE, 0, "read")
		update_permission_property("Sales Order", NO_DELETE_ROLE, 0, "write", 1)
		update_permission_property("Sales Order", NO_DELETE_ROLE, 0, "submit", 1)
		frappe.clear_cache(doctype="Sales Order")
		self.addCleanup(frappe.clear_cache, doctype="Sales Order")
		return _ensure_user("cabinet-no-delete-test@example.com", NO_DELETE_ROLE)

	def test_без_воркфлоу_принять_проводит(self):
		with patch("habibi_ai.cabinet.orders._workflow", return_value=None):
			self.assertEqual([a["kind"] for a in orders.actions(self.so.name)["actions"]], ["accept", "reject"])
			result = orders.apply(self.so.name, "submit")
		self.assertEqual(frappe.db.get_value("Sales Order", self.so.name, "docstatus"), 1)
		self.assertEqual(result["notify"]["text"], f"Заказ №{orders._number(self.so.name)} принят")
		self.assertNotIn("chat", result["notify"])

	def test_уведомление_с_коротким_номером_и_суммой_как_на_экране(self):
		"""Клиенту — тот же номер и та же сумма, что владелец видит на экране:
		«№18» и «3 870 ₸» (итог к оплате, разряды ru), а не имя документа и
		fmt_money сайта."""
		frappe.db.set_value("Telegram Message Template", "order_accepted", "default_template", "{order}: {total}")
		symbol = frappe.db.get_value("Currency", "KZT", "symbol") or "KZT"
		with patch("habibi_ai.cabinet.orders._workflow", return_value=None):
			result = orders.apply(self.so.name, "submit")
		self.assertEqual(
			result["notify"]["text"], f"№{orders._number(self.so.name)}: 3\u00a0870\u00a0{symbol}"
		)

	def test_без_воркфлоу_отклонить_удаляет_черновик(self):
		with patch("habibi_ai.cabinet.orders._workflow", return_value=None):
			result = orders.apply(self.so.name, "discard")
		self.assertFalse(frappe.db.exists("Sales Order", self.so.name))
		self.assertEqual(result["notify"]["kind"], "reject")

	def test_уведомление_уходит_в_чат_заказа(self):
		with patch("habibi_ai.cabinet.orders.telegram.send") as send:
			result = orders.notify(self.so.name, "Заказ принят")
		self.assertEqual(result, {"sent": True, "error": None})
		_channel, chat, text = send.call_args.args
		self.assertEqual(chat, self.quote_chat)
		self.assertEqual(text, "Заказ принят")

	def test_сорвавшаяся_отправка_не_откатывает_заказ(self):
		with patch("habibi_ai.cabinet.orders._workflow", return_value=None):
			orders.apply(self.so.name, "submit")
		with patch("habibi_ai.cabinet.orders.telegram.send", side_effect=Exception("403 Forbidden")):
			result = orders.notify(self.so.name, "Заказ принят")
		self.assertEqual(result["sent"], False)
		# Сырой текст исключения (сетевой) — не в ответе: только общая фраза
		self.assertNotIn("403", result["error"])
		self.assertIn("недоступен", result["error"])
		self.assertEqual(frappe.db.get_value("Sales Order", self.so.name, "docstatus"), 1)
		comments = frappe.get_all("Comment", filters={"reference_name": self.so.name}, pluck="content")
		self.assertTrue(any("не уведомлён" in c for c in comments))

	def test_ошибка_отправки_не_показывает_токен_бота(self):
		# requests кладёт в текст исключения полный URL с токеном — вида
		# https://api.telegram.org/bot123:SECRET/sendMessage
		leaking = "HTTPSConnectionPool: Max retries exceeded /bot123:SECRET/sendMessage"
		with patch("habibi_ai.cabinet.orders.telegram.send", side_effect=Exception(leaking)):
			result = orders.notify(self.so.name, "Заказ принят")
		self.assertFalse(result["sent"])
		self.assertNotIn("SECRET", result["error"])
		comments = frappe.get_all("Comment", filters={"reference_name": self.so.name}, pluck="content")
		self.assertTrue(comments)
		self.assertTrue(all("SECRET" not in c for c in comments))

	def test_сообщение_telegram_в_message_log_не_утекает(self):
		"""habibi_telegram (user_client.send_message) сам делает
		frappe.throw(describe_error(e)) на сбое — оно уже лежит в message_log
		и утекло бы в ответ вместе с обычным {"sent": False, ...}, если его не
		вычистить (задача 17/19)."""

		def _leaking_send(channel, chat, text):
			try:
				raise RuntimeError("phone_code_hash=hash-secret")
			except RuntimeError as e:
				frappe.throw(f"Telegram did not accept the message: {e}")

		frappe.local.message_log = []
		with (
			patch("habibi_ai.cabinet.orders.telegram.send", _leaking_send),
			patch("frappe.log_error"),
		):
			result = orders.notify(self.so.name, "Заказ принят")
		self.assertFalse(result["sent"])
		self.assertNotIn("hash-secret", result["error"])
		self.assertNotIn("hash-secret", frappe.as_json(frappe.local.message_log))

	def test_заказ_без_чата_не_предлагает_уведомление(self):
		frappe.db.set_value("AI Order Quote", {"sales_order": self.so.name}, "sales_order", None)
		self.assertFalse(orders.actions(self.so.name)["can_notify"])
		with patch("habibi_ai.cabinet.orders._workflow", return_value=None):
			self.assertIsNone(orders.apply(self.so.name, "submit")["notify"])

	def test_уведомление_после_удаления_черновика(self):
		# Ссылка AI Order Quote.sales_order переживает discard (hooks.py:
		# ignore_links_on_delete) — notify() находит чат по имени и без doc'а
		with patch("habibi_ai.cabinet.orders._workflow", return_value=None):
			result = orders.apply(self.so.name, "discard", reason="закончилась булка")
		self.assertIn("закончилась булка", result["notify"]["text"])
		self.assertFalse(frappe.db.exists("Sales Order", self.so.name))
		with patch("habibi_ai.cabinet.orders.telegram.send") as send:
			sent = orders.notify(self.so.name, result["notify"]["text"])
		self.assertTrue(sent["sent"])
		self.assertEqual(send.call_args.args[1], self.quote_chat)

	def test_недоступное_действие_отклоняется(self):
		with patch("habibi_ai.cabinet.orders._workflow", return_value=None):
			with self.assertRaises(frappe.ValidationError):
				orders.apply(self.so.name, "launch_rocket")

	def test_воркфлоу_без_ветки_отклонить_предлагает_удаление_черновика(self):
		"""Прод-воркфлоу («Habibi Burger Order»): из New выхода нет, кроме
		«Confirm». Владельцу всё равно нужен способ закрыть черновик."""
		with (
			patch("habibi_ai.cabinet.orders._workflow", return_value="Habibi Burger Order"),
			patch(
				"habibi_ai.cabinet.orders.get_transitions",
				return_value=[{"action": "Confirm", "state": "New", "next_state": "Confirmed"}],
			),
		):
			self.assertEqual(
				sorted(a["kind"] for a in orders.actions(self.so.name)["actions"]), ["accept", "reject"]
			)
			result = orders.apply(self.so.name, "discard")
		self.assertFalse(frappe.db.exists("Sales Order", self.so.name))
		self.assertEqual(result["notify"]["kind"], "reject")

	def test_уведомление_без_доступа_к_заказу_запрещено(self):
		frappe.set_user(self._no_access_user())
		with patch("habibi_ai.cabinet.orders.telegram.send") as send:
			with self.assertRaises(frappe.PermissionError):
				orders.notify(self.so.name, "Заказ принят")
		send.assert_not_called()

	def test_уведомление_без_расчёта_запрещено(self):
		# Заказа уже нет, и ни один расчёт на него не ссылается — в отличие от
		# discard (там AI Order Quote.sales_order выживает), имя тут ничем не
		# подтверждено вовсе
		frappe.db.set_value("AI Order Quote", {"sales_order": self.so.name}, "sales_order", None)
		frappe.delete_doc("Sales Order", self.so.name, ignore_permissions=True)
		with patch("habibi_ai.cabinet.orders.telegram.send") as send:
			with self.assertRaises(frappe.PermissionError):
				orders.notify(self.so.name, "Заказ принят")
		send.assert_not_called()

	def test_без_права_удалять_отклонить_не_предлагается(self):
		frappe.set_user(self._no_delete_user())
		self.assertEqual([a["kind"] for a in orders.actions(self.so.name)["actions"]], ["accept"])

	# --- Экран заказа (details) и состояние из поля воркфлоу -----------------

	def test_детали_отдают_состав_итог_валюту_и_чат(self):
		details = orders.details(self.so.name)
		self.assertEqual(
			[(i["item_name"], i["qty"], i["rate"], i["amount"]) for i in details["items"]],
			[("Test Burger", 1, 2490, 2490), ("Test Cola", 2, 690, 1380)],
		)
		self.assertIsNone(details["delivery"])
		self.assertEqual(details["total"], 3870)
		self.assertEqual(details["currency"], "KZT")
		self.assertEqual(details["currency_symbol"], frappe.db.get_value("Currency", "KZT", "symbol") or "KZT")
		self.assertEqual(details["chat"], self.quote_chat)
		self.assertEqual(details["state_kind"], "new")
		self.assertEqual(details["customer_name"], "Тест Клиент")
		self.assertEqual(details["number"], str(int(self.so.name.rsplit("-", 1)[-1])))
		self.assertEqual(details["source"], "Telegram")
		if frappe.get_meta("Sales Order").has_field("custom_whatsapp_number"):
			self.assertEqual(details["phone"], "+77019990011")

	def test_чат_вне_кабинета_не_отдаётся(self):
		"""Групповой чат кабинет не откроет (scope.in_scope) — ссылка
		«Переписка» вела бы на «не найдено»."""
		frappe.db.set_value("Telegram Chat", self.quote_chat, "type", "group")
		self.assertIsNone(orders.details(self.so.name)["chat"])

	def test_чат_без_пары_не_отдаётся(self):
		frappe.db.delete("AI Channel Chat", {"telegram_chat": self.quote_chat})
		self.assertIsNone(orders.details(self.so.name)["chat"])

	def test_причина_не_уходит_с_принять(self):
		"""Фронт шлёт reason с любым действием — в сообщение о приёме она не попадает."""
		frappe.db.set_value(
			"Telegram Message Template", "order_accepted", "default_template", "Принят{reason}"
		)
		with patch("habibi_ai.cabinet.orders._workflow", return_value=None):
			result = orders.apply(self.so.name, "submit", reason="закончилась булка")
		self.assertEqual(result["notify"]["text"], "Принят")

	def test_доставка_в_деталях_отдельной_строкой(self):
		if not frappe.db.exists("Item", "SRV-DELIVERY"):
			frappe.get_doc(
				{
					"doctype": "Item", "item_code": "SRV-DELIVERY", "item_name": "Доставка",
					"item_group": frappe.db.get_value("Item Group", {"lft": 1}), "stock_uom": "Nos",
					"is_stock_item": 0, "is_sales_item": 1,
				}
			).insert()
		self.so.append("items", {"item_code": "SRV-DELIVERY", "item_name": "Доставка (Центр)", "qty": 1, "rate": 800})
		self.so.save()
		details = orders.details(self.so.name)
		self.assertEqual(details["delivery"], {"label": "Доставка (Центр)", "amount": 800})
		self.assertNotIn("SRV-DELIVERY", [i["item_name"] for i in details["items"]])
		self.assertEqual(len(details["items"]), 2)
		self.assertEqual(details["total"], 4670)

	def test_детали_без_доступа_запрещены(self):
		frappe.set_user(self._no_access_user())
		with self.assertRaises(frappe.PermissionError):
			orders.details(self.so.name)

	def _workflow_in_db(self, state_field="custom_order_status"):
		"""Воркфлоу как на проде: состояние в custom_order_status.

		db_insert, а не insert: Workflow.on_update завёл бы на Sales Order
		Custom Field под поле состояния — это ALTER TABLE, неявный commit, и
		откат теста его бы не убрал. Имя воркфлоу подставляем патчем
		_workflow — get_workflow_name кэширует в Redis, отката там нет."""
		name = "_Habibi Test Burger Order"
		# _state_field/_targets кэшируются на запрос — между тестами кэш не
		# должен переносить поле состояния прошлого теста
		self._clear_request_cache()
		self.addCleanup(self._clear_request_cache)
		# Откат — на весь класс (IntegrationTestCase), а не на каждый тест:
		# воркфлоу мог остаться от соседнего теста
		if frappe.db.exists("Workflow", name):
			frappe.db.set_value("Workflow", name, "workflow_state_field", state_field)
			return name
		wf = frappe.get_doc(
			{
				"doctype": "Workflow", "name": name, "workflow_name": name, "document_type": "Sales Order",
				"workflow_state_field": state_field, "is_active": 0,
			}
		)
		wf.db_insert()
		for i, (state, action, next_state) in enumerate(
			(("New", "Confirm", "Confirmed"), ("Confirmed", "Cancel Order", "Cancelled"))
		):
			frappe.get_doc(
				{
					"doctype": "Workflow Transition", "parent": name, "parenttype": "Workflow",
					"parentfield": "transitions", "idx": i + 1, "state": state, "action": action,
					"next_state": next_state, "allowed": "System Manager",
				}
			).db_insert()
		return name

	@staticmethod
	def _clear_request_cache():
		if getattr(frappe.local, "request_cache", None) is not None:
			frappe.local.request_cache.clear()

	def test_состояние_читается_из_поля_воркфлоу(self):
		name = self._workflow_in_db()
		doc = self.so
		doc.workflow_state = "Не то поле"
		doc.custom_order_status = "Confirmed"
		doc.docstatus = 1
		with patch("habibi_ai.cabinet.orders._workflow", return_value=name):
			self.assertEqual(orders._state(doc), "Confirmed")
			self.assertEqual(orders._kind(doc), "accepted")
			doc.custom_order_status = "In Kitchen"
			self.assertEqual(orders._kind(doc), "other")
			doc.custom_order_status = "Cancelled"
			self.assertEqual(orders._kind(doc), "rejected")
			doc.docstatus = 0
			doc.custom_order_status = "New"
			self.assertEqual(orders._kind(doc), "new")

	def test_без_состояния_воркфлоу_действий_нет_но_экран_не_падает(self):
		"""Заказ до появления воркфлоу: get_transitions бросает
		WorkflowStateError — кабинет показывает «нет действий», а не 500."""
		from frappe.model.workflow import WorkflowStateError

		frappe.set_user(self._no_delete_user())
		with (
			patch("habibi_ai.cabinet.orders._workflow", return_value="Habibi Burger Order"),
			patch("habibi_ai.cabinet.orders.get_transitions", side_effect=WorkflowStateError),
		):
			self.assertEqual(orders.actions(self.so.name)["actions"], [])

	# --- Права пресета на проведение ------------------------------------------

	def _preset_role_user(self, role):
		"""Пользователь только с ролью кабинета и правами из пресета food."""
		presets.apply("food")
		self.addCleanup(frappe.clear_cache)
		frappe.clear_cache()
		return _ensure_user(f"cabinet-{role.split()[-1].lower()}-only@example.com", role)

	def test_владелец_проводит_заказ_бота(self):
		frappe.set_user(self._preset_role_user("Habibi Owner"))
		with patch("habibi_ai.cabinet.orders._workflow", return_value=None):
			orders.apply(self.so.name, "submit")
		self.assertEqual(frappe.db.get_value("Sales Order", self.so.name, "docstatus"), 1)

	def test_сотрудник_проводит_заказ_бота(self):
		frappe.set_user(self._preset_role_user("Habibi Staff"))
		with patch("habibi_ai.cabinet.orders._workflow", return_value=None):
			orders.apply(self.so.name, "submit")
		self.assertEqual(frappe.db.get_value("Sales Order", self.so.name, "docstatus"), 1)

	# --- Адаптеры списка заказов --------------------------------------------

	def test_статус_в_списке_без_воркфлоу_по_docstatus(self):
		with patch("habibi_ai.cabinet.orders._workflow", return_value=None):
			self.assertEqual(order_status.read([self.so.name]), {self.so.name: {"state": "Черновик", "kind": "new"}})
			orders.apply(self.so.name, "submit")
			self.assertEqual(
				order_status.read([self.so.name]), {self.so.name: {"state": "Принят", "kind": "accepted"}}
			)

	def test_статус_в_списке_из_поля_воркфлоу(self):
		# Список читает из базы, а колонки custom_order_status на dev нет —
		# поле состояния воркфлоу здесь стандартное текстовое po_no: важно
		# лишь, что читается поле из настроек воркфлоу, а не workflow_state
		name = self._workflow_in_db(state_field="po_no")
		frappe.db.set_value("Sales Order", self.so.name, "po_no", "In Kitchen", update_modified=False)
		frappe.db.set_value("Sales Order", self.so.name, "docstatus", 1, update_modified=False)
		with patch("habibi_ai.cabinet.orders._workflow", return_value=name):
			# Смысл (kind) — тот же _kind, что у экрана заказа: цвет бейджа
			# в списке и на экране один
			self.assertEqual(
				order_status.read([self.so.name]), {self.so.name: {"state": "In Kitchen", "kind": "other"}}
			)

	def test_сумма_в_списке_с_символом_валюты(self):
		symbol = frappe.db.get_value("Currency", "KZT", "symbol") or "KZT"
		self.assertEqual(order_total.read([self.so.name]), {self.so.name: f"3 870 {symbol}"})
		self.assertEqual(order_total.read([]), {})

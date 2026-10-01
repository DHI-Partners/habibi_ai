"""Очереди кухни и курьера: кто что вызывает и что получает.

Данные подменены: на dev-сайте нет прод-воркфлоу и custom-полей заказа, а
проверяются права, срез полей и логика «Взять» — не ERP.
"""

from datetime import datetime
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests import IntegrationTestCase

from habibi_ai.cabinet import fulfilment

ORDER = frappe._dict(
	name="SAL-ORD-2026-00015",
	modified=datetime(2026, 10, 1, 12, 18),
	customer_name="Динара",
	shipping_address=None,
	address_display="ул. Абая, 12<br>Алматы",
	contact_mobile="+77010001122",
	custom_kitchen_notes="без кунжута",
	custom_delivery_zone="Центр",
	custom_whatsapp_number="+77019990011",
)
ITEMS = {
	ORDER.name: [
		frappe._dict(item_code="BURGER", item_name="Бургер", qty=2.0),
		frappe._dict(item_code="SRV-DELIVERY", item_name="Доставка", qty=1.0),
	]
}
NOW = datetime(2026, 10, 1, 12, 30)


def _as(*roles):
	return patch("frappe.get_roles", return_value=list(roles))


class TestFulfilmentApi(IntegrationTestCase):
	def setUp(self):
		for target, kwargs in (
			("habibi_ai.cabinet.fulfilment._orders", {"return_value": [ORDER]}),
			("habibi_ai.cabinet.fulfilment._items", {"return_value": ITEMS}),
			("habibi_ai.cabinet.fulfilment.now_datetime", {"return_value": NOW}),
		):
			patcher = patch(target, **kwargs)
			setattr(self, target.rsplit(".", 1)[1].strip("_") + "_mock", patcher.start())
			self.addCleanup(patcher.stop)

	def test_кухня_получает_очередь_без_лишнего(self):
		with _as("Habibi Kitchen"):
			(card,) = fulfilment.kitchen_queue()
		self.assertEqual(card["age"], 12)
		self.assertEqual(card["notes"], "без кунжута")
		self.assertEqual(card["items"], [{"item_name": "Бургер", "qty": 2.0}])
		for forbidden in ("phone", "address", "customer_name", "zone"):
			self.assertNotIn(forbidden, card)
		self.orders_mock.assert_called_once_with(fulfilment.IN_KITCHEN)

	def test_курьер_не_вызывает_кухню_и_наоборот(self):
		with _as("Habibi Courier"), self.assertRaises(frappe.PermissionError):
			fulfilment.kitchen_queue()
		for method in (fulfilment.courier_mine, fulfilment.courier_free):
			with _as("Habibi Kitchen"), self.assertRaises(frappe.PermissionError):
				method()
		with _as("Habibi Kitchen"), self.assertRaises(frappe.PermissionError):
			fulfilment.courier_take("SAL-ORD-2026-00015")

	def test_владелец_с_system_manager_проходит(self):
		with _as("System Manager"):
			self.assertEqual(len(fulfilment.kitchen_queue()), 1)

	def test_свободные_без_телефона_и_состава(self):
		with _as("Habibi Courier"):
			(card,) = fulfilment.courier_free()
		self.assertNotIn("phone", card)
		self.assertNotIn("items", card)
		self.assertEqual(card["items_count"], 1)
		self.assertEqual(card["address"], "ул. Абая, 12, Алматы")
		self.orders_mock.assert_called_once_with(fulfilment.READY, unassigned=True, delivery_only=True)

	def test_мои_с_телефоном_и_только_свои(self):
		with _as("Habibi Courier"), patch.object(fulfilment, "_my_employee", return_value="HR-EMP-00001"):
			(card,) = fulfilment.courier_mine()
		self.assertEqual(card["phone"], "+77019990011")
		self.assertEqual(card["items"], [{"item_name": "Бургер", "qty": 2.0}])
		self.orders_mock.assert_called_once_with(fulfilment.OUT, courier="HR-EMP-00001")

	def test_курьер_без_employee_видит_ошибку_в_мои_но_свободные_работают(self):
		with _as("Habibi Courier"), patch.object(fulfilment, "_my_employee", return_value=None):
			with self.assertRaises(frappe.ValidationError):
				fulfilment.courier_mine()
			self.assertEqual(len(fulfilment.courier_free()), 1)


class TestWithoutWorkflow(IntegrationTestCase):
	def test_без_воркфлоу_очереди_пусты(self):
		"""Сайт без воркфлоу заказа (или без custom-полей) — это пустая
		очередь, а не 500 на экране кухни."""
		with (
			_as("Habibi Kitchen", "Habibi Courier"),
			patch.object(fulfilment, "_state_field", return_value=None),
			patch.object(fulfilment, "_my_employee", return_value="HR-EMP-00001"),
		):
			self.assertEqual(fulfilment.kitchen_queue(), [])
			self.assertEqual(fulfilment.courier_free(), [])
			self.assertEqual(fulfilment.courier_mine(), [])

	def test_без_custom_courier_очередь_курьера_пуста(self):
		with (
			_as("Habibi Courier"),
			patch.object(fulfilment, "_state_field", return_value="custom_order_status"),
			patch.object(fulfilment, "_has", return_value=False),
		):
			self.assertEqual(fulfilment._orders(fulfilment.READY, unassigned=True, delivery_only=True), [])


class TestTake(IntegrationTestCase):
	STATE = "custom_order_status"

	def _row(self, **kw):
		return frappe._dict(
			{"docstatus": 1, self.STATE: "Ready", "custom_courier": None, "custom_fulfilment_type": "Delivery", **kw}
		)

	def _take(self, row, employee="HR-EMP-00001"):
		doc = MagicMock()
		with (
			_as("Habibi Courier"),
			patch.object(fulfilment, "_state_field", return_value=self.STATE),
			patch.object(fulfilment, "_has", return_value=True),
			patch.object(fulfilment, "_my_employee", return_value=employee),
			patch("frappe.db.get_value", return_value=row) as get_value,
			patch("frappe.get_doc", return_value=doc),
			patch("frappe.db.set_value") as set_value,
			patch("habibi_ai.cabinet.fulfilment.apply_workflow") as apply,
		):
			# Порядок вызовов: apply_workflow перечитывает документ из БД
			# (doc.load_from_db), поэтому курьер должен быть записан раньше него
			calls = MagicMock()
			calls.attach_mock(set_value, "set_value")
			calls.attach_mock(apply, "apply")
			result = fulfilment.courier_take("SAL-ORD-2026-00013")
		self.calls = calls
		self.set_value = set_value
		return result, doc, apply, get_value

	def test_успех_назначает_курьера_и_диспатчит(self):
		result, doc, apply, get_value = self._take(self._row())
		self.assertEqual(result, {"taken": False})
		self.set_value.assert_called_once_with("Sales Order", "SAL-ORD-2026-00013", "custom_courier", "HR-EMP-00001")
		apply.assert_called_once_with(doc, "Dispatch")
		# Сначала курьер в БД, потом переход: условие Dispatch читает его из БД
		self.assertEqual([c[0] for c in self.calls.mock_calls], ["set_value", "apply"])
		self.assertTrue(get_value.call_args.kwargs["for_update"])

	def test_заказ_уже_взят_другим(self):
		result, _doc, apply, _ = self._take(self._row(custom_courier="HR-EMP-00002"))
		self.assertEqual(result, {"taken": True})
		apply.assert_not_called()
		self.set_value.assert_not_called()

	def test_заказ_не_в_готово(self):
		for state in ("In Kitchen", "Out for Delivery", "Cancelled"):
			with self.subTest(state):
				result, _doc, apply, _ = self._take(self._row(**{self.STATE: state}))
				self.assertEqual(result, {"taken": True})
				apply.assert_not_called()

	def test_самовывоз_брать_нельзя(self):
		result, _doc, apply, _ = self._take(self._row(custom_fulfilment_type="Pickup"))
		self.assertEqual(result, {"taken": True})
		apply.assert_not_called()

	def test_не_проведённый_заказ_брать_нельзя(self):
		result, _doc, apply, _ = self._take(self._row(docstatus=0))
		self.assertEqual(result, {"taken": True})
		apply.assert_not_called()

	def test_без_employee_понятная_ошибка(self):
		with self.assertRaises(frappe.ValidationError):
			self._take(self._row(), employee=None)

	def test_несуществующий_заказ(self):
		with self.assertRaises(frappe.DoesNotExistError):
			self._take(None)

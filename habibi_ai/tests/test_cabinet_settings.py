"""Режим работы, профиль и подключение Telegram — экраны кабинета «документ целиком»."""

from unittest.mock import MagicMock, patch

import frappe
from frappe.tests import IntegrationTestCase

from habibi_ai.cabinet import settings

CO = "_Habibi Settings Test Co"
NO_PROFILE_WRITE_ROLE = "_Habibi Cabinet No Profile Write"
ACCOUNT = "habibi_telegram.habibi_telegram.doctype.telegram_account.telegram_account.TelegramAccount"
CONF = {"telegram_api_id": "12345", "telegram_api_hash": "platform-hash-secret"}


def _ensure_company():
	"""Working Hours.company — Link на Company, реальную запись обязана
	видеть; своя компания, чтобы не зависеть от того, что заведено на сайте."""
	if not frappe.db.exists("Company", CO):
		frappe.get_doc(
			{
				"doctype": "Company",
				"company_name": CO,
				"abbr": "HBST",
				"default_currency": "KZT",
				"country": "Kazakhstan",
				"create_chart_of_accounts_based_on": "Standard Template",
				"chart_of_accounts": "Standard",
			}
		).insert()
	frappe.db.set_single_value("Habibi AI Settings", "company", CO)


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


class TestCabinetSettings(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		_ensure_company()

	def tearDown(self):
		# Компания и профиль — фикстуры класса (см. setUpClass): полный
		# frappe.db.rollback() здесь стёр бы их для всех тестов после первого,
		# кроме теста прав, который меняет пользователя и должен его вернуть.
		frappe.set_user("Administrator")

	def test_режим_работы_заменяется_целиком(self):
		settings.save_hours(
			[{"weekday": "Понедельник", "kind": "Работа", "opens": "10:00:00", "closes": "22:00:00"}], []
		)
		settings.save_hours(
			[{"weekday": "Вторник", "kind": "Работа", "opens": "11:00:00", "closes": "23:00:00"}], []
		)
		self.assertEqual([r["weekday"] for r in settings.get_hours()["schedule"]], ["Вторник"])

	def test_профиль_не_трогает_подсказки(self):
		doc = frappe.get_single("Business Profile")
		doc.rules = []
		doc.append("rules", {"title": "Доставка", "hint": "Сколько стоит?", "text": ""})
		doc.save()
		settings.save_profile(
			{
				"business_name": "Habibi",
				"rules": [{"title": "Доставка", "hint": "подмена", "text": "40 минут"}],
			}
		)
		rule = settings.get_profile()["rules"][0]
		self.assertEqual((rule["hint"], rule["text"]), ("Сколько стоит?", "40 минут"))

	def test_лишние_поля_профиля_отбрасываются(self):
		settings.save_profile({"business_name": "Habibi", "owner": "evil@x"})
		self.assertNotEqual(frappe.db.get_value("Business Profile", "Business Profile", "owner"), "evil@x")

	def test_профиль_без_права_записи_запрещён(self):
		"""Своя роль без единой записи прав на Business Profile — доступа нет вовсе."""
		_ensure_role(NO_PROFILE_WRITE_ROLE)
		frappe.set_user(_ensure_user("cabinet-settings-no-write-test@example.com", NO_PROFILE_WRITE_ROLE))
		with self.assertRaises(frappe.PermissionError):
			settings.save_profile({"business_name": "Habibi"})


# -- Telegram-аккаунт ----------------------------------------------------------
#
# Сеть Telegram в тестах недоступна: методы документа request_code / sign_in /
# log_out подменяются заглушками, которые делают то же, что user_client, —
# db_set статуса и полей личности.


def _fake_request_code(self):
	self.db_set({"status": "Code Sent", "phone_code_hash": "hash-secret"}, update_modified=False)
	return {"status": "Code Sent"}


def _fake_sign_in(self, code=None, password=None):
	self.db_set(
		{
			"status": "Connected",
			"full_name": "Habibi FSA",
			"username": "@habibi",
			"account_id": "500328598",
			"phone_code_hash": "",
		},
		update_modified=False,
	)
	return {"status": "Connected", "username": "@habibi"}


def _fake_password_needed(self, code=None, password=None):
	self.db_set("status", "Password Required", update_modified=False)
	return {"password_required": True}


def _fake_log_out(self):
	self.db_set({"status": "Disconnected", "phone_code_hash": ""}, update_modified=False)
	return {"status": "Disconnected"}


class PhoneCodeInvalidError(Exception):
	"""Двойник исключения Telethon: сопоставление идёт по имени класса."""


class FloodWaitError(Exception):
	def __init__(self, seconds):
		super().__init__(f"A wait of {seconds} seconds is required")
		self.seconds = seconds


def _engine(bots):
	client = MagicMock()
	client.list_bots.return_value = [{"id": b, "name": f"bot {b}"} for b in bots]
	return patch("habibi_ai.api.get_client", return_value=client)


class TestCabinetTelegramAccount(IntegrationTestCase):
	def setUp(self):
		# Аккаунты сайта могли остаться от ручной отладки — кабинет берёт
		# единственный, поэтому начинаем с пустого места. Откат — в конце класса.
		frappe.db.delete("Telegram Account")
		frappe.db.delete("Telegram Message", {"telegram_account": ("is", "set")})

	def tearDown(self):
		frappe.set_user("Administrator")

	def _code_sent(self):
		with patch.dict(frappe.conf, CONF), patch(f"{ACCOUNT}.request_code", _fake_request_code):
			return settings.request_code("+7 (700) 123-45-67")

	def _connected(self, bots=(7,)):
		self._code_sent()
		with _engine(bots), patch(f"{ACCOUNT}.sign_in", _fake_sign_in):
			return settings.sign_in("12345")

	def test_без_аккаунта_состояние_none(self):
		status = settings.telegram_status()
		self.assertEqual(status["state"], "none")
		self.assertIsNone(status["phone"])

	def test_без_ключей_платформы_понятная_ошибка(self):
		conf = {k: v for k, v in frappe.conf.items() if k not in CONF}
		with patch.dict(frappe.conf, conf, clear=True), patch(f"{ACCOUNT}.request_code") as request:
			with self.assertRaises(frappe.ValidationError) as ctx:
				settings.request_code("+77001234567")
		self.assertIn("не настроено на сервере", str(ctx.exception))
		request.assert_not_called()
		self.assertFalse(frappe.db.exists("Telegram Account", {}))

	def test_запрос_кода_создаёт_аккаунт_с_ключами_платформы(self):
		with patch.dict(frappe.conf, CONF), patch(f"{ACCOUNT}.request_code", autospec=True) as request:
			request.side_effect = _fake_request_code
			status = settings.request_code("+7 (700) 123-45-67")
		request.assert_called_once()
		name = frappe.db.get_value("Telegram Account", {})
		doc = frappe.get_doc("Telegram Account", name)
		self.assertEqual(doc.api_id, "12345")
		self.assertEqual(doc.get_password("api_hash"), "platform-hash-secret")
		self.assertEqual(doc.phone, "+77001234567")
		self.assertTrue(doc.enabled and doc.sync_enabled)
		self.assertEqual(status["state"], "code_sent")
		self.assertEqual(status["phone"], "+77001234567")

	def test_повторный_запрос_меняет_телефон_того_же_аккаунта(self):
		self._code_sent()
		with patch.dict(frappe.conf, CONF), patch(f"{ACCOUNT}.request_code", _fake_request_code):
			status = settings.request_code("+77009998877")
		self.assertEqual(frappe.db.count("Telegram Account"), 1)
		self.assertEqual(status["phone"], "+77009998877")

	def test_подключённый_аккаунт_не_перезапрашивает_код(self):
		self._connected()
		with patch.dict(frappe.conf, CONF), patch(f"{ACCOUNT}.request_code") as request:
			with self.assertRaises(frappe.ValidationError):
				settings.request_code("+77009998877")
		request.assert_not_called()

	def test_вход_подключает_и_включает_ии_при_одном_боте(self):
		status = self._connected(bots=(7,))
		self.assertEqual(status["state"], "connected")
		self.assertEqual((status["full_name"], status["username"]), ("Habibi FSA", "@habibi"))
		self.assertTrue(status["ai_ready"])
		name = frappe.db.get_value("Telegram Account", {})
		row = frappe.db.get_value(
			"Telegram Account", name, ["ai_enabled", "ai_bot", "ai_reply_in_groups"], as_dict=True
		)
		self.assertEqual((row.ai_enabled, row.ai_bot, row.ai_reply_in_groups), (1, "7", 0))

	def test_при_нескольких_ботах_ии_не_включается(self):
		status = self._connected(bots=(7, 8))
		self.assertEqual(status["state"], "connected")
		self.assertFalse(status["ai_ready"])
		self.assertIn("ИИ-бот не выбран", status["ai_note"])
		self.assertEqual(frappe.db.get_value("Telegram Account", {}, "ai_enabled"), 0)

	def test_заданный_бот_не_перезаписывается(self):
		self._code_sent()
		name = frappe.db.get_value("Telegram Account", {})
		frappe.db.set_value("Telegram Account", name, "ai_bot", "42")
		with _engine((7,)), patch(f"{ACCOUNT}.sign_in", _fake_sign_in):
			status = settings.sign_in("12345")
		self.assertTrue(status["ai_ready"])
		self.assertEqual(frappe.db.get_value("Telegram Account", name, "ai_bot"), "42")

	def test_движок_недоступен_вход_не_срывается(self):
		self._code_sent()
		client = MagicMock()
		client.list_bots.side_effect = RuntimeError("engine down")
		with (
			patch("habibi_ai.api.get_client", return_value=client),
			patch(f"{ACCOUNT}.sign_in", _fake_sign_in),
			patch("frappe.log_error") as log,
		):
			status = settings.sign_in("12345")
		self.assertEqual(status["state"], "connected")
		self.assertFalse(status["ai_ready"])
		name = frappe.db.get_value("Telegram Account", {})
		self.assertEqual(frappe.db.get_value("Telegram Account", name, "ai_enabled"), 0)
		# вход не откатился вместе с ИИ
		self.assertEqual(frappe.db.get_value("Telegram Account", name, "status"), "Connected")
		log.assert_called_once()

	def test_повторный_вход_не_трогает_настройки_ии(self):
		"""Администратор выключил ИИ и разрешил группы — перелогин это не сбрасывает."""
		self._connected()
		name = frappe.db.get_value("Telegram Account", {})
		frappe.db.set_value(
			"Telegram Account",
			name,
			{"ai_enabled": 0, "ai_reply_in_groups": 1, "enabled": 0, "sync_enabled": 0},
		)
		with patch(f"{ACCOUNT}.log_out", _fake_log_out):
			settings.disconnect()
		self._code_sent()
		with _engine((7,)) as engine, patch(f"{ACCOUNT}.sign_in", _fake_sign_in):
			status = settings.sign_in("12345")
		self.assertEqual(status["state"], "connected")
		row = frappe.db.get_value(
			"Telegram Account",
			name,
			["ai_enabled", "ai_reply_in_groups", "ai_bot", "enabled", "sync_enabled"],
			as_dict=True,
		)
		self.assertEqual((row.ai_enabled, row.ai_reply_in_groups, row.ai_bot), (0, 1, "7"))
		self.assertEqual((row.enabled, row.sync_enabled), (1, 1))
		engine.return_value.list_bots.assert_not_called()

	def test_вход_без_запрошенного_кода(self):
		frappe.get_doc(
			{
				"doctype": "Telegram Account",
				"title": "Idle",
				"phone": "+77001234567",
				"api_id": "1",
				"api_hash": "x",
			}
		).insert()
		with patch(f"{ACCOUNT}.sign_in") as sign_in:
			with self.assertRaises(frappe.ValidationError) as ctx:
				settings.sign_in("12345")
		self.assertIn("Сначала запросите код", str(ctx.exception))
		sign_in.assert_not_called()

	def test_пароль_двухэтапной_проверки(self):
		self._code_sent()
		with patch(f"{ACCOUNT}.sign_in", _fake_password_needed):
			status = settings.sign_in("12345")
		self.assertEqual(status["state"], "password_needed")
		with _engine((7,)), patch(f"{ACCOUNT}.sign_in", autospec=True) as sign_in:
			sign_in.side_effect = _fake_sign_in
			status = settings.sign_in("", password="pw")
		self.assertEqual(sign_in.call_args.kwargs.get("password"), "pw")
		self.assertEqual(status["state"], "connected")

	def test_статус_без_секретов(self):
		self._connected()
		status = settings.telegram_status()
		dump = frappe.as_json(status)
		for secret in ("platform-hash-secret", "hash-secret", "session", "api_"):
			self.assertNotIn(secret, dump)
		self.assertEqual(
			set(status),
			{"state", "phone", "full_name", "username", "last_message_at", "error", "ai_ready", "ai_note"},
		)

	def test_последнее_сообщение_аккаунта(self):
		self._connected()
		name = frappe.db.get_value("Telegram Account", {})
		if not frappe.db.exists("Telegram Chat", "-100500"):
			frappe.get_doc({"doctype": "Telegram Chat", "chat_id": "-100500", "title": "t"}).db_insert()
		# db_insert — мимо хуков: after_insert сообщения запустил бы ответ ИИ
		frappe.get_doc(
			{
				"doctype": "Telegram Message",
				"chat": "-100500",
				"message_id": "1",
				"telegram_account": name,
				"direction": "Outgoing",
				"content": "привет",
			}
		).db_insert()
		self.assertTrue(settings.telegram_status()["last_message_at"])

	def test_уже_подключённый_аккаунт_виден_подключённым(self):
		"""Как на проде: аккаунт вошёл руками из десктопа, ИИ настроен там же."""
		frappe.get_doc(
			{
				"doctype": "Telegram Account",
				"title": "Habibi FSA 500328598",
				"phone": "+77001234567",
				"api_id": "1",
				"api_hash": "x",
				"status": "Connected",
				"full_name": "Habibi FSA",
			}
		).insert()
		status = settings.telegram_status()
		self.assertEqual(status["state"], "connected")
		self.assertEqual(status["full_name"], "Habibi FSA")

	def test_из_нескольких_аккаунтов_берётся_с_ии(self):
		for title, ai in (("A first", 0), ("B with ai", 1)):
			frappe.get_doc(
				{
					"doctype": "Telegram Account",
					"title": title,
					"phone": "+77001234567",
					"api_id": "1",
					"api_hash": "x",
					"status": "Connected",
					"full_name": title,
				}
			).insert()
			frappe.db.set_value("Telegram Account", title, "ai_enabled", ai)
		self.assertEqual(settings.telegram_status()["full_name"], "B with ai")

	def test_отключение(self):
		self._connected()
		with patch(f"{ACCOUNT}.log_out", autospec=True) as log_out:
			log_out.side_effect = _fake_log_out
			status = settings.disconnect()
		log_out.assert_called_once()
		self.assertEqual(status["state"], "none")

	def test_ошибка_telegram_не_утекает(self):
		self._code_sent()

		def _wrong_code(self, code=None, password=None):
			try:
				raise PhoneCodeInvalidError("phone_code_hash=hash-secret invalid")
			except PhoneCodeInvalidError as e:
				frappe.throw(f"Wrong code {e}")

		frappe.local.message_log = []
		with patch(f"{ACCOUNT}.sign_in", _wrong_code), patch("frappe.log_error") as log:
			with self.assertRaises(frappe.ValidationError) as ctx:
				settings.sign_in("00000")
		self.assertEqual(str(ctx.exception), "Неверный код")
		self.assertNotIn("hash-secret", frappe.as_json(frappe.local.message_log))
		self.assertIn("hash-secret", str(log.call_args))

	def test_слишком_много_попыток(self):
		self._code_sent()

		def _flood(self, code=None, password=None):
			try:
				raise FloodWaitError(120)
			except FloodWaitError as e:
				frappe.throw(str(e))

		with patch(f"{ACCOUNT}.sign_in", _flood), patch("frappe.log_error"):
			with self.assertRaises(frappe.ValidationError) as ctx:
				settings.sign_in("00000")
		self.assertIn("Слишком много попыток", str(ctx.exception))

	def test_без_прав_нельзя_ни_смотреть_ни_входить(self):
		_ensure_role(NO_PROFILE_WRITE_ROLE)
		frappe.set_user(_ensure_user("cabinet-settings-no-write-test@example.com", NO_PROFILE_WRITE_ROLE))
		with self.assertRaises(frappe.PermissionError):
			settings.telegram_status()
		with patch.dict(frappe.conf, CONF), patch(f"{ACCOUNT}.request_code") as request:
			with self.assertRaises(frappe.PermissionError):
				settings.request_code("+77001234567")
		request.assert_not_called()

"""Переписки в кабинете: лента, пауза бота, ответ сотрудника — и права на них."""

from unittest.mock import patch

import frappe
from frappe.permissions import add_permission
from frappe.tests import IntegrationTestCase

from habibi_ai.cabinet import chats

NO_CHAT_ROLE = "_Habibi Cabinet No Chat Access"
CHAT_READ_ONLY_ROLE = "_Habibi Cabinet Chat Read Only"


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


class TestCabinetChats(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		bots = frappe.get_all("Telegram Bot", pluck="name", limit=1)
		if bots:
			self.bot = bots[0]
		else:
			# На пустом сайте бота нет вовсе — заводим фиктивный db_insert'ом:
			# обычный insert() позвал бы Telegram проверить токен (сеть)
			bot = frappe.get_doc(
				{"doctype": "Telegram Bot", "title": "_Habibi Cabinet Test Bot", "api_token": "123:test"}
			)
			bot.db_insert()
			self.bot = bot.name
		self.chat = frappe.get_doc(
			{"doctype": "Telegram Chat", "chat_id": "990001", "title": "Руслан", "type": "private"}
		).insert(ignore_if_duplicate=True)
		for direction, automated, text in (
			("Incoming", 0, "Привет"),
			("Outgoing", 1, "Здравствуйте!"),
			("Outgoing", 0, "Это Аня"),
		):
			frappe.get_doc(
				{
					"doctype": "Telegram Message",
					"chat": self.chat.name,
					"direction": direction,
					"is_automated": automated,
					"content": text,
					"telegram_bot": self.bot,
				}
			).db_insert()
		frappe.get_doc(
			{
				"doctype": "AI Channel Chat",
				"channel_doctype": "Telegram Bot",
				"channel_name": self.bot,
				"telegram_chat": self.chat.name,
			}
		).insert(ignore_if_duplicate=True)

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def _no_chat_access_user(self):
		"""Роль без единой записи прав на Telegram Chat — доступа к перепискам нет вовсе."""
		_ensure_role(NO_CHAT_ROLE)
		return _ensure_user("cabinet-chats-no-access-test@example.com", NO_CHAT_ROLE)

	def _chat_read_only_user(self):
		"""Читает чаты Telegram, но ни одной записи прав на AI Channel Chat —
		значит, не может ни ответить, ни поставить/снять паузу."""
		_ensure_role(CHAT_READ_ONLY_ROLE)
		add_permission("Telegram Chat", CHAT_READ_ONLY_ROLE, 0, "read")
		frappe.clear_cache(doctype="Telegram Chat")
		self.addCleanup(frappe.clear_cache, doctype="Telegram Chat")
		return _ensure_user("cabinet-chats-read-only-test@example.com", CHAT_READ_ONLY_ROLE)

	def test_авторы_различаются(self):
		authors = [m["author"] for m in chats.messages(self.chat.name)]
		self.assertEqual(authors[-3:], ["client", "bot", "staff"])

	def test_взять_на_себя_и_вернуть(self):
		"""pause/resume пишут через frappe.db.set_value — on_update не сработает,
		поэтому кабинет должен получить событие явным вызовом realtime."""
		with (
			patch("habibi_ai.cabinet.realtime._recipients", return_value=["owner@example.com"]),
			patch("habibi_ai.cabinet.realtime.frappe.publish_realtime") as pub,
		):
			chats.pause(self.chat.name)
			self.assertTrue(next(c for c in chats.list() if c["chat"] == self.chat.name)["paused"])
			chats.resume(self.chat.name)
			self.assertFalse(next(c for c in chats.list() if c["chat"] == self.chat.name)["paused"])
		self.assertEqual(pub.call_count, 2)
		for call in pub.call_args_list:
			self.assertEqual(
				call.args, ("habibi_cabinet", {"topic": "chats", "chat": self.chat.name})
			)
			self.assertEqual(call.kwargs, {"user": "owner@example.com", "after_commit": True})

	def test_ответ_сотрудника_ставит_паузу_и_уходит(self):
		with (
			patch("habibi_ai.cabinet.chats.telegram.send") as send,
			patch("habibi_ai.cabinet.realtime._recipients", return_value=["owner@example.com"]),
			patch("habibi_ai.cabinet.realtime.frappe.publish_realtime") as pub,
		):
			chats.send(self.chat.name, "Сейчас уточню")
		self.assertEqual(send.call_args.args[1:], (self.chat.name, "Сейчас уточню"))
		self.assertTrue(next(c for c in chats.list() if c["chat"] == self.chat.name)["paused"])
		# send() сам ставит паузу через telegram.pause (frappe.db.set_value,
		# без on_update) — событие о ней шлём явно, до отправки сообщения.
		pub.assert_any_call(
			"habibi_cabinet",
			{"topic": "chats", "chat": self.chat.name},
			user="owner@example.com",
			after_commit=True,
		)

	def test_ответ_сотрудника_в_ленте_от_сотрудника(self):
		def fake_send(channel, chat, text):
			# как настоящий client.send_message: вставляет исходящее с automated=1
			frappe.get_doc(
				{
					"doctype": "Telegram Message",
					"chat": chat,
					"direction": "Outgoing",
					"is_automated": 1,
					"content": text,
					"telegram_bot": self.bot,
				}
			).db_insert()

		with patch("habibi_ai.cabinet.chats.telegram.send", side_effect=fake_send):
			chats.send(self.chat.name, "Сейчас уточню")
		self.assertEqual(chats.messages(self.chat.name)[-1]["author"], "staff")

	def test_параллельный_ответ_бота_не_перекрашивается_в_сотрудника(self):
		"""Пока наш send() ставит паузу и шлёт, бот-раунд, начатый до паузы,
		может дописать в чат свой ответ. Перекраска must find только наше
		сообщение — по совпадению текста, а не «последнее исходящее»."""

		def fake_send(channel, chat, text):
			frappe.get_doc(
				{
					"doctype": "Telegram Message",
					"chat": chat,
					"direction": "Outgoing",
					"is_automated": 1,
					"content": text,
					"telegram_bot": self.bot,
				}
			).db_insert()
			# тем временем бот успел ответить своим раундом — это не ответ сотрудника
			frappe.get_doc(
				{
					"doctype": "Telegram Message",
					"chat": chat,
					"direction": "Outgoing",
					"is_automated": 1,
					"content": "Автоматический ответ бота",
					"telegram_bot": self.bot,
				}
			).db_insert()

		with patch("habibi_ai.cabinet.chats.telegram.send", side_effect=fake_send):
			chats.send(self.chat.name, "Сейчас уточню")
		rows = chats.messages(self.chat.name)
		ours = next(m for m in rows if m["text"] == "Сейчас уточню")
		bots = next(m for m in rows if m["text"] == "Автоматический ответ бота")
		self.assertEqual(ours["author"], "staff")
		self.assertEqual(bots["author"], "bot")

	def test_длинный_ответ_все_части_от_сотрудника(self):
		"""Telegram режет длинный текст на части (decisions.split_text) — все
		части нашего ответа должны стать author == staff, а не только первая:
		startswith по первой части не покрывает вторую и следующие."""
		staff_text = "Первая часть текста. Вторая часть текста. Третья часть."

		def fake_send(channel, chat, text):
			# как настоящий telegram.send(): режет на части тем же лимитом и
			# логирует каждую отдельным Outgoing-сообщением
			for part in chats.decisions.split_text(text, chats.telegram.PART_LIMIT):
				frappe.get_doc(
					{
						"doctype": "Telegram Message",
						"chat": chat,
						"direction": "Outgoing",
						"is_automated": 1,
						"content": part,
						"telegram_bot": self.bot,
					}
				).db_insert()

		with (
			patch("habibi_ai.cabinet.chats.telegram.PART_LIMIT", 12),
			patch("habibi_ai.cabinet.chats.telegram.send", side_effect=fake_send),
		):
			chats.send(self.chat.name, staff_text)
		expected_parts = chats.decisions.split_text(staff_text, 12)
		self.assertGreaterEqual(len(expected_parts), 2)
		authors = {m["text"]: m["author"] for m in chats.messages(self.chat.name)}
		for part in expected_parts:
			self.assertEqual(authors[part], "staff")

	def test_короткий_ответ_бота_не_совпадает_по_префиксу(self):
		"""«Да» бота — не префикс нашего «Да, сейчас уточню»: перекраска ищет
		точное совпадение с частью текста, а не startswith, иначе короткий
		параллельный ответ бота перекрасился бы в «сотрудник»."""

		def fake_send(channel, chat, text):
			frappe.get_doc(
				{
					"doctype": "Telegram Message",
					"chat": chat,
					"direction": "Outgoing",
					"is_automated": 1,
					"content": text,
					"telegram_bot": self.bot,
				}
			).db_insert()
			# тем временем бот успел коротко ответить своим раундом
			frappe.get_doc(
				{
					"doctype": "Telegram Message",
					"chat": chat,
					"direction": "Outgoing",
					"is_automated": 1,
					"content": "Да",
					"telegram_bot": self.bot,
				}
			).db_insert()

		with patch("habibi_ai.cabinet.chats.telegram.send", side_effect=fake_send):
			chats.send(self.chat.name, "Да, сейчас уточню")
		rows = chats.messages(self.chat.name)
		ours = next(m for m in rows if m["text"] == "Да, сейчас уточню")
		bots = next(m for m in rows if m["text"] == "Да")
		self.assertEqual(ours["author"], "staff")
		self.assertEqual(bots["author"], "bot")

	def test_пустой_ответ_не_отправляется(self):
		with self.assertRaises(frappe.ValidationError):
			chats.send(self.chat.name, "   ")

	def test_без_доступа_к_чатам_список_запрещён(self):
		frappe.set_user(self._no_chat_access_user())
		with self.assertRaises(frappe.PermissionError):
			chats.list()

	def test_без_права_писать_в_пару_ответ_и_пауза_запрещены(self):
		frappe.set_user(self._chat_read_only_user())
		with patch("habibi_ai.cabinet.chats.telegram.send") as send:
			with self.assertRaises(frappe.PermissionError):
				chats.send(self.chat.name, "Привет")
			send.assert_not_called()
		with self.assertRaises(frappe.PermissionError):
			chats.pause(self.chat.name)

	def test_ошибка_отправки_не_показывает_токен_бота(self):
		# requests кладёт в текст исключения полный URL с токеном — вида
		# https://api.telegram.org/bot123:SECRET/sendMessage
		leaking = "HTTPSConnectionPool: Max retries exceeded /bot123:SECRET/sendMessage"
		with patch("habibi_ai.cabinet.chats.telegram.send", side_effect=Exception(leaking)):
			with self.assertRaises(frappe.ValidationError) as ctx:
				chats.send(self.chat.name, "Сейчас уточню")
		self.assertNotIn("SECRET", str(ctx.exception))

	def test_сообщение_telegram_в_message_log_не_утекает(self):
		"""habibi_telegram (user_client.send_message) сам делает
		frappe.throw(describe_error(e)) до того, как исключение дойдёт до
		telegram.send() здесь — оно уже лежит в message_log и ушло бы в ответ
		раньше safe-текста, если его не вычистить (задача 17/19)."""

		def _leaking_send(channel, chat, text):
			try:
				raise RuntimeError("phone_code_hash=hash-secret")
			except RuntimeError as e:
				frappe.throw(f"Telegram did not accept the message: {e}")

		frappe.local.message_log = []
		with (
			patch("habibi_ai.cabinet.chats.telegram.send", _leaking_send),
			patch("frappe.log_error"),
		):
			with self.assertRaises(frappe.ValidationError) as ctx:
				chats.send(self.chat.name, "Сейчас уточню")
		self.assertNotIn("hash-secret", str(ctx.exception))
		self.assertNotIn("hash-secret", frappe.as_json(frappe.local.message_log))

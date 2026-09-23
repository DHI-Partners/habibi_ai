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
		chats.pause(self.chat.name)
		self.assertTrue(next(c for c in chats.list() if c["chat"] == self.chat.name)["paused"])
		chats.resume(self.chat.name)
		self.assertFalse(next(c for c in chats.list() if c["chat"] == self.chat.name)["paused"])

	def test_ответ_сотрудника_ставит_паузу_и_уходит(self):
		with patch("habibi_ai.cabinet.chats.telegram.send") as send:
			chats.send(self.chat.name, "Сейчас уточню")
		self.assertEqual(send.call_args.args[1:], (self.chat.name, "Сейчас уточню"))
		self.assertTrue(next(c for c in chats.list() if c["chat"] == self.chat.name)["paused"])

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

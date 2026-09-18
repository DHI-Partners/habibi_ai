"""Связка habibi_telegram → habibi_ai на живом сайте.

Нужен сайт с обоими приложениями (dev.localhost после Task 0). Движок и
Telegram подменяются: проверяется клей — поля, пары, пауза, задача ответа.
"""

from datetime import timedelta
from unittest.mock import Mock, patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import now_datetime

from habibi_ai import setup
from habibi_ai.channels import decisions
from habibi_ai.channels import telegram as bridge
from habibi_ai.engine import BotNotFound
from habibi_ai.loop import LoopExhausted

BOT = "ai-bridge-test-bot"
CHAT_ID = "5559100"


def make_bot(**values):
	if not frappe.db.exists("Telegram Bot", BOT):
		with patch(
			"habibi_telegram.telegram_api.TelegramBotAPI.get_me",
			return_value={"is_bot": True, "username": "ai_bridge_test_bot"},
		):
			frappe.get_doc({"doctype": "Telegram Bot", "title": BOT, "api_token": "1:test"}).insert()
	if values:
		frappe.db.set_value("Telegram Bot", BOT, values)
	return frappe.get_doc("Telegram Bot", BOT)


class TestПоляКанала(IntegrationTestCase):
	def test_поля_стоят_и_не_дублируются(self):
		setup.install_telegram_fields()
		setup.install_telegram_fields()
		for doctype in ("Telegram Bot", "Telegram Account"):
			meta = frappe.get_meta(doctype)
			self.assertTrue(meta.has_field("ai_enabled"), doctype)
			self.assertEqual(meta.get_field("ai_bot").fieldtype, "Autocomplete")
			self.assertEqual(
				frappe.db.count("Custom Field", {"dt": doctype, "fieldname": ["like", "ai_%"]}), 3
			)

	def test_без_telegram_ничего_не_ставится(self):
		with patch("frappe.get_installed_apps", return_value=["frappe", "habibi_ai"]):
			with patch("habibi_ai.setup.create_custom_fields") as create:
				setup.install_telegram_fields()
		create.assert_not_called()


class TestПроверкаКанала(IntegrationTestCase):
	def setUp(self):
		setup.install_telegram_fields()
		self.bot = make_bot(ai_enabled=0, ai_bot="")

	def test_без_бота_нельзя_включить(self):
		self.bot.ai_enabled = 1
		self.bot.ai_bot = ""
		with self.assertRaises(frappe.ValidationError):
			bridge.validate_channel(self.bot)

	def test_чужой_бот_не_проходит(self):
		self.bot.ai_enabled = 1
		self.bot.ai_bot = "77"
		client = Mock()
		client._check_bot = Mock(side_effect=BotNotFound(77))
		with patch("habibi_ai.api.get_client", return_value=client):
			with self.assertRaises(frappe.ValidationError):
				bridge.validate_channel(self.bot)

	def test_свой_бот_проходит(self):
		self.bot.ai_enabled = 1
		self.bot.ai_bot = "3"
		client = Mock()
		with patch("habibi_ai.api.get_client", return_value=client):
			bridge.validate_channel(self.bot)
		client._check_bot.assert_called_once_with(3)

	def test_выключенный_не_проверяется(self):
		with patch("habibi_ai.api.get_client") as get_client:
			bridge.validate_channel(self.bot)
		get_client.assert_not_called()

	def test_без_изменений_ИИ_движок_не_спрашивается(self):
		# Сохранение карточки ради другого поля не должно зависеть от движка
		frappe.db.set_value("Telegram Bot", BOT, {"ai_enabled": 1, "ai_bot": "3"})
		bot = frappe.get_doc("Telegram Bot", BOT)
		bot.notify_user = "Administrator"
		with patch("habibi_ai.api.get_client") as get_client:
			bot.save()
		get_client.assert_not_called()

	def test_недоступный_движок_понятной_ошибкой(self):
		import requests

		self.bot.ai_enabled = 1
		self.bot.ai_bot = "3"
		client = Mock()
		client._check_bot = Mock(side_effect=requests.ConnectionError("Connection refused"))
		with patch("habibi_ai.api.get_client", return_value=client):
			with self.assertRaises(frappe.ValidationError) as cm:
				bridge.validate_channel(self.bot)
		self.assertIn("Движок ИИ недоступен", str(cm.exception))


class TestПара(IntegrationTestCase):
	def test_имя_пары_и_снятие_паузы(self):
		make_bot()
		chat = frappe.get_doc({"doctype": "Telegram Chat", "chat_id": "5559001", "title": "Тест", "type": "private"})
		chat.insert(ignore_if_duplicate=True)
		pair = frappe.get_doc({
			"doctype": bridge.PAIR, "channel_doctype": "Telegram Bot", "channel_name": BOT,
			"telegram_chat": chat.name, "ai_paused": 1,
		}).insert()
		self.assertEqual(pair.name, decisions.pair_name("Telegram Bot", BOT, chat.name))
		self.assertEqual(pair.paused_reason, bridge.REASON_MANUAL)
		self.assertIsNotNone(pair.paused_on)

		pair.ai_paused = 0
		pair.save()
		self.assertFalse(pair.paused_reason)
		self.assertIsNone(pair.paused_on)


def make_chat(chat_id=CHAT_ID):
	name = frappe.db.get_value("Telegram Chat", {"chat_id": chat_id})
	if name:
		return name
	return frappe.get_doc({"doctype": "Telegram Chat", "chat_id": chat_id, "title": "Клиент", "type": "private"}).insert().name


def incoming(chat, message_id, text="Здравствуйте", minutes_ago=0, from_user=None):
	return frappe.get_doc({
		"doctype": "Telegram Message", "chat": chat, "message_id": str(message_id), "direction": "Incoming",
		"content": text, "telegram_bot": BOT, "sent_on": now_datetime() - timedelta(minutes=minutes_ago),
		"from_user": from_user,
	}).insert(ignore_permissions=True)


def outgoing(chat, message_id, automated, minutes_ago=None):
	sent_on = now_datetime() - timedelta(minutes=minutes_ago) if minutes_ago is not None else None
	return frappe.get_doc({
		"doctype": "Telegram Message", "chat": chat, "message_id": str(message_id), "direction": "Outgoing",
		"content": "ответ", "telegram_bot": BOT, "is_automated": 1 if automated else 0, "sent_on": sent_on,
	}).insert(ignore_permissions=True)


def make_user(user_id, conversation_state=""):
	name = frappe.db.get_value("Telegram User", {"telegram_user_id": user_id})
	if not name:
		name = frappe.get_doc({
			"doctype": "Telegram User", "telegram_user_id": user_id, "full_name": "Клиент",
		}).insert(ignore_permissions=True).name
	frappe.db.set_value("Telegram User", name, "conversation_state", conversation_state)
	return name


class _Base(IntegrationTestCase):
	def setUp(self):
		setup.install_telegram_fields()
		make_bot(ai_enabled=1, ai_bot="3", notify_user="Administrator")
		self.chat = make_chat()
		self.channel = ("Telegram Bot", BOT)
		frappe.db.delete(bridge.PAIR, {"telegram_chat": self.chat})
		frappe.db.delete("Telegram Message", {"chat": self.chat})
		self.marker = bridge.sending_marker(decisions.pair_name(*self.channel, self.chat))
		frappe.cache().delete_value(self.marker)

	def tearDown(self):
		frappe.cache().delete_value(self.marker)

	@classmethod
	def tearDownClass(cls):
		# Задача коммитит — откат теста её следы не уберёт. Включённый ИИ на
		# тестовом боте отвечал бы на сообщения других модулей тестов.
		chat = frappe.db.get_value("Telegram Chat", {"chat_id": CHAT_ID})
		if chat:
			frappe.db.delete(bridge.PAIR, {"telegram_chat": chat})
			frappe.db.delete("Telegram Message", {"chat": chat})
		frappe.db.set_value("Telegram Bot", BOT, {"ai_enabled": 0, "notify_user": None})
		frappe.db.commit()
		super().tearDownClass()


class TestХук(_Base):
	def test_входящее_ставит_задачу(self):
		with patch("frappe.enqueue") as enqueue:
			incoming(self.chat, 1)
		enqueue.assert_called_once()
		kwargs = enqueue.call_args.kwargs
		self.assertEqual(kwargs["queue"], "long")
		self.assertEqual(kwargs["job_id"], f"ai-reply:Telegram Bot:{BOT}:{self.chat}")
		self.assertTrue(kwargs["deduplicate"])
		self.assertTrue(kwargs["enqueue_after_commit"])

	def test_канал_выключен(self):
		frappe.db.set_value("Telegram Bot", BOT, "ai_enabled", 0)
		with patch("frappe.enqueue") as enqueue:
			incoming(self.chat, 2)
		enqueue.assert_not_called()

	def test_старое_не_ставит(self):
		with patch("frappe.enqueue") as enqueue:
			incoming(self.chat, 3, minutes_ago=10)
		enqueue.assert_not_called()

	def test_ручное_исходящее_ставит_паузу(self):
		outgoing(self.chat, 4, automated=False)
		pair = frappe.get_doc(bridge.PAIR, decisions.pair_name(*self.channel, self.chat))
		self.assertEqual(pair.ai_paused, 1)
		self.assertEqual(pair.paused_reason, bridge.REASON_OPERATOR)

	def test_автоматическое_исходящее_паузы_не_ставит(self):
		outgoing(self.chat, 5, automated=True)
		self.assertFalse(frappe.db.get_value(bridge.PAIR, decisions.pair_name(*self.channel, self.chat), "ai_paused"))

	def test_пока_ИИ_отправляет_ручное_исходящее_паузы_не_ставит(self):
		# Слушатель MTProto записал наш же ответ раньше, чем reply_job, — без
		# пометки is_automated
		frappe.cache().set_value(self.marker, 1, expires_in_sec=60)
		outgoing(self.chat, 9, automated=False)
		self.assertFalse(frappe.db.get_value(bridge.PAIR, decisions.pair_name(*self.channel, self.chat), "ai_paused"))

	def test_старое_ручное_исходящее_паузы_не_ставит(self):
		# Импорт истории
		outgoing(self.chat, 13, automated=False, minutes_ago=60)
		self.assertFalse(frappe.db.get_value(bridge.PAIR, decisions.pair_name(*self.channel, self.chat), "ai_paused"))

	def test_команда_не_ставит(self):
		with patch("frappe.enqueue") as enqueue:
			incoming(self.chat, 14, "/start")
		enqueue.assert_not_called()

	def test_посреди_диалога_авторизации_не_ставит(self):
		user = make_user("5559200", '{"step": "email"}')
		with patch("frappe.enqueue") as enqueue:
			incoming(self.chat, 15, "me@example.com", from_user=user)
		enqueue.assert_not_called()

	def test_служебный_чат_telegram_не_трогаем(self):
		chat = make_chat("777000")
		frappe.db.delete(bridge.PAIR, {"telegram_chat": chat})
		with patch("frappe.enqueue") as enqueue:
			incoming(chat, 16, "Код входа: 12345")
			outgoing(chat, 17, automated=False)
		enqueue.assert_not_called()
		self.assertFalse(frappe.db.exists(bridge.PAIR, decisions.pair_name(*self.channel, chat)))
		self.assertEqual(bridge.pending_messages(self.channel, chat), [])

	def test_канал_вещания_не_трогаем(self):
		chat = make_chat("-1005559300")
		frappe.db.set_value("Telegram Chat", chat, "type", "channel")
		frappe.db.delete(bridge.PAIR, {"telegram_chat": chat})
		with patch("frappe.enqueue") as enqueue:
			incoming(chat, 18)
			outgoing(chat, 19, automated=False)
		enqueue.assert_not_called()
		self.assertFalse(frappe.db.exists(bridge.PAIR, decisions.pair_name(*self.channel, chat)))
		self.assertEqual(bridge.pending_messages(self.channel, chat), [])

	def test_на_паузе_не_ставит(self):
		outgoing(self.chat, 6, automated=False)
		with patch("frappe.enqueue") as enqueue:
			incoming(self.chat, 7)
		enqueue.assert_not_called()

	def test_ошибка_хука_не_роняет_запись_сообщения(self):
		with patch("habibi_ai.channels.telegram.channel_settings", side_effect=RuntimeError("сбой")):
			doc = incoming(self.chat, 8)
		self.assertTrue(frappe.db.exists("Telegram Message", doc.name))

	def test_дедлок_и_таймаут_блокировки_пробрасываются(self):
		# Транзакция уже откачена базой — проглоти мы ошибку, вызывающий
		# закоммитил бы пустоту и потерял сообщения
		for error in (frappe.QueryDeadlockError, frappe.QueryTimeoutError):
			with self.subTest(error.__name__):
				with patch("habibi_ai.channels.telegram._on_message_insert", side_effect=error("сбой")):
					with self.assertRaises(error):
						bridge.on_message_insert(Mock())


class TestЗадача(_Base):
	def _run(self, run_turn=None, send_error=None):
		client = Mock()
		client.create_chat = Mock(return_value={"id": 42})
		telegram_api = Mock()
		telegram_api.send_message = Mock(
			side_effect=send_error,
			return_value={"message_id": 900, "chat": {"id": int(CHAT_ID), "type": "private"}, "text": "ответ ИИ"},
		)
		# Задача начинает раунд с rollback — без коммита она не увидела бы
		# ни сообщений теста, ни чистки из setUp
		frappe.db.commit()
		with (
			patch("time.sleep"),
			patch("frappe.enqueue"),
			patch("habibi_ai.api.get_client", return_value=client),
			patch("habibi_ai.api.run_turn", run_turn or Mock(return_value={"response": "ответ ИИ", "debug": []})) as turn,
			patch("habibi_telegram.client.get_bot", return_value=telegram_api),
		):
			bridge.reply_job("Telegram Bot", BOT, self.chat)
		return client, turn, telegram_api

	def test_отвечает_на_всё_накопленное_одним_сообщением(self):
		with patch("frappe.enqueue"):
			incoming(self.chat, 10, "Здравствуйте")
			incoming(self.chat, 11, "[photo]")
			last = incoming(self.chat, 12, "хочу пиццу")
		client, turn, telegram_api = self._run()

		client.create_chat.assert_called_once_with(3, f"telegram:Telegram Bot:{BOT}:{CHAT_ID}")
		args = turn.call_args.args
		self.assertEqual((args[1], args[2], args[3]), (42, "Здравствуйте\nхочу пиццу", 3))
		telegram_api.send_message.assert_called_once()

		pair = frappe.get_doc(bridge.PAIR, decisions.pair_name(*self.channel, self.chat))
		self.assertEqual(pair.engine_chat_id, 42)
		self.assertEqual(pair.last_processed_message, last.name)
		self.assertFalse(pair.ai_paused)
		reply = frappe.get_doc("Telegram Message", {"chat": self.chat, "message_id": "900"})
		self.assertEqual(reply.is_automated, 1)

	def test_перед_отправкой_ставится_маркер(self):
		seen = []
		with patch("frappe.enqueue"):
			incoming(self.chat, 13)
		original = bridge.send

		def send(*args):
			seen.append(frappe.cache().get_value(self.marker, use_local_cache=False))
			return original(*args)

		with patch("habibi_ai.channels.telegram.send", side_effect=send):
			self._run()
		self.assertEqual(seen, [1])

	def test_ответ_команде_и_диалогу_авторизации_не_уходит_в_движок(self):
		user = make_user("5559201", '{"step": "password"}')
		with patch("frappe.enqueue"):
			incoming(self.chat, 14, "секрет", from_user=user)
			incoming(self.chat, 15, "/login")
			incoming(self.chat, 16, "а доставка есть?")
		_, turn, _ = self._run()
		self.assertEqual(turn.call_args.args[2], "а доставка есть?")

	def test_старая_история_не_уходит_в_движок(self):
		with patch("frappe.enqueue"):
			incoming(self.chat, 20, "вчерашнее", minutes_ago=60 * 24)
			incoming(self.chat, 21, "сегодняшнее")
		_, turn, _ = self._run()
		self.assertEqual(turn.call_args.args[2], "сегодняшнее")

	def test_нечего_отвечать(self):
		_, turn, telegram_api = self._run()
		turn.assert_not_called()
		telegram_api.send_message.assert_not_called()

	def test_запрет_писать_ставит_паузу(self):
		from habibi_telegram.telegram_api import TelegramAPIError

		with patch("frappe.enqueue"):
			incoming(self.chat, 30)
		self._run(send_error=TelegramAPIError("Forbidden: bot was blocked by the user"))
		pair = frappe.get_doc(bridge.PAIR, decisions.pair_name(*self.channel, self.chat))
		self.assertEqual(pair.ai_paused, 1)
		self.assertEqual(pair.paused_reason, bridge.REASON_FORBIDDEN)

	def test_исчерпан_лимит_оповещает_оператора(self):
		with patch("frappe.enqueue"):
			incoming(self.chat, 40)
		before = frappe.db.count("Notification Log", {"for_user": "Administrator", "document_name": self.chat})
		_, _, telegram_api = self._run(run_turn=Mock(side_effect=LoopExhausted(8)))
		telegram_api.send_message.assert_not_called()
		after = frappe.db.count("Notification Log", {"for_user": "Administrator", "document_name": self.chat})
		self.assertEqual(after, before + 1)

	def test_пауза_проверяется_в_задаче(self):
		with patch("frappe.enqueue"):
			incoming(self.chat, 50)
		bridge.pause(self.channel, self.chat, bridge.REASON_OPERATOR)
		_, turn, _ = self._run()
		turn.assert_not_called()


class TestОтправкаАккаунтом(IntegrationTestCase):
	def setUp(self):
		title = "ai-bridge-test-account"
		self.account = frappe.db.get_value("Telegram Account", {"title": title})
		if not self.account:
			self.account = frappe.get_doc({
				"doctype": "Telegram Account", "title": title, "phone": "+70000000001",
				"api_id": "1", "api_hash": "x",
			}).insert().name
		self.chat = make_chat()

	def test_личный_аккаунт_шлёт_через_user_client(self):
		with patch("habibi_telegram.user_client.send_message") as send_message:
			bridge.send(("Telegram Account", self.account), self.chat, "текст")
		send_message.assert_called_once_with(self.account, CHAT_ID, "текст", automated=True)

	def test_длинный_ответ_аккаунтом_частями(self):
		# user_client шлёт как есть, а Telegram не принимает больше 4096 символов
		text = ("а" * 3000 + "\n") * 3
		with patch("habibi_telegram.user_client.send_message") as send_message:
			bridge.send(("Telegram Account", self.account), self.chat, text)
		sent = [c.args[2] for c in send_message.call_args_list]
		self.assertEqual(sent, decisions.split_text(text))
		self.assertGreater(len(sent), 1)
		self.assertTrue(all(c.kwargs["automated"] for c in send_message.call_args_list))


class TestКаналСообщения(IntegrationTestCase):
	def test_аккаунт_важнее_бота(self):
		# Сообщения аккаунтов до 1.3.8 несут бота по умолчанию в telegram_bot
		message = frappe._dict(telegram_bot="какой-то-бот", telegram_account="менеджер")
		self.assertEqual(bridge.channel_of(message), ("Telegram Account", "менеджер"))

	def test_только_бот(self):
		self.assertEqual(bridge.channel_of(frappe._dict(telegram_bot="бот")), ("Telegram Bot", "бот"))

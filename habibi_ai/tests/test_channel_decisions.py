"""Когда ИИ отвечает в канал.

Без frappe: это решения, ошибка в которых означает либо ответ на вчерашнее
сообщение, либо переписку двух ботов по кругу, — проверяются они первыми и
быстро.
"""

import unittest
from datetime import datetime, timedelta

from habibi_ai.channels import decisions

NOW = datetime(2026, 9, 18, 12, 0, 0)
ON = {"ai_enabled": 1, "ai_bot": "3"}


def message(**overrides):
	base = {"direction": "Incoming", "content": "Здравствуйте", "sent_on": NOW - timedelta(seconds=5)}
	base.update(overrides)
	return base


class TestShouldReply(unittest.TestCase):
	def test_отвечаем(self):
		self.assertTrue(decisions.should_reply(message(), ON, None, False, NOW))

	def test_не_отвечаем(self):
		cases = {
			"канал выключен": (message(), {"ai_enabled": 0, "ai_bot": "3"}, None, False),
			"бот не выбран": (message(), {"ai_enabled": 1, "ai_bot": ""}, None, False),
			"настроек нет": (message(), None, None, False),
			"чат на паузе": (message(), ON, {"ai_paused": 1}, False),
			"пишет бот": (message(), ON, None, True),
			"исходящее": (message(direction="Outgoing"), ON, None, False),
			"пусто": (message(content=""), ON, None, False),
			"пробелы": (message(content="   "), ON, None, False),
			"голосовое": (message(content="[voice]"), ON, None, False),
			"служебное": (message(content="[chatjoinedbylink]"), ON, None, False),
			"старое": (message(sent_on=NOW - timedelta(minutes=6)), ON, None, False),
			"команда боту": (message(content="/start"), ON, None, False),
		}
		for name, (msg, channel, pair, is_bot) in cases.items():
			with self.subTest(name):
				self.assertFalse(decisions.should_reply(msg, channel, pair, is_bot, NOW))

	def test_граница_свежести_включительно(self):
		self.assertTrue(decisions.should_reply(message(sent_on=NOW - decisions.FRESH_FOR), ON, None, False, NOW))

	def test_без_даты_считается_свежим(self):
		# Вебхук бота приходит сразу; дата пуста только у записей до миграции
		self.assertTrue(decisions.should_reply(message(sent_on=None), ON, None, False, NOW))

	def test_пауза_снята(self):
		self.assertTrue(decisions.should_reply(message(), ON, {"ai_paused": 0}, False, NOW))

	def test_подпись_к_фото_это_текст(self):
		self.assertTrue(decisions.should_reply(message(content="Сколько стоит это?"), ON, None, False, NOW))

	def test_идёт_диалог_авторизации(self):
		# Ответ на «введите email» — это данные для обработчика, не вопрос ИИ
		self.assertFalse(decisions.should_reply(message(), ON, None, False, NOW, sender_in_dialogue=True))
		self.assertFalse(decisions.is_replyable(message(), False, NOW, sender_in_dialogue=True))

	def test_косая_черта_не_в_начале_не_команда(self):
		self.assertTrue(decisions.should_reply(message(content="доставка 24/7?"), ON, None, False, NOW))


def outgoing(**overrides):
	base = {"direction": "Outgoing", "is_automated": 0, "sent_on": NOW - timedelta(seconds=5)}
	base.update(overrides)
	return base


class TestShouldPause(unittest.TestCase):
	def test_свежий_ручной_ответ_ставит_паузу(self):
		self.assertTrue(decisions.should_pause(outgoing(), NOW))

	def test_без_даты_считается_свежим(self):
		self.assertTrue(decisions.should_pause(outgoing(sent_on=None), NOW))

	def test_граница_свежести_включительно(self):
		self.assertTrue(decisions.should_pause(outgoing(sent_on=NOW - decisions.FRESH_FOR), NOW))

	def test_не_ставит(self):
		cases = {
			# Импорт истории пишет старые ответы оператора — это не повод
			# выключать ИИ во всех диалогах
			"старое": outgoing(sent_on=NOW - timedelta(minutes=6)),
			"автоматическое": outgoing(is_automated=1),
			"входящее": outgoing(direction="Incoming"),
		}
		for name, msg in cases.items():
			with self.subTest(name):
				self.assertFalse(decisions.should_pause(msg, NOW))


class TestСлужебныйЧат(unittest.TestCase):
	def test_коды_входа_telegram(self):
		self.assertTrue(decisions.is_service_chat("777000"))
		self.assertTrue(decisions.is_service_chat(777000))

	def test_обычный_чат(self):
		for chat_id in ("5559100", "-100777000", None, ""):
			with self.subTest(chat_id):
				self.assertFalse(decisions.is_service_chat(chat_id))


class TestГдеОтвечаетИИ(unittest.TestCase):
	def test_личный_чат(self):
		self.assertTrue(decisions.chat_allows_ai("385520093", "private"))

	def test_группы_только_с_разрешением(self):
		for chat_type in ("group", "supergroup"):
			with self.subTest(chat_type):
				self.assertFalse(decisions.chat_allows_ai("-1001325216994", chat_type))
				self.assertTrue(decisions.chat_allows_ai("-1001325216994", chat_type, reply_in_groups=True))

	def test_никогда(self):
		cases = {
			"канал вещания": ("-1001006840823", "channel", True),
			"служебный чат": ("777000", "private", True),
			"неизвестная группа без типа": ("-1001204445447", None, True),
			"тип неизвестен и id мусорный": ("abc", None, True),
		}
		for name, (chat_id, chat_type, in_groups) in cases.items():
			with self.subTest(name):
				self.assertFalse(decisions.chat_allows_ai(chat_id, chat_type, reply_in_groups=in_groups))

	def test_без_типа_решает_знак_id(self):
		# MTProto не всегда присылает сущность чата — тогда тип пуст, а
		# положительный id в Telegram бывает только у пользователя
		self.assertTrue(decisions.chat_allows_ai("385520093", None))


class TestРазметкаTelegram(unittest.TestCase):
	def test_меню_как_его_пишет_модель(self):
		text = "Здравствуйте! В меню есть:\n\n**Бургеры:**\n- Classic Burger — 2490 KZT  \n- Cola 0.5 L — 690 KZT  \n\nЧто хотите заказать?"
		self.assertEqual(
			decisions.to_telegram_html(text),
			"Здравствуйте! В меню есть:\n\n<b>Бургеры:</b>\n• Classic Burger — 2490 KZT\n• Cola 0.5 L — 690 KZT\n\nЧто хотите заказать?",
		)

	def test_заголовок_жирным(self):
		self.assertEqual(decisions.to_telegram_html("## Меню"), "<b>Меню</b>")

	def test_курсив(self):
		self.assertEqual(decisions.to_telegram_html("*острый* и _сладкий_"), "<i>острый</i> и <i>сладкий</i>")

	def test_список_звёздочками_не_курсив(self):
		self.assertEqual(decisions.to_telegram_html("* Кола\n* Вода"), "• Кола\n• Вода")

	def test_код_не_размечается_внутри(self):
		self.assertEqual(decisions.to_telegram_html("`**x**`"), "<code>**x**</code>")
		self.assertEqual(decisions.to_telegram_html("```\na **b**\n```"), "<pre>a **b**</pre>")

	def test_ссылка(self):
		self.assertEqual(
			decisions.to_telegram_html("[меню](https://example.org/menu)"),
			'<a href="https://example.org/menu">меню</a>',
		)

	def test_обычный_текст_не_трогаем(self):
		for text in ("Привет, как дела?", "snake_case_name", "2 * 3 * 4 = 24", "a_b и c_d"):
			with self.subTest(text):
				self.assertEqual(decisions.to_telegram_html(text), text)

	def test_ошибка_разбора_разметки(self):
		self.assertTrue(decisions.is_parse_error("Bad Request: can't parse entities: Unsupported start tag"))
		self.assertFalse(decisions.is_parse_error("Forbidden: bot was blocked by the user"))


class TestРазбиение(unittest.TestCase):
	def test_короткое_целиком(self):
		self.assertEqual(decisions.split_text("привет", 10), ["привет"])

	def test_ровно_лимит_целиком(self):
		self.assertEqual(decisions.split_text("a" * 10, 10), ["a" * 10])

	def test_режет_по_переводу_строки(self):
		self.assertEqual(decisions.split_text("aaaa\nbbbb\ncc", 10), ["aaaa\nbbbb", "cc"])

	def test_без_переводов_строки_режет_по_лимиту(self):
		self.assertEqual(decisions.split_text("a" * 25, 10), ["a" * 10, "a" * 10, "a" * 5])

	def test_куски_не_длиннее_лимита_и_без_пустых(self):
		text = "\n".join(["строка " * 50] * 40)
		chunks = decisions.split_text(text)
		self.assertTrue(all(0 < len(c) <= decisions.TELEGRAM_TEXT_LIMIT for c in chunks))
		self.assertGreater(len(chunks), 1)
		self.assertEqual("\n".join(chunks), text)


class TestCombine(unittest.TestCase):
	def test_склеивает_через_перевод_строки_без_пометок(self):
		self.assertEqual(
			decisions.combine(["Здравствуйте", "[photo]", " хочу пиццу ", ""]),
			"Здравствуйте\nхочу пиццу",
		)


class TestПраваНаЗапись(unittest.TestCase):
	def test_узнаёт_запрет(self):
		for text in (
			"Forbidden: bot was blocked by the user",
			"Bad Request: not enough rights to send text messages to the chat",
			"You can't write in this chat (caused by SendMessageRequest)",
			"CHAT_WRITE_FORBIDDEN",
			"Not enough rights in this chat",
			"You're banned from sending messages in supergroups/channels (caused by SendMessageRequest)",
		):
			with self.subTest(text):
				self.assertTrue(decisions.is_write_forbidden(text))

	def test_прочие_ошибки_не_запрет(self):
		for text in ("Telegram asks to wait 30 seconds before trying again", "Timeout", ""):
			with self.subTest(text):
				self.assertFalse(decisions.is_write_forbidden(text))


class TestИмена(unittest.TestCase):
	def test_имя_пары(self):
		self.assertEqual(decisions.pair_name("Telegram Bot", "shop", "555"), "Telegram Bot:shop:555")

	def test_внешний_пользователь_движка(self):
		self.assertEqual(
			decisions.external_user("Telegram Account", "manager", "555"),
			"telegram:Telegram Account:manager:555",
		)


def said(direction, content, automated=0):
	return {"direction": direction, "content": content, "is_automated": automated}


class TestИсторияПаузы(unittest.TestCase):
	def test_клиент_пользователь_сотрудник_ассистент_с_пометкой(self):
		self.assertEqual(
			decisions.history_from_pause(
				[said("Incoming", "где заказ?"), said("Outgoing", "везём, 10 минут")]
			),
			[("user", "где заказ?"), ("assistant", "[Ответил сотрудник] везём, 10 минут")],
		)

	def test_ответы_ИИ_пропускаются_они_уже_в_истории_движка(self):
		self.assertEqual(
			decisions.history_from_pause(
				[said("Incoming", "привет"), said("Outgoing", "Здравствуйте!", automated=1)]
			),
			[("user", "привет")],
		)

	def test_подряд_идущие_реплики_одной_роли_склеиваются(self):
		self.assertEqual(
			decisions.history_from_pause(
				[
					said("Incoming", "где заказ?"),
					said("Incoming", "уже час жду"),
					said("Outgoing", "извините"),
					said("Outgoing", "везём"),
				]
			),
			[
				("user", "где заказ?\nуже час жду"),
				("assistant", "[Ответил сотрудник] извините\nвезём"),
			],
		)

	def test_пропущенный_ответ_ИИ_не_рвёт_склейку(self):
		# Между двумя вопросами клиента ответил ИИ — его реплики в истории
		# движка уже есть, а здесь два вопроса подряд остаются одной репликой
		self.assertEqual(
			decisions.history_from_pause(
				[said("Incoming", "а"), said("Outgoing", "ответ ИИ", automated=1), said("Incoming", "б")]
			),
			[("user", "а\nб")],
		)

	def test_пустые_и_вложения_пропускаются(self):
		self.assertEqual(
			decisions.history_from_pause(
				[
					said("Incoming", ""),
					said("Incoming", "[photo]"),
					said("Outgoing", None),
					said("Incoming", " да "),
				]
			),
			[("user", "да")],
		)

	def test_нечего_дописывать(self):
		self.assertEqual(decisions.history_from_pause([]), [])

	def test_чего_бот_не_видит_то_не_дописывается(self):
		# Команды разбирает обработчик бота, ботов и ответы на вопросы входа
		# ИИ тоже не видит — в его историю они не идут
		self.assertEqual(
			decisions.history_from_pause(
				[
					said("Incoming", "/start"),
					dict(said("Incoming", "я бот"), sender_is_bot=1),
					dict(said("Incoming", "+79001234567"), sender_in_dialogue=1),
					said("Incoming", "где заказ?"),
				]
			),
			[("user", "где заказ?")],
		)


def row(name, direction, automated=0):
	return {"name": name, "direction": direction, "is_automated": automated}


class TestОкноПаузы(unittest.TestCase):
	def test_окно_до_последнего_ответа_сотрудника_хвост_после(self):
		rows = [
			row("q1", "Incoming"),
			row("s1", "Outgoing"),
			row("q2", "Incoming"),
			row("s2", "Outgoing"),
			row("ai", "Outgoing", automated=1),
			row("q3", "Incoming"),
		]
		window, trailing = decisions.split_pause_window(rows)
		self.assertEqual([r["name"] for r in window], ["q1", "s1", "q2", "s2"])
		self.assertEqual([r["name"] for r in trailing], ["ai", "q3"])

	def test_без_ответа_сотрудника_всё_остаётся_хвостом(self):
		rows = [row("q1", "Incoming"), row("ai", "Outgoing", automated=1)]
		window, trailing = decisions.split_pause_window(rows)
		self.assertEqual(window, [])
		self.assertEqual(trailing, rows)

	def test_пусто(self):
		self.assertEqual(decisions.split_pause_window([]), ([], []))

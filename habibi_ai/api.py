"""Whitelisted-методы модуля.

Единственное место, где берётся имя сайта. Ни один параметр запроса на tenant
не влияет: клиент не может назваться чужим тенантом, потому что tenant вообще
не читается из запроса.
"""

import uuid

import frappe

from habibi_ai import loop, tools
from habibi_ai.tools.orders import mark_answered
from habibi_ai.engine import BotNotFound, ChatNotFound, EngineClient, EngineError

# Сколько витков цикла допускается на один ход, когда бот не сказал иначе.
# Исчерпание — ошибка, а не молчаливая остановка: модель, которая вызывает
# инструменты и не приходит к ответу, не справляется с задачей имеющимися
# средствами, и человек должен об этом узнать.
MAX_LOOP = loop.DEFAULT_MAX_LOOP

# Ошибки движка, у которых есть понятное объяснение для пользователя. Ключ —
# фрагмент сообщения от движка, значение — что показать в интерфейсе.
KNOWN_ERRORS = {
	"API key not found": (
		"Движок ИИ не настроен: не задан ключ LLM. Пропишите OPENAI_API_KEY "
		"или ANTHROPIC_API_KEY в .env сервера и перезапустите ai-engine."
	),
	"bot_id is required": "У чата не выбран бот.",
}

# Кому положена трассировка обработки. Она содержит system prompt — это
# интеллектуальная собственность владельца инсталляции, а не тенанта, поэтому
# право отдельное и по умолчанию его нет ни у кого.
DEBUG_ROLE = "Habibi AI Debug"


def get_client():
	url = frappe.conf.get("habibi_ai_engine_url")
	token = frappe.conf.get("habibi_ai_engine_token")
	if not url or not token:
		frappe.throw(
			"Движок ИИ не настроен: habibi_ai_engine_url и habibi_ai_engine_token "
			"задаются в common_site_config.json"
		)
	return EngineClient(url, token, frappe.local.site)


def call(method, *args, **kwargs):
	"""Общая обработка ошибок движка.

	Без неё наружу уходит голое "500 Server Error for url: ...", по которому
	нельзя понять ни причину, ни что делать. Сообщение самого движка при этом
	пишется в лог целиком — в интерфейс идёт человеческая формулировка.
	"""
	try:
		return method(*args, **kwargs)
	except ChatNotFound:
		frappe.throw("Чат не найден", frappe.DoesNotExistError)
	except BotNotFound:
		# То же намеренное смешение, что у ChatNotFound: "бот чужой" и "такого
		# бота нет" должны выглядеть для клиента одинаково, иначе по ответу
		# можно перебором узнать чужие id.
		frappe.throw("Бот не найден", frappe.DoesNotExistError)
	except (loop.BadStep, loop.LoopExhausted) as e:
		frappe.throw(str(e))
	except EngineError as e:
		detail = str(e)
		frappe.log_error(title="Ошибка движка ИИ", message=detail)
		for fragment, explanation in KNOWN_ERRORS.items():
			if fragment in detail:
				frappe.throw(explanation)
		frappe.throw(f"Движок ИИ вернул ошибку: {detail}")


@frappe.whitelist()
def list_bots():
	return call(get_client().list_bots)


@frappe.whitelist()
def list_chats():
	return call(get_client().list_chats, frappe.session.user)


@frappe.whitelist()
def create_chat(bot_id):
	"""Заводит чат. Отдельный метод, потому что tenant проставляет сервер."""
	return call(get_client().create_chat, int(bot_id), frappe.session.user)


@frappe.whitelist()
def get_chat(chat_id):
	client = get_client()
	chat = call(_own_chat, client, int(chat_id))
	return {"chat": chat, "messages": call(client.get_messages, int(chat_id))}


def _own_chat(client, chat_id):
	"""Чат движка, только если он принадлежит текущему пользователю.

	Тенант движок проверяет сам, пользователя — нет. А в чатах тенанта
	теперь и переписка клиентов из Telegram: без этой проверки любой
	вошедший читал бы и продолжал её, подобрав номер. Чужой чат — тот же
	ChatNotFound, что и несуществующий, по той же причине, что в engine.
	"""
	chat = client.get_chat(chat_id)
	if chat.get("external_user") != frappe.session.user:
		raise ChatNotFound(chat_id)
	return chat


@frappe.whitelist()
def get_bot_config(bot_id):
	"""Конфигурация бота: глобальный промпт и сценарии с их инструментами.

	Тот же гейт, что у трассировки в send_message, и по той же причине: эти
	тексты — интеллектуальная собственность владельца инсталляции, не
	тенанта. Роль решает сервер (frappe.get_roles()), а не параметр запроса.
	"""
	if DEBUG_ROLE not in frappe.get_roles():
		# raise напрямую, а не frappe.throw: throw уходит в msgprint, которому
		# нужен привязанный к сайту frappe.local — а этот путь как раз обязан
		# проверяться тестами без поднятого сайта, как и остальной модуль.
		raise frappe.PermissionError("Конфигурация бота доступна только роли Habibi AI Debug")
	return call(get_client().get_bot_config, int(bot_id))


@frappe.whitelist()
def send_message(chat_id, message, bot_id=None):
	"""Ход ведёт run_turn; здесь — гейт трассировки и перевод ошибок.

	Флаг трассировки ставит сервер, а не клиент: в теле запроса от браузера
	поля debug нет вообще — ровно так же, как там нет tenant.
	"""
	debug = DEBUG_ROLE in frappe.get_roles()
	client = get_client()
	call(_own_chat, client, int(chat_id))
	result = call(run_turn, client, int(chat_id), message, bot_id, debug)
	# Ответ уходит этим же запросом — расчёт хода дошёл до человека
	mark_answered(result["turn_id"])
	response = {"success": True, "response": result["response"]}
	if result["debug"]:
		response["debug"] = result["debug"]
	return response


def run_turn(client, chat_id, message, bot_id=None, debug=False, channel_chat=None, message_at=None):
	"""Один ход агента — общий для браузера и каналов.

	Ошибки движка и цикла пробрасываются как есть: браузеру их переводит в
	человеческий текст call(), а канальный адаптер решает сам — повторить,
	поставить чат на паузу или оповестить оператора.

	Лимит задаётся полем бота; бот тот же, что и в step: явный bot_id, иначе
	бот чата.

	context собирается здесь, на сервере, и уходит только инструментам: чей
	это чат (channel_chat — канальный, если ход пришёл из канала) и какой это
	ход. turn_id новый на каждый ход, message_at — когда пришло сообщение
	клиента, на которое отвечает ход: по нему create_order узнаёт, что клиент
	ответил уже после расчёта. Канал передаёт время своего последнего
	входящего; консоль его не передаёт — там сообщение пришло сейчас.
	"""
	max_loop = loop.resolve_max_loop(client.get_max_loop(chat_id, bot_id), MAX_LOOP)
	offered = _tool_names()
	context = {
		"turn_id": uuid.uuid4().hex,
		"engine_chat_id": chat_id,
		"channel_chat": channel_chat,
		"message_at": message_at or frappe.utils.now_datetime(),
	}
	result = loop.run(
		lambda text, **kwargs: client.step(chat_id, text, bot_id, **kwargs),
		message,
		offered=offered,
		definitions=tools.definitions(offered),
		execute=lambda name, args: tools.execute(name, args, context),
		max_loop=max_loop,
		debug=debug,
	)
	# Канал отметит расчёты хода отправленными, когда ответ реально уйдёт
	result["turn_id"] = context["turn_id"]
	return result


def _tool_names():
	"""Какие инструменты habibi_ai готов исполнить.

	Пока весь реестр: отбор по сценариям делает движок, сверяя присланное с
	конфигурацией. Здесь остаётся граница «что вообще существует в коде».
	"""
	return sorted(tools.registry())

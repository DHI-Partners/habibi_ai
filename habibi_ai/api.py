"""Whitelisted-методы модуля.

Единственное место, где берётся имя сайта. Ни один параметр запроса на tenant
не влияет: клиент не может назваться чужим тенантом, потому что tenant вообще
не читается из запроса.
"""

import frappe

from habibi_ai.engine import BotNotFound, ChatNotFound, EngineClient, EngineError

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
	chat = call(client.get_chat, int(chat_id))
	return {"chat": chat, "messages": call(client.get_messages, int(chat_id))}


@frappe.whitelist()
def get_bot_config(bot_id):
	"""Конфигурация бота: глобальный промпт, роутер намерений, сценарии.

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
	"""Флаг трассировки ставит сервер, а не клиент.

	В теле запроса от браузера поля debug нет вообще — ровно так же, как там
	нет tenant. Иначе трассировку мог бы запросить любой пользователь тенанта.
	"""
	debug = DEBUG_ROLE in frappe.get_roles()
	return call(get_client().send_message, int(chat_id), message, bot_id, debug=debug)

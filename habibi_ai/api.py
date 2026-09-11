"""Whitelisted-методы модуля.

Единственное место, где берётся имя сайта. Ни один параметр запроса на tenant
не влияет: клиент не может назваться чужим тенантом, потому что tenant вообще
не читается из запроса.
"""

import frappe

from habibi_ai import tools
from habibi_ai.engine import BotNotFound, ChatNotFound, EngineClient, EngineError

# Сколько витков цикла допускается на один ход, когда бот не сказал иначе.
# Исчерпание — ошибка, а не молчаливая остановка: модель, которая вызывает
# инструменты и не приходит к ответу, не справляется с задачей имеющимися
# средствами, и человек должен об этом узнать.
MAX_LOOP = 8

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
	"""Ведёт цикл: движок решает, мы исполняем, пока не получим текст.

	Флаг трассировки ставит сервер, а не клиент: в теле запроса от браузера
	поля debug нет вообще — ровно так же, как там нет tenant.
	"""
	debug = DEBUG_ROLE in frappe.get_roles()
	client = get_client()

	# Лимит задаётся полем бота, а константа — только запасной вариант на
	# случай пустого поля. Непригодное значение (ноль, минус, мусор из ручной
	# правки) молча выродило бы цикл в одну ошибку «не смог ответить», поэтому
	# в дело идёт только положительное целое.
	configured = call(client.get_max_loop, int(chat_id), bot_id)
	max_loop = configured if isinstance(configured, int) and configured > 0 else MAX_LOOP

	turn = []
	collected_debug = []

	for _ in range(max_loop):
		step = call(
			client.step,
			int(chat_id),
			message,
			bot_id,
			turn=list(turn),
			tools=tools.definitions(_tool_names()),
			debug=debug,
		)

		if debug and step.get("debug"):
			collected_debug.extend(step["debug"])

		if step.get("type") == "text":
			result = {"success": True, "response": step.get("content", "")}
			if collected_debug:
				result["debug"] = collected_debug
			return result

		# Контракт шага фиксирован движком, но проверяем его здесь: без этого
		# чужой или устаревший ответ падал бы голым KeyError в логах прокси, и
		# причину искали бы в habibi_ai вместо движка, который её и создал.
		if step.get("type") != "tool_use" or not step.get("id") or not step.get("name"):
			frappe.throw(f"Движок вернул шаг неизвестной формы: {step!r}")

		# Вызов инструмента и его результат идут в turn парой — движку нужны
		# оба, чтобы на следующем витке видеть, что именно уже было исполнено.
		# В chat_messages они не попадают: это внутренняя кухня хода, а не
		# история переписки с пользователем.
		turn.append(
			{"type": "tool_use", "id": step["id"], "name": step["name"], "input": step.get("input") or {}}
		)
		turn.append(
			{"type": "tool_result", "id": step["id"], "content": tools.execute(step["name"], step.get("input") or {})}
		)

	# Предел исчерпан: модель зациклилась на вызовах инструментов и не пришла
	# к ответу. Молчаливая остановка скрыла бы это — пользователь должен
	# увидеть внятную ошибку, а не зависший чат.
	frappe.throw(
		f"Бот не смог завершить ответ за {max_loop} обращений к инструментам. "
		"Проверьте инструкции сценариев в трассировке."
	)


def _tool_names():
	"""Какие инструменты habibi_ai готов исполнить.

	Пока весь реестр: отбор по сценариям делает движок, сверяя присланное с
	конфигурацией. Здесь остаётся граница «что вообще существует в коде».
	"""
	return sorted(tools.registry())

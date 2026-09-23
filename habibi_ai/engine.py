"""Клиент движка ИИ (Directus).

Не импортирует frappe умышленно: изоляция тенантов — единственное место, где
ошибка означает чужую переписку в ответе, и она должна быть покрыта быстрыми
тестами, а не проверяться вручную на поднятом сайте.
"""

import json

import requests

TIMEOUT = 60

# Длина заголовка и превью в списке чатов. Названия у чата нет: поля под него в
# customer_chats не существует, а заводить его в общей продовой схеме ради
# подписи в списке — дороже, чем достать из сообщений.
EXCERPT = 60

# Верхняя граница на число сообщений, которые _previews вообще смотрит — по
# всем чатам сразу, а не на чат. Без неё "limit: -1" тащит из продового
# Directus всю историю каждого чата целиком на каждый заход в раздел и после
# каждого отправленного сообщения, ради двух строк по 60 символов. Сообщения
# отсортированы chat_id,sort (по возрастанию), поэтому заголовок — первое
# сообщение пользователя — почти всегда попадает в лимит: чат начинается с
# него. Расплата — у чата длиннее лимита preview перестаёт быть последним
# сообщением и застревает на том, что попало в срез. Для ярлыка в списке
# чатов это приемлемо: это не история переписки, а её подпись, и устаревшая
# на несколько сообщений подпись всё ещё узнаваема.
PREVIEW_MESSAGES_LIMIT = 500


def scoped_filter(tenant, extra=None, allow_shared=False):
	"""Фильтр Directus, ограничивающий выборку одним тенантом.

	Единственное место, где строится это условие. Отдельный фильтр в каждом
	методе означал бы, что про один из них однажды забудут, а цена такой
	забывчивости — чужая переписка в ответе.

	allow_shared=True добавляет записи без тенанта: общие боты и промпты,
	доступные всем сайтам. Для чатов и сообщений так делать нельзя — там
	tenant обязателен на уровне схемы.
	"""
	if not tenant:
		raise ValueError("tenant обязателен")

	own = {"tenant": {"_eq": tenant}}
	base = {"_or": [own, {"tenant": {"_null": True}}]} if allow_shared else own

	if extra:
		return {"_and": [base, extra]}
	return base


class EngineError(Exception):
	"""Движок ответил ошибкой.

	Отдельный класс, чтобы наружу шло сообщение самого движка, а не голое
	"500 Server Error": причина (нет ключа LLM, не настроен бот) лежит в теле
	ответа, и без неё пользователь видит цифру и ничего больше.
	"""


class ChatNotFound(Exception):
	"""Чат не существует либо принадлежит другому тенанту.

	Один класс на оба случая намеренно: разные ошибки позволили бы перебором
	номеров узнать, какие чаты есть у соседей.
	"""


class BotNotFound(Exception):
	"""Бот не существует либо не принадлежит тенанту (и не общий).

	Как и ChatNotFound — один класс на оба случая: отдельная ошибка для "бот
	есть, но чужой" позволила бы перебором id узнать, какие боты вообще
	существуют у других тенантов.
	"""


class EngineClient:
	def __init__(self, url, token, tenant):
		if not tenant:
			raise ValueError("tenant обязателен")
		self.url = url.rstrip("/")
		self.tenant = tenant
		self.session = requests.Session()
		self.session.headers["Authorization"] = f"Bearer {token}"

	def _check(self, response):
		"""Превращает ответ с ошибкой в EngineError с текстом причины."""
		if response.status_code < 400:
			return

		detail = ""
		try:
			errors = response.json().get("errors") or []
			detail = "; ".join(e.get("message", "") for e in errors if e.get("message"))
		except ValueError:
			# Не JSON — например, страница ошибки от прокси.
			detail = ""

		if detail:
			raise EngineError(detail)
		raise EngineError(f"движок ответил {response.status_code} на {response.url}")

	def _items(self, collection, params):
		# filter уходит JSON-строкой: словарь requests разложил бы в query по
		# ключам верхнего уровня, и Directus ответил бы 400 на filter=_or.
		query = dict(params)
		if isinstance(query.get("filter"), dict):
			query["filter"] = json.dumps(query["filter"])

		response = self.session.get(
			f"{self.url}/items/{collection}", params=query, timeout=TIMEOUT
		)
		self._check(response)
		return response.json().get("data", [])

	def _post(self, path, payload):
		response = self.session.post(
			f"{self.url}/{path.lstrip('/')}", json=payload, timeout=TIMEOUT
		)
		self._check(response)
		return response.json()

	def list_bots(self):
		"""Боты тенанта плюс общие."""
		return self._items(
			"ai_bots",
			{
				"filter": scoped_filter(self.tenant, allow_shared=True),
				"fields": "id,name,person_key,avatar",
				"sort": "name",
			},
		)

	def list_chats(self, external_user):
		"""Чаты тенанта, заведённые этим пользователем, с подписью."""
		chats = self._items(
			"customer_chats",
			{
				"filter": scoped_filter(self.tenant, {"external_user": {"_eq": external_user}}),
				"fields": "id,bot_id",
				"sort": "-id",
			},
		)
		if not chats:
			return []

		previews = self._previews([chat["id"] for chat in chats])
		for chat in chats:
			chat.update(previews.get(chat["id"], {"title": "", "preview": ""}))
		return chats

	def _previews(self, chat_ids):
		"""Заголовок и превью для каждого чата одним запросом.

		Один запрос на все чаты, а не по запросу на чат: список открывается на
		каждый заход в раздел, и N+1 здесь виден глазом.
		"""
		messages = self._items(
			"chat_messages",
			{
				"filter": scoped_filter(self.tenant, {"chat_id": {"_in": chat_ids}}),
				"fields": "chat_id,role,content,sort",
				"sort": "chat_id,sort",
				"limit": PREVIEW_MESSAGES_LIMIT,
			},
		)

		result = {}
		for message in messages:
			entry = result.setdefault(message["chat_id"], {"title": "", "preview": ""})
			content = (message.get("content") or "")[:EXCERPT]
			if not entry["title"] and message.get("role") == "user":
				entry["title"] = content
			entry["preview"] = content
		return result

	def _check_bot(self, bot_id):
		"""Убеждается, что bot_id — свой или общий бот тенанта.

		bot_id приезжает из браузера непроверенным. Без этой проверки чужой
		числовой id ушёл бы в движок с сервисным токеном, который тенантов не
		различает, и ответил бы, используя global_system_prompt чужого бота —
		вплоть до того, что его можно было бы попросить пересказать.
		"""
		bots = self._items(
			"ai_bots",
			{
				"filter": scoped_filter(self.tenant, {"id": {"_eq": bot_id}}, allow_shared=True),
				"fields": "id",
				"limit": 1,
			},
		)
		if not bots:
			raise BotNotFound(bot_id)

	def get_bot_config(self, bot_id):
		"""Конфигурация бота, которая на самом деле его определяет.

		В Directus это три коллекции: у ai_bots — персона и факты о бизнесе
		прозой в global_system_prompt; у chatbot_scenarios — по строке на
		сценарий, но её initial_prompt — числовая ссылка, а не текст, так что
		сценарий сам по себе выглядит пустым. Здесь всё сведено в один ответ
		с уже подставленным текстом промпта вместо его id — как list_chats
		сводит чаты и сообщения.

		Кто имеет право это увидеть — решает api.py, не этот метод: тексты
		промптов защищены тем же гейтом, что и трассировка send_message.
		"""
		bots = self._items(
			"ai_bots",
			{
				"filter": scoped_filter(self.tenant, {"id": {"_eq": bot_id}}, allow_shared=True),
				"fields": "id,name,global_system_prompt",
				"limit": 1,
			},
		)
		if not bots:
			raise BotNotFound(bot_id)
		bot = bots[0]

		scenarios = self._items(
			"chatbot_scenarios",
			{
				"filter": scoped_filter(self.tenant, {"bot_id": {"_eq": bot_id}}, allow_shared=True),
				"fields": "scenario_key,description,initial_prompt,tools",
				"sort": "scenario_key",
			},
		)

		prompts_by_id = self._prompts_by_id(
			[s["initial_prompt"] for s in scenarios if s.get("initial_prompt")]
		)

		return {
			"bot": bot,
			"scenarios": [
				{
					"scenario_key": s["scenario_key"],
					"description": s.get("description"),
					# Имена инструментов, а не лимиты истории и стека: стека больше
					# нет, а длину истории ядро не режет. Консоль отладки должна
					# показывать то, что на самом деле уходит в модель.
					"tools": s.get("tools") or [],
					"prompt": prompts_by_id.get(s.get("initial_prompt"), ""),
				}
				for s in scenarios
			],
		}

	def _prompts_by_id(self, prompt_ids):
		"""Тексты промптов сценариев одним запросом.

		Тот же приём, что и _previews: без него на каждый сценарий бота ушёл
		бы отдельный запрос к ai_prompts, а сценариев у бота обычно несколько.
		"""
		if not prompt_ids:
			return {}
		prompts = self._items(
			"ai_prompts",
			{
				"filter": scoped_filter(self.tenant, {"id": {"_in": prompt_ids}}, allow_shared=True),
				"fields": "id,system_prompt",
			},
		)
		return {p["id"]: p.get("system_prompt") for p in prompts}

	def create_chat(self, bot_id, external_user):
		"""Заводит чат от имени тенанта.

		Создавать чат должен именно прокси: tenant в customer_chats обязателен,
		а расширение движка о тенантах не знает — чат, созданный им самим,
		не пройдёт INSERT. _check_bot идёт до _post по той же причине, что
		get_chat идёт до step: движок бы принял чужой bot_id без
		возражений.
		"""
		self._check_bot(bot_id)
		payload = {
			"bot_id": bot_id,
			"tenant": self.tenant,
			"external_user": external_user,
		}
		created = self._post("items/customer_chats", payload)
		return created["data"] if isinstance(created, dict) and "data" in created else created

	def get_chat(self, chat_id):
		chats = self._items(
			"customer_chats",
			{
				"filter": scoped_filter(self.tenant, {"id": {"_eq": chat_id}}),
				"fields": "*",
				"limit": 1,
			},
		)
		if not chats:
			raise ChatNotFound(chat_id)
		return chats[0]

	def get_messages(self, chat_id):
		"""История чата. Принадлежность проверяется до выборки сообщений."""
		self.get_chat(chat_id)
		return self._items(
			"chat_messages",
			{
				"filter": scoped_filter(self.tenant, {"chat_id": {"_eq": chat_id}}),
				"fields": "id,role,content,date_created",
				"sort": "sort,date_created",
			},
		)

	def add_messages(self, chat_id, messages):
		"""Дописывает реплики [(роль, текст)] в конец истории чата движка.

		Нужно, когда разговор шёл мимо движка — сотрудник вёл чат вручную:
		иначе бот, вернувшись, не знал бы, о чём уже договорились. Роли только
		user и assistant — те, что читает сам движок; system в истории был бы
		инструкцией модели, взятой из переписки.

		sort — как в createMessage движка: продолжение после текущего максимума
		чата. Принадлежность чата проверяется get_chat до записи: сервисный
		токен тенантов не различает и дописал бы в чужую историю.
		"""
		if not messages:
			return []
		for role, _content in messages:
			if role not in ("user", "assistant"):
				raise ValueError(f"роль {role!r} в истории чата недопустима")

		self.get_chat(chat_id)
		last = self._items(
			"chat_messages",
			{
				"filter": scoped_filter(self.tenant, {"chat_id": {"_eq": chat_id}}),
				"fields": "sort",
				"sort": "-sort",
				"limit": 1,
			},
		)
		start = (last[0].get("sort") or 0) if last else 0

		# Одним запросом: Directus принимает массив, и история не остаётся
		# дописанной наполовину, если запрос оборвётся посередине
		payload = [
			{"chat_id": chat_id, "role": role, "content": content, "sort": start + i, "tenant": self.tenant}
			for i, (role, content) in enumerate(messages, start=1)
		]
		return self._post("items/chat_messages", payload)

	def get_max_loop(self, chat_id, bot_id=None):
		"""Сколько витков цикла разрешено этому боту, или None, если не задано.

		Лимит — свойство бота, а не кода: сценарию, где инструменты идут
		цепочкой (посмотреть меню, проверить остаток, создать заказ), витков
		нужно больше, чем боту, который только отвечает текстом. Пустое поле
		означает «значение по умолчанию», и решает его вызывающий — здесь мы
		честно отдаём None, а не подставляем число, о котором движок не знает.

		Бот берётся тот же, что и в step: явный bot_id, иначе бот чата. Чтение
		идёт через scoped_filter, поэтому чужой id не даст ни лимита, ни факта
		существования бота.
		"""
		if bot_id is None:
			bot_id = self.get_chat(chat_id).get("bot_id")
		if bot_id is None:
			return None

		bots = self._items(
			"ai_bots",
			{
				"filter": scoped_filter(self.tenant, {"id": {"_eq": bot_id}}, allow_shared=True),
				"fields": "max_loop",
				"limit": 1,
			},
		)
		if not bots:
			raise BotNotFound(bot_id)
		return bots[0].get("max_loop")

	def step(self, chat_id, message, bot_id=None, turn=None, tools=None, debug=False, tenant_context=None):
		"""Один шаг обработки: движок отвечает текстом либо просит вызвать инструмент.

		get_chat вызывается ДО обращения к движку намеренно: сам endpoint о
		тенантах ничего не знает, и без этой проверки номер чужого чата ушёл бы
		в него в обход фильтра. Тем же образом bot_id, если его передали,
		проверяется _check_bot — цикл в api.send_message вызывает step на
		каждом витке с одним и тем же bot_id, но именно из браузера он приходит
		непроверенным, и без этой проверки чужой числовой id ушёл бы в движок с
		сервисным токеном на каждом из витков.

		Цикл ведёт вызывающий (api.send_message), а не движок: инструменты
		исполняются под правами тенанта, и учётные данные тенантов движку не
		нужны и не передаются.
		"""
		self.get_chat(chat_id)
		if bot_id is not None:
			self._check_bot(bot_id)

		payload = {"chat_id": chat_id, "user_message": message, "turn": turn or [], "tools": tools or []}
		if bot_id is not None:
			payload["bot_id"] = bot_id
		if debug:
			payload["debug"] = True
		# Данные компании из кабинета. Старый движок поле игнорирует — поэтому
		# выкатка habibi_ai и движка не обязана быть одновременной.
		if tenant_context:
			payload["tenant_context"] = tenant_context

		return self._post("ai-process-message", payload)

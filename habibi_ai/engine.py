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
				"fields": "id,bot_id,current_scenario",
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
				"limit": -1,
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

	def create_chat(self, bot_id, external_user):
		"""Заводит чат от имени тенанта.

		Создавать чат должен именно прокси: tenant в customer_chats обязателен,
		а расширение движка о тенантах не знает — чат, созданный им самим,
		не пройдёт INSERT. _check_bot идёт до _post по той же причине, что
		get_chat идёт до send_message: движок бы принял чужой bot_id без
		возражений.
		"""
		self._check_bot(bot_id)
		payload = {
			"bot_id": bot_id,
			"tenant": self.tenant,
			"external_user": external_user,
			"scenario_stack": [],
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

	def send_message(self, chat_id, message, bot_id=None, debug=False):
		"""Отправка сообщения в движок.

		get_chat вызывается ДО обращения к движку намеренно: сам endpoint
		ai-process-message о тенантах ничего не знает, и без этой проверки
		номер чужого чата ушёл бы в него в обход фильтра. Тем же образом
		bot_id, если его передали (смена бота внутри чата), проверяется
		_check_bot — иначе браузер мог бы подставить чужого бота в свой же
		чат. Когда bot_id не передан, движок берёт бот из chat.bot_id, а тот
		уже проверен: чужим он быть не может, потому что create_chat сам
		проходит через _check_bot.

		debug решает вызывающий, а не клиент: трассировка содержит system
		prompt, и право на неё — вопрос ролей, о которых engine.py не знает.
		"""
		self.get_chat(chat_id)
		if bot_id is not None:
			self._check_bot(bot_id)
		payload = {"chat_id": chat_id, "user_message": message}
		if bot_id is not None:
			payload["bot_id"] = bot_id
		if debug:
			payload["debug"] = True
		return self._post("ai-process-message", payload)

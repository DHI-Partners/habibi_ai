"""Клиент движка ИИ (Directus).

Не импортирует frappe умышленно: изоляция тенантов — единственное место, где
ошибка означает чужую переписку в ответе, и она должна быть покрыта быстрыми
тестами, а не проверяться вручную на поднятом сайте.
"""

import json

import requests

TIMEOUT = 60


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
		"""Чаты тенанта, заведённые этим пользователем."""
		return self._items(
			"customer_chats",
			{
				"filter": scoped_filter(self.tenant, {"external_user": {"_eq": external_user}}),
				"fields": "id,bot_id,current_scenario",
				"sort": "-id",
			},
		)

	def create_chat(self, bot_id, external_user):
		"""Заводит чат от имени тенанта.

		Создавать чат должен именно прокси: tenant в customer_chats обязателен,
		а расширение движка о тенантах не знает — чат, созданный им самим,
		не пройдёт INSERT.
		"""
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

	def send_message(self, chat_id, message, bot_id=None):
		"""Отправка сообщения в движок.

		get_chat вызывается ДО обращения к движку намеренно: сам endpoint
		ai-process-message о тенантах ничего не знает, и без этой проверки
		номер чужого чата ушёл бы в него в обход фильтра.
		"""
		self.get_chat(chat_id)
		payload = {"chat_id": chat_id, "user_message": message}
		if bot_id is not None:
			payload["bot_id"] = bot_id
		return self._post("ai-process-message", payload)

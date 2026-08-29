"""Клиент движка ИИ (Directus).

Не импортирует frappe умышленно: изоляция тенантов — единственное место, где
ошибка означает чужую переписку в ответе, и она должна быть покрыта быстрыми
тестами, а не проверяться вручную на поднятом сайте.
"""

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

	def _items(self, collection, params):
		response = self.session.get(
			f"{self.url}/items/{collection}", params=params, timeout=TIMEOUT
		)
		response.raise_for_status()
		return response.json().get("data", [])

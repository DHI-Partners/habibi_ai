"""Реестр инструментов агента.

Инструменты объявляются кодом, а не в админке: аргументы приходят от языковой
модели, и проверка их правдоподобия — это то, что должно лежать под тестами и
ревью. Сценарий в Directus только выбирает имена из объявленного здесь.
"""

_REGISTRY = {}


def tool(name, description, input_schema, context=False):
	"""Объявляет функцию инструментом, видимым модели.

	context=True — инструмент получает серверный контекст хода аргументом
	context: чей это чат и какой это ход. Модели он не виден и подменить его
	она не может — из него инструмент заказа узнаёт клиента.
	"""

	def decorator(func):
		_REGISTRY[name] = {
			"name": name,
			"description": description,
			"input_schema": input_schema,
			"run": func,
			"context": context,
		}
		return func

	return decorator


def registry():
	return dict(_REGISTRY)


def definitions(names):
	"""Описания для модели — только по запрошенным именам.

	Неизвестное имя пропускается: конфигурацию правят без ревью, и опечатка не
	повод обрывать диалог. Движок такие имена показывает в трассировке.
	"""
	return [
		{k: v for k, v in _REGISTRY[n].items() if k not in ("run", "context")}
		for n in names
		if n in _REGISTRY
	]


def execute(name, args, context=None):
	"""Исполняет инструмент, всегда возвращая строку для модели.

	Отказ — тоже строка, а не исключение: модель должна увидеть причину и
	исправиться сама. Исключение оборвало бы весь ход.

	Ключ context из аргументов модели выбрасывается до вызова: иначе модель
	могла бы подложить чужой чат туда, где инструмент ждёт серверный.
	"""
	entry = _REGISTRY.get(name)
	if entry is None:
		return f"Инструмент {name} недоступен. Доступные: {', '.join(sorted(_REGISTRY))}"

	args = {k: v for k, v in (args or {}).items() if k != "context"}
	try:
		if entry["context"]:
			return entry["run"](context=context or {}, **args)
		return entry["run"](**args)
	except TypeError as e:
		return f"Неверные аргументы для {name}: {e}"
	except Exception as e:
		return f"Инструмент {name} завершился ошибкой: {e}"


from habibi_ai.tools import menu  # noqa: E402,F401  регистрация при импорте пакета
from habibi_ai.tools import delivery  # noqa: E402,F401  регистрация при импорте пакета
from habibi_ai.tools import hours  # noqa: E402,F401  регистрация при импорте пакета

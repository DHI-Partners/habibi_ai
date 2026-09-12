"""Реестр инструментов агента.

Инструменты объявляются кодом, а не в админке: аргументы приходят от языковой
модели, и проверка их правдоподобия — это то, что должно лежать под тестами и
ревью. Сценарий в Directus только выбирает имена из объявленного здесь.
"""

_REGISTRY = {}


def tool(name, description, input_schema):
	"""Объявляет функцию инструментом, видимым модели."""

	def decorator(func):
		_REGISTRY[name] = {
			"name": name,
			"description": description,
			"input_schema": input_schema,
			"run": func,
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
		{k: v for k, v in _REGISTRY[n].items() if k != "run"}
		for n in names
		if n in _REGISTRY
	]


def execute(name, args):
	"""Исполняет инструмент, всегда возвращая строку для модели.

	Отказ — тоже строка, а не исключение: модель должна увидеть причину и
	исправиться сама. Исключение оборвало бы весь ход.
	"""
	entry = _REGISTRY.get(name)
	if entry is None:
		return f"Инструмент {name} недоступен. Доступные: {', '.join(sorted(_REGISTRY))}"

	try:
		return entry["run"](**(args or {}))
	except TypeError as e:
		return f"Неверные аргументы для {name}: {e}"
	except Exception as e:
		return f"Инструмент {name} завершился ошибкой: {e}"


from habibi_ai.tools import menu  # noqa: E402,F401  регистрация при импорте пакета
from habibi_ai.tools import delivery  # noqa: E402,F401  регистрация при импорте пакета

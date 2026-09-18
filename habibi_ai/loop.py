"""Цикл хода агента: движок решает, вызывающий исполняет, пока не будет текста.

Не импортирует frappe — по той же причине, что engine.py, и ещё по одной:
вместе они и есть переносимая часть ИИ-модуля. Системе не на Frappe хватит
этих двух файлов и своего клея — откуда сообщение, как исполнить инструмент,
куда отправить ответ.

Инструменты исполняет вызывающий, а не движок: они работают под правами
тенанта, и учётные данные тенантов движку не нужны и не передаются.
"""

DEFAULT_MAX_LOOP = 8


class BadStep(Exception):
	"""Движок вернул шаг неизвестной формы.

	Контракт шага фиксирован движком, но проверяем его здесь: без этого чужой
	или устаревший ответ падал бы голым KeyError, и причину искали бы в
	habibi_ai вместо движка, который её и создал.
	"""

	def __init__(self, step):
		super().__init__(f"Движок вернул шаг неизвестной формы: {step!r}")
		self.step = step


class LoopExhausted(Exception):
	"""Модель зациклилась на вызовах инструментов и не пришла к ответу.

	Молчаливая остановка скрыла бы это — человек должен увидеть внятную
	ошибку, а не зависший чат.
	"""

	def __init__(self, max_loop):
		super().__init__(
			f"Бот не смог завершить ответ за {max_loop} обращений к инструментам. "
			"Проверьте инструкции сценариев в трассировке."
		)
		self.max_loop = max_loop


def resolve_max_loop(configured, default=DEFAULT_MAX_LOOP):
	"""Лимит витков из поля бота либо значение по умолчанию.

	Непригодное значение (ноль, минус, мусор из ручной правки) молча выродило
	бы цикл в одну ошибку «не смог ответить», поэтому в дело идёт только
	положительное целое. bool — подкласс int, его отсекаем явно.
	"""
	if isinstance(configured, int) and not isinstance(configured, bool) and configured > 0:
		return configured
	return default


def run(step, message, *, offered, definitions, execute, max_loop, debug=False):
	"""Ведёт ход до текстового ответа.

	step(message, *, turn, tools, debug) — один шаг движка (EngineClient.step
	с уже привязанными chat_id и bot_id); execute(name, args) — исполнение
	инструмента, всегда строка. offered — имена, которые предложены модели на
	этом ходу: исполняется только имя из этого списка.
	"""
	turn = []
	collected_debug = []

	for iteration in range(1, max_loop + 1):
		# Собственный шаг трассировки цикла, а не движка: номер витка и лимит
		# известны только здесь. Кладём его ДО обращения к движку за этот
		# виток, чтобы граница витков в трассировке была видна.
		if debug:
			collected_debug.append({"step": "loop", "data": {"iteration": iteration, "max_loop": max_loop}})

		result = step(message, turn=list(turn), tools=definitions, debug=debug)

		if debug and result.get("debug"):
			collected_debug.extend(result["debug"])

		if result.get("type") == "text":
			return {"response": result.get("content", ""), "debug": collected_debug}

		if result.get("type") != "tool_use" or not result.get("id") or not result.get("name"):
			raise BadStep(result)

		# Вызов и результат идут в turn парой — движку нужны оба, чтобы на
		# следующем витке видеть, что уже исполнено. В историю переписки они
		# не попадают: это внутренняя кухня хода.
		entry = {
			"type": "tool_use",
			"id": result["id"],
			"name": result["name"],
			"input": result.get("input") or {},
		}
		# raw — ответ модели целиком, как его прислал движок. Цикл его не
		# читает, только возит: у моделей с адаптивным мышлением рядом с
		# tool_use лежат блоки thinking, и собранный заново виток без них
		# провайдер отвергает. Нет поля — нет и ключа: для движка отсутствие и
		# пустое значение не одно и то же.
		if result.get("raw") is not None:
			entry["raw"] = result["raw"]
		turn.append(entry)

		if result["name"] in offered:
			content = execute(result["name"], result.get("input") or {})
		else:
			# Движок решает, что предложить модели, но не что исполнять под
			# правами тенанта. Модель получает отказ текстом и может
			# исправиться на следующем витке.
			content = (
				f"Инструмент {result['name']} не был предложен на этом ходу. "
				f"Доступные: {', '.join(offered)}"
			)
			if debug:
				collected_debug.append(
					{"step": "tool_rejected", "data": {"name": result["name"], "offered": list(offered)}}
				)

		turn.append({"type": "tool_result", "id": result["id"], "content": content})

	raise LoopExhausted(max_loop)

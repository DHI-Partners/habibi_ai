"""Цикл хода агента: движок решает, вызывающий исполняет, пока не будет текста.

Не импортирует frappe — по той же причине, что engine.py, и ещё по одной:
вместе они и есть переносимая часть ИИ-модуля. Системе не на Frappe хватит
этих двух файлов и своего клея — откуда сообщение, как исполнить инструмент,
куда отправить ответ.

Инструменты исполняет вызывающий, а не движок: они работают под правами
тенанта, и учётные данные тенантов движку не нужны и не передаются.

Страж обязательств живёт здесь, потому что только цикл видит и текст модели, и
результаты инструментов хода. Про предметную область он ничего не знает:
обязательства приходят снаружи.
"""

from habibi_ai.agent.checks import VIOLATED, check_commitment

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


FALLBACK = "Не удалось выполнить действие. Повторите, пожалуйста, просьбу или свяжитесь с оператором."


def _emit(on_event, kind, name, detail=""):
	"""Сообщает о событии стража. Сбой колбэка не должен стоить клиенту ответа."""
	if on_event is None:
		return
	try:
		on_event({"kind": kind, "commitment": name, "detail": str(detail)[:200]})
	except Exception:
		pass


def _violated(commitments, text, turn, known, on_event):
	"""Первое обязательство, которое текст нарушает, или None.

	Сбой самой проверки — не повод молчать или ронять ход: страж добавляет
	надёжности и не должен сам её отнимать.
	"""
	for commitment in commitments:
		try:
			if check_commitment(commitment, text, turn, known).status == VIOLATED:
				return commitment
		except Exception as e:
			_emit(on_event, "guard_error", commitment.name, repr(e))
	return None


def _recap(commitment, turn):
	try:
		return commitment.recap(turn) or FALLBACK
	except Exception:
		return FALLBACK


def _answer(text, debug, step_result, used=()):
	answer = {"response": text, "debug": debug}
	# Движок не сохранил ответ (persist_answer: false) — сохранить итоговый
	# текст должен вызывающий. Ключ только тогда: старый движок пишет сам, и
	# вызывающий не должен дублировать реплику.
	if step_result.get("persisted") is False:
		answer["unpersisted"] = True
	# Какие инструменты реально вызывались: из них код собирает метку ответа
	# в журнале. Ключа нет, если инструментов не было — прежний формат не меняется.
	if used:
		answer["tools"] = list(dict.fromkeys(used))
	return answer


def run(
	step,
	message,
	*,
	offered,
	definitions,
	execute,
	max_loop,
	debug=False,
	commitments=(),
	known=frozenset(),
	on_event=None,
):
	"""Ведёт ход до текстового ответа.

	step(message, *, turn, tools, debug) — один шаг движка (EngineClient.step
	с уже привязанными chat_id и bot_id); execute(name, args) — исполнение
	инструмента, всегда строка. offered — имена, которые предложены модели на
	этом ходу: исполняется только имя из этого списка.

	commitments — обязательства (name, claims, confirmed, fulfil, recap), known —
	номера, известные из предыдущих ходов, on_event(dict) — журнал событий стража.
	Текст, утверждающий несовершённое действие, клиенту не уходит: действие
	довыполняет код, а ответ строится по его результату.
	"""
	used = []
	fulfilled = set()
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
			text = result.get("content", "")
			violated = _violated(commitments, text, turn, known, on_event)
			if violated is None:
				return _answer(text, collected_debug, result, used)

			_emit(on_event, "violated", violated.name, text)
			if debug:
				collected_debug.append(
					{"step": "commitment_violation", "data": {"name": violated.name, "text": text[:200]}}
				)
			if violated.name in fulfilled:
				# Довыполнили, а модель снова утверждает своё: клиенту уходит
				# то, что собрал код по результату инструмента, не её текст
				return _answer(_recap(violated, turn), collected_debug, result, used)
			if violated.fulfil not in offered:
				# Инструмент недоступен (стадия не та): действия не будет, а
				# ложное утверждение наружу выходить не должно
				return _answer(_recap(violated, turn), collected_debug, result, used)

			# Согласие клиента получено, действие не совершено — совершает код,
			# а модель на следующем витке пересказывает настоящий результат.
			# Пара в turn — как у обычного вызова, без raw: формат поддержан
			# обоими провайдерами.
			fulfilled.add(violated.name)
			guard_id = f"guard-{len(fulfilled)}"
			turn.append({"type": "tool_use", "id": guard_id, "name": violated.fulfil, "input": {}})
			content = execute(violated.fulfil, {})
			used.append(violated.fulfil)
			turn.append({"type": "tool_result", "id": guard_id, "content": content})
			_emit(on_event, "fulfilled", violated.name, content)
			if iteration == max_loop:
				# Виток на пересказ уже некому потратить: вместо LoopExhausted
				# после совершённого действия клиент получает пересказ кода
				return _answer(_recap(violated, turn), collected_debug, result, used)
			continue

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
			used.append(result["name"])
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

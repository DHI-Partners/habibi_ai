"""Реестр модулей агента: что подключается к ядру одной записью.

Модуль — заказы, а завтра запись, напоминания, задачи — объявляет свои
правила стадий и обязательства. Проекция (state.py) и цикл про предметную
область не знают ничего и берут правила отсюда. Без frappe, как весь пакет.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Capability:
	"""tool — имя инструмента; available(events, now) -> bool — открыт ли он клиенту
	при таком журнале. Решает код по фактам: модель просить открыть не может."""

	tool: str
	available: Callable


@dataclass(frozen=True)
class Stage:
	"""Где клиент в ходе дел и куда его вести — подсказка модели, не принуждение."""

	name: str
	label: str
	hint: str


@dataclass(frozen=True)
class Module:
	"""name — для трассировки; feature — ключ из features.FEATURES или None
	(включён всегда); stage(events, now) -> Stage | None; pin — типы событий,
	последнее из которых показывается всегда, даже старше окна; labels —
	{инструмент: метка} для записи «Бот ответил: …»; метка берётся из
	фактически вызванных инструментов."""

	name: str
	feature: str | None
	stage: Callable | None
	commitments: tuple
	pin: tuple
	labels: dict = field(default_factory=dict)
	capabilities: tuple = ()


_MODULES = []


def register(module):
	_MODULES.append(module)
	return module


def active(enabled):
	"""Модули, включённые на сайте: enabled — множество ключей возможностей."""
	return [m for m in _MODULES if m.feature is None or m.feature in enabled]


def tool_labels(modules):
	"""Метки инструментов всех переданных модулей одним словарём."""
	labels = {}
	for module in modules:
		labels.update(module.labels)
	return labels


def blocked_tools(modules, events, now):
	"""Инструменты, которые сейчас нельзя предлагать модели.

	Закрыт только тот, что объявлен возможностью, и ни одна из возможностей
	не доступна. Сбой условия считается «доступно»: нерабочая проверка не
	должна лишать клиента инструмента.
	"""
	declared, allowed = set(), set()
	for module in modules:
		for capability in module.capabilities:
			declared.add(capability.tool)
			try:
				opened = capability.available(events, now)
			except Exception:
				opened = True
			if opened:
				allowed.add(capability.tool)
	return declared - allowed

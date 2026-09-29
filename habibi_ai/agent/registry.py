"""Реестр модулей агента: что подключается к ядру одной записью.

Модуль — заказы, а завтра запись, напоминания, задачи — объявляет свои
правила стадий и обязательства. Проекция (state.py) и цикл про предметную
область не знают ничего и берут правила отсюда. Без frappe, как весь пакет.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field


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

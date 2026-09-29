"""Реестр модулей агента: что подключается к ядру одной записью.

Модуль — заказы, а завтра запись, напоминания, задачи — объявляет свои
правила стадий и обязательства. Проекция (state.py) и цикл про предметную
область не знают ничего и берут правила отсюда. Без frappe, как весь пакет.
"""

from dataclasses import dataclass
from typing import Callable, Optional


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
	последнее из которых показывается всегда, даже старше окна."""

	name: str
	feature: Optional[str]
	stage: Optional[Callable]
	commitments: tuple
	pin: tuple


_MODULES = []


def register(module):
	_MODULES.append(module)
	return module


def active(enabled):
	"""Модули, включённые на сайте: enabled — множество ключей возможностей."""
	return [m for m in _MODULES if m.feature is None or m.feature in enabled]

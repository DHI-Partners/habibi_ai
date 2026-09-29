"""Базовый модуль: справочные инструменты, которые есть у любого бота.

Своих стадий и обязательств у него нет — только метки для записи «Бот
ответил: меню». Включён всегда: без него ответ по меню в журнале был бы
безымянным.
"""

from habibi_ai.agent import registry

MODULE = registry.register(
	registry.Module(
		name="base",
		feature=None,
		stage=None,
		commitments=(),
		pin=(),
		labels={
			"get_menu": "меню",
			"get_working_hours": "режим работы",
			"get_delivery_zones": "зоны доставки",
		},
	)
)

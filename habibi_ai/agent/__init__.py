"""Ядро агента без frappe: реестр модулей, обязательства, проекция «хода дел».

Модуль заказов регистрируется при импорте пакета — как инструменты в tools.
"""

from habibi_ai.agent.registry import active, register, tool_labels  # noqa: F401

from habibi_ai.agent import orders  # noqa: E402,F401  регистрация при импорте пакета
from habibi_ai.agent import base  # noqa: E402,F401  регистрация при импорте пакета

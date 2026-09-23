"""Уведомление клиента о решении по заказу. Без frappe."""

NO_WORKFLOW_ACCEPT = "submit"
NO_WORKFLOW_REJECT = "discard"


class _Keep(dict):
	def __missing__(self, key):
		return "{" + key + "}"


def render(template, values):
	"""Рендер шаблона уведомления. Неправильный шаблон (пользователь опечатался) не роняет уведомление."""
	template = template or ""
	prepared = {k: "" if v is None else v for k, v in values.items()}
	try:
		return template.format_map(_Keep(prepared))
	except (ValueError, IndexError):
		# Шаблон сломан (открытая скобка, позиционный аргумент и т.д.).
		# Заменяем только известные плейсхолдеры, всё остальное оставляем как есть.
		result = template
		for key, value in prepared.items():
			result = result.replace("{" + key + "}", str(value))
		return result


def kind_of(action, accept, reject):
	if action == accept:
		return "accept"
	if action == reject:
		return "reject"
	return "other"

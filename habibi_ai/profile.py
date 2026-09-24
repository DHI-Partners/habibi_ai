"""Бизнес-профиль → текст для system prompt. Без frappe.

Промпт бота общий и живёт в Directus; здесь — только то, что владелец написал
о своей компании. Пустое не попадает: «Залог:» без текста модель восприняла
бы как «залога нет».
"""

# Пределы длины — одни для кабинета (settings.save_profile отказывает с
# понятной ошибкой) и для промпта (render обрезает то, что заведено в Desk в
# обход кабинета): бот не должен получить мегабайт текста в system prompt.
DESCRIPTION_MAX = 1000
RULE_TITLE_MAX = 80
RULE_TEXT_MAX = 1500
RULES_MAX = 20

TONES = {
	"friendly": "дружелюбный, на «ты» не переходить без повода клиента",
	"neutral": "нейтральный, вежливый",
	"formal": "официальный, на «вы»",
}

CORE = (
	("business_name", "Название"),
	("business_kind", "Вид деятельности"),
	("address", "Адрес"),
	("phone", "Телефон"),
)


def _clip(text, limit, what, warn):
	"""Обрезать с «…», а не молча: владелец увидит в тексте бота, что край."""
	text = (text or "").strip()
	if len(text) <= limit:
		return text
	if warn:
		warn(f"Бизнес-профиль: {what} длиннее {limit} символов, обрезано")
	return text[: limit - 1] + "…"


def render(profile, rules, warn=None):
	"""warn(message) — куда сказать об обрезке; модуль без frappe, лог — у вызывающего."""
	lines = [f"{label}: {profile[key].strip()}" for key, label in CORE if (profile.get(key) or "").strip()]
	description = _clip(profile.get("description"), DESCRIPTION_MAX, "описание", warn)
	if description:
		lines.append(description)
	if profile.get("tone") in TONES:
		lines.append(f"Тон общения: {TONES[profile['tone']]}.")
	if len(rules) > RULES_MAX and warn:
		warn(f"Бизнес-профиль: блоков больше {RULES_MAX}, лишние не попали в промпт")
	blocks = [
		f"{_clip(r.get('title'), RULE_TITLE_MAX, 'заголовок блока', warn)}:\n"
		f"{_clip(r['text'], RULE_TEXT_MAX, 'текст блока', warn)}"
		for r in rules[:RULES_MAX]
		if (r.get("text") or "").strip()
	]
	if not lines and not blocks:
		return ""
	head = "О компании (данные владельца, отвечай по ним):\n" + "\n".join(lines)
	return "\n\n".join([head, *blocks])

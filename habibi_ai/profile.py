"""Бизнес-профиль → текст для system prompt. Без frappe.

Промпт бота общий и живёт в Directus; здесь — только то, что владелец написал
о своей компании. Пустое не попадает: «Залог:» без текста модель восприняла
бы как «залога нет».
"""

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


def render(profile, rules):
	lines = [f"{label}: {profile[key].strip()}" for key, label in CORE if (profile.get(key) or "").strip()]
	if (profile.get("description") or "").strip():
		lines.append(profile["description"].strip())
	if profile.get("tone") in TONES:
		lines.append(f"Тон общения: {TONES[profile['tone']]}.")
	blocks = [f"{r['title'].strip()}:\n{r['text'].strip()}" for r in rules if (r.get("text") or "").strip()]
	if not lines and not blocks:
		return ""
	head = "О компании (данные владельца, отвечай по ним):\n" + "\n".join(lines)
	return "\n\n".join([head, *blocks])

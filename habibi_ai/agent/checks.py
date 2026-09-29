"""Вердикт проверки: единая форма для кода и, позже, для модели-контролёра.

Сейчас утверждения проверяет только код (Commitment). Второй проверяющий —
модель в теневом режиме, которая пишет вердикты в журнал, но ничего не
блокирует, — вернёт тот же Verdict, и цикл переделывать не придётся. Без frappe.
"""

from dataclasses import dataclass

OK = "ok"
VIOLATED = "violated"
UNSURE = "unsure"


@dataclass(frozen=True)
class Verdict:
	"""status — OK, VIOLATED или UNSURE; reason — для журнала и оператора."""

	status: str
	reason: str = ""


def check_commitment(commitment, text, turn, known):
	"""Кодовая проверка обязательства: не заявлено или подтверждено — OK, иначе VIOLATED."""
	if not commitment.claims(text):
		return Verdict(OK)
	if commitment.confirmed(text, turn, known):
		return Verdict(OK)
	return Verdict(VIOLATED, f"утверждение не подтверждено ({commitment.name})")

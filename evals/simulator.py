"""Симулятор пользователя (D4): отвечает на уточнения агента по `EvalCase.simulated_answers`.

Ключи simulated_answers:
  destination / dates / guests — ответ на уточняющий вопрос (выбирается по словам вопроса);
  suggestion — ответ на «Посмотреть варианты в <район>?» после запроса вне Большого Сочи (по умолчанию «да»);
  relax      — ответ на «нет вариантов + что можно поменять» (plan.relax): «да» — применить первое, или своё
               («не требовать всё включено»). Нет ключа — симулятор молчит: «нет вариантов» и есть честный итог;
  confirm    — ответ на «Оформить?» в конце сценария (по умолчанию «да»);
  default    — если вопрос не распознан.
Ответов нет — симулятор молчит (None): значит, без человека сценарий не дошёл бы до конца.
"""

from __future__ import annotations

from harness.contracts import EvalCase

QUESTION_WORDS = {
    "destination": ("район", "куда", "формат", "отдых"),
    "dates": ("когда", "дат", "ночей"),
    "guests": ("сколько", "гост", "взросл", "дет"),
}


class UserSimulator:
    def __init__(self, case: EvalCase) -> None:
        self.case = case
        self.used: set[str] = set()

    def _take(self, key: str) -> str | None:
        answer = self.case.simulated_answers.get(key)
        if answer is None or key in self.used:
            return None
        self.used.add(key)
        return answer

    def reply(self, answer: dict) -> str | None:
        """Ответ на уточнение агента или None, если ответить нечем."""
        pending = answer.get("pending_action") or {}
        if pending.get("suggested_area"):                       # вне локации: «посмотреть похожий район?»
            if "suggestion" in self.used:
                return None
            self.used.add("suggestion")
            return self.case.simulated_answers.get("suggestion", "да")
        text = " ".join([answer.get("text", ""), *answer.get("questions", [])]).lower()
        for key, words in QUESTION_WORDS.items():
            if any(w in text for w in words):
                found = self._take(key)
                if found:
                    return found
        for key in ("destination", "dates", "guests", "default"):   # вопрос не распознали — что осталось
            found = self._take(key)
            if found:
                return found
        return None

    def relax(self, answer: dict) -> str | None:
        """Ответ на «нет вариантов» с предложениями (plan.relax) — один раз и только если он задан в кейсе."""
        if not (answer.get("plan") or {}).get("relax"):
            return None
        return self._take("relax")

    def confirm(self) -> str:
        return self.case.simulated_answers.get("confirm", "да")

"""Даты поездки из текста — запасной путь, если LLM дат не вернула (L20).

Все относительные даты считаются от `today` (передаётся явно — так тесты не зависят от календаря).
Соглашение: «на выходные» = ближайшие пятница → воскресенье (2 ночи); прошедшая дата → тот же день в следующем году.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

MONTHS = {"январ": 1, "феврал": 2, "март": 3, "апрел": 4, "мая": 5, "май": 5, "июн": 6, "июл": 7, "август": 8,
          "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12}
_MONTH_RE = "(" + "|".join(MONTHS) + r")[а-я]*"
WEEKDAYS_RU = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")


def _month(word: str) -> int:
    return next(v for k, v in MONTHS.items() if word.startswith(k))


def _future(d: date, today: date) -> date:
    return d if d >= today else d.replace(year=d.year + 1)


def next_weekend(today: date) -> tuple[date, date]:
    """Пятница ближайших выходных и воскресенье. В пт–вс считаем, что речь о следующих выходных."""
    days = (4 - today.weekday()) % 7
    if today.weekday() >= 4:
        days = 7 - today.weekday() + 4
    fri = today + timedelta(days=days)
    return fri, fri + timedelta(days=2)


def parse_dates(text: str, today: date) -> tuple[date | None, date | None, str | None]:
    """(заезд, выезд, пояснение-допущение) по тексту; чего нет — None."""
    low = text.lower()
    m = re.search(r"(\d{1,2})\s*(?:-|–|—|по)\s*(\d{1,2})\s+" + _MONTH_RE, low)
    if m:
        month = _month(m.group(3))
        start = _future(date(today.year, month, int(m.group(1))), today)
        end = date(start.year, month, int(m.group(2)))
        if end <= start:
            return start, None, None
        return start, end, None
    m = re.search(r"\bс\s+(\d{1,2})\s+" + _MONTH_RE, low)
    if m:
        return _future(date(today.year, _month(m.group(2)), int(m.group(1))), today), None, None
    if "выходн" in low:
        fri, sun = next_weekend(today)
        return fri, sun, "«На выходные» понял как пятница — воскресенье, 2 ночи."
    return None, None, None


def month_from_text(text: str) -> int | None:
    m = re.search(r"\bв\s+" + _MONTH_RE, text.lower())
    return _month(m.group(1)) if m else None


_NUM = {"один": 1, "день": 1, "два": 2, "две": 2, "три": 3, "четыре": 4, "пять": 5, "неделю": 7}


def parse_shift(text: str) -> int | None:
    """«на два дня позже» → +2, «на день раньше» → −1, «на неделю позже» → +7; иначе None."""
    m = re.search(r"на\s+(\d+|один|день|два|две|три|четыре|пять|неделю)\s*(?:дн[а-я]*|день|сутки|недел[а-я]*)?\s*"
                  r"(позже|раньше)", text.lower())
    if not m:
        return None
    n = int(m.group(1)) if m.group(1).isdigit() else _NUM[m.group(1)]
    return n if m.group(2) == "позже" else -n

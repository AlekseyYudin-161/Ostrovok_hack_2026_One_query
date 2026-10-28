"""Общая проверка ограничений `Constraint` против кандидата-отеля.

Используется и агентом (детерминированная проверка перед ответом), и eval (метрика Hard Pass).
Ключи, которых нет у кандидата, считаются непроверяемыми (None), а не проваленными.
"""
from __future__ import annotations

import logging
from typing import Any

from harness.contracts import Constraint, HotelCandidate

log = logging.getLogger(__name__)


def _value_for(key: str, hotel: HotelCandidate) -> Any:
    if key == "budget_total":
        return hotel.price_total
    if key == "has_amenity":
        return [a.lower() for a in hotel.amenities]
    if key == "meal":
        return (hotel.meal or "").lower() or None
    return getattr(hotel, key, None)


def check(constraint: Constraint, hotel: HotelCandidate) -> bool | None:
    """True/False — проверено; None — у кандидата нет данных для проверки."""
    actual = _value_for(constraint.key, hotel)
    if actual is None:
        return None
    expected = constraint.value
    op = constraint.op
    try:
        if op == "<=":
            return actual <= expected
        if op == ">=":
            return actual >= expected
        if op == "==":
            return actual == expected
        if op == "in":
            return actual in expected
        if op == "contains":
            if isinstance(actual, list):
                return str(expected).lower() in actual
            return str(expected).lower() in str(actual).lower()
    except TypeError:
        log.warning("несравнимые типы: %s %s %r vs %r", constraint.key, op, actual, expected)
        return False
    raise ValueError(f"неизвестный оператор: {op}")


def check_all(constraints: list[Constraint], hotel: HotelCandidate) -> dict[str, bool | None]:
    return {f"{c.key}{c.op}{c.value}": check(c, hotel) for c in constraints}

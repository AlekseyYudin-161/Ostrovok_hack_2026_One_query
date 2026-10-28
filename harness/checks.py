"""Детерминированные проверки состыковок поездки (метрика оргов «ошибки в состыковках»).

Буферы — параметры с честными значениями по умолчанию (эвристики, не нормативы):
после посадки до выхода из аэропорта/вокзала и до вылета/отправления.
"""
from __future__ import annotations

import logging
from datetime import datetime, time, timedelta

from harness.contracts import ConnectionCheck, Itinerary

log = logging.getLogger(__name__)

AFTER_ARRIVAL_MIN = {"flight": 45, "train": 15, "local_train": 10, "bus": 10}
BEFORE_DEPARTURE_MIN = {"flight": 120, "train": 30, "local_train": 15, "bus": 15}


def _at(day, hhmm: str) -> datetime:
    h, m = (int(x) for x in hhmm.split(":"))
    return datetime.combine(day, time(h, m))


def _transfer_min(it: Itinerary, idx: int) -> int:
    return it.transfers[idx].minutes if len(it.transfers) > idx else 0


def check_connections(it: Itinerary) -> list[ConnectionCheck]:
    checks: list[ConnectionCheck] = []
    hotel = it.hotel
    checkin_dt = _at(it.checkin, hotel.checkin_time if hotel else "14:00")
    checkout_dt = _at(it.checkout, hotel.checkout_time if hotel else "12:00")

    if it.outbound:
        o = it.outbound
        ready = o.arrive.replace(tzinfo=None) + timedelta(minutes=AFTER_ARRIVAL_MIN[o.kind] + _transfer_min(it, 0))
        if ready.date() > it.checkin:
            checks.append(ConnectionCheck(
                rule="dates_match", ok=False, severity="error",
                message=f"Доберётесь до отеля {ready:%d.%m %H:%M} — позже даты заезда {it.checkin:%d.%m}: ночь пропадёт.",
                fixes=["сдвинуть заезд на день позже", "выбрать более ранний рейс"]))
        elif ready < checkin_dt - timedelta(hours=3):
            wait = checkin_dt - ready
            checks.append(ConnectionCheck(
                rule="arrival_vs_checkin", ok=True, severity="warning",
                message=f"Будете в отеле в {ready:%H:%M}, заселение с {checkin_dt:%H:%M} — ждать ≈{wait.seconds // 3600} ч.",
                fixes=["запросить ранний заезд", "оставить багаж в отеле", "оплатить ночь накануне"]))
        else:
            checks.append(ConnectionCheck(rule="arrival_vs_checkin", ok=True, severity="info",
                                          message=f"Прибытие в отель ≈{ready:%H:%M}, заселение с {checkin_dt:%H:%M}."))

    if it.inbound:
        i = it.inbound
        latest_leave = i.depart.replace(tzinfo=None) - timedelta(
            minutes=BEFORE_DEPARTURE_MIN[i.kind] + _transfer_min(it, 1))
        if latest_leave < checkout_dt - timedelta(hours=12):
            checks.append(ConnectionCheck(
                rule="checkout_vs_departure", ok=False, severity="error",
                message=f"Выезжать нужно {latest_leave:%d.%m %H:%M} — раньше дня выезда из отеля: лишняя оплаченная ночь.",
                fixes=["сократить проживание на ночь", "выбрать более поздний рейс"]))
        elif latest_leave < checkout_dt:
            checks.append(ConnectionCheck(
                rule="checkout_vs_departure", ok=True, severity="warning",
                message=f"Выезжать из отеля в {latest_leave:%H:%M}, до расчётного часа {checkout_dt:%H:%M}.",
                fixes=[]))
        elif latest_leave - checkout_dt > timedelta(hours=4):
            checks.append(ConnectionCheck(
                rule="checkout_vs_departure", ok=True, severity="warning",
                message=f"После выезда в {checkout_dt:%H:%M} до отъезда в аэропорт/вокзал ≈"
                        f"{(latest_leave - checkout_dt).seconds // 3600} ч.",
                fixes=["запросить поздний выезд", "оставить багаж в отеле"]))
        else:
            checks.append(ConnectionCheck(rule="checkout_vs_departure", ok=True, severity="info",
                                          message=f"Выезд {checkout_dt:%H:%M}, к транспорту — к {latest_leave:%H:%M}."))

    if it.outbound and it.inbound and it.inbound.depart <= it.outbound.arrive:
        checks.append(ConnectionCheck(rule="dates_match", ok=False, severity="error",
                                      message="Обратный рейс раньше прибытия туда.", fixes=["пересобрать даты"]))
    log.debug("checks: %s", [(c.rule, c.severity) for c in checks])
    return checks


def connection_errors(it: Itinerary) -> int:
    """Число ошибок (severity=error) — для eval."""
    return sum(1 for c in check_connections(it) if c.severity == "error")

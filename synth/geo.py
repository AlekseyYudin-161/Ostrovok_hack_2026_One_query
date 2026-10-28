"""Геометрия без координат: побережье — отрезок (coast_km), горы — ветка от Адлера (mountain_km).

Все расстояния и время в пути выводятся отсюда, поэтому всегда согласованы между собой.
"""
from __future__ import annotations

import itertools
import math

from synth.refdata import PRICING

SPEED = PRICING["road_speed_kmh"]


def area_road(a: dict, b: dict) -> tuple[float, float]:
    """(км по побережью, км по горной дороге) между центрами районов."""
    if a["area_id"] == b["area_id"]:
        return 0.0, 0.0
    am, bm = a.get("mountain_km", 0), b.get("mountain_km", 0)
    if am and bm:
        return 0.0, abs(am - bm) + 2
    return abs(a["coast_km"] - b["coast_km"]), am + bm


def drive_minutes(coast_km: float, mountain_km: float, local_km: float) -> int:
    m = (coast_km / SPEED["coast"] + mountain_km / SPEED["mountain"] + local_km / SPEED["in_area"]) * 60
    return int(math.ceil((m + PRICING["road_overhead_min"]) / 5) * 5)


def route(a: dict, b: dict, local_a: float = 0.0, local_b: float = 0.0) -> dict:
    """Маршрут между точками внутри районов a и b (local_* — км от центра района)."""
    coast, mount = area_road(a, b)
    local = local_a + local_b if a["area_id"] != b["area_id"] else max(abs(local_a - local_b), 0.5)
    km = round(coast + mount + local, 1)
    return {"km": km, "coast_km": coast, "mountain_km": mount, "local_km": local,
            "drive_min": drive_minutes(coast, mount, local)}


def line_minutes(stops_km: list[float], i: int, j: int, speed_kmh: float = 45, stop_min: int = 2) -> int:
    """Время поездки на электричке/автобусе между остановками i<j по позициям км."""
    dist = abs(stops_km[j] - stops_km[i])
    return int(dist / speed_kmh * 60 + stop_min * abs(j - i))


def line_positions(areas: dict[str, dict], stops: list[str]) -> list[float]:
    """Позиция остановок вдоль линии (км от первой остановки)."""
    pos, acc = [0.0], 0.0
    for prev, cur in itertools.pairwise(stops):
        c, m = area_road(areas[prev], areas[cur])
        acc += c + m
        pos.append(acc)
    return pos

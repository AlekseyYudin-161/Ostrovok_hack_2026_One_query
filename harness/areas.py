"""Справочник районов Большого Сочи для агента (ADR-011, ADR-019).

Откуда берутся районы:
  * ODIN_AREAS_FILE=<путь>   — из указанного файла (формат synth/data/areas.json от ML);
  * ODIN_AREAS_FILE=builtin  — встроенные 5 районов (так работают тесты: результат не зависит от данных ML);
  * переменная не задана     — synth/data/areas.json, если он есть в репо, иначе встроенные 5.
Синонимы («Сириус» → Имеретинка) берутся из поля `aliases` файла ML; пока его нет — из BUILTIN_ALIASES.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from pydantic import BaseModel

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent.parent
ML_AREAS = ROOT / "synth" / "data" / "areas.json"

# Словарь пожеланий — тот же, что best_for у ML (synth/schema.py: AREA_BEST_FOR)
WISHES = ("море", "горы/лыжи", "с детьми", "тихо", "бюджетно", "тусовка", "оздоровление",
          "экскурсии", "активный отдых", "романтика", "люкс")


class Area(BaseModel):
    area_id: str
    name: str
    best_for: list[str] = []
    aliases: list[str] = []
    description: str = ""


BUILTIN = [
    Area(area_id="sochi_center", name="Сочи-центр", best_for=["море", "экскурсии", "тусовка"], aliases=["сочи", "центр сочи"]),
    Area(area_id="adler", name="Адлер", best_for=["море", "бюджетно", "с детьми"], aliases=["адлер"]),
    Area(area_id="khosta", name="Хоста", best_for=["тихо", "бюджетно", "оздоровление", "море"], aliases=["хоста"]),
    Area(area_id="sirius", name="Сириус", best_for=["с детьми", "море", "активный отдых"],
         aliases=["сириус", "имеретин", "олимпийский парк"]),
    Area(area_id="krasnaya_polyana", name="Красная Поляна", best_for=["горы/лыжи", "активный отдых", "романтика"],
         aliases=["поляна", "горы", "лыж"]),
]
# Синонимы для районов из файла ML, пока в нём нет поля aliases (ключ — начало названия района)
BUILTIN_ALIASES = {"имерет": ["сириус", "олимпийский парк", "имеретин"], "красная поляна": ["поляна", "горы", "лыж"],
                   "роза": ["роза хутор"], "адлер": ["адлер"], "хоста": ["хоста"], "сочи": ["сочи"]}


def load_areas(path: str | Path | None = None) -> list[Area]:
    choice = str(path) if path else os.getenv("ODIN_AREAS_FILE", "")
    if choice == "builtin":
        return BUILTIN
    file = Path(choice) if choice else ML_AREAS
    if not file.exists():
        if choice:
            log.warning("файл районов %s не найден — встроенные 5", file)
        return BUILTIN
    raw = json.loads(file.read_text(encoding="utf-8"))
    areas = []
    for a in raw:
        area = Area(area_id=a["area_id"], name=a["name"], best_for=a.get("best_for", []),
                    aliases=[s.lower() for s in a.get("aliases", [])], description=a.get("description", ""))
        if not area.aliases:
            low = area.name.lower()
            area.aliases = [low] + next((v for k, v in BUILTIN_ALIASES.items() if low.startswith(k)), [])
        areas.append(area)
    log.info("районы из %s: %d", file, len(areas))
    return areas or BUILTIN


def find_area(areas: list[Area], text: str) -> Area | None:
    """Район по названию или синониму, встреченному в тексте (для правил и для проверки ответа LLM)."""
    low = text.lower()
    for a in areas:
        if a.name.lower() in low:
            return a
    return next((a for a in areas if any(al in low for al in a.aliases)), None)


def suggest_area(areas: list[Area], wishes: list[str]) -> Area:
    """Похожий район внутри Большого Сочи: больше всего совпадений по «для чего», при равенстве — первый."""
    return max(areas, key=lambda a: len(set(a.best_for) & set(wishes)))

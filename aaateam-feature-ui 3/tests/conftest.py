"""Общие настройки тестов: районы — встроенные 5, чтобы результат не зависел от данных ML (harness/areas.py)."""
import os

os.environ.setdefault("ODIN_AREAS_FILE", "builtin")
os.environ.setdefault("ODIN_CATALOG", "fake")   # старые тесты — на фейковом поиске; сборку поездки проверяет test_trip.py

"""Общие настройки тестов: районы — встроенные 5, чтобы результат не зависел от данных ML (harness/areas.py)."""
import os

os.environ.setdefault("ODIN_AREAS_FILE", "builtin")

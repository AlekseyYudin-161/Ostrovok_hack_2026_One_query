"""Тесты UI. AppTest гоняет приложение на фейковом агенте (AGENT_BACKEND=fake по умолчанию)."""
from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parents[1] / "ui" / "app.py")


def _text(at: AppTest) -> str:
    return " ".join(m.value for m in at.markdown)


def test_start_screen_renders() -> None:
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception
    assert "Готовые" in _text(at)


def test_chat_goes_to_results() -> None:
    at = AppTest.from_file(APP, default_timeout=30).run()
    at.chat_input[0].set_value("Питер на выходные").run()
    assert not at.exception
    assert at.session_state["screen"] == "results"
    assert "Санкт-Петербург" in _text(at)


def test_question_goes_to_clarify() -> None:
    at = AppTest.from_file(APP, default_timeout=30).run()
    at.chat_input[0].set_value("хочу отдохнуть").run()  # города нет: фейк просит уточнить
    assert not at.exception
    assert at.session_state["screen"] == "clarify"


def test_every_demo_screen_renders() -> None:
    for name in ["start", "working", "clarify", "areas", "results", "diff", "cart", "paid", "none"]:
        at = AppTest.from_file(APP, default_timeout=30)
        at.query_params["screen"] = name
        at.run()
        assert not at.exception, name


def test_confirm_screen_has_yes_no():
    at = AppTest.from_file(APP, default_timeout=30)
    at.query_params["screen"] = "confirm"
    at.run()
    assert not at.exception
    assert [b.label for b in at.button] == ["Да", "Нет"]


def test_results_show_agent_question_and_changes() -> None:
    at = AppTest.from_file(APP, default_timeout=30)
    at.query_params["screen"] = "results"
    at.run()
    assert not at.exception
    page = _text(at)
    assert "Вопрос агента" in page and "Оформить вариант 3" in page and "Изменил: Даты" in page


def test_paid_screen_keeps_chat_input() -> None:
    at = AppTest.from_file(APP, default_timeout=30)
    at.query_params["screen"] = "paid"
    at.run()
    assert not at.exception
    assert len(at.chat_input) == 1

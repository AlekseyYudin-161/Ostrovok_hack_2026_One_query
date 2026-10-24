from streamlit.testing.v1 import AppTest


def test_streamlit_chat_renders_answer() -> None:
    at = AppTest.from_file("../ui/app.py", default_timeout=30).run()
    assert not at.exception
    at.chat_input[0].set_value("Питер на выходные").run()
    assert not at.exception
    texts = " ".join(m.markdown[0].value for m in at.chat_message if m.markdown)
    assert "Санкт-Петербург" in texts

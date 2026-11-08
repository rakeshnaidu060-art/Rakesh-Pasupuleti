"""Tests for the Streamlit app (app.py), run headless with AppTest.

They use the real secrets from .streamlit/secrets.toml, overriding single
values to simulate failures. The flow test calls the OpenAI API.
"""

import tomllib
from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from backend.graph import check_connection, close_driver

ROOT = Path(__file__).resolve().parent.parent
SECRETS_FILE = ROOT / ".streamlit" / "secrets.toml"

pytestmark = pytest.mark.skipif(not SECRETS_FILE.exists(), reason="No .streamlit/secrets.toml")


def app(**overrides) -> AppTest:
    """An AppTest of app.py using the real secrets, with some values overridden."""
    close_driver()  # make the app open a fresh connection with these secrets
    st.cache_data.clear()  # forget connection checks cached by earlier tests
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=240)
    secrets = tomllib.loads(SECRETS_FILE.read_text(encoding="utf-8"))
    for key, value in {**secrets, **overrides}.items():
        at.secrets[key] = value
    return at


@pytest.fixture(autouse=True)
def fresh_driver():
    yield
    close_driver()


@pytest.fixture
def neo4j_up():
    ok, message = check_connection()
    if not ok:
        pytest.skip(message)


def all_text(at: AppTest) -> str:
    parts = [m.value for m in at.markdown] + [c.value for c in at.caption] + [e.value for e in at.error]
    return "\n".join(parts)


def test_home_page(neo4j_up):
    at = app().run()
    assert not at.exception
    assert at.title[0].value == "AI Architecture Advisor"
    assert not at.chat_input[0].disabled
    text = all_text(at)
    assert "Documented facts" in text and "Inferences" in text and "Recommendations" in text
    assert "P180" in at.info[0].value


def test_neo4j_unreachable_shows_friendly_message():
    at = app(NEO4J_URI="neo4j+s://unreachable.invalid").run()
    assert not at.exception
    errors = " ".join(e.value for e in at.error)
    assert "cannot be reached" in errors and "paused" in errors
    assert at.chat_input[0].disabled


def test_bad_openai_key_shows_friendly_message(neo4j_up):
    at = app(OPENAI_API_KEY="sk-invalid-key-for-testing").run()
    at.chat_input[0].set_value("What would be affected if we replaced the Booking System?").run()
    assert not at.exception
    assert any("rejected the API key" in e.value for e in at.error)


def test_clarification_then_answer(neo4j_up):
    at = app().run()
    at.chat_input[0].set_value("What happens if we replace the booking tool?").run()
    assert not at.exception
    options = [b for b in at.button if b.label == "Booking System"]
    assert options, "expected a clickable clarification choice"

    options[0].click().run()
    assert not at.exception
    text = all_text(at)
    assert "Replacing Booking System affects 7 capabilities; 6 would be left uncovered." in text
    for heading in ("Documented facts", "Inferences", "Recommendations", "Not documented in P180", "P180 evidence"):
        assert heading in text, heading
    assert "P180 p. 22" in text
    assert any(e.label.startswith("p. 22") for e in at.expander)
    assert at.toggle[0].label == "Show mind map" and at.toggle[0].value
    messages = at.session_state["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant", "user", "assistant"]
    assert messages[-1]["response"].kind == "answer"

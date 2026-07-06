import os
import sys
from unittest.mock import MagicMock

import pytest

from utils.llm import Completion

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture
def mock_anthropic_client():
    """Anthropic client whose .messages.create() returns whatever the test sets.

    Usage:
        def test_x(mock_anthropic_client):
            mock_anthropic_client.messages.create.return_value = make_response("YES")
            ...
    """
    client = MagicMock()
    return client


def make_text_response(text: str):
    """Build a fake Anthropic response with a single text block."""
    response = MagicMock()
    block = MagicMock()
    block.type = "text"
    block.text = text
    response.content = [block]
    response.usage = MagicMock(input_tokens=10, output_tokens=5)
    return response


def make_tool_use_response(tool_name: str, tool_input: dict):
    """Build a fake Anthropic response with a tool_use block (for trader agent tests)."""
    response = MagicMock()
    block = MagicMock()
    block.type = "tool_use"
    block.name = tool_name
    block.input = tool_input
    response.content = [block]
    response.usage = MagicMock(input_tokens=20, output_tokens=15)
    return response


@pytest.fixture
def mock_db_cursor():
    """Cursor stub recording executed SQL and returning configured rows."""
    cursor = MagicMock()
    cursor.fetchone.return_value = None
    cursor.fetchall.return_value = []
    cursor.rowcount = 0
    return cursor


@pytest.fixture
def mock_db_conn(mock_db_cursor):
    """Connection stub. cursor() returns a context-managed mock_db_cursor."""
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value = mock_db_cursor
    return conn


def make_completion(text: str, prompt_tokens: int = 10, completion_tokens: int = 5) -> Completion:
    """Build a Completion for provider-based agent tests."""
    return Completion(text=text, prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)


@pytest.fixture
def mock_llm():
    """LLMProvider stub. Set .complete/.structured return_value or side_effect per test."""
    llm = MagicMock()
    llm.complete.return_value = Completion(text="")
    llm.structured.return_value = {}
    return llm

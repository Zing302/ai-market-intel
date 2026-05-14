"""Sanity tests for shared fixtures in conftest.py.

If these break, all downstream agent tests break too.
"""
from tests.conftest import make_text_response, make_tool_use_response


def test_mock_anthropic_text_response():
    response = make_text_response("BULLISH")
    assert response.content[0].type == "text"
    assert response.content[0].text == "BULLISH"
    assert response.usage.input_tokens == 10


def test_mock_anthropic_tool_use_response():
    payload = {"action": "BUY", "score": 0.7, "rationale": "x" * 60}
    response = make_tool_use_response("submit_recommendation", payload)
    block = response.content[0]
    assert block.type == "tool_use"
    assert block.name == "submit_recommendation"
    assert block.input["action"] == "BUY"
    assert block.input["score"] == 0.7


def test_mock_anthropic_client_records_calls(mock_anthropic_client):
    mock_anthropic_client.messages.create.return_value = make_text_response("ok")
    result = mock_anthropic_client.messages.create(model="claude-haiku-4-5-20251001")
    assert result.content[0].text == "ok"
    mock_anthropic_client.messages.create.assert_called_once()


def test_mock_db_cursor_records_execute(mock_db_cursor):
    mock_db_cursor.execute("SELECT 1")
    mock_db_cursor.execute.assert_called_once_with("SELECT 1")


def test_mock_db_conn_context_manages_cursor(mock_db_conn, mock_db_cursor):
    with mock_db_conn.cursor() as cur:
        cur.execute("SELECT 1")
    mock_db_cursor.execute.assert_called_once_with("SELECT 1")

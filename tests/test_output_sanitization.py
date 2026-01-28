import pytest

from app.core.mcp_wrapper import _extract_user_visible_answer


def test_extract_user_visible_answer_final_answer_marker():
    text = "Thought: x\nFinal Answer: hello world"
    assert _extract_user_visible_answer(text) == "hello world"


def test_extract_user_visible_answer_react_trace_without_final_answer():
    text = "Thought: a\nAction: Search\nObservation: something\n\nThis is the final response."
    assert _extract_user_visible_answer(text) == "This is the final response."


def test_extract_user_visible_answer_plain_text_passthrough():
    text = "I cannot provide that information."
    assert _extract_user_visible_answer(text) == text

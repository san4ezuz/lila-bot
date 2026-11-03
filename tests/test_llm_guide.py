"""Tests for the real src.llm.guide plumbing (not the autouse stub from conftest.py, which every
other test relies on). We capture a reference to the real `_call` before any test's monkeypatch
fixture runs, so these tests exercise the actual fallback-on-failure behavior without needing a
live ANTHROPIC_API_KEY — see docs/PLAN.md §8.5."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import anthropic
import pytest

import src.llm.guide as guide_module
from src.core.board import get_default_board
from src.llm.client import extract_text, supports_effort
from src.llm.prompts import (
    build_deepen_context,
    build_interpret_cell_context,
    build_summary_context,
)

_real_call = guide_module._call  # captured before conftest's autouse monkeypatch applies


def test_extract_text_skips_non_text_blocks():
    message = SimpleNamespace(
        content=[
            SimpleNamespace(type="thinking", text="internal reasoning"),
            SimpleNamespace(type="text", text="visible reply"),
        ]
    )
    assert extract_text(message) == "visible reply"


def test_extract_text_joins_multiple_text_blocks():
    message = SimpleNamespace(
        content=[
            SimpleNamespace(type="text", text="part one"),
            SimpleNamespace(type="text", text="part two"),
        ]
    )
    assert extract_text(message) == "part one\npart two"


async def test_call_falls_back_on_api_error(monkeypatch):
    fake_client = SimpleNamespace(
        messages=SimpleNamespace(
            create=AsyncMock(side_effect=anthropic.APIConnectionError(request=SimpleNamespace()))
        )
    )
    monkeypatch.setattr("src.llm.guide.get_client", lambda: fake_client)

    result = await _real_call("some context", max_tokens=100, effort="medium", fallback="FALLBACK TEXT")

    assert result == "FALLBACK TEXT"


async def test_call_falls_back_on_missing_credentials(monkeypatch):
    """Regression test for a real bug found via a live smoke test (not caught by mocks alone):
    the SDK raises a plain TypeError — not an AnthropicError subclass — when it can't resolve
    credentials at request-build time (e.g. ANTHROPIC_API_KEY unset). Before this was handled,
    it 500'd the whole request instead of falling back."""

    async def _raise_type_error(*args, **kwargs):
        raise TypeError(
            "Could not resolve authentication method. Expected one of api_key, auth_token, "
            "or credentials to be set."
        )

    fake_client = SimpleNamespace(messages=SimpleNamespace(create=_raise_type_error))
    monkeypatch.setattr("src.llm.guide.get_client", lambda: fake_client)

    result = await _real_call("ctx", max_tokens=100, effort="medium", fallback="NO KEY FALLBACK")

    assert result == "NO KEY FALLBACK"


async def test_call_falls_back_on_timeout(monkeypatch):
    async def _hangs(*args, **kwargs):
        import asyncio

        await asyncio.sleep(10)

    fake_client = SimpleNamespace(messages=SimpleNamespace(create=_hangs))
    monkeypatch.setattr("src.llm.guide.get_client", lambda: fake_client)
    monkeypatch.setattr("src.llm.guide._CALL_TIMEOUT_SECONDS", 0.05)

    result = await _real_call("ctx", max_tokens=100, effort="medium", fallback="TIMEOUT FALLBACK")

    assert result == "TIMEOUT FALLBACK"


async def test_call_returns_extracted_text_on_success(monkeypatch):
    response = SimpleNamespace(
        content=[SimpleNamespace(type="text", text="  a real reply  ")], stop_reason="end_turn"
    )
    fake_client = SimpleNamespace(
        messages=SimpleNamespace(create=AsyncMock(return_value=response))
    )
    monkeypatch.setattr("src.llm.guide.get_client", lambda: fake_client)

    result = await _real_call("ctx", max_tokens=100, effort="medium", fallback="unused")

    assert result == "a real reply"


async def test_call_falls_back_and_warns_when_thinking_used_up_max_tokens(monkeypatch, caplog):
    """Real Haiku 5.5 behavior: thinking counts toward max_tokens, so a tight ceiling can stop the
    response after a thinking block with no text at all."""
    response = SimpleNamespace(
        content=[SimpleNamespace(type="thinking", thinking="")], stop_reason="max_tokens"
    )
    fake_client = SimpleNamespace(
        messages=SimpleNamespace(create=AsyncMock(return_value=response))
    )
    monkeypatch.setattr("src.llm.guide.get_client", lambda: fake_client)

    result = await _real_call("ctx", max_tokens=100, effort="low", fallback="FALLBACK")

    assert result == "FALLBACK"
    assert "stopped early: max_tokens" in caplog.text


@pytest.mark.parametrize(
    ("model", "supported"),
    [
        ("claude-haiku-5-5", True),
        ("claude-opus-5-5", True),
        ("claude-haiku-4-5", False),
        ("claude-haiku-4-5-20251001", False),
    ],
)
def test_supports_effort(model, supported):
    assert supports_effort(model) is supported


async def test_interpret_cell_uses_real_call_and_board_data(monkeypatch):
    board = get_default_board()
    cell = board.get_cell(6)
    captured = {}

    async def _capture(context, max_tokens, effort, fallback):
        captured["context"] = context
        captured["max_tokens"] = max_tokens
        captured["effort"] = effort
        captured["fallback"] = fallback
        return "мокнутый ответ"

    monkeypatch.setattr("src.llm.guide._call", _capture)

    result = await guide_module.interpret_cell(
        intention="разобраться с застоем",
        cell=cell,
        event="entered",
        transition=None,
        visited=[],
        recent_player_answers=[],
    )

    assert result == "мокнутый ответ"
    assert cell.name in captured["context"]
    assert "разобраться с застоем" in captured["context"]
    assert captured["effort"] == "low"
    assert captured["max_tokens"] == 2000


async def test_summarize_game_leaves_room_for_thinking(monkeypatch):
    captured = {}

    async def _capture(context, max_tokens, effort, fallback):
        captured["effort"] = effort
        captured["max_tokens"] = max_tokens
        return "резюме"

    monkeypatch.setattr("src.llm.guide._call", _capture)

    result = await guide_module.summarize_game("тест", ["6 Заблуждение", "9 Чувственный план"])

    assert result == "резюме"
    assert captured["effort"] == "low"
    assert captured["max_tokens"] == 3000


def test_build_interpret_cell_context_includes_facets_and_questions():
    context = build_interpret_cell_context(
        intention="тест",
        cell_id=6,
        cell_name="Заблуждение",
        theme="тема клетки",
        facets=["грань 1", "грань 2"],
        questions=["вопрос 1?"],
        event="entered",
        transition=None,
        visited=[],
        recent_player_answers=[],
    )
    assert "Заблуждение" in context
    assert "грань 1" in context
    assert "вопрос 1?" in context
    assert "тест" in context


def test_build_deepen_context_includes_player_answer():
    context = build_deepen_context("тест", "Заблуждение", "мой ответ")
    assert "мой ответ" in context
    assert "Заблуждение" in context


def test_build_summary_context_joins_path():
    context = build_summary_context("тест", ["6 Заблуждение", "9 Чувственный план"])
    assert "6 Заблуждение → 9 Чувственный план" in context


def test_build_interpret_cell_context_names_the_skipped_transition_source():
    context = build_interpret_cell_context(
        intention="тест",
        cell_id=8,
        cell_name="Алчность",
        theme="тема",
        facets=["грань"],
        questions=["вопрос?"],
        event="moved",
        transition="snake",
        visited=[],
        recent_player_answers=[],
        skipped_cell_id=12,
        skipped_cell_name="Зависть",
        skipped_cell_theme="тема пропущенной",
    )
    assert "12" in context
    assert "Зависть" in context


def test_build_interpret_cell_context_gender_instruction():
    male_context = build_interpret_cell_context(
        intention=None, cell_id=1, cell_name="x", theme="t", facets=[], questions=["q"],
        event="moved", transition=None, visited=[], recent_player_answers=[], gender="male",
    )
    female_context = build_interpret_cell_context(
        intention=None, cell_id=1, cell_name="x", theme="t", facets=[], questions=["q"],
        event="moved", transition=None, visited=[], recent_player_answers=[], gender="female",
    )
    assert "мужском роде" in male_context
    assert "женском роде" in female_context


def test_first_try_entry_message_congratulates_without_deep_analysis():
    message = guide_module.first_try_entry_message("разобраться с работой")
    assert "Заблуждение" in message
    assert "разобраться с работой" in message
    assert "поздравляю" in message.lower()


def test_six_extra_roll_note_mentions_the_bonus_roll():
    assert "ещё один бросок" in guide_module.six_extra_roll_note()


def test_intention_ack_message_uses_gendered_verb():
    assert "пришла" in guide_module.intention_ack_message("тест", gender="female")
    assert "пришёл" in guide_module.intention_ack_message("тест", gender="male")

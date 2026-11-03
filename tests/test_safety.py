from types import SimpleNamespace
from unittest.mock import AsyncMock

import anthropic
import pytest

from src.llm import safety
from src.llm.client import LlmResult

CRISIS_PHRASES = [
    "я не хочу жить дальше",
    "хочу умереть",
    "суицид",
    "думаю о суициде",
    "хочется повеситься",
    "Повешусь, наверное",
    "убить себя",
    "покончу с собой",
    "она покончила с собой, и я думаю о том же",
    "не хочу больше жить",
    "Больше не хочу жить.",
    "жить не хочется совсем",
    "вскрыть вены",
    "порезала себя вчера",
    "режу себя, когда плохо",
    "лучше бы меня не было",
    "Лучше бы меня не стало",
    "хочу, чтобы меня не было",
    "мысли о самоубийстве",
    "наложить на себя руки",
    "свести счёты с жизнью",
    "нет смысла жить",
    "устала жить",
    "лучше бы я умерла",
    "наглоталась таблеток",
    "выпить все таблетки",
    "выйти в окно? нет, выпрыгнуть в окно",
    "спрыгнуть с крыши",
    "брошусь под поезд",
    "не хочу больше просыпаться",
    "хочу уснуть и не проснуться",
    "убьюсь",
    "селфхарм",
    "самоповреждение",
    "причинить себе вред",
]

SAFE_PHRASES = [
    "хочу убить время до вечера",
    "умираю со смеху",
    "смертельно устала на работе",
    "резко себя почувствовала лучше",
    "выпила таблетку от головы и легла",
    "умерла моя старая версия, рождается новая",
    "хочу жить по-новому",
    "хочу больше жить для себя",
    "змея утащила меня вниз",
    "повесила картину в новой квартире",
    "отношения с мужем",
]


@pytest.mark.parametrize("text", CRISIS_PHRASES)
def test_screen_catches_crisis_phrases(text):
    assert safety.screen(text)


@pytest.mark.parametrize("text", SAFE_PHRASES)
def test_screen_ignores_everyday_phrases(text):
    assert not safety.screen(text)


@pytest.mark.parametrize(
    ("answer", "risk"), [("RISK", True), ("risk", True), ("SAFE", False), ("", False)]
)
def test_parse_verdict(answer, risk):
    assert safety.parse_verdict(answer) is risk


def _fake_client(create: AsyncMock) -> SimpleNamespace:
    return SimpleNamespace(messages=SimpleNamespace(create=create))


async def test_classify_reads_the_model_verdict(monkeypatch):
    monkeypatch.undo()  # drop the conftest stub, exercise the real call
    reply = SimpleNamespace(content=[SimpleNamespace(type="text", text="RISK")])
    create = AsyncMock(return_value=reply)
    monkeypatch.setattr("src.llm.client.get_client", lambda: _fake_client(create))

    result = await safety.classify("мне кажется, всем будет легче без меня")
    assert safety.is_risk(result) is True
    assert result.kind == "classifier"
    sent = create.call_args.kwargs["messages"][0]["content"]
    assert "<message>" in sent


async def test_classify_fails_open_on_api_error(monkeypatch):
    monkeypatch.undo()
    request = SimpleNamespace(method="POST", url="https://api.anthropic.com")
    create = AsyncMock(side_effect=anthropic.APIConnectionError(request=request))  # type: ignore[arg-type]
    monkeypatch.setattr("src.llm.client.get_client", lambda: _fake_client(create))

    result = await safety.classify("что угодно")
    assert safety.is_risk(result) is False
    assert result.fallback is True


async def test_check_skips_classifier_when_dictionary_fires(monkeypatch):
    verdict = LlmResult(text="RISK", kind="classifier", model="stub")
    classify = AsyncMock(return_value=verdict)
    monkeypatch.setattr(safety, "classify", classify)

    assert await safety.check("хочу умереть") == ("lexical", None)
    classify.assert_not_awaited()
    assert await safety.check("всем будет легче без меня") == ("classifier", verdict)
